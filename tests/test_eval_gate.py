"""Offline tests for the eval quality-gate logic and dataset integrity.

These run in CI without any API keys or a live vector store: the scoring and
gate logic lives in ``src.eval.gates`` (stdlib only, no langchain/langgraph),
and the dataset check only reads JSON. The full LLM-as-judge run
(``python -m src.eval.llm_eval``) is a separate, secret-gated CI job.
"""

import json
from pathlib import Path

from src.eval.gates import (
    DEFAULT_FAITHFULNESS_GATE,
    DEFAULT_PRECISION_GATE,
    DEFAULT_RELEVANCY_GATE,
    evaluate_strict_gate,
    summarize_by_agent,
    summarize_scores,
    write_markdown_report,
)

REQUIRED_KEYS = {"question", "ground_truth", "persist_key"}
DATASET = Path(__file__).resolve().parents[1] / "src" / "eval" / "eval_dataset.json"


# --------------------------------------------------------------------------- #
# summarize_scores (legacy faithfulness-only gate) - unchanged behaviour
# --------------------------------------------------------------------------- #
def test_gate_passes_when_faithfulness_at_or_above_threshold():
    s = summarize_scores([0.9, 1.0], [0.5], [0.5], gate=0.9)
    assert s["passed"] is True
    assert s["faithfulness"] == 0.95


def test_gate_fails_below_threshold():
    s = summarize_scores([0.8, 0.7], [1.0], [1.0], gate=0.9)
    assert s["passed"] is False


def test_gate_uses_faithfulness_only_not_other_metrics():
    # High relevancy/precision must NOT rescue low faithfulness.
    s = summarize_scores([0.0], [1.0], [1.0], gate=0.9)
    assert s["passed"] is False


def test_empty_scores_are_zero_not_crash():
    s = summarize_scores([], [], [], gate=0.9)
    assert s == {
        "faithfulness": 0.0,
        "answer_relevancy": 0.0,
        "context_precision": 0.0,
        "unsupported_claim_rate": 0.0,
        "total_cases": 0,
        "gate": 0.9,
        "passed": False,
    }


def test_unsupported_claim_rate_counts_cases_not_claims():
    s = summarize_scores(
        [1.0, 1.0, 1.0], [1.0], [1.0], gate=0.9, unsupported_claim_counts=[0, 2, 1]
    )
    assert s["unsupported_claim_rate"] == 0.667
    assert s["total_cases"] == 3


# --------------------------------------------------------------------------- #
# evaluate_strict_gate (RAGAS-style multi-metric gate)
# --------------------------------------------------------------------------- #
def test_strict_gate_passes_when_all_metrics_clear_thresholds():
    summary = {"faithfulness": 0.92, "answer_relevancy": 0.75, "context_precision": 0.65}
    res = evaluate_strict_gate(summary)
    assert res["passed"] is True
    assert res["failures"] == []
    assert res["thresholds"]["faithfulness"] == DEFAULT_FAITHFULNESS_GATE


def test_strict_gate_fails_and_reports_each_failing_metric():
    # faithfulness ok, but relevancy and precision are below their thresholds.
    summary = {"faithfulness": 0.95, "answer_relevancy": 0.5, "context_precision": 0.4}
    res = evaluate_strict_gate(summary)
    assert res["passed"] is False
    failed = {f["metric"] for f in res["failures"]}
    assert failed == {"answer_relevancy", "context_precision"}


def test_strict_gate_high_relevancy_cannot_rescue_low_faithfulness():
    summary = {"faithfulness": 0.1, "answer_relevancy": 1.0, "context_precision": 1.0}
    res = evaluate_strict_gate(summary)
    assert res["passed"] is False
    assert res["failures"][0]["metric"] == "faithfulness"


def test_strict_gate_respects_custom_thresholds():
    summary = {"faithfulness": 0.8, "answer_relevancy": 0.8, "context_precision": 0.8}
    # Lower every threshold -> passes.
    assert (
        evaluate_strict_gate(
            summary, faithfulness_gate=0.7, relevancy_gate=0.7, precision_gate=0.7
        )["passed"]
        is True
    )
    # Raise faithfulness threshold -> fails on faithfulness only.
    res = evaluate_strict_gate(summary, faithfulness_gate=0.9)
    assert res["passed"] is False
    assert [f["metric"] for f in res["failures"]] == ["faithfulness"]


