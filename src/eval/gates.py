"""Pure, dependency-free eval scoring + quality-gate logic.

This module deliberately imports **nothing heavy** (stdlib only). All the
number-crunching and pass/fail decisions for the LLM-as-judge eval live here so
they can be unit-tested in the offline CI job (and in any sandbox) without API
keys, a vector store, or the langchain/langgraph stack.

``llm_eval.py`` re-exports these names, so existing import paths keep working:

    from src.eval.llm_eval import summarize_scores   # still valid
    from src.eval.gates import summarize_scores       # offline-importable

What lives here:
  * ``summarize_scores``     - aggregate raw judge scores into means + the
                              legacy faithfulness-only gate decision.
  * ``evaluate_strict_gate`` - a STRICT multi-metric gate: faithfulness AND
                              answer_relevancy AND context_precision must each
                              clear their own threshold (RAGAS-style).
  * ``summarize_by_agent``   - PER-AGENT breakdown so a regression in one agent
                              (e.g. ncert) does not hide behind a healthy mean.
  * ``write_markdown_report``- render the summary, per-agent table, and strict
                              gate result as Markdown evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

# Default thresholds. Faithfulness is the strictest (anti-hallucination core);
# relevancy and precision are somewhat lower because a partially-off-topic chunk
# is far less harmful than a fabricated fact.
DEFAULT_FAITHFULNESS_GATE = 0.9
DEFAULT_RELEVANCY_GATE = 0.7
DEFAULT_PRECISION_GATE = 0.6

_METRIC_KEYS = ("faithfulness", "answer_relevancy", "context_precision")


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(sum(values) / len(values), 3) if values else 0.0


def summarize_scores(
    faith_scores: list[float],
    rel_scores: list[float],
    prec_scores: list[float],
    gate: float,
    unsupported_claim_counts: list[int] | None = None,
) -> dict:
    """Aggregate raw judge scores into means + gate decision (pure, testable).

    ``passed`` uses faithfulness only (the historical, backward-compatible
    gate). Use :func:`evaluate_strict_gate` for the multi-metric gate.
    """
    unsupported_claim_counts = unsupported_claim_counts or []
    total_cases = len(faith_scores)
    cases_with_unsupported = sum(1 for count in unsupported_claim_counts if count > 0)
    unsupported_rate = round(cases_with_unsupported / total_cases, 3) if total_cases else 0.0

    faith_mean = _mean(faith_scores)
    return {
        "faithfulness": faith_mean,
        "answer_relevancy": _mean(rel_scores),
        "context_precision": _mean(prec_scores),
        "unsupported_claim_rate": unsupported_rate,
        "total_cases": total_cases,
        "gate": gate,
        "passed": faith_mean >= gate,
    }


def evaluate_strict_gate(
    summary: dict[str, Any],
    *,
    faithfulness_gate: float = DEFAULT_FAITHFULNESS_GATE,
    relevancy_gate: float = DEFAULT_RELEVANCY_GATE,
    precision_gate: float = DEFAULT_PRECISION_GATE,
) -> dict:
    """Strict multi-metric gate: EVERY metric must clear its own threshold.

    Returns ``{passed, failures, thresholds}`` where ``failures`` lists exactly
    which metrics fell short (with value + threshold) so CI logs and the report
    can point at the specific regression. Pure and fully testable offline.
    """
    thresholds = {
        "faithfulness": faithfulness_gate,
        "answer_relevancy": relevancy_gate,
        "context_precision": precision_gate,
    }
    failures = [
        {"metric": key, "value": float(summary.get(key, 0.0)), "threshold": thresholds[key]}
        for key in _METRIC_KEYS
        if float(summary.get(key, 0.0)) < thresholds[key]
    ]
    return {"passed": not failures, "failures": failures, "thresholds": thresholds}


def summarize_by_agent(
    rows: list[dict[str, Any]],
    *,
    faithfulness_gate: float = DEFAULT_FAITHFULNESS_GATE,
    relevancy_gate: float | None = None,
    precision_gate: float | None = None,
) -> dict[str, dict]:
    """Group per-case rows by their ``agent`` tag and score each agent.

    Each row is expected to look like::

        {"agent": "ncert", "faithfulness": 0.9, "answer_relevancy": 0.8,
         "context_precision": 0.7, "unsupported_claims": 0}

    Rows without an ``agent`` are bucketed under ``"rag"``. When both
    ``relevancy_gate`` and ``precision_gate`` are given, each agent is judged by
    the strict multi-metric gate; otherwise it falls back to faithfulness-only.
    Pure and testable offline.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        agent = row.get("agent") or "rag"
        groups.setdefault(agent, []).append(row)

    strict = relevancy_gate is not None and precision_gate is not None
    out: dict[str, dict] = {}
    for agent in sorted(groups):
        agent_rows = groups[agent]
        summary = {
            "agent": agent,
            "total_cases": len(agent_rows),
            "faithfulness": _mean([r.get("faithfulness", 0.0) for r in agent_rows]),
            "answer_relevancy": _mean([r.get("answer_relevancy", 0.0) for r in agent_rows]),
            "context_precision": _mean([r.get("context_precision", 0.0) for r in agent_rows]),
            "unsupported_claims": sum(int(r.get("unsupported_claims", 0) or 0) for r in agent_rows),
        }
        if strict:
            gate_res = evaluate_strict_gate(
                summary,
                faithfulness_gate=faithfulness_gate,
                relevancy_gate=relevancy_gate,
                precision_gate=precision_gate,
            )
            summary["passed"] = gate_res["passed"]
            summary["failures"] = gate_res["failures"]
        else:
            summary["passed"] = summary["faithfulness"] >= faithfulness_gate
            summary["failures"] = (
                []
                if summary["passed"]
                else [
                    {
                        "metric": "faithfulness",
                        "value": summary["faithfulness"],
                        "threshold": faithfulness_gate,
                    }
                ]
            )
        out[agent] = summary
    return out


