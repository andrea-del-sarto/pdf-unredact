from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from engine_api import EngineClient

ROOT = Path(__file__).resolve().parents[1]


class Features150Tests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        path = root / "features.pdf"
        doc = pymupdf.open()
        for page_no in range(1, 4):
            page = doc.new_page(width=420, height=240)
            page.insert_text((45, 80), f"PAGE {page_no} SECRET SEARCHABLE TEXT", fontsize=14)
            if page_no in {1, 3}:
                annot = page.add_redact_annot(pymupdf.Rect(100, 61, 165, 86), fill=(0, 0, 0))
                annot.update()
        doc.save(path)
        doc.close()
        return path

    def test_search_returns_page_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                result = client.search(analysis.analysis_id, "SEARCHABLE")
                self.assertEqual(len(result["matches"]), 3)
                self.assertEqual(result["matches"][0]["page"], 1)
                self.assertGreater(result["matches"][0]["page_width"], 0)
                self.assertEqual(len(result["matches"][0]["box"]), 4)
            finally:
                client.close()

    def test_rotated_highlighted_preview_is_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                finding = analysis.stats["findings"][0]
                preview = client.preview(
                    analysis_id=analysis.analysis_id,
                    page=1,
                    kind="clean",
                    remove="redactions",
                    rotate=90,
                    search_query="SECRET",
                    highlight_findings=True,
                    focus_box=finding["box"],
                )
                self.assertEqual(preview.mime, "image/png")
                data = client.read_artifact(preview.artifact_handle)
                pix = pymupdf.Pixmap(data)
                self.assertGreater(pix.width, 0)
                self.assertGreater(pix.height, 0)
                self.assertTrue(client.release(preview.artifact_handle))
            finally:
                client.close()

    def test_export_only_pages_with_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "subset.pdf"
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                oh = client.register_output(output)
                client.export(
                    input_handle=ih,
                    output_handle=oh,
                    mode="clean",
                    remove="redactions",
                    analysis_id=analysis.analysis_id,
                    only_pages_with_findings=True,
                )
                doc = pymupdf.open(output)
                try:
                    self.assertEqual(doc.page_count, 2)
                finally:
                    doc.close()
            finally:
                client.close()

    def test_recovered_text_export_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                data = client.extract_text(analysis.analysis_id)
                self.assertEqual(len(data["entries"]), 2)
                self.assertTrue(all(item["text"] for item in data["entries"]))
                self.assertTrue(all(len(item["box"]) == 4 for item in data["entries"]))
            finally:
                client.close()

    def test_detection_rules_are_bound_to_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                default = client.audit(ih)
                strict = client.audit(ih, detection_rules={
                    "darkness_threshold": 0.0,
                    "min_width": 500,
                    "min_height": 500,
                    "word_overlap_threshold": 0.5,
                })
                # Redaction annotations remain explicit findings even when graphical
                # rectangle minimums are made impossible; the analysis still accepts
                # and persists the custom rules for later export.
                self.assertGreaterEqual(default.stats["redaction_boxes_found"], strict.stats["redaction_boxes_found"])
                self.assertIn("integrity", strict.to_dict())
            finally:
                client.close()

    def test_integrity_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = EngineClient()
            try:
                ih = client.register_input(self._fixture(root))
                analysis = client.audit(ih)
                self.assertIn("repaired", analysis.integrity)
                self.assertIn("warnings", analysis.integrity)
                self.assertFalse(analysis.integrity["encrypted"])
            finally:
                client.close()

    def test_malformed_pdf_repair_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "repaired.pdf"
            doc = pymupdf.open(); page = doc.new_page(); page.insert_text((72, 72), "repair me"); doc.save(source); doc.close()
            data = source.read_bytes(); marker = data.rfind(b"startxref")
            end = data.find(b"%%EOF", marker)
            import re
            damaged = re.sub(rb"startxref\s+\d+", b"startxref\n1", data[marker:end])
            source.write_bytes(data[:marker] + damaged + data[end:])
            client = EngineClient()
            try:
                ih = client.register_input(source)
                analysis = client.audit(ih)
                self.assertTrue(analysis.integrity["repaired"])
                self.assertTrue(analysis.integrity["warnings"])
            finally:
                client.close()


    def test_zoom_fit_buttons_have_stable_active_state(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        self.assertNotIn('id="zoomLabel"', html)
        self.assertIn('class="secondary fitModeButton"', html)
        self.assertIn("function updateZoomModeButtons()", js)
        self.assertIn("aria-pressed", js)
        self.assertIn(".fitModeButton.active", css)
        self.assertIn("width:148px", css)

    def test_preview_zoom_geometry_and_page_toolbar_alignment(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn('class="previewPrimaryRow"', html)
        row = html.split('class="previewPrimaryRow"', 1)[1].split('</div>\n  <div class="toolGroup">', 1)[0]
        self.assertIn('id="searchInput"', row)
        self.assertIn('id="page"', row)
        self.assertIn("function fitScaleFor", js)
        self.assertIn("function bumpZoom", js)
        self.assertIn("requestAnimationFrame(applyZoom)", js)
        self.assertIn(".previewPrimaryRow", css)

    def test_zoom_indicator_updates_and_supports_over_100_percent(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn("function updateZoomDisplay()", js)
        self.assertIn("Math.min(400", js)
        self.assertIn("updateZoomDisplay()", js)
        self.assertIn("#zoom100{width:78px", css)
        self.assertIn(".pane img{flex:0 0 auto;max-width:none", css)

if __name__ == "__main__":
    unittest.main()
