import json
import re
import unittest
from pathlib import Path

import i18n


ROOT = Path(__file__).resolve().parents[1]


class I18nTests(unittest.TestCase):
    def test_locales_validate(self):
        i18n.validate_locales()

    def test_frontend_translation_keys_exist(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        used = set(re.findall(r'data-i18n="([^"]+)"', html))
        used.update(re.findall(r"tr\(['\"]([^'\"]+)", js))

        for language in i18n.SUPPORTED_LANGUAGES:
            catalog = i18n.frontend_catalog(language)
            self.assertFalse(used - set(catalog), f"missing {language} keys: {sorted(used - set(catalog))}")

    def test_side_by_side_label_is_localized(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-i18n="side_by_side"', html)
        self.assertNotIn('<strong>Side-by-side</strong>', html)


    def test_browser_translation_prompt_is_suppressed(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('translate="no"', html)
        self.assertIn('class="notranslate"', html)
        self.assertIn('<meta name="google" content="notranslate">', html)
        self.assertIn('lang="__HTML_LANG__"', html)

    def test_export_uses_save_file_picker_when_available(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("showSaveFilePicker", js)
        self.assertIn("createWritable", js)
        self.assertIn("choose_save_location", js)
        self.assertIn("download_started", js)

    def test_preview_fullscreen_is_localized_and_wired(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="fullscreenPreview"', html)
        self.assertIn('data-i18n="full_screen"', html)
        self.assertIn("requestFullscreen", js)
        self.assertIn("exit_full_screen", js)

    def test_findings_details_are_collapsed_by_default(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<details class="card findingsCard" id="findingsDetails">', html)
        self.assertIn('data-i18n="findings_details"', html)
        self.assertNotIn('<details class="card findingsCard" id="findingsDetails" open>', html)

    def test_sanitization_description_matches_1_4_4_scope(self):
        it = json.loads((ROOT / "locales" / "it.json").read_text(encoding="utf-8"))["frontend"]["sanitize_active_content_desc"]
        en = json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8"))["frontend"]["sanitize_active_content_desc"]
        for term in ("JavaScript", "XFA", "multimediali", "file incorporati"):
            self.assertIn(term, it)
        for term in ("JavaScript", "XFA", "multimedia", "embedded files"):
            self.assertIn(term, en)

    def test_italian_uses_sanificare_terminology(self):
        catalog = json.dumps(i18n.frontend_catalog("it"), ensure_ascii=False).lower()
        self.assertNotIn("sanitizz", catalog)
        self.assertIn("sanifica", catalog)
        self.assertIn("sanificazione", catalog)

    def test_preview_keyboard_navigation_is_wired_and_localized(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("ArrowLeft", js)
        self.assertIn("ArrowRight", js)
        self.assertIn("navigatePreview", js)
        self.assertIn("isEditableTarget", js)
        self.assertIn("document.fullscreenElement", js)
        it = json.loads((ROOT / "locales" / "it.json").read_text(encoding="utf-8"))["frontend"]["preview_desc"]
        en = json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8"))["frontend"]["preview_desc"]
        self.assertIn("←", it)
        self.assertIn("→", it)
        self.assertIn("←", en)
        self.assertIn("→", en)

    def test_1_5_0_ui_features_are_wired(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for control in ("searchInput", "zoomIn", "fitPage", "rotateLeft", "findingFilter", "highlightFindings", "onlyFindingPages", "advancedDetection"):
            self.assertIn(f'id="{control}"', html)
        for token in ("/api/search/", "/api/recovered-text/", "previewRotation", "only_pages_with_findings", "detectionRules", "watchProgress"):
            self.assertIn(token, js)
        self.assertIn('id="diagnosticDetails"', html)
        self.assertIn('diagnosticPanel hidden', html)

    def test_translation_placeholders_are_localized(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'data-i18n-placeholder="([^"]+)"', html))
        for language in i18n.SUPPORTED_LANGUAGES:
            self.assertFalse(used - set(i18n.frontend_catalog(language)))

    def test_1_5_0_topbar_icons_match_svg_style(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="infoButton"', html)
        self.assertIn('<circle cx="12" cy="12" r="9"/>', html)
        self.assertIn('const themeIcons=', js)
        self.assertNotIn("$('infoButton').textContent='ⓘ'", js)

    def test_diagnostics_toggle_label_tracks_state(self):
        it = json.loads((ROOT / "locales" / "it.json").read_text(encoding="utf-8"))["frontend"]
        en = json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8"))["frontend"]
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(it["disable_diagnostics"], "Disabilita la modalità diagnostica")
        self.assertEqual(en["disable_diagnostics"], "Disable diagnostics mode")
        self.assertIn("enabled?'disable_diagnostics':'enable_diagnostics'", js)

    def test_recovered_text_exports_use_save_picker(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function exportRecoveredText(format)", js)
        self.assertIn("chooseFileDestination", js)
        self.assertIn("exportRecoveredText('txt')", js)
        self.assertIn("exportRecoveredText('json')", js)
        self.assertIn("exportRecoveredText('csv')", js)

    def test_upload_card_hides_after_successful_load(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("$('uploadCard').classList.add('hidden')", js)

    def test_sidebar_card_order(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        aside = html.split('<aside class="sideCol">', 1)[1].split('</aside>', 1)[0]
        keys = [
            'data-i18n="export_heading"',
            'data-i18n="output_format"',
            'data-i18n="annotations"',
            'data-i18n="security"',
            'data-i18n="advanced_detection"',
        ]
        positions = [aside.index(key) for key in keys]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
