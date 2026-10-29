"""Retrieval-quality evaluation harness (isolates the RETRIEVER, not the answer).

Why this exists alongside ``llm_eval.py``:
  * ``llm_eval.py`` judges final ANSWER quality with an LLM (needs API keys and
    blends retrieval + generation quality into one number).
  * This harness isolates RETRIEVAL quality with pure, deterministic metrics
    (hit@k, MRR, precision@k) and runs an A/B of *dense-only* vs *hybrid
    re-ranked* ordering, so we can actually prove whether the lexical re-rank in
    ``src.core.retrieval`` improves retrieval instead of just assuming it does.

CI split (mirrors ``llm_eval.py``):
  * The scoring functions below are pure -> no API key, no vector store -> they
    run in the offline CI ``tests`` job via ``tests/test_retrieval_eval.py``.
  * ``run_retrieval_eval()`` needs a populated vector store, so the live run is
    nightly / secret-gated.

Relevance labels (proxy): full corpus-level relevance judgements are expensive,
so each dataset row may carry ``relevant_keywords`` -- the salient terms a
correctly retrieved chunk must contain. A chunk counts as relevant when it
covers at least ``min_overlap`` of those keywords (whole-word, case-insensitive).
Because this is a proxy we report rank-sensitive success metrics (hit@k / MRR /
precision@k) and deliberately NOT recall@k, which would need exhaustive
per-chunk gold labels over the whole corpus. Rows without ``relevant_keywords``
are skipped, keeping the dataset backward compatible with ``llm_eval.py``.

Usage::

    uv run python -m src.eval.retrieval_eval
    uv run python -m src.eval.retrieval_eval --k 5 --gate 0.7 --strict \
        --report src/eval/retrieval_eval_report.md
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Pure, stdlib-only helper (safe to import without API keys / vector backend).
from src.core.retrieval import rerank_scored_documents

logger = logging.getLogger(__name__)

DEFAULT_DATASET = Path(__file__).parent / "eval_dataset.json"
DEFAULT_REPORT = Path(__file__).parent / "retrieval_eval_report.md"
DEFAULT_K = 5
DEFAULT_MIN_OVERLAP = 0.5
DEFAULT_GATE = 0.7  # gate on hybrid MRR


# --------------------------------------------------------------------------- #
# Pure relevance labelling + metrics (no API key / vector store needed)
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def keyword_coverage(chunk_text: str, relevant_keywords: list[str]) -> float:
    """Fraction of gold keywords present in a chunk (whole-word, case-insensitive)."""
    keywords = [k for k in (relevant_keywords or []) if k and k.strip()]
    if not keywords:
        return 0.0
    haystack = _normalize(chunk_text)
    hits = 0
    for kw in keywords:
        norm = _normalize(kw)
        if norm and re.search(rf"(?<!\w){re.escape(norm)}(?!\w)", haystack):
            hits += 1
    return round(hits / len(keywords), 3)


def chunk_is_relevant(
    chunk_text: str, relevant_keywords: list[str], *, min_overlap: float = DEFAULT_MIN_OVERLAP
) -> bool:
    """True when a chunk covers at least ``min_overlap`` of the gold keywords."""
    return keyword_coverage(chunk_text, relevant_keywords) >= min_overlap


def relevance_flags(
    ranked_chunk_texts: list[str],
    relevant_keywords: list[str],
    *,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
) -> list[bool]:
    """Map an ordered list of chunk texts to per-rank relevance booleans."""
    return [
        chunk_is_relevant(text, relevant_keywords, min_overlap=min_overlap)
        for text in ranked_chunk_texts
    ]


def hit_at_k(flags: list[bool], k: int) -> float:
    """1.0 if any of the top-k results is relevant, else 0.0 (a.k.a. success@k)."""
    return 1.0 if any(flags[:k]) else 0.0


def precision_at_k(flags: list[bool], k: int) -> float:
    """Fraction of the top-k results that are relevant."""
    if k <= 0:
        return 0.0
    top = flags[:k]
    if not top:
        return 0.0
    return round(sum(1 for f in top if f) / len(top), 3)


def reciprocal_rank(flags: list[bool]) -> float:
    """1 / rank of the first relevant result (0.0 if none are relevant)."""
    for i, flag in enumerate(flags, start=1):
        if flag:
            return round(1.0 / i, 3)
    return 0.0


def summarize_retrieval_scores(
    per_query_flags: list[list[bool]], k: int, gate: float, *, label: str = "hybrid"
) -> dict:
    """Aggregate per-query relevance flags into mean metrics + gate decision (pure).

    The gate is applied to mean MRR (the rank-sensitive quality signal).
    """

    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    hits = [hit_at_k(flags, k) for flags in per_query_flags]
    mrrs = [reciprocal_rank(flags) for flags in per_query_flags]
    precisions = [precision_at_k(flags, k) for flags in per_query_flags]
    mrr_mean = _mean(mrrs)
    return {
        "label": label,
        "k": k,
        "total_queries": len(per_query_flags),
        "hit_at_k": _mean(hits),
        "mrr": mrr_mean,
        "precision_at_k": _mean(precisions),
        "gate": gate,
        "passed": mrr_mean >= gate,
    }


def compare_summaries(dense: dict, hybrid: dict) -> dict:
    """A/B lift of hybrid re-rank over dense-only ordering (pure)."""
    return {
        "mrr_dense": dense.get("mrr", 0.0),
        "mrr_hybrid": hybrid.get("mrr", 0.0),
        "mrr_lift": round(hybrid.get("mrr", 0.0) - dense.get("mrr", 0.0), 3),
        "hit_dense": dense.get("hit_at_k", 0.0),
        "hit_hybrid": hybrid.get("hit_at_k", 0.0),
        "hit_lift": round(hybrid.get("hit_at_k", 0.0) - dense.get("hit_at_k", 0.0), 3),
    }


# --------------------------------------------------------------------------- #
# Ordering helpers (turn one scored candidate set into two rival rankings)
# --------------------------------------------------------------------------- #
def dense_order(scored: list[tuple[Any, float | None]]) -> list[Any]:
    """Docs ordered by raw vector relevance score (None scores sink to the end)."""
    return [
        doc
        for doc, _ in sorted(scored, key=lambda ds: (ds[1] is not None, ds[1] or 0.0), reverse=True)
    ]


def hybrid_order(scored: list[tuple[Any, float | None]], query: str) -> list[Any]:
    """Docs ordered by the project's hybrid (vector + lexical) re-ranker."""
    return [row["doc"] for row in rerank_scored_documents(scored, query)]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def write_retrieval_report(
    dense: dict, hybrid: dict, ab: dict, rows: list[dict[str, Any]], path: Path
) -> None:
    """Write a concise retrieval-eval report for README / interview evidence."""
    status = "PASS" if hybrid.get("passed") else "FAIL"
    k = hybrid.get("k")
    lines = [
        "# UPSC AI Retrieval Evaluation Report",
        "",
        f"Status (hybrid MRR gate): **{status}**",
        "",
        "## Summary (dense-only vs hybrid re-rank)",
        "",
        f"- Queries evaluated: {hybrid.get('total_queries', 0)}",
        f"- k: {k}",
        f"- MRR: dense {ab.get('mrr_dense')} -> hybrid {ab.get('mrr_hybrid')} "
        f"(lift {ab.get('mrr_lift'):+})",
        f"- hit@{k}: dense {ab.get('hit_dense')} -> hybrid {ab.get('hit_hybrid')} "
        f"(lift {ab.get('hit_lift'):+})",
        f"- precision@{k}: dense {dense.get('precision_at_k')} -> "
        f"hybrid {hybrid.get('precision_at_k')}",
        f"- MRR gate: {hybrid.get('gate')}",
        "",
        "## Per-query reciprocal rank",
        "",
        "| # | dense RR | hybrid RR | Question |",
        "|---:|---:|---:|---|",
    ]
    for i, row in enumerate(rows, start=1):
        q = str(row.get("question", "")).replace("|", "\\|")[:90]
        lines.append(f"| {i} | {row.get('dense_rr', 0.0)} | {row.get('hybrid_rr', 0.0)} | {q} |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Generated by `src.eval.retrieval_eval`.",
            "- Relevance is a keyword-coverage proxy (`relevant_keywords` per row); "
            "curate those keywords for stronger guarantees.",
            "- recall@k is intentionally omitted (no exhaustive corpus-level gold labels).",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Live run (needs a populated vector store; nightly / secret-gated)
# --------------------------------------------------------------------------- #
def _load_labelled(dataset_path: Path) -> list[dict]:
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Eval dataset {dataset_path} must be a JSON list")
    return [row for row in data if row.get("relevant_keywords")]


def run_retrieval_eval(
    dataset_path: Path = DEFAULT_DATASET,
    k: int = DEFAULT_K,
    gate: float = DEFAULT_GATE,
    min_overlap: float = DEFAULT_MIN_OVERLAP,
    report_path: Path | None = None,
) -> bool:
    """Evaluate retrieval on the labelled dataset; return True if hybrid MRR gate passes."""
    # Deferred heavy import so the pure metrics above stay importable in offline CI.
    from src.core.vector_store import load_vector_store

    labelled = _load_labelled(dataset_path)
    if not labelled:
        logger.warning(
            "No dataset rows carry 'relevant_keywords'; nothing to evaluate. "
            "Add keyword labels to eval_dataset.json to enable retrieval eval."
        )
        return True

    dense_flags: list[list[bool]] = []
    hybrid_flags: list[list[bool]] = []
    rows: list[dict[str, Any]] = []

    for row in labelled:
        question = row["question"]
        persist_key = row["persist_key"]
        keywords = row["relevant_keywords"]

        db = load_vector_store(persist_key)
        if db is None:
            logger.warning("No vector store for key '%s'; skipping query.", persist_key)
            continue

        try:
            scored = db.similarity_search_with_relevance_scores(question, k=k)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Scored search failed for '%s' (%s); using plain search", persist_key, exc
            )
            scored = [(doc, None) for doc in db.similarity_search(question, k=k)]

        dflags = relevance_flags(
            [getattr(d, "page_content", "") for d in dense_order(scored)],
            keywords,
            min_overlap=min_overlap,
        )
        hflags = relevance_flags(
            [getattr(d, "page_content", "") for d in hybrid_order(scored, question)],
            keywords,
            min_overlap=min_overlap,
        )
        dense_flags.append(dflags)
        hybrid_flags.append(hflags)
        rows.append(
            {
                "question": question,
                "dense_rr": reciprocal_rank(dflags),
                "hybrid_rr": reciprocal_rank(hflags),
            }
        )
        logger.info(
            "Scored | dense_rr=%.2f hybrid_rr=%.2f | %s",
            reciprocal_rank(dflags),
            reciprocal_rank(hflags),
            question[:55],
        )

    dense_summary = summarize_retrieval_scores(dense_flags, k, gate, label="dense")
    hybrid_summary = summarize_retrieval_scores(hybrid_flags, k, gate, label="hybrid")
    ab = compare_summaries(dense_summary, hybrid_summary)

    print("\n" + "=" * 60)
    print(
        f"RETRIEVAL EVAL (dense-only vs hybrid re-rank, k={k}, n={hybrid_summary['total_queries']})"
    )
    print(
        f"  MRR            dense {ab['mrr_dense']}  ->  hybrid {ab['mrr_hybrid']}  (lift {ab['mrr_lift']:+})"
    )
    print(
        f"  hit@{k}         dense {ab['hit_dense']}  ->  hybrid {ab['hit_hybrid']}  (lift {ab['hit_lift']:+})"
    )
    print(
        f"  precision@{k}   dense {dense_summary['precision_at_k']}  ->  hybrid {hybrid_summary['precision_at_k']}"
    )
    print("=" * 60)

    if report_path is not None:
        write_retrieval_report(dense_summary, hybrid_summary, ab, rows, report_path)
        print(f"Report written: {report_path}")

    passed = hybrid_summary["passed"]
    print(
        f"Hybrid MRR gate >= {gate}: {'PASS' if passed else 'FAIL'} (got {hybrid_summary['mrr']})"
    )
    return passed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Retrieval-quality eval (hit@k / MRR / precision@k)"
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--gate", type=float, default=DEFAULT_GATE)
    parser.add_argument("--min-overlap", type=float, default=DEFAULT_MIN_OVERLAP)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Markdown report path")
    parser.add_argument("--no-report", action="store_true", help="Do not write a markdown report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when the hybrid MRR gate fails (default: report-only).",
    )
    args = parser.parse_args()

    try:
        passed = run_retrieval_eval(
            args.dataset,
            k=args.k,
            gate=args.gate,
            min_overlap=args.min_overlap,
            report_path=None if args.no_report else args.report,
        )
    finally:
        # Release DB pools opened by the vector-store / memory wiring, if any.
        try:
            from src.graph.memory import close_memory

            close_memory()
        except Exception:  # pragma: no cover - defensive
            pass

    if args.strict:
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
