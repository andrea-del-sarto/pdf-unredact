from __future__ import annotations

import inspect
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

from engine_api import ENGINE_API_VERSION, EngineClient, EngineError, EngineErrorCode, EngineLimits
import pdf_unredact


class SecurityHardeningTests(unittest.TestCase):
    def _fixture(self, directory: Path, *, pages: int = 1, extended_active=False) -> Path:
        source = directory / "input.pdf"
        doc = pymupdf.open()
        for page_no in range(pages):
            page = doc.new_page(width=400, height=200)
            page.insert_text((50, 80), f"VISIBLE SECRET TEXT {page_no + 1}", fontsize=14)
            annot = page.add_redact_annot(pymupdf.Rect(105, 62, 165, 84), fill=(0, 0, 0))
            annot.update()
            if page_no == 0:
                page.insert_link({
                    "kind": pymupdf.LINK_URI,
                    "from": pymupdf.Rect(40, 110, 180, 130),
                    "uri": "https://example.invalid/",
                })
        doc.embfile_add("payload.txt", b"fixture", filename="payload.txt")
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", "<</S/JavaScript/JS(app.alert\\(1\\))>>")
        if extended_active:
            acro = doc.get_new_xref()
            doc.update_object(acro, "<</XFA (active-xfa)>>")
            doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"{acro} 0 R")
            names = doc.get_new_xref()
            doc.update_object(names, "<</JavaScript <</Names [(x) <</S/JavaScript/JS(app.alert\\(2\\))>>]>>>>")
            doc.xref_set_key(doc.pdf_catalog(), "Names", f"{names} 0 R")
        doc.save(source)
        doc.close()
        return source

    def _registered(self, client: EngineClient, source: Path, output: Path | None = None):
        ih = client.register_input(source)
        oh = client.register_output(output) if output is not None else None
        return ih, oh

    def test_export_sanitizes_extended_active_content_and_preserves_hidden_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root, extended_active=True)
            output = root / "clean.pdf"
            client = EngineClient()
            try:
                ih, oh = self._registered(client, source, output)
                result = client.export(
                    input_handle=ih, output_handle=oh, mode="clean", remove="redactions"
                )
                self.assertTrue(result["active_content_sanitized"])
                doc = pymupdf.open(output)
                try:
                    self.assertEqual(doc.embfile_count(), 0)
                    self.assertEqual(sum(len(page.get_links()) for page in doc), 0)
                    self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), "OpenAction")[0], "null")
                    self.assertIn("SECRET", " ".join(page.get_text() for page in doc))
                    for xref in range(1, doc.xref_length()):
                        obj = " ".join(doc.xref_object(xref, compressed=True).split())
                        self.assertNotRegex(obj, r"/XFA\s+(?!null(?:\s|/|>|$))")
                        self.assertNotRegex(obj, r"/JavaScript\s+(?!null(?:\s|/|>|$))")
                finally:
                    doc.close()
            finally:
                client.close()

    def test_sanitization_can_be_disabled_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            output = root / "unsanitized.pdf"
            client = EngineClient()
            try:
                ih, oh = self._registered(client, source, output)
                result = client.export(
                    input_handle=ih, output_handle=oh, mode="clean", remove="redactions",
                    sanitize_active_content=False,
                )
                self.assertFalse(result["active_content_sanitized"])
                doc = pymupdf.open(output)
                try:
                    self.assertGreater(doc.embfile_count(), 0)
                finally:
                    doc.close()
            finally:
                client.close()

    def test_requested_sanitization_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            output = root / "must-not-survive.pdf"
            with mock.patch.object(pdf_unredact, "_sanitize_active_content", lambda doc: None):
                with self.assertRaises(pdf_unredact.SecuritySanitizationError):
                    pdf_unredact.make_clean_pdf(str(source), str(output), sanitize_active_content=True)
            self.assertFalse(output.exists())

    def test_input_is_immutable_after_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient()
            try:
                ih = client.register_input(source)
                source.unlink()
                analysis = client.audit(ih)
                self.assertEqual(analysis.pages, 1)
                self.assertGreaterEqual(analysis.stats["redaction_boxes_found"], 1)
            finally:
                client.close()

    def test_input_size_limit_is_enforced_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient(limits=EngineLimits(max_input_bytes=10))
            try:
                with self.assertRaises(EngineError) as ctx:
                    client.register_input(source)
                self.assertEqual(ctx.exception.code, EngineErrorCode.INPUT_TOO_LARGE)
            finally:
                client.close()

    def test_export_is_atomic_and_preserves_existing_destination_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            output = root / "existing.pdf"
            output.write_bytes(b"ORIGINAL")
            client = EngineClient()
            try:
                ih, oh = self._registered(client, source, output)
                with mock.patch.object(client, "_run", side_effect=RuntimeError("worker failed")):
                    with self.assertRaises(RuntimeError):
                        client.export(input_handle=ih, output_handle=oh, mode="clean", remove="redactions")
                self.assertEqual(output.read_bytes(), b"ORIGINAL")
                self.assertEqual(list(root.glob(".pdf-unredact-*.tmp.pdf")), [])
            finally:
                client.close()

    def test_engine_staging_obeys_workspace_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient(limits=EngineLimits(max_workspace_bytes=10))
            try:
                with self.assertRaises(EngineError) as ctx:
                    client.register_input(source)
                self.assertEqual(ctx.exception.code, EngineErrorCode.WORKSPACE_QUOTA_EXCEEDED)
            finally:
                client.close()

    def test_max_pages_is_enforced_by_export_and_preview_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root, pages=2)
            output = root / "out.pdf"
            client = EngineClient(limits=EngineLimits(max_pages=2))
            try:
                ih, oh = self._registered(client, source, output)
                analysis = client.audit(ih)
                client.limits = EngineLimits(max_pages=1)
                with self.assertRaises(Exception):
                    client.preview(
                        analysis_id=analysis.analysis_id, page=1, kind="clean", remove="redactions"
                    )
                with self.assertRaises(Exception):
                    client.export(input_handle=ih, output_handle=oh, mode="clean", remove="redactions")
            finally:
                client.close()

    def test_engine_public_api_uses_capabilities_not_authorization_roots(self):
        self.assertIn("input_handle", inspect.signature(EngineClient.audit).parameters)
        self.assertNotIn("input_path", inspect.signature(EngineClient.audit).parameters)
        self.assertNotIn("input_path", inspect.signature(EngineClient.preview).parameters)
        self.assertNotIn("output_path", inspect.signature(EngineClient.preview).parameters)
        self.assertNotIn("authorized_output_roots", inspect.signature(EngineClient.export).parameters)
        self.assertIn("input_handle", inspect.signature(EngineClient.export).parameters)
        self.assertIn("output_handle", inspect.signature(EngineClient.export).parameters)

    def test_release_dependency_is_exactly_pinned(self):
        pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        constraints = (Path(__file__).parents[1] / "constraints-release.txt").read_text(encoding="utf-8")
        self.assertIn('"pymupdf==1.26.7"', pyproject)
        self.assertIn("pymupdf==1.26.7", constraints)
        self.assertEqual(pymupdf.__version__, "1.26.7")

    def test_engine_api_is_versioned_and_errors_are_structured(self):
        client = EngineClient()
        try:
            info = client.api_info()
            self.assertEqual(info["api_version"], ENGINE_API_VERSION)
            self.assertIn("cancel", info["operations"])
            self.assertIn(EngineErrorCode.CANCELLED, info["error_codes"])
            with self.assertRaises(EngineError) as ctx:
                client.audit("not-a-handle")
            payload = ctx.exception.to_dict()
            self.assertEqual(payload["api_version"], ENGINE_API_VERSION)
            self.assertEqual(payload["code"], EngineErrorCode.HANDLE_UNAVAILABLE)
        finally:
            client.close()

    def test_pre_cancelled_operation_fails_with_cancelled_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient()
            try:
                ih = client.register_input(source)
                op = client.create_operation("audit")
                self.assertTrue(client.cancel(op))
                with self.assertRaises(EngineError) as ctx:
                    client.audit(ih, operation_id=op)
                self.assertEqual(ctx.exception.code, EngineErrorCode.CANCELLED)
                client.close_operation(op)
            finally:
                client.close()

    def test_progress_events_are_structured_and_frontend_neutral(self):
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient(progress_callback=events.append)
            try:
                ih = client.register_input(source)
                result = client.audit(ih)
                self.assertEqual(result.to_dict()["api_version"], ENGINE_API_VERSION)
                payloads = [event.to_dict() for event in events]
                self.assertTrue(any(p["phase"] == "starting" for p in payloads))
                self.assertTrue(any(p["phase"] == "worker_running" for p in payloads))
                completed = [p for p in payloads if p["phase"] == "completed"]
                self.assertTrue(completed)
                self.assertEqual(completed[-1]["current"], 1)
                self.assertEqual(completed[-1]["total"], 1)
            finally:
                client.close()

    def test_release_is_idempotent_for_capability_handles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            output = root / "out.pdf"
            client = EngineClient()
            try:
                ih = client.register_input(source)
                oh = client.register_output(output)
                analysis = client.audit(ih)
                self.assertTrue(client.release(analysis.analysis_id))
                self.assertFalse(client.release(analysis.analysis_id))
                self.assertTrue(client.release(oh))
                self.assertTrue(client.release(ih))
            finally:
                client.close()

    def test_running_operation_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient()
            try:
                ih = client.register_input(source)
                op = client.create_operation("audit")
                started = threading.Event()
                captured = []

                def fake_run_worker(*args, cancel_event=None, **kwargs):
                    started.set()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if cancel_event is not None and cancel_event.is_set():
                            from worker_client import WorkerCancelledError
                            raise WorkerCancelledError("cancelled")
                        time.sleep(0.01)
                    raise AssertionError("operation was not cancelled")

                def run():
                    try:
                        with mock.patch("engine_api.run_worker", side_effect=fake_run_worker):
                            client.audit(ih, operation_id=op)
                    except BaseException as exc:
                        captured.append(exc)

                thread = threading.Thread(target=run)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertTrue(client.cancel(op))
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(captured), 1)
                self.assertIsInstance(captured[0], EngineError)
                self.assertEqual(captured[0].code, EngineErrorCode.CANCELLED)
                client.close_operation(op)
            finally:
                client.close()

    def test_preview_returns_opaque_artifact_handle_and_release_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                artifact = client.preview(analysis_id=analysis.analysis_id, page=1, kind="clean", remove="redactions")
                self.assertEqual(len(artifact.artifact_handle), 32)
                self.assertEqual(artifact.mime, "image/png")
                self.assertGreater(len(client.read_artifact(artifact.artifact_handle)), 0)
                self.assertTrue(client.release(artifact.artifact_handle))
                with self.assertRaises(EngineError) as ctx:
                    client.read_artifact(artifact.artifact_handle)
                self.assertEqual(ctx.exception.code, EngineErrorCode.HANDLE_UNAVAILABLE)
            finally:
                client.close()

    def test_preview_artifact_limit_is_enforced_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient(limits=EngineLimits(max_preview_artifacts=1))
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                first = client.preview(analysis_id=analysis.analysis_id, page=1, kind="clean", remove="redactions")
                with self.assertRaises(EngineError) as ctx:
                    client.preview(analysis_id=analysis.analysis_id, page=1, kind="clean", remove="redactions")
                self.assertEqual(ctx.exception.code, EngineErrorCode.WORKSPACE_QUOTA_EXCEEDED)
                client.release(first.artifact_handle)
                second = client.preview(analysis_id=analysis.analysis_id, page=1, kind="clean", remove="redactions")
                client.release(second.artifact_handle)
            finally:
                client.close()

    def test_handle_and_analysis_limits_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient(limits=EngineLimits(max_input_handles=1, max_output_handles=1, max_analyses=1))
            try:
                ih = client.register_input(source)
                with self.assertRaises(EngineError):
                    client.register_input(source)
                first_output = client.register_output(root / "a.pdf")
                with self.assertRaises(EngineError):
                    client.register_output(root / "b.pdf")
                analysis = client.audit(ih)
                with self.assertRaises(EngineError):
                    client.audit(ih)
                client.release(analysis.analysis_id)
                analysis2 = client.audit(ih)
                client.release(analysis2.analysis_id)
                client.release(first_output)
            finally:
                client.close()

    def test_concurrent_input_registration_cannot_overbook_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            size = source.stat().st_size
            client = EngineClient(limits=EngineLimits(max_workspace_bytes=size + 64, max_input_handles=4, min_free_bytes=0))
            barrier = threading.Barrier(2)
            results = []
            original_validate = client.validate_input
            def validate(path):
                value = original_validate(path)
                barrier.wait(timeout=2)
                return value
            try:
                with mock.patch.object(client, "validate_input", side_effect=validate):
                    threads = [threading.Thread(target=lambda: results.append(("ok", client.register_input(source))) if False else None) for _ in range(0)]
                    def run():
                        try:
                            results.append(("ok", client.register_input(source)))
                        except EngineError as exc:
                            results.append((exc.code, None))
                    threads = [threading.Thread(target=run) for _ in range(2)]
                    for t in threads: t.start()
                    for t in threads: t.join(3)
                self.assertEqual(sum(1 for code, _ in results if code == "ok"), 1)
                self.assertEqual(sum(1 for code, _ in results if code == EngineErrorCode.WORKSPACE_QUOTA_EXCEEDED), 1)
            finally:
                client.close()

    def test_web_send_swallows_normal_client_disconnects(self):
        import web_app

        handler = object.__new__(web_app.Handler)
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler._security_headers = mock.Mock(return_value={})
        handler.end_headers = mock.Mock(side_effect=BrokenPipeError(32, "Broken pipe"))
        handler.wfile = mock.Mock()

        # A browser that closes before headers are flushed must not escape as an error.
        handler._send(200, b"payload")

        handler.end_headers = mock.Mock()
        handler.wfile.write = mock.Mock(side_effect=ConnectionResetError(104, "reset"))
        handler._send(200, b"payload")

    def test_web_send_file_swallows_disconnect_and_still_deletes_temporary_file(self):
        import web_app

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.png"
            path.write_bytes(b"x" * 32)
            handler = object.__new__(web_app.Handler)
            handler.send_response = mock.Mock()
            handler.send_header = mock.Mock()
            handler._security_headers = mock.Mock(return_value={})
            handler.end_headers = mock.Mock()
            handler.wfile = mock.Mock()
            handler.wfile.write = mock.Mock(side_effect=ConnectionAbortedError(103, "aborted"))

            handler._send_file(200, path, "image/png", delete_after=True)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
