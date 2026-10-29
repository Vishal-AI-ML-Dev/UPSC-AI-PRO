"""
Ingest sources into the Mentor Knowledge Base.

Run from the PROJECT ROOT (with your venv active):
    python scripts/ingest_mentor_kb.py

What it ingests:
  1) Curated verified UPSC facts  -> data/mentor_kb/upsc_facts.md
  2) Topper interview transcripts -> from the YouTube URLs in TOPPER_VIDEOS
  3) (optional) Official PDFs      -> notification / syllabus in PDF_FILES

Edit the lists below, then run. Re-run anytime to fully rebuild the KB.
NOTE: ingestion calls the Gemini embedding API, so make sure your API quota
is available (free tier is limited).
"""
import os
import sys
import logging

# Allow running from the project root: make 'src' importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import mentor_kb
from src.agents.lecture.graph import extract_video_id, get_transcript

log = logging.getLogger("ingest_mentor_kb")

# ── 1) Curated verified facts (durable, dated) ──
FACTS_FILE = os.path.join("data", "mentor_kb", "upsc_facts.md")

# ── 2) Topper interview videos (YouTube). Add 3-5 good ones. ──
#    'topper'/'year' are used as the CITATION shown to users, so fill them in.
TOPPER_VIDEOS = [
    # {"url": "https://www.youtube.com/watch?v=XXXXXXXXXXX", "topper": "AIR 5 - Srushti Deshmukh", "year": 2018},
    # {"url": "https://youtu.be/YYYYYYYYYYY", "topper": "AIR 1 - Shubham Kumar", "year": 2020},
]

# ── 3) Official PDFs (optional): notification, syllabus ──
PDF_FILES = [
    # {"path": "data/mentor_kb/upsc_notification_2025.pdf", "source": "UPSC Notification 2025 (official)"},
    # {"path": "data/mentor_kb/upsc_syllabus.pdf", "source": "UPSC Syllabus (official)"},
]


def _read_pdf(path: str) -> str:
    if not os.path.exists(path):
        log.warning("PDF not found: %s", path)
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            log.warning("pypdf/PyPDF2 not installed; skipping PDF %s", path)
            return ""
    try:
        reader = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        log.warning("PDF read failed %s: %s", path, e)
        return ""


def gather_sources():
    sources = []

    # 1) facts
    if os.path.exists(FACTS_FILE):
        with open(FACTS_FILE, "r", encoding="utf-8") as f:
            sources.append({
                "text": f.read(),
                "metadata": {"source": "Verified UPSC Facts (upsc.gov.in)", "type": "fact"},
            })
        log.info("Added curated facts: %s", FACTS_FILE)
    else:
        log.warning("Facts file not found: %s", FACTS_FILE)

    # 2) topper transcripts
    for v in TOPPER_VIDEOS:
        url = v.get("url", "")
        vid = extract_video_id(url)
        if not vid:
            log.warning("Skipping bad YouTube URL: %s", url)
            continue
        try:
            text, lang = get_transcript(vid)
        except Exception as e:
            log.warning("Transcript failed for %s: %s", url, e)
            continue
        topper = v.get("topper", "UPSC Topper")
        year = v.get("year", "")
        label = f"Topper interview: {topper}" + (f" ({year})" if year else "") + " - YouTube"
        sources.append({
            "text": text,
            "metadata": {"source": label, "type": "topper", "video_id": vid},
        })
        log.info("Added topper transcript: %s (%d chars, lang=%s)", label, len(text), lang)

    # 3) official PDFs
    for p in PDF_FILES:
        text = _read_pdf(p.get("path", ""))
        if text.strip():
            sources.append({
                "text": text,
                "metadata": {"source": p.get("source", os.path.basename(p["path"])), "type": "official"},
            })
            log.info("Added PDF: %s", p.get("path"))

    return sources


if __name__ == "__main__":
    try:
        from src.core.logging_config import setup_logging
        setup_logging()
    except Exception:
        logging.basicConfig(level=logging.INFO)

    srcs = gather_sources()
    if not srcs:
        log.error("No sources gathered. Add TOPPER_VIDEOS / PDF_FILES or check FACTS_FILE.")
        sys.exit(1)
    n = mentor_kb.build_kb(srcs, rebuild=True)
    log.info("DONE. Mentor KB indexed %d chunks at %s", n, mentor_kb.kb_dir())
