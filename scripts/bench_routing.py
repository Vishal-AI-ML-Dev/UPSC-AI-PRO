"""Mentor routing benchmark for UPSC-AI-PRO (Phase 05, offline, in-process).

Runs multi-message Mentor conversations through the REAL compiled supervisor
graph (``src.graph.supervisor.build_supervisor``) on an ``InMemorySaver``
checkpointer, with the router LLM and every specialist replaced by offline
stubs. It answers three questions about per-message routing:

  A. correctness  - how many messages are answered by the specialist the router
                    chose for THAT message (misrouted = stale route reused).
  B. router load  - router LLM calls per conversation / per message, and the
                    router input size per call (system prompt + message text,
                    plus the structured-output schema), in characters and as an
                    ESTIMATED token count (chars / 4 heuristic, not a tokenizer).
  C. overhead     - offline graph overhead per message (stub router + stub
                    specialists, so this excludes all model latency).

Works on both the pre-fix code (route persisted in the checkpoint) and the
fixed code, so the same command gives the "before" and "after" numbers.

Live router latency and money cost need real provider keys and are NOT
measured here; ``docs/ENGINEERING_METRICS.md`` gives the cost formula.

Isolation (same as ``bench_jobs.py``): the environment is reduced to OS basics +
offline settings and the process ``chdir``s into a fresh temp dir before
importing ``src``, so no ``.env`` is read. No network, no keys.

Usage (repo root)::

    uv run python scripts/bench_routing.py
    uv run python scripts/bench_routing.py --conversations 50 --out <path outside the repo>.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import isolate_environment  # noqa: E402

# One realistic conversation: (router decision, message). Mixed intents on one
# thread, which is what a Mentor conversation looks like.
CONVERSATION = [
    ("planner", "Make me a UPSC 2027 study plan, 6 hours a day, 1st attempt"),
    ("mentor", "Explain the difference between Fundamental Rights and DPSP"),
    ("current_affairs", "Give me today's current affairs"),
    ("mentor", "How should I revise polity in the last month before prelims?"),
    (
        "evaluator",
        "Question: What is federalism? Answer: Division of powers between centre and states.",
    ),
    ("mentor", "What is the basic structure doctrine?"),
    ("current_affairs", "Monthly current affairs for March 2026"),
    ("mentor", "I feel low today, how do I stay consistent?"),
    ("planner", "Revise my plan for 4 hours a day"),
    ("mentor", "Summarise the 73rd amendment"),
]


def _boot(workdir: str) -> None:
    isolate_environment(workdir)
    os.environ["LOG_LEVEL"] = "WARNING"
    os.chdir(workdir)
    sys.path.insert(0, str(REPO_ROOT))


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def run(conversations: int) -> dict:
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from src.graph import supervisor

    def node(name):
        return lambda state: {"answer": name, "messages": [AIMessage(content=name)]}

    for attr, name in (
        ("build_mentor_graph", "mentor"),
        ("build_planner_subgraph", "planner"),
        ("build_evaluator_subgraph", "evaluator"),
        ("build_current_affairs_subgraph", "current_affairs"),
    ):
        setattr(supervisor, attr, lambda name=name: node(name))
    if hasattr(supervisor, "build_rag_subgraph"):  # pre-fix code only
        supervisor.build_rag_subgraph = lambda label="rag": node("rag")

    script: list[str] = []
    router_inputs: list[int] = []

    class _Router:
        def invoke(self, messages, *a, **k):
            router_inputs.append(sum(len(str(content)) for _role, content in messages))
            return supervisor.RouteDecision(route=script.pop(0))

    supervisor._structured_llm = lambda schema: _Router()
    graph = supervisor.build_supervisor(checkpointer=InMemorySaver())

    schema_chars = len(json.dumps(supervisor.RouteDecision.model_json_schema()))
    route_labels = list(supervisor.RouteDecision.model_fields["route"].annotation.__args__)
    per_message_ms: list[float] = []
    calls_per_conversation: list[int] = []
    misrouted = 0
    messages = 0
    for c in range(conversations):
        cfg = {"configurable": {"thread_id": f"bench-{c}"}}
        before = len(router_inputs)
        for expected, text in CONVERSATION:
            script[:] = [expected]
            t0 = time.perf_counter()
            out = graph.invoke({"question": text, "student_context": {"stage": "prelims"}}, cfg)
            per_message_ms.append((time.perf_counter() - t0) * 1000)
            messages += 1
            if out.get("answer") != expected:
                misrouted += 1
        calls_per_conversation.append(len(router_inputs) - before)

    system_chars = len(supervisor._SUP_SYS)
    mean_input = statistics.mean(router_inputs) if router_inputs else 0.0
    return {
        "conversations": conversations,
        "messages_per_conversation": len(CONVERSATION),
        "messages": messages,
        "misrouted_messages": misrouted,
        "misrouted_pct": round(100 * misrouted / messages, 1),
        "router_calls_total": len(router_inputs),
        "router_calls_per_conversation": statistics.mean(calls_per_conversation),
        "router_calls_per_message": round(len(router_inputs) / messages, 3),
        "route_labels": route_labels,
        "router_system_prompt_chars": system_chars,
        "router_schema_chars": schema_chars,
        "router_input_chars_mean": round(mean_input, 1),
        "router_input_tokens_est_mean": round((mean_input + schema_chars) / 4, 1),
        "graph_overhead_ms_p50": round(statistics.median(per_message_ms), 3),
        "graph_overhead_ms_p95": round(_pct(per_message_ms, 0.95), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conversations", type=int, default=20)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    out_path = Path(args.out).resolve() if args.out else None
    # ignore_cleanup_errors: on Windows the settings' SQLite file can still be
    # open when the temp dir is removed.
    with tempfile.TemporaryDirectory(
        prefix="bench_routing_", ignore_cleanup_errors=True
    ) as workdir:
        _boot(workdir)
        try:
            result = run(args.conversations)
        finally:
            os.chdir(REPO_ROOT)

    print(json.dumps(result, indent=2))
    ok = result["misrouted_messages"] == 0 and result["router_calls_per_message"] == 1
    print(f"ALL PASS: {ok}")
    if out_path:
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
