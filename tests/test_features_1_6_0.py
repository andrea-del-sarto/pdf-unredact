from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from engine_api import EngineClient
from pdf_unredact import classify_document_structure, compute_redaction_stats

ROOT = Path(__file__).resolve().parents[1]


class DocumentStructure160Tests(unittest.TestCase):
    def _flattened_scan(self, path: Path) -> Path:
        src = pymupdf.open()
        page = src.new_page(width=420, height=240)
        page.insert_text((40, 80), "VISIBLE SECRET TEXT", fontsize=16)
        page.draw_rect(pymupdf.Rect(105, 62, 175, 84), fill=(0, 0, 0), color=(0, 0, 0))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        src.close()

        doc = pymupdf.open()
        out = doc.new_page(width=420, height=240)
        out.insert_image(out.rect, pixmap=pix)
        doc.save(path)
        doc.close()
        return path

    def _scan_with_hidden_ocr(self, path: Path) -> Path:
        self._flattened_scan(path)
        doc = pymupdf.open(path)
        page = doc[0]
        # PDF text rendering mode 3 = invisible text, the common OCR-overlay pattern.
        page.insert_text((40, 80), "VISIBLE SECRET TEXT", fontsize=16, render_mode=3)
        tmp = path.with_suffix(".rewrite.pdf")
        doc.save(tmp)
        doc.close()
        tmp.replace(path)
        return path

    def _separate_layers(self, path: Path) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=420, height=240)
        page.insert_text((40, 80), "VISIBLE SECRET TEXT", fontsize=16)
        annot = page.add_redact_annot(pymupdf.Rect(105, 62, 175, 84), fill=(0, 0, 0))
        annot.update()
        doc.save(path)
        doc.close()
        return path

    def _classify(self, path: Path) -> dict:
        stats = compute_redaction_stats(str(path))
        return classify_document_structure(str(path), stats)

    def test_flattened_scan_is_not_recoverable_from_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._classify(self._flattened_scan(Path(tmp) / "flat.pdf"))
            self.assertEqual(result["category"], "flattened_scan")
            self.assertEqual(result["recoverability"], "not_recoverable_from_pixels")
            self.assertEqual(result["scan_pages"], 1)
            self.assertEqual(result["hidden_ocr_pages"], 0)

    def test_scan_with_invisible_ocr_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._classify(self._scan_with_hidden_ocr(Path(tmp) / "ocr.pdf"))
            self.assertEqual(result["category"], "scan_with_hidden_ocr")
            self.assertEqual(result["recoverability"], "potentially_recoverable")
            self.assertGreater(result["hidden_text_chars"], 0)

    def test_native_pdf_with_separate_redaction_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._classify(self._separate_layers(Path(tmp) / "layers.pdf"))
            self.assertEqual(result["category"], "separate_pdf_layers")
            self.assertTrue(result["separate_overlay_evidence"])

    def test_engine_audit_exposes_document_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._scan_with_hidden_ocr(Path(tmp) / "engine-ocr.pdf")
            client = EngineClient()
            try:
                handle = client.register_input(path)
                analysis = client.audit(handle)
                structure = analysis.integrity.get("document_structure", {})
                self.assertEqual(structure.get("category"), "scan_with_hidden_ocr")
                self.assertIn("page_details", structure)
            finally:
                client.close()

    def test_ui_has_localized_document_structure_status(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="documentStructureStatus"', html)
        self.assertIn("function renderDocumentStructure()", js)
        self.assertIn("structure_flattened_scan", js)
        self.assertIn("structure_hidden_ocr", js)
        self.assertIn("structure_separate_layers", js)

    def test_frontend_javascript_syntax_is_valid(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        app_js = ROOT / "static" / "app.js"
        proc = subprocess.run([node, "--check", str(app_js)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
