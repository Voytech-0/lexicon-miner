"""Pipeline stages, each a subprocess entry point. Idempotent per stage.

Usage: uv run python pipeline.py <stage> <recording_id|path>
Stages: ingest, transcribe, candidates, translate
"""
import hashlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import config
import db

# ---------------------------------------------------------------- helpers


def _progress(done: float, total: float) -> None:
    """Structured progress line for the UI's progress bar."""
    print(f"PROGRESS\t{done}\t{total}", flush=True)


def media_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ---------------------------------------------------------------- ingest


def ingest(path: str) -> None:
    db.init_db()
    src = __import__("pathlib").Path(path).resolve()
    if not src.is_file():
        raise SystemExit(f"not a file: {src}")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    con = db.connect()
    try:
        if con.execute(
            "SELECT id FROM recordings WHERE media_sha=?", (sha,)
        ).fetchone():
            print("already ingested")
            return
        if db.DATA_DIR in src.parents:
            dest = src  # already under data/<lang>/Media/
        else:
            folder = db.MEDIA_DIR / src.stem
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / src.name
            shutil.copy2(src, dest)
        cur = con.execute(
            "INSERT INTO recordings(title, media_path, media_sha, added_at)"
            " VALUES(?,?,?,?)",
            (
                src.stem,
                str(dest.relative_to(db.DATA_DIR)),
                sha,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
        print(f"recording id {cur.lastrowid}")
    finally:
        con.close()


# ---------------------------------------------------------------- transcribe


def load_audio(media: str):
    """Decode via ffmpeg to 16 kHz mono f32 PCM.

    Bypasses faster-whisper's PyAV decoder (av>=15 removed metadata_errors,
    older av has no arm64 wheels). Returns a numpy array whisper accepts.
    """
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-i", media, "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-loglevel", "error", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def transcribe(recording_id: int) -> None:
    from faster_whisper import WhisperModel

    con = db.connect()
    try:
        rec = con.execute(
            "SELECT media_path FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        if rec is None:
            raise SystemExit(f"no recording {recording_id}")
        n_done = con.execute(
            "SELECT COUNT(*) FROM segments WHERE recording_id=?", (recording_id,)
        ).fetchone()[0]
        if n_done:
            print(f"already transcribed ({n_done} segments)")
            return
        media = str(db.DATA_DIR / rec["media_path"])
        duration = media_duration(media)
        print("loading whisper medium int8 ...", flush=True)
        model = WhisperModel("medium", device="cpu", compute_type="int8")
        print("decoding audio ...", flush=True)
        audio = load_audio(media)
        print("transcribing ...", flush=True)
        segs, _ = model.transcribe(
            audio, language=config.SOURCE, word_timestamps=True, vad_filter=True
        )
        count = 0
        for seg in segs:
            cur = con.execute(
                "INSERT INTO segments(recording_id, start_s, end_s, text)"
                " VALUES(?,?,?,?)",
                (recording_id, seg.start, seg.end, seg.text.strip()),
            )
            words = [
                (cur.lastrowid, w.word, w.start, w.end)
                for w in (seg.words or [])
            ]
            con.executemany(
                "INSERT INTO segment_words(segment_id, word, start_s, end_s)"
                " VALUES(?,?,?,?)",
                words,
            )
            count += 1
            if count % 5 == 0:
                con.commit()
            _progress(int(seg.end), int(duration) or 1)
        con.commit()
        print(f"done: {count} segments")
    finally:
        con.close()


# ---------------------------------------------------------------- candidates


def _glossary_map(con) -> dict[str, str]:
    """word -> literal gloss; 'freq' (deck definitions) beats 'wikdict'."""
    glosses: dict[str, str] = {}
    for r in con.execute(
        "SELECT word, gloss, source FROM glossary"
        " ORDER BY CASE source WHEN 'freq' THEN 0 ELSE 1 END"
    ):
        glosses.setdefault(r["word"], r["gloss"])
    return glosses


def candidates(recording_id: int) -> None:
    import nlp

    con = db.connect()
    try:
        freq = {
            r["word"]: r["rank"]
            for r in con.execute("SELECT word, rank FROM frequency_words")
        }
        known = {
            r["lemma"] for r in con.execute("SELECT lemma FROM words")
        }
        glosses = _glossary_map(con)
        seen = {  # lemmas already proposed for this recording (first wins)
            r["lemma"]
            for r in con.execute(
                "SELECT DISTINCT lemma FROM candidates WHERE recording_id=?",
                (recording_id,),
            )
        }
        seen_surface = {  # also dedupe surface forms: spaCy can give one
            # word two lemmas (laat -> laat/laten) depending on context
            r["surface"].lower() for r in con.execute(
                "SELECT DISTINCT surface FROM candidates WHERE recording_id=?",
                (recording_id,),
            )
        }
        segs = con.execute(
            "SELECT id, text FROM segments WHERE recording_id=? ORDER BY id",
            (recording_id,),
        ).fetchall()
        n = 0
        for i, seg in enumerate(segs, 1):
            rows = []
            for form, lemma, pos, is_stop, is_word in nlp.tag_segment(seg["text"]):
                if not is_word or is_stop:
                    continue
                lem = nlp.normalize(lemma)
                if lem in known or lem in seen:
                    continue
                if form.lower() in seen_surface:
                    continue
                seen.add(lem)
                seen_surface.add(form.lower())
                # sentence-initial words come capitalized; decapitalise
                # unless it's a proper noun (Ernie, Bert stay)
                surface = form if pos == "PROPN" else form.lower()
                gloss = (
                    glosses.get(lem)
                    or glosses.get(nlp.normalize(surface))
                )
                rows.append(
                    (
                        recording_id,
                        lem,
                        surface,
                        pos,
                        gloss,
                        seg["id"],
                        freq.get(nlp.normalize(surface)),
                        "pending",
                    )
                )
            if rows:
                con.executemany(
                    "INSERT OR IGNORE INTO candidates"
                    "(recording_id, lemma, surface, pos, translation,"
                    " segment_id, freq_rank, status) VALUES(?,?,?,?,?,?,?,?)",
                    rows,
                )
                n += len(rows)
            _progress(i, len(segs))
        con.commit()
        print(f"done: {n} new candidates")
    finally:
        con.close()


# ---------------------------------------------------------------- translate


def translate_stage(recording_id: int) -> None:
    import translate

    con = db.connect()
    try:
        seg_rows = con.execute(
            "SELECT id, text FROM segments"
            " WHERE recording_id=? AND translation IS NULL ORDER BY id",
            (recording_id,),
        ).fetchall()
        cand_rows = con.execute(
            "SELECT c.id, c.surface, s.text AS sentence FROM candidates c"
            " JOIN segments s ON s.id = c.segment_id"
            " WHERE c.recording_id=? AND c.status='pending' AND c.translation"
            " IS NULL ORDER BY c.id",
            (recording_id,),
        ).fetchall()
        if not seg_rows and not cand_rows:
            print("nothing to translate")
            return
        print(
            f"translating {len(seg_rows)} segments,"
            f" {len(cand_rows)} candidate words ...",
            flush=True,
        )
        total = len(seg_rows) + len(cand_rows)

        def make_prog(base):
            def prog(done, _unused):
                _progress(base + done, total)
            return prog

        if seg_rows:
            texts = [r["text"] for r in seg_rows]
            outs = translate.translate(texts, progress=make_prog(0))
            con.executemany(
                "UPDATE segments SET translation=? WHERE id=?",
                list(zip(outs, (r["id"] for r in seg_rows))),
            )
            con.commit()
        if cand_rows:
            items = [(r["surface"], r["sentence"]) for r in cand_rows]
            outs = translate.translate_words(
                items, progress=make_prog(len(seg_rows))
            )
            con.executemany(
                "UPDATE candidates SET translation=? WHERE id=?",
                list(zip(outs, (r["id"] for r in cand_rows))),
            )
            con.commit()
        translate.unload()
        print("done")
    finally:
        con.close()


# ---------------------------------------------------------------- main


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    stage, arg = sys.argv[1], sys.argv[2]
    if stage == "ingest":
        ingest(arg)
    elif stage in ("transcribe", "candidates", "translate"):
        rid = int(arg)
        {"transcribe": transcribe, "candidates": candidates,
         "translate": translate_stage}[stage](rid)
    else:
        raise SystemExit(f"unknown stage {stage}")


if __name__ == "__main__":
    main()
