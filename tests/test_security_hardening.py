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
    def _fixture(self, directory: Path, *, pages: int = 1) -> Path:
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
        doc.xref_set_key(
            doc.pdf_catalog(),
            "OpenAction",
            "<</S/JavaScript/JS(app.alert\\(1\\))>>",
        )
        doc.save(source)
        doc.close()
        return source

    def test_export_sanitizes_active_content_and_preserves_hidden_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            output = root / "clean.pdf"
            client = EngineClient()
            try:
                result = client.export(
                    input_path=source,
                    output_path=output,
                    mode="clean",
                    remove="redactions",
                    authorized_output_roots=[root],
                )
                self.assertTrue(result["active_content_sanitized"])
                doc = pymupdf.open(output)
                try:
                    self.assertEqual(doc.embfile_count(), 0)
                    self.assertEqual(sum(len(page.get_links()) for page in doc), 0)
                    self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), "OpenAction")[0], "null")
                    self.assertIn("SECRET", " ".join(page.get_text() for page in doc))
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
                result = client.export(
                    input_path=source,
                    output_path=output,
                    mode="clean",
                    remove="redactions",
                    authorized_output_roots=[root],
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
                    pdf_unredact.make_clean_pdf(
                        str(source), str(output), sanitize_active_content=True
                    )
            self.assertFalse(output.exists())

    def test_input_size_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient(limits=EngineLimits(max_input_bytes=10))
            try:
                with self.assertRaises(ValueError):
                    client.validate_input(source)
            finally:
                client.close()

    def test_output_must_be_authorized(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            source = self._fixture(root)
            client = EngineClient()
            try:
                with self.assertRaises(ValueError):
                    client.export(
                        input_path=source,
                        output_path=Path(outside) / "out.pdf",
                        mode="clean",
                        remove="redactions",
                        authorized_output_roots=[root],
                    )
            finally:
                client.close()

    def test_max_pages_is_enforced_by_export_and_preview_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root, pages=2)
            client = EngineClient(limits=EngineLimits(max_pages=2))
            try:
                analysis = client.audit(source)
                client.limits = EngineLimits(max_pages=1)
                with self.assertRaises(Exception):
                    client.preview(
                        input_path=source,
                        analysis_id=analysis.analysis_id,
                        output_path=root / "preview.png",
                        page=1,
                        kind="clean",
                        remove="redactions",
                        authorized_output_roots=[root],
                    )
                with self.assertRaises(Exception):
                    client.export(
                        input_path=source,
                        output_path=root / "out.pdf",
                        mode="clean",
                        remove="redactions",
                        authorized_output_roots=[root],
                    )
            finally:
                client.close()

    def test_engine_public_api_does_not_expose_temp_or_stats_paths(self):
        self.assertNotIn("work_dir", inspect.signature(EngineClient.audit).parameters)
        self.assertNotIn("work_dir", inspect.signature(EngineClient.probe).parameters)
        self.assertNotIn("stats_path", inspect.signature(EngineClient.preview).parameters)
        self.assertIn("analysis_id", inspect.signature(EngineClient.preview).parameters)


if __name__ == "__main__":
    unittest.main()
