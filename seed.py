"""Optional one-off import: a Routledge "A Frequency Dictionary of <X>" Anki
deck (.apkg) -> frequency_words + glossary (source='freq'). Not shipped:
the decks are copyrighted. Without one, use seed_freq.py.

Usage: uv run python seed.py [path.apkg]  (default: the Dutch deck in root)

apkg = zip; notes live in collection.anki21 (or .anki2 on older exports).
Fields: Rank, Word, Part-of-Speech, Definition, Dutch, English, Freq, Audio.
Notes come from six sub-decks (Core, General, Fiction, Newspapers, Web,
Spoken), each with its own per-list numbering — so list rank is meaningless
globally. Global rank is computed by sorting on the Freq field (corpus
frequency) descending. A word in several lists keeps its best (highest-freq)
entry.
"""
import re
import sqlite3
import sys
import tempfile
import zipfile

import config
import db
from seed_wikdict import clean_gloss

APKG = db.ROOT / "A_Frequency_Dictionary_of_Dutch.apkg"


def main() -> None:
    db.init_db()
    apkg = sys.argv[1] if len(sys.argv) > 1 else APKG
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(apkg) as z:
        z.extractall(tmp)
        col = None
        for name in ("collection.anki21", "collection.anki2"):
            try:
                con = sqlite3.connect(f"{tmp}/{name}")
                con.execute("SELECT 1 FROM notes LIMIT 1")
                col = con
                break
            except sqlite3.Error:
                continue
        if col is None:
            raise SystemExit("no notes table found in apkg")
        best = {}  # word -> (freq, list)
        glosses = {}  # word -> English definition (human-written)
        for (flds,) in con.execute("SELECT flds FROM notes"):
            parts = flds.split("\x1f")
            if len(parts) < 7:
                continue
            m = re.match(r"([A-Za-z]+)_\d+", parts[0].strip())
            word = parts[1].strip().lower()
            try:
                freq = float(parts[6])
            except ValueError:
                continue
            if not m or not word:
                continue
            definition = parts[3].strip()  # Definition field (English)
            if word not in best or freq > best[word][0]:
                best[word] = (freq, m.group(1))
            if definition:
                glosses[word] = definition
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
        cur = db.connect()
        try:
            cur.execute("DELETE FROM frequency_words")
            cur.executemany(
                "INSERT INTO frequency_words(word, rank, list) VALUES(?,?,?)",
                [(w, i + 1, l) for i, (w, (f, l)) in enumerate(ranked)],
            )
            if config.TARGET != "en":
                glosses = {}  # deck definitions are English only
            cur.execute("DELETE FROM glossary WHERE source='freq'")
            cur.executemany(
                "INSERT OR IGNORE INTO glossary(word, gloss, source)"
                " VALUES(?,?,?)",
                [(w, clean_gloss(g), "freq") for w, g in glosses.items()],
            )
            cur.commit()
        finally:
            cur.close()
    print(f"imported {len(ranked)} frequency words, {len(glosses)} glosses")


if __name__ == "__main__":
    main()
