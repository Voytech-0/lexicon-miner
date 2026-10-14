"""Streamlit UI. Never imports heavy libs directly; stages run as
subprocesses so models load, run, and exit one at a time."""
import subprocess

import streamlit as st

import anki
import config
import db
import pipeline

st.set_page_config(page_title=config.SOURCE_NAME, layout="wide")


def run_stage(args: list[str]) -> None:
    """Spawn a pipeline subprocess; stream stdout, show a progress bar
    for PROGRESS lines and plain text for the rest."""
    with st.status(f"running: {' '.join(args)}") as status:
        proc = subprocess.Popen(
            [  "uv", "run", "--no-progress", "python", *args],
            cwd=db.ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout
        bar = st.progress(0.0, text="")
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("PROGRESS\t"):
                _, done, total = line.split("\t")
                frac = min(float(done) / max(float(total), 1.0), 1.0)
                bar.progress(frac, text=f"{done} / {total}")
            elif line:
                st.write(line)
        bar.empty()
        code = proc.wait()
        if code != 0:
            status.update(state="error", label="stage failed")
            st.stop()
        status.update(state="complete", label="done")
    st.rerun()


def stage_state(recording_id: int) -> dict:
    con = db.connect()
    try:
        one = lambda q, *a: con.execute(q, a).fetchone()[0]  # noqa
        return {
            "segments": one(
                "SELECT COUNT(*) FROM segments WHERE recording_id=?", recording_id
            ),
            "untranslated": one(
                "SELECT COUNT(*) FROM segments WHERE recording_id=?"
                " AND translation IS NULL",
                recording_id,
            )
            + one(
                "SELECT COUNT(*) FROM candidates WHERE recording_id=?"
                " AND status='pending' AND translation IS NULL",
                recording_id,
            ),
            "candidates": one(
                "SELECT COUNT(*) FROM candidates WHERE recording_id=?"
                " AND status='pending'",
                recording_id,
            ),
            "accepted": one(
                "SELECT COUNT(*) FROM candidates WHERE recording_id=?"
                " AND status='accepted'",
                recording_id,
            ),
        }
    finally:
        con.close()


def extract_clip(src: str, start: float, end: float, out) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{max(start - 0.2, 0):.2f}", "-to", f"{end + 0.2:.2f}",
            "-i", src, "-vn", "-q:a", "4", str(out),
        ],
        cwd=db.DATA_DIR,
        check=True,
    )


# ---------------------------------------------------------------- sidebar

st.sidebar.header("Recordings")
con = db.connect()
recs = con.execute("SELECT id, title FROM recordings ORDER BY id").fetchall()
freq_count = con.execute("SELECT COUNT(*) FROM frequency_words").fetchone()[0]
con.close()

if freq_count == 0:
    if st.sidebar.button("Import frequency list (~1 MB, one-off)"):
        run_stage(["seed_freq.py"])
con = db.connect()
wikdict_count = con.execute(
    "SELECT COUNT(*) FROM glossary WHERE source='wikdict'"
).fetchone()[0]
con.close()
if wikdict_count == 0:
    if st.sidebar.button("Import WikDict glossary (one-off)"):
        run_stage(["seed_wikdict.py"])

up = st.sidebar.file_uploader(
    "Add recording", type=["mp3", "mp4", "m4a", "wav"]
)
if up is not None:
    folder = db.MEDIA_DIR / up.name.rsplit(".", 1)[0]
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / up.name
    if not dest.exists():
        dest.write_bytes(up.getbuffer())
    run_stage(["pipeline.py", "ingest", str(dest)])

sel = st.sidebar.selectbox(
    "Open", [r["title"] for r in recs] or ["(no recordings)"]
)
rec = next((r for r in recs if r["title"] == sel), None)

# ---------------------------------------------------------------- stages

if rec is None:
    st.info(f"Add a {config.SOURCE_NAME} mp3/mp4 in the sidebar to begin.")
    st.stop()

