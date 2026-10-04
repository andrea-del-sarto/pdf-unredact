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

    def test_italian_uses_sanificare_terminology(self):
        catalog = json.dumps(i18n.frontend_catalog("it"), ensure_ascii=False).lower()
        self.assertNotIn("sanitizz", catalog)
        self.assertIn("sanifica", catalog)
        self.assertIn("sanificazione", catalog)


if __name__ == "__main__":
    unittest.main()
