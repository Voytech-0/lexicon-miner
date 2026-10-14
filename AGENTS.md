# lexicon-miner

Pipeline that turns audio/video recordings in a source language (default:
Dutch) into a personal dictionary of words to learn, with example sentences,
audio clips, and translations — later exported to Anki.

## Languages

`config.toml` holds `source` and `target` (ISO 639-1; default `nl` → `en`).
`config.py` derives everything language-specific from it: whisper
`language`, TranslateGemma `source_lang_code`/`target_lang_code`, spaCy
pipeline name, WikDict pair, FrequencyWords list, Anki deck/model/field
names, and `DATA_DIR = data/<source>/`. No other module hardcodes a
language. One data dir per source language; changing `target` does not
re-translate stored rows.

Everything runs locally. No cloud APIs, no API keys, no telemetry.

## Memory discipline (hard constraint)

RAM and VRAM are the same pool on this machine (unified memory). Rules:

- **Only one model resident at a time.** Stages are strictly sequential;
  transcribe finishes and releases before translate loads.
- Heavy stages (transcribe, translate) run in a **subprocess** spawned via
  `uv run python pipeline.py <stage> ...`. Process exit returns all memory
  to the OS — no reliance on Python GC. Streamlit never imports
  faster-whisper/transformers; it only spawns subprocesses and tails their
  stdout for progress.
- **Never keep a model loaded in the background.** No Streamlit model caching:
  `@st.cache_resource` on a model is a bug. No background loader threads.
- Faster re-runs are not worth resident memory — a 30 s model reload on each
  stage invocation is the accepted cost.
- Quantization everywhere: whisper int8; translation model is 4-bit.

## Stack (decided, do not change without asking)

- **Runtime**: Python 3.15, managed with `uv` (`uv run` for everything).
- **Transcription**: `faster-whisper` (CTranslate2), model `medium`, int8
  quantization. Model file ~1.5 GB, RAM ~2–3 GB — respects the ≤3 GB model
  constraint. `word_timestamps=True`. Runs on CPU (arm64) with int8.
  Audio is decoded via **ffmpeg to raw f32 PCM** and passed as a numpy array —
  faster-whisper's PyAV decoder is bypassed (av>=15 removed `metadata_errors`,
  av<15 has no arm64 wheels). Keep the `av>=15` pin; never let uv resolve av
  to a source build.
- **Translation, sentences**: `mlx-community/translategemma-4b-it-4bit`
  via `mlx-lm` (Apple-native, no daemon, ~2.5 GB resident during the
  translate stage, never coexisting with whisper). `temperature=0.0`,
  natural idiomatic target language — it preserves discourse markers better than
  general LLMs (misschien -> "perhaps").
- **Translation, single words: glossary lookup, not a model.** Literal word
  translation is a dictionary task. `glossary` table, two sources:
  WikDict `<source>-<target>` (`seed_wikdict.py`, nl-en: 75k words, ~10 MB,
  CC-BY-SA) and, optionally, English definitions from a user-owned Routledge
  frequency apkg (`seed.py`, human-written, preferred; skipped when
  target != en). Missing WikDict pair → everything falls back to TranslateGemma. The candidates stage
  fills `candidates.translation` from the glossary; TranslateGemma
  single-word output is only a fallback for glossary misses (expected
  residue: interjections, names, mishearings), and the review screen allows
  inline gloss editing before accepting. History: MarianMT and
  Qwen2.5-7B-Instruct were both replaced — see git-less design notes above.
- **NLP**: `spaCy` with `<source>_core_news_md` (en/zh: `_core_web_md`),
  downloaded lazily as a wheel into `~/.cache/lexicon-miner/spacy/` and loaded
  from its data dir — not a project dependency (one extra per language would
  make `uv lock` download every model). One pass produces
  lemma, POS tag (UPOS), and stopword flag per token — replaces a separate
  lemmatizer. Dictionary is keyed on lemmas; candidate matching is
  case-insensitive exact lemma first, then normalized (strip diacritics,
  lowercase).
