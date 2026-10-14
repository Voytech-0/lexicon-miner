"""spaCy wrapper: lemma + POS tagging, candidate extraction.

The model for config.SOURCE is downloaded lazily (wheel = zip) into
~/.cache/lexicon-miner/spacy/ and loaded from its data dir, so it is not a
project dependency and survives `uv sync`.
"""
import pathlib
import unicodedata
import urllib.request
import zipfile

import spacy

import config

CACHE = pathlib.Path.home() / ".cache" / "lexicon-miner" / "spacy"

_nlp = None


def model_path() -> pathlib.Path:
    name, ver = config.SPACY_MODEL, config.SPACY_VERSION
    data = CACHE / name / f"{name}-{ver}"
    if not data.is_dir():
        url = ("https://github.com/explosion/spacy-models/releases/download/"
               f"{name}-{ver}/{name}-{ver}-py3-none-any.whl")
        print(f"downloading spaCy model {name}-{ver} ...", flush=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        whl = CACHE / f"{name}-{ver}.whl"
        urllib.request.urlretrieve(url, whl)
        with zipfile.ZipFile(whl) as z:
            z.extractall(CACHE, [m for m in z.namelist() if m.startswith(f"{name}/")])
        whl.unlink()
    return data


def load():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load(model_path(), disable=["parser", "ner"])
    return _nlp


def normalize(text: str) -> str:
    """lowercase + strip diacritics, for fuzzy matching"""
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in stripped if unicodedata.category(c) != "Mn")


def tag_segment(text: str):
    """Yield (word_form, lemma, pos, is_stop, is_word) per token."""
    for tok in load()(text):
        is_word = tok.is_alpha  # excludes numbers, punctuation
        yield (tok.text, tok.lemma_, tok.pos_, tok.is_stop, is_word)
