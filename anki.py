"""AnkiConnect client (http://localhost:8765). Mirrors the frequency deck's
field layout: Rank, Word, Part-of-Speech, Definition, <Source>, <Target>,
Freq, Word Audio — language names from config (Dutch/English for nl->en)."""
import base64
import json
import pathlib
import urllib.request

import config
import db

URL = "http://localhost:8765"
SRC, TGT = config.SOURCE_NAME, config.TARGET_NAME
DECK = f"{SRC} Media"
MODEL = f"{SRC} Media Word"
FIELDS = [
    "Rank", "Word", "Part-of-Speech", "Definition",
    SRC, TGT, "Freq", "Word Audio",
]
CSS = """.card {
  font-family: arial;
  font-size: 20px;
  text-align: center;
  color: black;
  background-color: white;
}
.word {
  color: #0384ca;
  font-weight: bold;
  margin-bottom: 10px;
}
.definition {
  font-weight: bold;
  margin-bottom: 14px;
}
.part-of-speech {
  font-size: 95%;
  font-weight: normal;
  font-style: italic;
  margin-left: 3px;
  color: gray;
}
.source, .target {
  display: inline-block;
  text-align: left;
}
.source br, .target br {
  content: " ";
  display: block;
  margin: 10px 0;
}
"""
# Card layout follows the frequency dictionary deck:
# front = word + example sentence + recording; back = translation.
FRONT = (
    '<div class="word">{{Word}} '
    '<span class="part-of-speech">{{Part-of-Speech}}</span> {{Word Audio}}</div>\n'
    f'<div class="source">{{{{{SRC}}}}}</div>\n'
)
BACK = (
    "{{FrontSide}}\n\n<hr id=answer>\n\n"
    '<div class="definition">{{Definition}}</div>\n'
    f'<div class="target">{{{{{TGT}}}}}</div>\n'
)


def request(action: str, **params) -> object:
    payload = json.dumps(
        {"action": action, "version": 6, "params": params}
    ).encode()
    req = urllib.request.Request(
        URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.load(resp)
    if result.get("error") is not None:
        raise RuntimeError(f"AnkiConnect: {result['error']}")
    return result["result"]


def ensure_model() -> None:
    if MODEL not in request("modelNames"):
        request(
            "createModel",
            modelName=MODEL,
            inOrderFields=FIELDS,
            css=CSS,
            isCloze=False,
            cardTemplates=[{
                "Name": "Card 1",
                "Front": FRONT,
                "Back": BACK,
            }],
        )
    else:
        # keep template and styling in sync with this code
        request(
            "updateModelTemplates",
            model={
                "name": MODEL,
                "templates": {"Card 1": {"Front": FRONT, "Back": BACK}},
            },
        )
        request("updateModelStyling", model={"name": MODEL, "css": CSS})


def export_words(word_ids: list[int] | None = None) -> int:
    """Push accepted dictionary words to Anki. Returns count added."""
    ensure_model()
    request("createDeck", deck=DECK)
    con = db.connect()
    try:
        if word_ids is None:
            rows = con.execute(
                "SELECT * FROM words ORDER BY id"
            ).fetchall()
        else:
            marks = ",".join("?" * len(word_ids))
            rows = con.execute(
                f"SELECT * FROM words WHERE id IN ({marks}) ORDER BY id",
                word_ids,
            ).fetchall()
        added = 0
        for w in rows:
            q = f'deck:"{DECK}" Word:"{w["lemma"]}"'
            if request("findNotes", query=q):
                continue
            seg = con.execute(
                "SELECT text, translation FROM segments WHERE id=?",
                (w["segment_id"],),
            ).fetchone()
            # show the inflection mapping next to the POS, e.g.
            # "VERB · gemaakt → maken", when the encountered form differs
            pos = w["pos"] or ""
            if (
                w["surface"]
                and w["surface"].lower() != w["lemma"].lower()
            ):
                pos = f"{pos} · {w['surface']} → {w['lemma']}".strip(" ·")
            clip = db.CLIPS_DIR / f"{w['id']}.mp3"
            audio = ""
            if clip.is_file():
                b64 = base64.b64encode(clip.read_bytes()).decode()
                fname = f"{SRC.lower()}_{w['id']}.mp3"
                request(
                    "storeMediaFile", filename=fname, data=b64
                )
                audio = f"[sound:{fname}]"
            request(
                "addNote",
                note={
                    "deckName": DECK,
                    "modelName": MODEL,
                    "fields": {
                        "Rank": f"{w['id']:06d}",
                        "Word": w["lemma"],
                        "Part-of-Speech": pos,
                        "Definition": w["translation"] or "",
                        SRC: seg["text"] if seg else "",
                        TGT: seg["translation"] if seg else "",
                        "Freq": "",
                        "Word Audio": audio,
                    },
                    "tags": [f"{SRC.lower()}-media"],
                },
            )
            added += 1
        return added
    finally:
        con.close()


if __name__ == "__main__":
    print(f"added {export_words()} notes")