- **UI**: Streamlit (`streamlit run app.py`). One page, stages driven by
  buttons; long stages (transcribe/translate) show progress and disable input.
- **DB**: SQLite, `data/<source>/app.db`, WAL mode. Accessed only through
  `db.py` helpers (plain sqlite3, no ORM).
- **Audio clips**: `ffmpeg` extracts per-segment mp3s into `data/<source>/clips/`.
- **Anki export**: AnkiConnect plugin (already installed in Anki desktop),
  HTTP to `http://localhost:8765`. No genanki, no .apkg files.

## Pipeline stages

```
ingest -> transcribe -> candidates -> translate -> review (UI) -> dictionary -> anki export
```

1. **ingest** — copy/register media file under `data/<source>/Media/`,
   one folder per recording; `media_path` is relative to `data/<source>/`. `recordings` row created (sha256 dedup guard). Never modify or
   delete source media.
2. **transcribe** — subprocess: faster-whisper over ffmpeg-decoded audio;
   segments and word timestamps stored in `segments` / `segment_words`.
3. **candidates** — subprocess: spaCy pass; a lemma is a candidate if it is
   not in `words` and not filtered (spaCy stopwords, numbers, pure
   punctuation). POS (UPOS) recorded. Candidates land in `candidates` with
   `status='pending'` and `translation` filled from the glossary (literal
   word gloss; NULL on miss). Lemmas present in `frequency_words` are NOT
   filtered — flagged `freq_rank` instead. First occurrence of a lemma
   per recording wins.
4. **translate** — subprocess: TranslateGemma over untranslated segments,
   then over pending candidates whose glossary lookup missed
   (`candidates.translation IS NULL`). Idempotent. Runs AFTER candidates.
5. **review** — Streamlit screen: per candidate, show surface form, lemma,
   sentence, translation, audio player for the segment, and an inline
   editable gloss. User accepts/rejects via checkboxes. Default: ALL
   candidates pre-checked, frequency-list or not. Frequency-list candidates
   are still visually marked (orange + rank badge) so they are easy to spot
   and uncheck.
   Accepted → inserted into `words` + occurrence row + ffmpeg clip extracted.
   Rejected → `status='rejected'` (never re-proposed for the same recording;
   global ignore list optional later).
6. **anki export** — AnkiConnect. Note model mirrors the frequency deck's
   `A Frequency Dictionary of Dutch+` model, field for field:
   `Rank, Word, Part-of-Speech, Definition, <Source>, <Target>, Freq,
   Word Audio`, where `<Source>`/`<Target>` are language names
   (Dutch/English for nl→en, so existing decks stay valid). Deck
   `<Source> Media`, model `<Source> Media Word`, media files
   `<source name>_{word_id}.mp3`.
   Mapping: Rank = `words.id` (padded, e.g. `Core_10023`), Word = lemma,
   Part-of-Speech = `words.pos` (UPOS, e.g. NOUN/VERB), plus the inflection
   mapping when the encountered form differs from the lemma
   ("VERB · gemaakt → maken"), Definition = word
   translation, <Source> = example sentence, <Target> = sentence translation,
   Freq = blank, Word Audio = `[sound:<source name>_{word_id}.mp3]` uploaded via
   `storeMediaFile`.
   Model and deck are created via `createModel`/`createDeck` on first export;
   subsequent exports `addNotes` only (skip lemmas already in the deck,
   dedupe on Word field via `findNotes`). Card layout follows the frequency
   deck's convention: front = word + POS + audio + example sentence;
   back = same + definition + English sentence. Template and CSS are
   re-synced on every export via `updateModelTemplates`/
   `updateModelStyling`.

Every stage must be re-runnable and idempotent (safe to hit twice).

## DB schema (authoritative)

