"""LLM-as-judge evaluation suite with faithfulness + strict multi-metric gates.

Runs the RAG subgraph over a labelled dataset and scores each answer on three
metrics, using the project's own Gemini model as the judge:

* faithfulness        - is every claim in the answer supported by the retrieved
                        context? (the core anti-hallucination metric)
* answer_relevancy    - does the answer directly address the question?
* context_precision   - how much of the retrieved context is actually relevant?

Gates (all pure logic lives in ``src.eval.gates``):

* Default: faithfulness-only gate (backward compatible).
* ``--strict``: RAGAS-style multi-metric gate - faithfulness AND relevancy AND
  precision must each clear their own threshold, AND every agent must pass its
  own per-agent gate. This is what CI uses to block regressions before deploy.

Per-agent: each dataset row may carry an ``agent`` tag (e.g. "ncert",
"lecture", "upload"). Results are broken down per agent so a regression in one
agent cannot hide behind a healthy overall mean. Rows without ``agent`` fall
under "rag".

Why LLM-as-judge instead of RAGAS: RAGAS does not yet support langchain 1.x
(its import chain references modules removed in newer langchain), and forcing a
compatible RAGAS would downgrade the app's core stack. This harness reuses the
same conceptual metrics with zero extra dependencies and full control.

Dataset format (eval_dataset.json) - a list of objects::

    {"question": str, "ground_truth": str, "persist_key": str, "agent": str?}

Usage::

    uv run python -m src.eval.llm_eval
    uv run python -m src.eval.llm_eval --gate 0.9 --strict \
        --relevancy-gate 0.7 --precision-gate 0.6
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.llm import get_llm
from src.core.vector_store import (
    load_vector_store,
    similarity_search_with_sources,
)

# Pure, dependency-free scoring + gate logic (offline-importable / testable).
from src.eval.gates import (
    DEFAULT_FAITHFULNESS_GATE,
    DEFAULT_PRECISION_GATE,
    DEFAULT_RELEVANCY_GATE,
    evaluate_strict_gate,
    summarize_by_agent,
    summarize_scores,
    write_markdown_report,
)
from src.graph.rag_graph import build_rag_subgraph

logger = logging.getLogger(__name__)

DEFAULT_DATASET = Path(__file__).parent / "eval_dataset.json"
DEFAULT_GATE = DEFAULT_FAITHFULNESS_GATE
DEFAULT_REPORT = Path(__file__).parent / "eval_report.md"
TOP_K = 5


class JudgeScore(BaseModel):
    """Structured judgement returned by the evaluator LLM."""

    score: float = Field(..., ge=0.0, le=1.0, description="Score between 0.0 and 1.0")
    reason: str = Field(..., description="One-sentence justification for the score")


_FAITHFULNESS_SYS = (
    "You are a strict RAG evaluator. Given CONTEXT and an ANSWER, rate from 0.0 "
    "to 1.0 how well every factual claim in the ANSWER is supported by the "
    "CONTEXT. 1.0 = every claim is grounded in the context; 0.0 = the answer is "
    "unsupported or hallucinated. Judge grounding only, not writing quality."
)
_RELEVANCY_SYS = (
    "You are a strict evaluator. Given a QUESTION and an ANSWER, rate from 0.0 to "
    "1.0 how directly and completely the ANSWER addresses the QUESTION. 1.0 = "
    "fully on-point; 0.0 = off-topic or evasive."
)
_PRECISION_SYS = (
    "You are a strict retrieval evaluator. Given a QUESTION and the retrieved "
    "CONTEXT, rate from 0.0 to 1.0 the fraction of the CONTEXT that is relevant "
    "to answering the QUESTION. 1.0 = all of it is relevant; 0.0 = none is."
)


def _structured_judge():
    """Structured-output judge that tolerates a provider-fallback wrapper."""
    base = get_llm()
    if hasattr(base, "with_structured_output"):
        return base.with_structured_output(JudgeScore)
    primary = base.runnable.with_structured_output(JudgeScore)
    fallbacks = [r.with_structured_output(JudgeScore) for r in base.fallbacks]
    return primary.with_fallbacks(fallbacks)


def _judge(judge, system_prompt: str, payload: str) -> float:
    """Run a single judgement and return its numeric score (0.0 on failure)."""
    try:
        result = judge.invoke([("system", system_prompt), ("human", payload)])
        return max(0.0, min(1.0, float(result.score)))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Judge call failed (%s); scoring 0.0", exc)
        return 0.0


def _load_dataset(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data:
        raise ValueError(f"Eval dataset {path} must be a non-empty JSON list")
    return data


def _retrieve_contexts(persist_key: str, question: str) -> list[str]:
    """Fetch the same chunks the RAG graph would retrieve, as plain text."""
    db = load_vector_store(persist_key)
    if db is None:
        return []
    result = similarity_search_with_sources(db, question, k=TOP_K, label="eval")
    return [chunk["excerpt"] for chunk in result.get("chunks", [])]


def _generate_answer(rag_graph, persist_key: str, question: str) -> str:
    out = rag_graph.invoke({"question": question, "persist_key": persist_key})
    return out.get("answer") or ""


def run_eval(
    dataset_path: Path = DEFAULT_DATASET,
    gate: float = DEFAULT_GATE,
    report_path: Path | None = None,
    *,
    strict: bool = False,
    relevancy_gate: float = DEFAULT_RELEVANCY_GATE,
    precision_gate: float = DEFAULT_PRECISION_GATE,
) -> bool:
    """Evaluate the dataset and return True if the active gate passes.

    When ``strict`` is False (default) the gate is faithfulness-only (legacy).
    When ``strict`` is True the multi-metric gate must pass overall AND every
    agent must pass its own per-agent strict gate.
    """
    dataset = _load_dataset(dataset_path)
    rag_graph = build_rag_subgraph(label="rag")
    judge = _structured_judge()

    faith_scores, rel_scores, prec_scores = [], [], []
    unsupported_claim_counts, rows = [], []

    for row in dataset:
        question = row["question"]
        persist_key = row["persist_key"]
        agent = row.get("agent") or "rag"
        contexts = _retrieve_contexts(persist_key, question)
        out = rag_graph.invoke({"question": question, "persist_key": persist_key})
        answer = out.get("answer") or ""
        unsupported_claims = out.get("unsupported_claims") or []
        context_blob = "\n\n".join(contexts) if contexts else "(no context retrieved)"

        faith = _judge(
            judge,
            _FAITHFULNESS_SYS,
            f"CONTEXT:\n{context_blob}\n\nANSWER:\n{answer}",
        )
        rel = _judge(
            judge,
            _RELEVANCY_SYS,
            f"QUESTION:\n{question}\n\nANSWER:\n{answer}",
        )
        prec = _judge(
            judge,
            _PRECISION_SYS,
            f"QUESTION:\n{question}\n\nCONTEXT:\n{context_blob}",
        )

        faith_scores.append(faith)
        rel_scores.append(rel)
        prec_scores.append(prec)
        unsupported_claim_counts.append(len(unsupported_claims))
        rows.append(
            {
                "question": question,
                "agent": agent,
                "faithfulness": faith,
                "answer_relevancy": rel,
                "context_precision": prec,
                "unsupported_claims": len(unsupported_claims),
            }
        )
        logger.info(
            "Scored | agent=%s faith=%.2f rel=%.2f prec=%.2f | %s",
            agent,
            faith,
            rel,
            prec,
            question[:50],
        )

    summary = summarize_scores(
        faith_scores, rel_scores, prec_scores, gate, unsupported_claim_counts
    )
    strict_result = evaluate_strict_gate(
        summary,
        faithfulness_gate=gate,
        relevancy_gate=relevancy_gate,
        precision_gate=precision_gate,
    )
    per_agent = summarize_by_agent(
        rows,
        faithfulness_gate=gate,
        relevancy_gate=relevancy_gate if strict else None,
        precision_gate=precision_gate if strict else None,
    )

    print("\n" + "=" * 60)
    print(f"LLM-AS-JUDGE RESULTS (mean over {len(dataset)} samples)")
    print(f"  faithfulness             {summary['faithfulness']}")
    print(f"  answer_relevancy         {summary['answer_relevancy']}")
    print(f"  context_precision        {summary['context_precision']}")
    print(f"  unsupported_claim_rate   {summary['unsupported_claim_rate']}")
    print("-" * 60)
    print("PER-AGENT")
    for name in sorted(per_agent):
        a = per_agent[name]
        print(
            f"  {name:<14} n={a['total_cases']:<3} faith={a['faithfulness']} "
            f"rel={a['answer_relevancy']} prec={a['context_precision']} "
            f"-> {'PASS' if a['passed'] else 'FAIL'}"
        )
    print("=" * 60)

    if report_path is not None:
        write_markdown_report(
            summary, rows, report_path, per_agent=per_agent, strict_gate=strict_result
        )
        print(f"Report written: {report_path}")

    if strict:
        agents_ok = all(a["passed"] for a in per_agent.values())
        passed = strict_result["passed"] and agents_ok
        print(
            f"STRICT gate (faith>={gate}, rel>={relevancy_gate}, prec>={precision_gate}, "
            f"all agents pass): {'PASS' if passed else 'FAIL'}"
        )
        if strict_result["failures"]:
            for f in strict_result["failures"]:
                print(f"  overall FAIL {f['metric']}: {f['value']} < {f['threshold']}")
        for name, a in per_agent.items():
            if not a["passed"]:
                print(f"  agent FAIL {name}: {[f['metric'] for f in a['failures']]}")
    else:
        passed = summary["passed"]
        print(
            f"Faithfulness gate >= {gate}: {'PASS' if passed else 'FAIL'} "
            f"(got {summary['faithfulness']})"
        )
    return passed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="LLM-as-judge eval with quality gates")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--gate", type=float, default=DEFAULT_GATE, help="Faithfulness threshold")
    parser.add_argument("--relevancy-gate", type=float, default=DEFAULT_RELEVANCY_GATE)
    parser.add_argument("--precision-gate", type=float, default=DEFAULT_PRECISION_GATE)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Gate on ALL metrics + every agent (default: faithfulness-only).",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Markdown report path")
    parser.add_argument("--no-report", action="store_true", help="Do not write a markdown report")
    args = parser.parse_args()

    try:
        passed = run_eval(
            args.dataset,
            args.gate,
            None if args.no_report else args.report,
            strict=args.strict,
            relevancy_gate=args.relevancy_gate,
            precision_gate=args.precision_gate,
        )
    finally:
        # Release DB pools opened by the RAG graph's memory wiring.
        from src.graph.memory import close_memory

        close_memory()
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
