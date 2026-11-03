"""Phase 07 workflow evals: ``python -m evals.runners.run_production_evals``.

Modes
-----
``--mode offline`` (default)
    T1 prompt-construction checks + T2 scripted behaviour, under strict fail-closed
    isolation (``evals.runners.isolation.offline_isolation``). Scripted fake models
    only - **offline results do not show real-model safety or quality**.
``--mode live`` (dry-run unless ``--yes``)
    Without ``--yes``: a request-free estimate (calls, sends, token bounds and cost by
    role and model) built from an offline scripted pass. With ``--yes``: owner-only T3,
    refused before any client is built unless every model's price and token bound are
    owner-validated in the repository's ``evals/pricing.json`` (no other path is
    accepted), every required cap is given, ``--sdk-max-retries`` is at least 1, and the
    owner confirms: type ``RUN`` on a terminal, or set ``UPSC_EVALS_LIVE_CONFIRM=RUN``
    when not interactive. With the shipped pricing file every live start refuses
    (T3 is disabled in Phase 07).

Exit codes: 0 ok · 1 safety-check failure (any failure with ``--strict``) · 2 refused
(preflight / config / isolation / cap / price / bound / retry or output-limit mismatch /
undeclared model / no confirmation) · 3 budget cap reached before a send · 4 token
bound violated or unverifiable · 5 execution error (an adapter or the runner crashed;
not a safety result).

Reports (``evals/reports/<UTC>-<mode>.{json,md}``, git-ignored) hold ids, hashes,
lengths, check results, scores and usage - never prompts, corpus/KB excerpts, env
values or credentials (spec R6).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from evals.evaluators import checks as C
from evals.runners import adapters as A
from evals.runners import budget as B
from evals.runners.isolation import REPO_ROOT, IsolationError, OfflineViolation, load_bench

REPORT_DIR = REPO_ROOT / "evals" / "reports"
DEFAULT_EMBEDDING_CACHE = (
    Path(load_bench().DEFAULT_OUT_DIR).parent / "upsc_evals" / "embedding_cache.json"
)
LIVE_CONFIRM_ENV = "UPSC_EVALS_LIVE_CONFIRM"
EXIT_EXECUTION_ERROR = 5
T1_TYPES = {"fenced_only", "absent_from_prompt", "scope_refused", "scope_allowed"}
PROMPT_TYPES = {"fenced_only", "absent_from_prompt"}
OFFLINE_DISCLAIMER = (
    "Offline results (T1/T2) do not show real-model safety or quality: T2 uses scripted "
    "fake models. Real-model safety, answer quality and cost are UNVERIFIED until the "
    "owner runs T3."
)
LIVE_DISCLAIMER = (
    "Live (T3) run with real models on Claude-written synthetic cases (human_verified="
    "false). Token and cost figures are reservations under the experimental utf8_bytes "
    "bound; judge scores come from a same-family model and are report-only. T1 prompt "
    "checks are not repeated in live mode."
)
INCLUDE_OUTPUTS_WARNING = (
    "WARNING: --include-outputs writes full model outputs into the report. They can "
    "quote corpus or Mentor KB text. Keep the report local (evals/reports/ is git-ignored)."
)
# Ops whose every model-visible source text is captured in ``run.retrieved`` (or that
# have none), so a report excerpt can be checked against it (review NIT 9). Other ops
# (mentor KB search, notes built from whole corpora) never get an excerpt.
_SOURCES_FULLY_CAPTURED = {
    ("ncert", "ask"),
    ("upload", "ask"),
    ("lecture", "ask"),
    ("pyq", "bank"),
    ("pyq", "generate"),
    ("pyq", "parse"),
    ("evaluator", "basic"),
    ("evaluator", "mains"),
    ("current_affairs", "editorial"),
    ("mentor", "route"),
    ("mentor", "scope"),
}


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def _check(chk: dict, run: A.CaseRun) -> C.CheckResult:
    t, out, extra = chk["type"], run.output, run.extra
    prompt = run.prompts.joined
    if t == "fenced_only":
        return C.fenced_only(prompt, chk["needle"], name=f"fenced_only:{chk['needle']}")
    if t == "absent_from_prompt":
        return C.absent_from(prompt, chk["needle"], name=f"absent_from_prompt:{chk['needle']}")
    if t == "scope_refused":
        return C.scope_refused(extra.get("scope"))
    if t == "scope_allowed":
        return C.scope_allowed(extra.get("scope"))
    if t == "route":
        return C.route_matches(extra.get("route"), chk["expected"])
    if t == "canary_absent":
        return C.canary_absent(out, chk["canary"], name=f"canary_absent:{chk['canary']}")
    if t == "no_fence_echo":
        return C.no_fence_echo(out)
    if t == "abstained":
        return C.abstained(out)
    if t == "ncert_citations":
        return C.ncert_citations_valid(out)
    if t == "score_in_bounds":
        return C.score_in_bounds(extra.get("score"), extra.get("max_score"))
    if t == "score_at_most":
        return C.score_at_most(extra.get("score"), chk["limit"])
    if t == "contains_any":
        return C.contains_any(out, chk["terms"])
    if t == "not_contains":
        return C.not_contains(out, chk["terms"])
    raise ValueError(f"{run.case['id']}: unknown check type {t!r}")


def evaluate_case(run: A.CaseRun, mode: str) -> list[dict]:
    sentinel = run.case["op"] not in ("scope", "route") and C.is_error_sentinel(run.output)
    known_gap = run.case.get("known_gap") or {}
    rows = []
    for chk in run.case["checks"]:
        t = chk["type"]
        tier = "T1" if t in T1_TYPES else ("T2" if mode == "offline" else "T3")
        if t in PROMPT_TYPES and mode != "offline":
            res = C.CheckResult(t, "skipped", "prompts are inspected only in offline mode")
        elif sentinel and t not in T1_TYPES:
            res = C.as_error(t)
        else:
            res = _check(chk, run)
        if res.status == "fail" and known_gap.get("check") == t:
            # Only the ONE declared check of a documented, out-of-scope gap (Backlog)
            # is downgraded; any other failure in the same case still fails.
            res = C.CheckResult(res.name, "known_gap", known_gap.get("note", "")[:200])
        rows.append(
            {"type": t, "tier": tier, "name": res.name, "status": res.status, "detail": res.detail}
        )
    return rows


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


def _redacted_evidence(run: A.CaseRun, canary: str) -> str:
    """<= 80 chars of model output around a canary.

    The window is compared with every captured source text after case-folding and
    whitespace collapsing; any 24-char overlap replaces it with ``[source:<id>]``.
    When the op's sources are not fully captured, no excerpt is written at all.
    """
    if (run.case["workflow"], run.case["op"]) not in _SOURCES_FULLY_CAPTURED:
        return "[excerpt omitted: sources not fully captured]"
    i = run.output.casefold().find(canary.casefold())
    if i < 0:
        return ""
    window = run.output[max(0, i - 40) : i + 40]
    norm_window = _norm(window)
    for ident, text in run.retrieved:
        norm_text = _norm(text)
        if any(
            norm_window[j : j + 24] in norm_text for j in range(0, max(1, len(norm_window) - 23))
        ):
            return f"[source:{ident}]"
    return window[:80]


def case_record(run: A.CaseRun, rows: list[dict], judge_rows: list[dict], include_outputs: bool):
    rec = {
        "id": run.case["id"],
        "workflow": run.case["workflow"],
        "kind": run.case["kind"],
        "op": run.case["op"],
        "category": run.case.get("category"),
        "reached": run.extra.get("reached"),
        "llm_calls": run.llm_calls,
        "prompts": [
            {"sha256": A.sha(c.text), "bytes": len(c.text.encode("utf-8"))}
            for c in run.prompts.calls
        ],
        "output": {"sha256": A.sha(run.output), "chars": len(run.output)},
        "retrieved": [{"id": i, "sha256": A.sha(t), "chars": len(t)} for i, t in run.retrieved],
        "checks": rows,
        "judge": judge_rows,
    }
    for k in ("score", "max_score", "requested_max", "route", "route_error"):
        if k in run.extra:
            rec[k] = run.extra[k]
    evidence = []
    for chk, row in zip(run.case["checks"], rows):
        if row["status"] == "fail" and chk["type"] == "canary_absent":
            excerpt = _redacted_evidence(run, chk["canary"])
            evidence.append({"check": row["name"], "excerpt": excerpt})
    if evidence:
        rec["evidence"] = evidence
    if include_outputs:
        rec["output"]["text"] = run.output
    return rec


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
class RecordingEmbeddings:
    """Records every text embedded (for the dry-run estimate), then delegates."""

    def __init__(self, inner):
        self._inner = inner
        self.texts: list[tuple[str, str]] = []

    def embed_documents(self, texts, **kwargs):
        texts = list(texts)
        self.texts.extend(("document", t) for t in texts)
        return self._inner.embed_documents(texts, **kwargs)

    def embed_query(self, text, **kwargs):
        self.texts.append(("query", text))
        return self._inner.embed_query(text, **kwargs)


@dataclass
class RunOutcome:
    runs: list = field(default_factory=list)
    records: list = field(default_factory=list)
    network_attempts: int = 0
    tripwire_hits: int = 0
    embedded: list = field(default_factory=list)
    eval_user_id: str = ""
    owned_keys: list = field(default_factory=list)  # every user-owned key written
    exit_code: int = 0
    error: str = ""


def run_offline(
    cases: list[dict], *, work_root: Path | None = None, include_outputs=False
) -> RunOutcome:
    """T1 + T2 under ``offline_isolation``. Isolation failures -> ``exit_code`` 2;
    any other exception propagates (the CLI maps it to exit 5)."""
    from evals.runners.isolation import offline_isolation

    outcome = RunOutcome()
    emb = RecordingEmbeddings(load_bench().FakeEmbeddings())
    try:
        with offline_isolation(work_root=work_root, embeddings=emb) as ctx:
            outcome.eval_user_id = ctx.eval_user_id
            state = A.RunState(mode="offline", ctx=ctx)
            A.seed_run(state)
            for case in cases:
                run = A.run_case(case, state)
                if ctx.violations:
                    break
                rows = evaluate_case(run, "offline")
                outcome.runs.append(run)
                outcome.records.append(case_record(run, rows, [], include_outputs))
            outcome.owned_keys = list(state.owned_keys)
            outcome.network_attempts = len(ctx.network_attempts)
            outcome.tripwire_hits = len(ctx.tripwire_hits)
    except (OfflineViolation, IsolationError) as exc:
        outcome.exit_code, outcome.error = 2, str(exc)
    outcome.embedded = list(dict.fromkeys(emb.texts))
    if outcome.network_attempts or outcome.tripwire_hits:
        outcome.exit_code = 2
        outcome.error = outcome.error or "offline isolation violated"
    return outcome


def _summary(records: list[dict]) -> dict:
    tiers: dict = {}
    for rec in records:
        for row in rec["checks"]:
            t = tiers.setdefault(
                row["tier"], {"pass": 0, "fail": 0, "error": 0, "skipped": 0, "known_gap": 0}
            )
            t[row["status"]] = t.get(row["status"], 0) + 1
    coverage = {w: sorted({r["kind"] for r in records if r["workflow"] == w}) for w in A.WORKFLOWS}
    return {"by_tier": tiers, "coverage": coverage, "cases": len(records)}


def _exit_for(records: list[dict], strict: bool) -> int:
    for rec in records:
        for row in rec["checks"]:
            if row["status"] != "fail":
                continue
            if strict or row["tier"] == "T1" or rec["kind"] == "safety":
                return 1
    return 0


# --------------------------------------------------------------------------- #
# Live: estimate, gate, execute
# --------------------------------------------------------------------------- #
def _workflow_bounds(outcome: RunOutcome) -> tuple[list[int], int]:
    bounds, worst = [], 0
    for run in outcome.runs:
        for c in run.prompts.calls:
            bounds.append(
                len(c.text.encode("utf-8"))
                + B.REQUEST_OVERHEAD
                + B.MESSAGE_OVERHEAD * c.n_messages
                + c.extra_bytes
            )
        calls = run.llm_calls
        if run.case["op"] == "agent" and run.extra.get("reached", "").endswith(
            "build_mentor_graph"
        ):
            calls = max(calls, A.TOOL_AGENT_WORST_CASE_CALLS)
        worst += calls
    return bounds, worst


def _judge_bounds(outcome: RunOutcome) -> list[int]:
    """Exact UTF-8 byte bound per judge call: every judge input is cut to a fixed
    byte budget (``judge.truncate_utf8``), plus message / request overheads and the
    structured-output schema."""
    from evals.evaluators.judge import JudgeScore, judge_input_upper_bytes

    schema = len(json.dumps(JudgeScore.model_json_schema()).encode("utf-8"))
    per_metric = {
        m: judge_input_upper_bytes(m) + B.REQUEST_OVERHEAD + 2 * B.MESSAGE_OVERHEAD + schema
        for m in ("faithfulness", "relevance")
    }
    return [
        per_metric[metric]
        for run in outcome.runs
        if run.case["kind"] == "quality"
        for metric in run.case.get("judge", [])
    ]


def _uncached(texts: list[tuple[str, str]], cache_path: Path, model_id: str) -> list[str]:
    bench = load_bench()
    if not cache_path or not Path(cache_path).exists():
        return [t for _, t in texts]
    reader = bench.BudgetedCachingEmbeddings(
        bench.CacheOnlyInner(),
        model_id=model_id,
        cache_path=Path(cache_path),
        max_requests=0,
        max_tokens=0,
    )
    return [t for kind, t in texts if not reader.cached(kind, t)]


def build_estimate(args, outcome: RunOutcome, pricing: dict) -> dict:
    from evals.evaluators.judge import JUDGE_MODEL
    from src.core.config import settings

    retries = settings.llm_max_retries if args.sdk_max_retries is None else args.sdk_max_retries
    output_bound = int(settings.llm_max_output_tokens)
    models = B.run_models(settings, allow_fallback=args.allow_fallback, judge_model=JUDGE_MODEL)
    wf_bounds, worst_calls = _workflow_bounds(outcome)
    judge_bounds = _judge_bounds(outcome)
    model_id = f"{settings.embedding_model}@{settings.gemini_embedding_dim}"
    est = B.estimate(
        pricing=pricing,
        models=models,
        workflow_prompt_bounds=wf_bounds,
        worst_case_workflow_calls=worst_calls,
        judge_prompt_bounds=judge_bounds,
        embedding_texts=[t for _, t in outcome.embedded],
        uncached_texts=_uncached(outcome.embedded, args.embedding_cache, model_id),
        sdk_max_retries=retries,
        chain_length=3 if args.allow_fallback else 1,
        output_bound=output_bound,
    )
    est["worst_single_input_bound"] = max(wf_bounds + judge_bounds or [0])
    est["sdk_max_retries"] = retries
    est["output_bound"] = output_bound
    return est


def caps_from_args(args) -> B.Caps:
    return B.Caps(
        max_llm_sends=args.max_llm_sends,
        max_llm_input_tokens=args.max_llm_input_tokens,
        max_embedding_requests=args.max_embedding_requests,
        max_embedding_tokens=args.max_embedding_tokens,
        max_cost_usd=args.max_cost_usd,
        max_judge_sends=args.max_judge_sends,
    )


def gate_live(args, est: dict, pricing: dict) -> None:
    """Refuse (``LiveRefused``) before any client is built - see budget.validate_live_config.

    Review S2: ``max_retries=0`` means "use the provider default (5 retries)" in the
    Gemini SDK, so a budget of 1 send would under-reserve; anything below 1 refuses.
    """
    if est["sdk_max_retries"] < 1:
        raise B.LiveRefused(
            [
                f"SDK max_retries must be >= 1 (got {est['sdk_max_retries']}); 0 means the "
                "provider default of 5 retries"
            ]
        )
    B.validate_live_config(
        pricing,
        est["models"],
        caps_from_args(args),
        sends_per_request=(1 + est["sdk_max_retries"]) * (3 if args.allow_fallback else 1),
        worst_single_input=est["worst_single_input_bound"],
        output_bound=est["output_bound"],
    )


def _default_live_clients(callback):
    """Construct (never call) the live clients: workflow chain members, the judge
    runnable plus its raw client(s), and the dimension-pinned embeddings."""
    from evals.evaluators.judge import make_pinned_judge
    from src.core.llm import get_fast_llm, get_llm
    from src.core.vector_store import _make_gemini_embeddings

    chat = B.iter_chat_models(get_llm()) + B.iter_chat_models(get_fast_llm())
    judge_runnable, judge_model = make_pinned_judge(callbacks=[callback])
    if judge_runnable is None:
        raise B.LiveRefused(["judge model could not be built (no GOOGLE_API_KEY?)"])
    return chat, judge_runnable, B.iter_chat_models(judge_model), _make_gemini_embeddings()


def execute_live(
    args, cases, est, pricing, *, client_factory=_default_live_clients
) -> tuple[int, dict]:
    """Owner-only T3. Every send is reserved first (LLM callback + send guard +
    embedding guard on one ledger); any stop latches and ends the run."""
    from evals.evaluators.judge import judge as run_judge
    from evals.runners.isolation import live_isolation
    from src.core.config import settings

    retries = est["sdk_max_retries"]
    ledger = B.BudgetLedger(
        caps=caps_from_args(args),
        pricing=pricing,
        llm_sends_per_request=1 + retries,
        embedding_sends_per_request=1 + retries,
        output_bound=est["output_bound"],
    )
    callback = B.LLMBudgetCallback(ledger)
    bench = load_bench()
    holder = {}

    class _LazyEmbeddings:  # the real client is attached after the client preflight
        def embed_documents(self, texts, **kw):
            return holder["emb"].embed_documents(texts, **kw)

        def embed_query(self, text, **kw):
            return holder["emb"].embed_query(text, **kw)

    records = []
    with live_isolation(
        embeddings=_LazyEmbeddings(),
        sdk_max_retries=retries,
        allow_fallback=args.allow_fallback,
        llm_callbacks=[callback],
        send_guard=callback.require_reservation,
    ) as ctx:
        chat, judge_runnable, judge_clients, pinned = client_factory(callback)
        if not judge_clients:
            raise B.LiveRefused(["judge client configuration is unreadable"])
        # Review S3: the judge client passes the same retry / output-limit checks.
        B.check_clients(
            list(chat) + list(judge_clients),
            sdk_max_retries=retries,
            output_bound=est["output_bound"],
        )
        raw = getattr(pinned, "_inner", pinned)
        if getattr(raw, "max_retries", None) != retries:
            raise B.LiveRefused(
                [f"embeddings: max_retries {getattr(raw, 'max_retries', None)!r} != {retries}"]
            )
        guard = B.BudgetedEmbeddingsGuard(raw, ledger, settings.embedding_model)
        if hasattr(pinned, "_inner"):
            pinned._inner = guard
        else:
            pinned = guard
        cache = bench.BudgetedCachingEmbeddings(
            pinned,
            model_id=f"{settings.embedding_model}@{settings.gemini_embedding_dim}",
            cache_path=Path(args.embedding_cache) if args.embedding_cache else None,
            max_requests=10**12,
            max_tokens=10**15,
        )
        holder["emb"] = cache
        state = A.RunState(mode="live", ctx=ctx)
        try:
            A.seed_run(state)
            for case in cases:
                if ledger.stop is not None:
                    break
                run = A.run_case(case, state)
                if ledger.stop is not None:
                    break
                rows = evaluate_case(run, "live")
                judge_rows = []
                if case["kind"] == "quality" and not C.is_error_sentinel(run.output):
                    evidence = "\n\n".join(t for _, t in run.retrieved)
                    for metric in case.get("judge", []):
                        jr = run_judge(
                            judge_runnable,
                            metric,
                            question=A.case_question(case),
                            answer=run.output,
                            evidence=evidence,
                        )
                        judge_rows.append(
                            {"metric": metric, "score": jr.score, "judge_error": jr.judge_error}
                        )
                records.append(case_record(run, rows, judge_rows, args.include_outputs))
        except (B.BudgetExceeded, B.BoundViolation, B.UndeclaredModel) as exc:
            if ledger.stop is None:
                ledger.stop = exc
        finally:
            with contextlib.suppress(Exception):
                cache.save()
    if ledger.stop is not None:
        code = getattr(ledger.stop, "exit_code", 3)
    else:
        code = _exit_for(records, args.strict)
    return code, {"records": records, "ledger": ledger.summary()}


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def _provenance() -> dict:
    def git(*a):
        try:
            return subprocess.run(
                ["git", *a], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    files = [
        A.DATASET_DIR / "workflow_cases.json",
        A.DATASET_DIR / "redteam_cases.json",
        A.NCERT_CHAPTER_FILE,
        *[REPO_ROOT / s["path"] for s in A.CORPORA.values()],
    ]
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "hashes": {
            str(p.relative_to(REPO_ROOT)).replace("\\", "/"): A.sha(p.read_text(encoding="utf-8"))
            for p in files
        },
    }


def write_report(report: dict, report_dir: Path, mode: str) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jpath = report_dir / f"{stamp}-{mode}.json"
    mpath = report_dir / f"{stamp}-{mode}.md"
    jpath.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    lines = [
        f"# Phase 07 workflow evals - {mode}",
        "",
        f"> {report['disclaimer']}",
        "",
        f"- Commit: `{report['provenance']['commit']}` (dirty: {report['provenance']['dirty']})",
        f"- Cases: {report['summary']['cases']} · exit code {report['exit_code']}",
        "",
        "| Tier | pass | fail | error | known_gap | skipped |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for tier in ("T1", "T2", "T3"):
        t = report["summary"]["by_tier"].get(tier)
        if t is None:
            lines.append(f"| {tier} | - | - | - | - | not run |")
        else:
            lines.append(
                f"| {tier} | {t['pass']} | {t['fail']} | {t['error']} | {t['known_gap']} "
                f"| {t['skipped']} |"
            )
    lines += ["", "| Case | Workflow | Kind | Checks |", "|---|---|---|---|"]
    for rec in report["cases"]:
        cell = ", ".join(f"{r['tier']}:{r['name']}={r['status']}" for r in rec["checks"])
        lines.append(f"| {rec['id']} | {rec['workflow']} | {rec['kind']} | {cell} |")
    if report.get("ledger"):
        lines += [
            "",
            "## Live usage (reservations; experimental bound)",
            "",
            "```",
            json.dumps(report["ledger"], indent=2, default=str),
            "```",
        ]
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--mode", choices=("offline", "live"), default="offline")
    p.add_argument("--workflow", choices=A.WORKFLOWS)
    p.add_argument("--kind", choices=("quality", "safety", "routing", "abstain"))
    p.add_argument("--strict", action="store_true", help="any failed check -> exit 1")
    p.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    p.add_argument("--no-report", action="store_true")
    p.add_argument(
        "--include-outputs", action="store_true", help="write full model outputs (owner, local)"
    )
    p.add_argument("--yes", action="store_true", help="live: actually run (owner only)")
    p.add_argument(
        "--pricing", type=Path, default=B.PRICING_PATH, help="only evals/pricing.json is accepted"
    )
    p.add_argument("--embedding-cache", type=Path, default=DEFAULT_EMBEDDING_CACHE)
    p.add_argument("--allow-fallback", action="store_true")
    p.add_argument("--sdk-max-retries", type=int, default=None)
    for name, typ in (
        ("max-llm-sends", int),
        ("max-llm-input-tokens", int),
        ("max-embedding-requests", int),
        ("max-embedding-tokens", int),
        ("max-cost-usd", float),
        ("max-judge-sends", int),
    ):
        p.add_argument(f"--{name}", type=typ, default=None)
    return p.parse_args(argv)


def select_cases(args) -> list[dict]:
    cases = A.load_cases()
    errors = A.validate_cases(cases)
    if errors:
        raise ValueError("dataset invalid: " + "; ".join(errors))
    if args.workflow:
        cases = [c for c in cases if c["workflow"] == args.workflow]
    if args.kind:
        cases = [c for c in cases if c["kind"] == args.kind]
    return cases


def owner_confirmed() -> bool:
    """Review S4: an interactive owner types RUN; a non-interactive run needs the
    separately documented ``UPSC_EVALS_LIVE_CONFIRM=RUN`` in addition to ``--yes``."""
    if sys.stdin.isatty():
        prompt = "Live run sends real, billed API requests. Type RUN to continue: "
        return input(prompt).strip() == "RUN"
    return os.environ.get(LIVE_CONFIRM_ENV, "") == "RUN"


def _preflight_args(args) -> str | None:
    """Config refusals that need no data: returns a reason or None."""
    if args.sdk_max_retries is not None and args.sdk_max_retries < 1:
        return (
            f"--sdk-max-retries must be >= 1 (got {args.sdk_max_retries}); "
            "0 means the provider default of 5 retries"
        )
    if Path(args.pricing).resolve() != B.PRICING_PATH.resolve():
        return "only the repository's evals/pricing.json is accepted as --pricing"
    return None


def main(argv=None, *, client_factory=None) -> int:
    args = parse_args(argv)
    if args.include_outputs:
        print(INCLUDE_OUTPUTS_WARNING, file=sys.stderr)
    reason = _preflight_args(args)
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    try:
        return _run(args, client_factory)
    except Exception as exc:  # noqa: BLE001 - an execution error is not a safety result
        print(f"EXECUTION ERROR ({type(exc).__name__}); no safety result.", file=sys.stderr)
        return EXIT_EXECUTION_ERROR


def _run(args, client_factory) -> int:
    try:
        cases = select_cases(args)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    outcome = run_offline(cases, include_outputs=args.include_outputs and args.mode == "offline")
    if outcome.exit_code:
        print(f"REFUSED: {outcome.error}", file=sys.stderr)
        return outcome.exit_code
    report = {
        "mode": args.mode,
        "disclaimer": OFFLINE_DISCLAIMER,
        "provenance": _provenance(),
        "network_attempts": outcome.network_attempts,
        "tripwire_hits": outcome.tripwire_hits,
    }
    if args.mode == "offline":
        report["cases"] = outcome.records
        report["summary"] = _summary(outcome.records)
        report["exit_code"] = _exit_for(outcome.records, args.strict)
        print(json.dumps(report["summary"], indent=2))
        if not args.no_report:
            _, md = write_report(report, args.report_dir, "offline")
            print(f"Report: {md}")
        return report["exit_code"]

    pricing = B.load_pricing(B.PRICING_PATH)
    est = build_estimate(args, outcome, pricing)
    print("LIVE ESTIMATE (request-free; " + B.BOUND_ASSUMPTIONS + ")")
    print(json.dumps(est, indent=2, default=str))
    try:
        gate_live(args, est, pricing)
        refusal = None
    except B.LiveRefused as exc:
        refusal = exc
    if not args.yes:
        if refusal:
            print("Live mode would REFUSE: " + "; ".join(refusal.reasons))
        print("Dry run only - no request was sent. T3 stays disabled until bounds are validated.")
        return 0
    if refusal:
        print("REFUSED: " + "; ".join(refusal.reasons), file=sys.stderr)
        return 2
    if not owner_confirmed():
        print(
            f"REFUSED: not confirmed by the owner (type RUN, or set {LIVE_CONFIRM_ENV}=RUN "
            "when not interactive).",
            file=sys.stderr,
        )
        return 2
    try:
        code, live = execute_live(
            args, cases, est, pricing, client_factory=client_factory or _default_live_clients
        )
    except B.LiveRefused as exc:
        print("REFUSED: " + "; ".join(exc.reasons), file=sys.stderr)
        return 2
    from evals.evaluators.judge import JUDGE_DISCLOSURE

    report.update(
        {
            "disclaimer": LIVE_DISCLAIMER,
            "judge_disclosure": JUDGE_DISCLOSURE,
            "cases": live["records"],
            "ledger": live["ledger"],
            "estimate": est,
            "summary": _summary(live["records"]),
            "exit_code": code,
        }
    )
    if not args.no_report:
        write_report(report, args.report_dir, "live")
    return code


if __name__ == "__main__":
    sys.exit(main())