```sql
recordings(
  id INTEGER PK, title TEXT, media_path TEXT,
  media_sha TEXT,           -- dedup guard
  added_at TEXT
)
segments(
  id INTEGER PK, recording_id FK, start_s REAL, end_s REAL,
  text TEXT, translation TEXT
)
segment_words(
  id INTEGER PK, segment_id FK, word TEXT, lemma TEXT, pos TEXT,
  start_s REAL, end_s REAL
)
frequency_words(            -- seed_freq.py (FrequencyWords) or seed.py (apkg)
  id INTEGER PK, word TEXT UNIQUE, rank INTEGER, list TEXT
)
-- word = normalized surface form (candidates look up normalize(surface)).
-- FrequencyWords: OpenSubtitles 2018 top-50k, rank = line order,
-- list = 'subtitles'. Routledge apkg: six per-genre sub-decks (Core,
-- General, Fiction, Newspapers, Web, Spoken) each numbered from 1, so
-- `rank` is computed by sorting on the Freq field across all notes.
-- Whichever seed runs last replaces the table.
glossary(                  -- literal source->target word glosses
  id INTEGER PK, word TEXT, gloss TEXT,
  source TEXT,              -- 'freq' (deck definitions) | 'wikdict'
  UNIQUE(word, gloss)
)
candidates(
  id INTEGER PK, recording_id FK, lemma TEXT, surface TEXT, pos TEXT,
  translation TEXT,          -- word translation, filled by translate stage
  segment_id FK, freq_rank INTEGER,   -- NULL if not in frequency list
  status TEXT CHECK(status IN ('pending','accepted','rejected')),
  UNIQUE(recording_id, lemma, segment_id)
)
words(                      -- the working dictionary; one row = one Anki card
  id INTEGER PK, lemma TEXT UNIQUE, translation TEXT, pos TEXT,
  surface TEXT,             -- form as first encountered
  segment_id FK,            -- first/example occurrence
  recording_id FK,
  created_at TEXT
)
```

`words.segment_id` is the example sentence; the clip path is derived:
`clips/{word_id}.mp3`.

## Conventions

- Single package, flat modules: `pipeline.py` (stages), `db.py` (schema +
  helpers), `translate.py`, `nlp.py` (spaCy pipeline: lemma + POS),
  `app.py` (Streamlit UI), `config.py` (language settings),
  `seed_freq.py` (one-off FrequencyWords import), `seed.py` (optional
  apkg import), `seed_wikdict.py` (one-off WikDict import), `anki.py`
  (AnkiConnect client). No subpackages, no plugin layers.
- Paths: project root is this directory. It lives inside iCloud Drive — the
  path contains spaces; always quote. Never write large caches here; models
  stay in default HF/CTranslate2 cache dirs (`~/.cache`).
- All user-visible strings in the UI in English; content strings in the source language
  stay verbatim, never "corrected".
- Timestamps in seconds, REAL. ISO-8601 UTC strings for datetimes.
- Stage scripts are callable both from Streamlit (as subprocesses) and
  directly as `uv run` commands — same entry point.
- Models are downloaded once and cached; code must never require network after
  first run.
- `data/` and `*.apkg` are gitignored: user data and copyrighted decks
  never go in the repo.
- No deletion of `data/` (Media, db, clips) unless the user asks in
  plain words.

## Commands

```bash
uv run streamlit run app.py        # main UI
uv run python seed_freq.py         # one-off: import FrequencyWords list
uv run python seed.py [deck.apkg]  # optional: import Routledge apkg
uv run python seed_wikdict.py      # one-off: import WikDict glossary
uv run python pipeline.py <ingest|transcribe|candidates|translate> <arg>
uv run python anki.py              # push dictionary to Anki via AnkiConnect
uv run pytest                      # tests (only for db.py + nlp.py logic)
```

Model downloads happen lazily on first use (~1.5 GB whisper-medium-int8,
~2.5 GB translategemma-4b-it-4bit, ~40 MB spaCy pipeline for the source language).

## Decisions still open

- Review screen batch size and sorting (default: order of appearance).
- Anki deck name and whether new notes go to a separate subdeck (default:
  deck "<Source> Media" separate from the frequency deck).
