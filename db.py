"""SQLite schema and helpers. Plain sqlite3, WAL, no ORM."""
import sqlite3

from config import DATA_DIR, ROOT  # noqa: F401  (ROOT re-exported)

DB_PATH = DATA_DIR / "app.db"
MEDIA_DIR = DATA_DIR / "Media"
CLIPS_DIR = DATA_DIR / "clips"
CAND_CLIPS_DIR = CLIPS_DIR / "candidates"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  media_path TEXT NOT NULL,
  media_sha TEXT NOT NULL UNIQUE,
  added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY,
  recording_id INTEGER NOT NULL REFERENCES recordings(id),
  start_s REAL NOT NULL,
  end_s REAL NOT NULL,
  text TEXT NOT NULL,
  translation TEXT
);
CREATE TABLE IF NOT EXISTS segment_words(
  id INTEGER PRIMARY KEY,
  segment_id INTEGER NOT NULL REFERENCES segments(id),
  word TEXT NOT NULL,
  lemma TEXT,
  pos TEXT,
  start_s REAL,
  end_s REAL
);
CREATE INDEX IF NOT EXISTS idx_segment_words_seg ON segment_words(segment_id);
CREATE TABLE IF NOT EXISTS frequency_words(
  id INTEGER PRIMARY KEY,
  word TEXT NOT NULL UNIQUE,
  rank INTEGER NOT NULL,
  list TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS glossary(     -- literal source->target word glosses
  id INTEGER PRIMARY KEY,
  word TEXT NOT NULL,
  gloss TEXT NOT NULL,
  source TEXT NOT NULL,       -- 'freq' (deck definitions) | 'wikdict'
  UNIQUE(word, gloss)
);
CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY,
  recording_id INTEGER NOT NULL REFERENCES recordings(id),
  lemma TEXT NOT NULL,
  surface TEXT NOT NULL,
  pos TEXT,
  translation TEXT,
  segment_id INTEGER NOT NULL REFERENCES segments(id),
  freq_rank INTEGER,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','accepted','rejected')),
  UNIQUE(recording_id, lemma, segment_id)
);
CREATE INDEX IF NOT EXISTS idx_candidates_rec ON candidates(recording_id, status);
CREATE TABLE IF NOT EXISTS words(
  id INTEGER PRIMARY KEY,
  lemma TEXT NOT NULL UNIQUE,
  translation TEXT,
  pos TEXT,
  surface TEXT,
  segment_id INTEGER NOT NULL REFERENCES segments(id),
  recording_id INTEGER NOT NULL REFERENCES recordings(id),
  created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = connect()
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    init_db()
    print(f"initialized {DB_PATH}")