# --------------------------------------------------------------------------- #
# summarize_by_agent (per-agent breakdown)
# --------------------------------------------------------------------------- #
def _row(agent, faith, rel=1.0, prec=1.0, unsupported=0):
    return {
        "agent": agent,
        "faithfulness": faith,
        "answer_relevancy": rel,
        "context_precision": prec,
        "unsupported_claims": unsupported,
    }


def test_per_agent_groups_and_averages_by_agent():
    rows = [_row("ncert", 1.0), _row("ncert", 0.8), _row("lecture", 0.6)]
    out = summarize_by_agent(rows, faithfulness_gate=0.9)
    assert set(out) == {"ncert", "lecture"}
    assert out["ncert"]["total_cases"] == 2
    assert out["ncert"]["faithfulness"] == 0.9


def test_per_agent_flags_the_specific_failing_agent():
    # ncert healthy, lecture regressed on faithfulness.
    rows = [_row("ncert", 0.95), _row("lecture", 0.4)]
    out = summarize_by_agent(rows, faithfulness_gate=0.9)
    assert out["ncert"]["passed"] is True
    assert out["lecture"]["passed"] is False


def test_per_agent_strict_mode_gates_all_metrics():
    rows = [_row("ncert", 0.95, rel=0.3, prec=0.9)]
    out = summarize_by_agent(rows, faithfulness_gate=0.9, relevancy_gate=0.7, precision_gate=0.6)
    assert out["ncert"]["passed"] is False
    assert out["ncert"]["failures"][0]["metric"] == "answer_relevancy"


def test_per_agent_defaults_missing_agent_to_rag():
    rows = [{"faithfulness": 0.95, "answer_relevancy": 1.0, "context_precision": 1.0}]
    out = summarize_by_agent(rows, faithfulness_gate=0.9)
    assert "rag" in out


# --------------------------------------------------------------------------- #
# Dataset integrity + report writer
# --------------------------------------------------------------------------- #
def test_eval_dataset_schema_is_valid():
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data, "dataset must be a non-empty list"
    for row in data:
        assert REQUIRED_KEYS <= set(row), f"row missing keys: {row}"
        assert all(isinstance(row[k], str) and row[k] for k in REQUIRED_KEYS)
        # 'agent', when present, must be a non-empty string.
        if "agent" in row:
            assert isinstance(row["agent"], str) and row["agent"]


def test_markdown_report_writer_with_per_agent_and_strict(tmp_path):
    path = tmp_path / "eval_report.md"
    summary = summarize_scores([0.9], [0.8], [0.7], gate=0.9, unsupported_claim_counts=[0])
    strict = evaluate_strict_gate(summary)
    per_agent = summarize_by_agent(
        [_row("ncert", 0.9, rel=0.8, prec=0.7)],
        faithfulness_gate=0.9,
        relevancy_gate=DEFAULT_RELEVANCY_GATE,
        precision_gate=DEFAULT_PRECISION_GATE,
    )
    write_markdown_report(
        summary,
        [
            {
                "question": "What is science?",
                "agent": "ncert",
                "faithfulness": 0.9,
                "answer_relevancy": 0.8,
                "context_precision": 0.7,
                "unsupported_claims": 0,
            }
        ],
        path,
        per_agent=per_agent,
        strict_gate=strict,
    )
    text = path.read_text(encoding="utf-8")
    assert "UPSC AI RAG Evaluation Report" in text
    assert "Per-agent results" in text
    assert "Strict multi-metric gate" in text
    assert "What is science?" in text


def test_markdown_report_writer_backward_compatible_minimal_call(tmp_path):
    # Older 3-arg call site must still work.
    path = tmp_path / "eval_report.md"
    summary = summarize_scores([0.9], [0.8], [0.7], gate=0.9)
    write_markdown_report(summary, [{"question": "Q", "faithfulness": 0.9}], path)
    assert "UPSC AI RAG Evaluation Report" in path.read_text(encoding="utf-8")
