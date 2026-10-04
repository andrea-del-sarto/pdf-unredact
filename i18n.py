import json
import string
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
SUPPORTED_LANGUAGES = ("en", "it")
DEFAULT_LANGUAGE = "en"


def normalize_language(language: str | None) -> str:
    value = (language or "").lower().replace("_", "-")
    return "it" if value.startswith("it") else DEFAULT_LANGUAGE


@lru_cache(maxsize=None)
def load_locale(language: str) -> dict:
    lang = normalize_language(language)
    path = LOCALES_DIR / f"{lang}.json"
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("frontend"), dict) or not isinstance(data.get("backend"), dict):
        raise ValueError(f"Invalid locale file: {path}")
    return data


def validate_locales() -> None:
    reference = load_locale(DEFAULT_LANGUAGE)
    for lang in SUPPORTED_LANGUAGES:
        current = load_locale(lang)
        for section in ("frontend", "backend"):
            expected = set(reference[section])
            actual = set(current[section])
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            if missing or extra:
                parts = []
                if missing:
                    parts.append(f"missing {section} keys: {', '.join(missing)}")
                if extra:
                    parts.append(f"extra {section} keys: {', '.join(extra)}")
                raise ValueError(f"Locale {lang}: " + "; ".join(parts))

            formatter = string.Formatter()
            for key in sorted(expected):
                def fields(message: str) -> set[str]:
                    return {name for _, name, _, _ in formatter.parse(message) if name}

                reference_fields = fields(str(reference[section][key]))
                current_fields = fields(str(current[section][key]))
                if current_fields != reference_fields:
                    raise ValueError(
                        f"Locale {lang}: placeholder mismatch in {section}.{key}: "
                        f"expected {sorted(reference_fields)}, got {sorted(current_fields)}"
                    )


def translate(key: str, language: str | None = None, section: str = "backend", **values) -> str:
    lang = normalize_language(language)
    locale = load_locale(lang)
    fallback = load_locale(DEFAULT_LANGUAGE)
    message = locale.get(section, {}).get(key, fallback.get(section, {}).get(key, key))
    return message.format(**values) if values else message


def frontend_catalog(language: str | None = None) -> dict:
    lang = normalize_language(language)
    return dict(load_locale(lang)["frontend"])


validate_locales()
