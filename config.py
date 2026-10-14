"""Language settings from config.toml. Every language-specific name, code,
and path is derived here; other modules never hardcode a language."""
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent

_cfg = tomllib.loads((ROOT / "config.toml").read_text())
SOURCE: str = _cfg["source"]  # ISO 639-1, language of the media
TARGET: str = _cfg.get("target", "en")  # language of translations

NAMES = {
    "ca": "Catalan", "da": "Danish", "de": "German", "el": "Greek",
    "en": "English", "es": "Spanish", "fi": "Finnish", "fr": "French",
    "hr": "Croatian", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "lt": "Lithuanian", "mk": "Macedonian", "nb": "Norwegian", "nl": "Dutch",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sl": "Slovenian", "sv": "Swedish", "uk": "Ukrainian", "zh": "Chinese",
}
SOURCE_NAME = NAMES.get(SOURCE, SOURCE)
TARGET_NAME = NAMES.get(TARGET, TARGET)

# spaCy trained pipelines: "<lang>_core_news_md" except en/zh (web genre).
SPACY_MODEL = _cfg.get(
    "spacy_model",
    f"{SOURCE}_core_web_md" if SOURCE in ("en", "zh") else f"{SOURCE}_core_news_md",
)
SPACY_VERSION = _cfg.get("spacy_version", "3.8.0")

# one data dir per source language: db, Media/, clips/, downloads
DATA_DIR = ROOT / "data" / SOURCE