def write_markdown_report(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    path: Path,
    *,
    per_agent: dict[str, dict] | None = None,
    strict_gate: dict[str, Any] | None = None,
) -> None:
    """Write a concise eval report (README / interview evidence).

    ``per_agent`` and ``strict_gate`` are optional so older callers that only
    pass ``(summary, rows, path)`` keep working unchanged.
    """
    if strict_gate is not None:
        status = "PASS" if strict_gate.get("passed") else "FAIL"
        status_label = "strict multi-metric gate"
    else:
        status = "PASS" if summary.get("passed") else "FAIL"
        status_label = "faithfulness gate"

    lines = [
        "# UPSC AI RAG Evaluation Report",
        "",
        f"Status ({status_label}): **{status}**",
        "",
        "## Summary",
        "",
        f"- Total cases: {summary.get('total_cases', 0)}",
        f"- Faithfulness: {summary.get('faithfulness', 0.0)}",
        f"- Answer relevancy: {summary.get('answer_relevancy', 0.0)}",
        f"- Context precision: {summary.get('context_precision', 0.0)}",
        f"- Unsupported claim rate: {summary.get('unsupported_claim_rate', 0.0)}",
        f"- Faithfulness gate: {summary.get('gate')}",
    ]

    if strict_gate is not None:
        thresholds = strict_gate.get("thresholds", {})
        lines += [
            "",
            "## Strict multi-metric gate",
            "",
            f"- faithfulness >= {thresholds.get('faithfulness')}",
            f"- answer_relevancy >= {thresholds.get('answer_relevancy')}",
            f"- context_precision >= {thresholds.get('context_precision')}",
        ]
        failures = strict_gate.get("failures") or []
        if failures:
            lines.append("")
            lines.append("Failing metrics:")
            for f in failures:
                lines.append(f"- {f['metric']}: {f['value']} < {f['threshold']}")

    if per_agent:
        lines += [
            "",
            "## Per-agent results",
            "",
            "| Agent | Cases | Faithfulness | Relevancy | Precision | Unsupported | Gate |",
            "|---|---:|---:|---:|---:|---:|:--:|",
        ]
        for agent in sorted(per_agent):
            a = per_agent[agent]
            gate_mark = "PASS" if a.get("passed") else "FAIL"
            lines.append(
                f"| {agent} | {a.get('total_cases', 0)} | {a.get('faithfulness', 0.0)} | "
                f"{a.get('answer_relevancy', 0.0)} | {a.get('context_precision', 0.0)} | "
                f"{a.get('unsupported_claims', 0)} | {gate_mark} |"
            )

    lines += [
        "",
        "## Case results",
        "",
        "| # | Agent | Faithfulness | Relevancy | Context precision | Unsupported claims | Question |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for i, row in enumerate(rows, start=1):
        q = str(row.get("question", "")).replace("|", "\\|")[:90]
        lines.append(
            f"| {i} | {row.get('agent', 'rag')} | {row.get('faithfulness', 0.0)} | "
            f"{row.get('answer_relevancy', 0.0)} | {row.get('context_precision', 0.0)} | "
            f"{row.get('unsupported_claims', 0)} | {q} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- This report is generated by `src.eval.llm_eval` (gate logic in `src.eval.gates`).",
        "- Metrics are a regression signal, not a claim of perfect factual correctness.",
        "- Keep expanding the dataset (more agents / cases) before treating these as production guarantees.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
