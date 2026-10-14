"""One-off import: WikDict source->target dictionary -> glossary (source='wikdict').

Downloads the WikDict SQLite DB (CC-BY-SA, extracted from Wiktionary) and
copies `simple_translation` (word -> grouped translations) into the
glossary. nl-en: ~75k words, ~10 MB download, kept in data/<source>/.
"""
import re
import sqlite3
import urllib.error
import urllib.request

import config
import db

PAIR = f"{config.SOURCE}-{config.TARGET}"
URL = f"https://download.wikdict.com/dictionaries/sqlite/2_2026-06/{PAIR}.sqlite3"
LOCAL = config.DATA_DIR / f"wikdict-{PAIR}.sqlite3"

# WikDict multi-sense markers: "a) to make b) to repair" -> "to make; to repair"
SENSE_MARKER = re.compile(r"\s*[a-z]\)\s*")


def clean_gloss(text: str) -> str:
    gloss = SENSE_MARKER.sub("; ", text)
    gloss = re.sub(r"(;\s*)+", "; ", gloss).strip("; ")
    return gloss.strip() or text.strip()


def main() -> None:
    db.init_db()
    if not LOCAL.exists():
        print(f"downloading WikDict {PAIR} ...", flush=True)
        try:
            urllib.request.urlretrieve(URL, LOCAL)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"no WikDict dictionary for {PAIR} ({e.code});"
                             " see https://download.wikdict.com/dictionaries/sqlite/")
    src = sqlite3.connect(LOCAL)
    cur = db.connect()
    try:
        rows = src.execute(
            "SELECT written_rep, trans_list FROM simple_translation"
        ).fetchall()
        cur.execute("DELETE FROM glossary WHERE source='wikdict'")
        cur.executemany(
            "INSERT OR IGNORE INTO glossary(word, gloss, source)"
            " VALUES(?,?,?)",
            [
                (w.strip(), clean_gloss(t), "wikdict")
                for w, t in rows
            ],
        )
        cur.commit()
    finally:
        src.close()
        cur.close()
    print(f"imported {len(rows)} wikdict glosses")


if __name__ == "__main__":
    main()
