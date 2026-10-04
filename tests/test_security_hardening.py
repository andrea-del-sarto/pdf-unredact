from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

from engine_api import EngineClient, EngineLimits
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
                with self.assertRaises(ValueError):
                    client.register_input(source)
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
                with self.assertRaises(OSError):
                    client.register_input(source)
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


if __name__ == "__main__":
    unittest.main()
