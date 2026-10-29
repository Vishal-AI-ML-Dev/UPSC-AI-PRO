"""Ingest distilled topper strategy .md files into the Mentor KB.

Why this script:
- Fast + free-tier friendly: sirf embedding calls (koi YouTube download nahi,
  koi LLM distillation nahi). Files already distilled hain.
- Har file ka YAML frontmatter (topper_name, rank) citeable 'source' label banta
  hai, taaki mentor jawab me attribute kar sake.
- Mentor KB ko CURRENT embedding model pe REBUILD karta hai (consistent 768-dim),
  isliye purana dimension-mismatch dobara nahi aayega.

Usage (project root se, venv active):
    uv run python scripts/ingest_topper_md.py

Apni files yahan daal: data/mentor_kb/toppers/*.md  (frontmatter ke saath)
"""
import os
import sys
import glob

# 'src' importable ho project root se.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import mentor_kb  # noqa: E402

TOPPER_DIR = os.path.join("data", "mentor_kb", "toppers")
FACTS_FILE = os.path.join("data", "mentor_kb", "upsc_facts.md")


def parse_frontmatter(raw):
    """Return (meta: dict, body: str). Minimal, dependency-free YAML-ish parser."""
    meta, body = {}, raw
    if raw.lstrip().startswith("---"):
        raw2 = raw.lstrip()
        parts = raw2.split("---", 2)
        if len(parts) >= 3:
            fm, body = parts[1], parts[2]
            for line in fm.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta, body.strip()


def source_label(meta, filepath):
    name = meta.get("topper_name")
    rank = meta.get("rank")
    if name and rank:
        return f"{name} ({rank})"
    if name:
        return name
    return os.path.splitext(os.path.basename(filepath))[0]


def collect_sources():
    sources = []
    files = sorted(glob.glob(os.path.join(TOPPER_DIR, "*.md")))
    for fp in files:
        with open(fp, encoding="utf-8") as fh:
            raw = fh.read()
        meta, body = parse_frontmatter(raw)
        if not body:
            continue
        label = source_label(meta, fp)
        md = {
            "source": label,
            "source_type": meta.get("source_type", "topper_interview"),
        }
        if meta.get("tags"):
            md["tags"] = meta["tags"]
        # Attribution har chunk me rahe (splitter ke baad bhi), isliye prepend.
        text = f"# {label} - UPSC strategy notes\n\n{body}"
        sources.append({"text": text, "metadata": md})
        print(f"  + {os.path.basename(fp):45s} source='{label}'  ({len(body)} chars)")

    # Optional curated facts file (agar disk pe ho to include kar lo).
    if os.path.exists(FACTS_FILE):
        with open(FACTS_FILE, encoding="utf-8") as fh:
            _, body = parse_frontmatter(fh.read())
        if body:
            sources.append({
                "text": body,
                "metadata": {"source": "Verified UPSC facts", "source_type": "curated_facts"},
            })
            print(f"  + {'upsc_facts.md':45s} source='Verified UPSC facts'  ({len(body)} chars)")
    return sources


def main():
    print("=" * 64)
    print("Ingest topper strategy markdown -> Mentor KB")
    print("=" * 64)

    if not os.path.isdir(TOPPER_DIR):
        os.makedirs(TOPPER_DIR, exist_ok=True)
        print(f"Folder banaya: {TOPPER_DIR}")
        print("Apni .md files usme daal ke script dobara chala.")
        return

    print(f"Reading from: {TOPPER_DIR}")
    sources = collect_sources()
    if not sources:
        print(f"\nKoi .md file nahi mili {TOPPER_DIR} me. Files daal ke dobara chala.")
        return

    print(f"\nIngesting {len(sources)} sources (rebuild=True, current embedding model)...")
    n = mentor_kb.build_kb(sources, rebuild=True)
    print(f"DONE: {n} chunks indexed -> {mentor_kb.kb_location()}")
    print(f"KB exists now : {mentor_kb.kb_exists()}")

    print("\n--- search smoke test (grounding check) ---")
    for q in [
        "answer writing strategy for mains",
        "Hindi medium me GS me acche marks kaise laaye",
        "mains marks kaise badhaye",
    ]:
        res = mentor_kb.search_kb(q, k=2)
        ctx = (res.get("context") or "")[:150].replace("\n", " ")
        print(f"\nQ: {q}")
        print(f"   grounded={res.get('grounded')}  citations={res.get('citations')}")
        print(f"   {ctx}")

    print("\n" + "=" * 64)
    print("Ho gaya. Ab server restart karke topper-strategy sawaal pooch.")
    print("=" * 64)


if __name__ == "__main__":
    main()
