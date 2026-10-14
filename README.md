# lexicon-miner

Mine vocabulary from native media: turn audio/video in the language you're
learning into a personal dictionary of words to learn, with example
sentences, audio clips, and translations — then push them to Anki. Built for Dutch, works for any language that
whisper, spaCy, and TranslateGemma support (see [Languages](#languages)).

You drop in a recording (podcast, YouTube audio, episode), and the project:

1. transcribes it locally (whisper medium, int8) with word-level timestamps,
2. extracts candidate words you don't have yet (lemmatized, POS-tagged),
3. translates every sentence (TranslateGemma-4B) and fills in
   literal word glosses from a dictionary (WikDict, optionally a
   frequency deck),
4. lets you pick which words to keep — frequent words are highlighted
   with their rank so they're easy to spot,
5. stores the keepers with their sentence, translations, and audio clip,
6. exports them to Anki as cards modelled on the *A Frequency Dictionary
   of …* decks (front: word + example sentence + recording; back:
   translation).

Everything runs on your machine. No cloud APIs, no keys, no telemetry.

## Setup (once)

Requirements: macOS (Apple Silicon — translation uses MLX),
[uv](https://docs.astral.sh/uv/), ffmpeg (`brew install ffmpeg`), Anki
desktop with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159)
plugin.

1. Pick your languages in `config.toml`:

   ```toml
   source = "nl"   # language of the media
   target = "en"   # language of the translations
   ```

2. Install and import the dictionaries:

   ```bash
   uv sync                         # Python + deps (~5 min)
   uv run python seed_freq.py      # frequency list, ~1 MB (one-off)
   uv run python seed_wikdict.py   # word glossary (one-off)
   ```

   Both also have a button in the UI sidebar.

Model weights (~4 GB total: whisper, TranslateGemma, the spaCy pipeline for
your language) download lazily on first use and are cached in `~/.cache`.

## Languages

A source language needs:

- **whisper** transcription — ~99 languages;
- a **spaCy** trained pipeline (`<code>_core_news_md`) for lemmas/POS —
  ca, da, de, el, en, es, fi, fr, hr, it, ja, ko, lt, mk, nb, nl, pl, pt,
  ro, ru, sl, sv, uk, zh. Some need extra packages (ja: `sudachipy`,
  ru/uk: `pymorphy3`, …) — `uv add` them if loading fails. Override the
  pipeline name/version in `config.toml` if needed;
- a **FrequencyWords** list ([hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords),
  OpenSubtitles 2018, CC-BY-SA-4.0) — ~60 languages;
- a **WikDict** dictionary for the source-target pair
  ([list](https://download.wikdict.com/dictionaries/sqlite/), CC-BY-SA).
  Without one, word glosses fall back to TranslateGemma.

Each source language keeps its own data in `data/<source>/`, so switching
`source` in `config.toml` switches to a separate dictionary and Anki deck
(`<Language> Media`). Changing `target` later does not re-translate what's
already stored.

### Optional: a Routledge frequency deck

If you own an Anki deck of *A Frequency Dictionary of <Language>*
(Routledge), import it instead of FrequencyWords — it adds human-written
English definitions, which beat WikDict glosses:

```bash
uv run python seed.py path/to/deck.apkg
```

Decks are copyrighted and not part of this repo.

## Day to day

```bash
uv run streamlit run app.py
```

Then, per recording:

1. **Add** — drop the mp3/mp4 into the sidebar uploader.
2. **Transcribe**, **Find candidates**, **Translate** — one button each.
   Each stage loads one model, runs, and exits; nothing stays in RAM.
3. **Review** — checkboxes per candidate with sentence, translation, and a
   play button for the audio, and an editable gloss. All words are
   pre-checked; frequency-list words show orange with their rank so they
   are easy to spot and uncheck.
   *Add checked to dictionary* or *Reject unchecked*.
4. **Export to Anki** — with Anki open. Re-runs are safe: only words not yet
   in the deck are added.

Every stage is idempotent — if something crashes mid-way, just click again.

## What lives where

| Path | Contents |
|---|---|
| `config.toml` | source/target language |
| `data/<source>/app.db` | SQLite: recordings, segments, candidates, dictionary |
| `data/<source>/Media/` | your source recordings (untouched) |
| `data/<source>/clips/` | per-word audio segments used on Anki cards |
| `AGENTS.md` | design decisions and conventions (kept current) |

## Layout

- `app.py` — Streamlit UI; spawns stages as subprocesses (memory discipline)
- `pipeline.py` — CLI stages: `ingest`, `transcribe`, `candidates`, `translate`
- `config.py` — reads `config.toml`; language names, spaCy model, data dir
- `db.py` — SQLite schema and connection
- `nlp.py` — spaCy: lemmas, POS, stopwords; lazy model download
- `translate.py` — TranslateGemma-4B via `mlx-lm`
- `seed_freq.py` — one-off FrequencyWords import
- `seed_wikdict.py` — one-off WikDict glossary import
- `seed.py` — optional one-off Routledge apkg import
- `anki.py` — AnkiConnect client (deck `<Language> Media`)

Stages are runnable without the UI:

```bash
uv run python pipeline.py transcribe 1
```

## Troubleshooting

- **Anki export fails** — Anki isn't running or AnkiConnect is missing.
  Start Anki, retry.
- **`av` build failure on `uv sync`** — the lockfile pins a wheel-only `av`.
  Never let it resolve to a source build; whisper audio decode goes through
  ffmpeg instead.
- **"no WikDict dictionary for xx-yy"** — WikDict doesn't publish that pair.
  Glosses come from TranslateGemma; edit them in the review screen.
- **Word glosses look odd on interjections or misheard words** — those fall
  through the dictionary to TranslateGemma. Edit the gloss inline in the
  review screen before accepting, or directly in the db.

## Tuning

- Whisper model: change `"medium"` in `pipeline.py` (int8 keeps RAM ~2.5 GB).
- POS tags are UPOS (`NOUN`, `VERB`); the frequency deck uses its own
  abbreviations — a mapping table in `anki.py` would align them if you care.
- Re-run `candidates` after changing the spaCy model: only new
  recordings pick it up automatically.