rid = rec["id"]
con = db.connect()
rec_row = con.execute(
    "SELECT media_path FROM recordings WHERE id=?", (rid,)
).fetchone()
media_rel = rec_row["media_path"]
state = stage_state(rid)
st.header(f"{rec['title']}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("segments", state["segments"])
c2.metric("untranslated", state["untranslated"])
c3.metric("pending candidates", state["candidates"])
c4.metric("accepted words", state["accepted"])

if state["segments"] == 0:
    if st.button("Transcribe (whisper medium, ~min)"):
        run_stage(["pipeline.py", "transcribe", str(rid)])
    st.stop()

if state["candidates"] == 0:
    if st.button("Find candidates (spaCy)"):
        run_stage(["pipeline.py", "candidates", str(rid)])
    st.stop()

if state["untranslated"] > 0:
    if st.button("Translate (MarianMT)"):
        run_stage(["pipeline.py", "translate", str(rid)])
    st.stop()

# ---------------------------------------------------------------- review

cands = con.execute(
    "SELECT c.*, s.start_s, s.end_s, s.text AS sentence,"
    " s.translation AS sentence_tr"
    " FROM candidates c JOIN segments s ON s.id = c.segment_id"
    " WHERE c.recording_id=? AND c.status='pending' ORDER BY c.segment_id",
    (rid,),
).fetchall()

st.subheader(f"Review candidates ({len(cands)})")
st.caption(
    "All words are checked by default; frequency-list words are orange"
    " with their rank — uncheck any you don't want."
)

db.CAND_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
with st.form("review"):
    checks = []
    for c in cands:
        clip = db.CAND_CLIPS_DIR / f"{c['id']}.mp3"
        if not clip.exists():
            try:
                extract_clip(
                    media_rel, c["start_s"], c["end_s"], clip
                )
            except subprocess.CalledProcessError:
                clip = None
        cols = st.columns([0.08, 0.22, 0.7])
        default = True  # all candidates pre-checked; frequency words stay
        # orange-badged so they are easy to spot and uncheck
        label = c["surface"]
        if c["freq_rank"] is not None:
            label = f":orange[{label} #{c['freq_rank']}]"
        with cols[0]:
            on = st.checkbox(
                "add", value=default, key=f"chk{c['id']}", label_visibility="hidden"
            )
        with cols[1]:
            if c["lemma"] and c["lemma"] != c["surface"].lower():
                st.markdown(
                    f"**{label}**  \n{c['surface']} → {c['lemma']}"
                    f"  \n{c['pos'] or ''}"
                )
            else:
                st.markdown(f"**{label}**  \n{c['pos'] or ''}")
            # inline edit of the literal gloss before accepting
            st.text_input(
                "gloss", value=c["translation"] or "", key=f"gloss{c['id']}",
                label_visibility="collapsed",
            )
        with cols[2]:
            st.write(f"{c['sentence']}  \n_{c['sentence_tr']}_")
            if clip:
                st.audio(str(clip))
        checks.append((c, on))

    col_a, col_r = st.columns(2)
    accept = col_a.form_submit_button("Add checked to dictionary")
    reject = col_r.form_submit_button("Reject unchecked")

if accept or reject:
    from datetime import datetime, timezone

    accepted, rejected = [], []
    for c, on in checks:
        if accept and on:
            accepted.append(c)
        elif reject and not on:
            rejected.append(c)
    for c in rejected:
        con.execute(
            "UPDATE candidates SET status='rejected' WHERE id=?", (c["id"],)
        )
    for c in accepted:
        edited = st.session_state.get(f"gloss{c['id']}", "").strip()
        cur = con.execute(
            "INSERT INTO words(lemma, translation, pos, surface, segment_id,"
            " recording_id, created_at) VALUES(?,?,?,?,?,?,?)",
            (
                c["lemma"], edited or c["translation"], c["pos"],
                c["surface"], c["segment_id"], c["recording_id"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.execute(
            "UPDATE candidates SET status='accepted' WHERE id=?", (c["id"],)
        )
        con.commit()  # need words.id before clip naming
        extract_clip(
            media_rel, c["start_s"], c["end_s"],
            db.CLIPS_DIR / f"{cur.lastrowid}.mp3",
        )
    con.commit()
    con.close()
    st.toast(f"{len(accepted)} added, {len(rejected)} rejected")
    st.rerun()

# ---------------------------------------------------------------- export

st.divider()
words = con.execute("SELECT COUNT(*) FROM words").fetchone()[0]
if st.button(f"Export {words} words to Anki (AnkiConnect)"):
    try:
        n = anki.export_words()
        st.toast(f"added {n} notes to deck '{anki.DECK}'")
    except Exception as e:  # Anki not running / plugin absent
        st.error(f"AnkiConnect unreachable: {e}")
con.close()
