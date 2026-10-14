"""One-off import: FrequencyWords list -> frequency_words.

hermitdave/FrequencyWords (CC-BY-SA-4.0): top 50k word forms per language,
counted over OpenSubtitles 2018, one "word count" pair per line, sorted by
count. Surface forms, not lemmas — matching the candidates stage, which
looks up the normalized surface form. Replaces whatever frequency list
was imported before (e.g. by seed.py).
"""
import urllib.request

import config
import db
from nlp import normalize

URL = ("https://raw.githubusercontent.com/hermitdave/FrequencyWords/"
       f"master/content/2018/{config.SOURCE}/{config.SOURCE}_50k.txt")
LOCAL = config.DATA_DIR / f"frequencywords-{config.SOURCE}.txt"


def main() -> None:
    db.init_db()
    if not LOCAL.exists():
        print(f"downloading FrequencyWords {config.SOURCE} (~1 MB) ...", flush=True)
        urllib.request.urlretrieve(URL, LOCAL)
    words = [
        normalize(line.split(" ", 1)[0])
        for line in LOCAL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cur = db.connect()
    try:
        cur.execute("DELETE FROM frequency_words")
        # rank = line order; diacritic-stripped duplicates keep the better rank
        cur.executemany(
            "INSERT OR IGNORE INTO frequency_words(word, rank, list)"
            " VALUES(?,?,?)",
            [(w, i + 1, "subtitles") for i, w in enumerate(words)],
        )
        cur.commit()
        n = cur.execute("SELECT COUNT(*) FROM frequency_words").fetchone()[0]
    finally:
        cur.close()
    print(f"imported {n} frequency words")


if __name__ == "__main__":
    main()
