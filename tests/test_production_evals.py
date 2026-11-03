"""Phase 07 workflow-eval harness (evals/) - fully offline.

Covers: dataset schema and coverage (AC1/AC2), the full offline run through every
production function with 0 network attempts and 0 real clients (AC1/AC3/AC5),
fail-closed isolation and state restoration (AC5), the live budget / gate with zero
requests (AC7) and report data minimisation (AC7 / R6).

Offline results use scripted fake models: they prove prompt construction and
plumbing, NOT real-model safety or quality.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import src.core.llm as llm_mod
from evals.runners import adapters as A
from evals.runners import budget as B
from evals.runners import isolation as I
from evals.runners import run_production_evals as R
from src.core.config import settings

REPO = Path(__file__).resolve().parent.parent
ALL_CASES = A.load_cases()
_ORIG = {
    "connect": socket.socket.connect,
    "getaddrinfo": socket.getaddrinfo,
    "make_gemini": llm_mod._make_gemini,
    "make_groq": llm_mod._make_groq,
    "qdrant_path": settings.qdrant_path,
}


def _case(case_id):
    return next(c for c in ALL_CASES if c["id"] == case_id)


@contextlib.contextmanager
def armed_guards():
    """The R4a network guard + client tripwires, armed around code under test."""
    ctx = I.IsolationContext("test", REPO, REPO, "eval-test")
    with contextlib.ExitStack() as stack:
        I._install_network_guard(stack, ctx)
        I._install_tripwires(stack, ctx)
        yield ctx


# --------------------------------------------------------------------------- #
# Dataset + coverage
# --------------------------------------------------------------------------- #
def test_datasets_are_valid_and_marked_unverified():
    assert A.validate_cases(ALL_CASES) == []
    assert all(c["author"] == "claude" and c["human_verified"] is False for c in ALL_CASES)


def test_every_workflow_has_three_quality_cases():
    for wf in A.WORKFLOWS:
        n = sum(1 for c in ALL_CASES if c["workflow"] == wf and c["kind"] == "quality")
        assert n >= 3, wf


def test_red_team_categories_are_covered():
    cats = {(c["workflow"], c.get("category")) for c in ALL_CASES if c["kind"] == "safety"}
    for wf in ("upload", "lecture", "pyq", "mentor"):
        assert (wf, "indirect_injection") in cats, wf
    for wf in ("upload", "lecture", "mentor"):
        assert (wf, "metadata_injection") in cats, wf
    assert ("evaluator", "score_manipulation") in cats
    assert ("mentor", "system_prompt_extraction") in cats
    assert ("mentor", "jailbreak") in cats
    assert any(cat == "fence_spoofing" for _, cat in cats)
    abstain = {c["workflow"] for c in ALL_CASES if c["kind"] == "abstain"}
    assert {"ncert", "upload", "lecture", "pyq"} <= abstain


def test_every_inventory_metadata_field_has_a_fenced_check():
    needles = {
        chk["needle"] for c in ALL_CASES for chk in c["checks"] if chk["type"] == "fenced_only"
    }
    # upload chat + notes (book_name/subject/paper), lecture chat (topic/paper),
    # lecture notes (topic/paper/syllabus/medium), mentor profile
    for n in (
        "ZQX-BOOK-11",
        "ZQX-SUBJ-12",
        "ZQX-PAPER-13",
        "ZQX-BOOK-21",
        "ZQX-SUBJ-22",
        "ZQX-PAPER-23",
        "ZQX-TOPIC-31",
        "ZQX-PAPER-32",
        "ZQX-TOPIC-41",
        "ZQX-PAPER-42",
        "ZQX-SYL-43",
        "ZQX-PROF-51",
    ):
        assert n in needles, n
    nested = [
        c for c in ALL_CASES if c["id"] in ("rt-upload-nested-marker", "rt-upload-unicode-marker")
    ]
    assert len(nested) == 2  # review B1 / S5 attacks are in the red-team set
    absent = {
        chk["needle"]
        for c in ALL_CASES
        for chk in c["checks"]
        if chk["type"] == "absent_from_prompt"
    }
    assert "ZQXCONF99" in absent  # book_info.confidence is control-only
    assert "ZQX-MEDIUM-44" in absent  # review S6: output language is allow-listed


def test_shipped_pricing_is_unvalidated_and_unpriced():
    pricing = B.load_pricing()
    for name, entry in pricing["models"].items():
        tb = entry["token_bound"]
        assert tb["method"] == "utf8_bytes"
        assert (
            tb["status"] == "unvalidated" and tb["validated_on"] is None and tb["evidence"] is None
        )
        assert entry["input_usd_per_million"] is None and entry["output_usd_per_million"] is None
        assert not B.bound_is_validated(entry), name
    assert (
        pricing["models"]["gemini-embedding-001"]["token_bound"]["provider_usage_check"]
        == "unavailable"
    )


# --------------------------------------------------------------------------- #
# Full offline run (T1 + T2) through every production function
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def offline_outcome():
    return R.run_offline(ALL_CASES)


def test_offline_run_reaches_every_production_function(offline_outcome):
    assert offline_outcome.exit_code == 0, offline_outcome.error
    reached = {(r["workflow"], r["reached"]) for r in offline_outcome.records}
    expected = {
        ("mentor", "src.graph.mentor_graph.build_mentor_graph"),
        ("mentor", "src.graph.supervisor._structured_llm"),
        ("mentor", "src.core.scope_gate.check_scope"),
        ("ncert", "src.agents.ncert.graph.ask_ncert"),
        ("upload", "src.agents.upload.graph.ask_upload"),
        ("upload", "src.agents.upload.graph.process_upload"),
        ("lecture", "src.agents.lecture.graph.ask_lecture"),
        ("lecture", "src.agents.lecture.graph.process_lecture_from_text"),
        ("pyq", "src.agents.pyq.graph.generate_questions"),
        ("pyq", "src.agents.pyq.graph.generate_from_bank"),
        ("pyq", "src.agents.pyq.graph.parse_questions"),
        ("evaluator", "src.agents.evaluator.graph.evaluate_answer"),
        ("evaluator", "src.agents.evaluator.graph.evaluate_mains"),
        ("current_affairs", "src.agents.current_affairs.graph.get_daily_ca"),
        ("current_affairs", "src.agents.current_affairs.graph.get_editorial"),
        ("planner", "src.agents.planner.graph.generate_plan"),
    }
    assert expected <= reached


def test_offline_run_makes_zero_network_attempts_and_builds_no_real_client(offline_outcome):
    assert offline_outcome.network_attempts == 0
    assert offline_outcome.tripwire_hits == 0


def test_offline_t1_checks_all_pass_after_the_fixes(offline_outcome):
    rows = [
        (r["id"], row)
        for r in offline_outcome.records
        for row in r["checks"]
        if row["tier"] == "T1"
    ]
    failing = [(cid, row["name"], row["detail"]) for cid, row in rows if row["status"] == "fail"]
    assert failing == []
    known = [cid for cid, row in rows if row["status"] == "known_gap"]
    assert known == ["rt-scope-song"]  # documented Backlog gap, reported not hidden
    fenced = [row for _, row in rows if row["type"] == "fenced_only"]
    expected = sum(1 for c in ALL_CASES for chk in c["checks"] if chk["type"] == "fenced_only")
    assert len(fenced) == expected and all(row["status"] == "pass" for row in fenced)


def test_offline_t2_checks_pass_and_retrieval_is_captured(offline_outcome):
    t2 = [row for r in offline_outcome.records for row in r["checks"] if row["tier"] == "T2"]
    assert t2 and all(row["status"] == "pass" for row in t2)
    by_id = {r["id"]: r for r in offline_outcome.records}
    assert by_id["upload-q1"]["retrieved"], "retrieved chunk ids/hashes recorded"
    assert by_id["eval-mains-q2"]["score"] == 9.0 and by_id["eval-mains-q2"]["max_score"] == 15


def test_owned_keys_written_by_the_run_carry_its_eval_user_id(offline_outcome):
    # Review S7: inspect the keys the runner actually wrote, not a key built here.
    uid = offline_outcome.eval_user_id
    assert uid.startswith("eval-") and len(uid) > 30
    keys = offline_outcome.owned_keys
    owned_cases = [
        c
        for c in ALL_CASES
        if (c["workflow"], c["op"])
        in {("upload", "ask"), ("upload", "notes"), ("lecture", "ask"), ("pyq", "bank")}
    ]
    assert len(keys) == len(owned_cases)
    assert all(uid in key for key in keys), keys
    assert any(k.startswith("upload_") for k in keys) and any(
        k.startswith("pyqbank_") for k in keys
    )
    assert any(k.startswith("lecture_") for k in keys)


# --------------------------------------------------------------------------- #
# Fail-closed isolation (AC5)
# --------------------------------------------------------------------------- #
def test_constructing_a_real_client_fails_the_run(monkeypatch):
    def _op(case, state):
        try:  # production code swallows errors - the count must still catch it
            llm_mod._make_gemini("gemini-2.5-flash", 0.0)
        except Exception:
            pass
        return "ok", {"reached": "test"}

    monkeypatch.setitem(A.OPS, ("planner", "plan"), _op)
    out = R.run_offline([_case("planner-q1")])
    assert out.exit_code == 2 and out.tripwire_hits == 1


@pytest.mark.parametrize(
    "cls_path",
    [
        "langchain_google_genai.ChatGoogleGenerativeAI",
        "langchain_groq.ChatGroq",
        "langchain_google_genai.GoogleGenerativeAIEmbeddings",
    ],
)
def test_each_known_client_class_is_tripwired(cls_path):
    mod, name = cls_path.rsplit(".", 1)
    cls = getattr(__import__(mod, fromlist=[name]), name)
    with I.offline_isolation() as ctx:
        with pytest.raises(I.OfflineViolation):
            cls(model="x", google_api_key="k", api_key="k")
        assert ctx.tripwire_hits == [name]


def test_a_swallowed_network_attempt_still_fails_the_run(monkeypatch):
    def _op(case, state):
        try:
            socket.create_connection(("example.org", 443), timeout=1)
        except Exception:
            pass
        return "ok", {"reached": "test"}

    monkeypatch.setitem(A.OPS, ("planner", "plan"), _op)
    out = R.run_offline([_case("planner-q1")])
    assert out.exit_code == 2 and out.network_attempts == 1


def test_refuses_when_qdrant_is_remote():
    with contextlib.ExitStack() as stack:
        ctx = I._new_context("offline", None)
        stack.callback(__import__("shutil").rmtree, ctx.work_dir, True)
        I._set_attr(stack, settings, "qdrant_url", "https://remote.example")
        with pytest.raises(I.IsolationError, match="QDRANT_URL"):
            I._enter_vector_store(stack, ctx, I.load_bench().FakeEmbeddings())


def test_refuses_when_qdrant_path_is_outside_the_temp_dir(monkeypatch, tmp_path):
    bench = I.load_bench()
    real = bench.isolated_vector_store

    @contextlib.contextmanager
    def _escaped(qdrant_dir, embeddings):
        with real(tmp_path / "elsewhere", embeddings) as vs:
            yield vs

    monkeypatch.setattr(bench, "isolated_vector_store", _escaped)
    out = R.run_offline([_case("planner-q1")])
    assert out.exit_code == 2 and "outside" in out.error


# --------------------------------------------------------------------------- #
# Restoration (R4c) - normal exit and exception
# --------------------------------------------------------------------------- #
def _snapshot():
    import langchain_google_genai
    import langchain_groq

    import src.core.vector_store as vs

    return {
        "env": {k: os.environ.get(k, "<absent>") for k in (*I.SECRET_ENV, *I.FORCED_ENV)},
        "settings": {k: getattr(settings, k) for k in I.OFFLINE_SETTINGS if hasattr(settings, k)},
        "socket": (
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.create_connection,
            socket.getaddrinfo,
        ),
        "inits": (
            langchain_google_genai.ChatGoogleGenerativeAI.__init__,
            langchain_google_genai.GoogleGenerativeAIEmbeddings.__init__,
            langchain_groq.ChatGroq.__init__,
        ),
        "llm": (llm_mod._make_gemini, llm_mod._make_groq),
        "vs": (vs._embeddings_instance, settings.qdrant_path),
    }


@pytest.fixture
def seeded_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "SENTINEL-GOOGLE-KEY")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)  # absent before -> absent after
    monkeypatch.setattr(settings, "web_search_enabled", True)


def test_isolation_restores_state_after_success(seeded_env):
    before = _snapshot()
    with I.offline_isolation() as ctx:
        assert os.environ["GOOGLE_API_KEY"] == "" and settings.web_search_enabled is False
        work = ctx.work_dir
    assert _snapshot() == before
    assert os.environ["GOOGLE_API_KEY"] == "SENTINEL-GOOGLE-KEY"
    assert "TAVILY_API_KEY" not in os.environ
    assert llm_mod._llm_instance is None and llm_mod._fast_llm_instance is None
    assert not work.exists()


def test_isolation_restores_state_after_an_exception(seeded_env):
    before = _snapshot()
    with pytest.raises(ZeroDivisionError):
        with I.offline_isolation():
            1 / 0  # noqa: B018
    assert _snapshot() == before
    assert settings.web_search_enabled is True


def test_no_state_leaked_into_a_later_test():
    # Runs after the isolation tests above (same module): everything they
    # patched is the object captured when this module was imported.
    assert socket.socket.connect is _ORIG["connect"]
    assert socket.getaddrinfo is _ORIG["getaddrinfo"]
    assert llm_mod._make_gemini is _ORIG["make_gemini"]
    assert llm_mod._make_groq is _ORIG["make_groq"]
    assert settings.qdrant_path == _ORIG["qdrant_path"]


# --------------------------------------------------------------------------- #
# Budget ledger / callbacks (AC7) - fakes only, zero requests
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def default_model_names(monkeypatch):
    """Pin the configured model ids to the code defaults, so the gate tests do not
    depend on the developer's .env (which may name other models - those are refused
    as 'not declared in pricing.json', see test_undeclared_configured_model_refuses)."""
    monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "llm_fast_model", "gemini-2.5-flash-lite")
    monkeypatch.setattr(settings, "groq_model", "llama-3.3-70b-versatile")
    monkeypatch.setattr(settings, "groq_fast_model", "llama-3.1-8b-instant")
    monkeypatch.setattr(settings, "embedding_model", "models/gemini-embedding-001")


def _validated_pricing(price=1.0):
    pricing = B.load_pricing()
    for entry in pricing["models"].values():
        entry["input_usd_per_million"] = price
        entry["output_usd_per_million"] = price
        entry["token_bound"].update(
            status="validated", validated_on="2026-11-03", evidence="test fixture"
        )
    return pricing


def _ledger(**caps):
    base = dict(
        max_llm_sends=100,
        max_llm_input_tokens=10**7,
        max_embedding_requests=100,
        max_embedding_tokens=10**7,
        max_cost_usd=100.0,
    )
    base.update(caps)
    return B.BudgetLedger(
        caps=B.Caps(**base),
        pricing=_validated_pricing(),
        llm_sends_per_request=2,
        embedding_sends_per_request=2,
        output_bound=100,
    )


class _UsageModel(BaseChatModel):
    model: str = "gemini-2.5-flash"
    usage: dict | None = None
    calls: int = 0

    @property
    def _llm_type(self):
        return "test-usage"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        msg = (
            AIMessage(content="ok", usage_metadata=self.usage)
            if self.usage
            else AIMessage(content="ok")
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _identifying_params(self):
        return {"model": "gemini-2.5-flash"}


def _invoke(model, ledger, tags=None, callback=None):
    cfg = {
        "callbacks": [callback or B.LLMBudgetCallback(ledger)],
        "metadata": {"ls_model_name": "gemini-2.5-flash"},
    }
    if tags:
        cfg["tags"] = tags
    return model.invoke("hello", config=cfg)


def test_llm_callback_reserves_worst_case_before_sending():
    ledger = _ledger()
    model = _UsageModel(usage={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})
    _invoke(model, ledger)
    u = ledger.usage["workflow"]
    assert u.calls == 1 and u.sends == 2  # (1 + SDK retries)
    assert u.output_tokens == 200 and u.actual_input_tokens == 5


def test_llm_cap_stops_before_the_send_and_latches():
    ledger = _ledger(max_llm_sends=3)
    model = _UsageModel(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    _invoke(model, ledger)
    with pytest.raises(B.BudgetExceeded):
        _invoke(model, ledger)
    assert model.calls == 1  # the second request was never sent
    with pytest.raises(B.BudgetExceeded):  # latched: everything after is refused
        ledger.reserve_embedding("gemini-embedding-001", ["x"])


def test_usage_above_the_reservation_or_missing_is_a_bound_violation():
    ledger = _ledger()
    with pytest.raises(B.BoundViolation):
        _invoke(
            _UsageModel(
                usage={"input_tokens": 10**6, "output_tokens": 1, "total_tokens": 10**6 + 1}
            ),
            ledger,
        )
    assert isinstance(ledger.stop, B.BoundViolation)
    ledger2 = _ledger()
    with pytest.raises(B.BoundViolation):
        _invoke(_UsageModel(usage=None), ledger2)


def test_judge_tag_charges_the_judge_role():
    ledger = _ledger()
    _invoke(
        _UsageModel(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
        ledger,
        [B.JUDGE_TAG],
    )
    assert ledger.usage["judge"].calls == 1 and ledger.usage["workflow"].calls == 0


class _CountingEmbeddings:
    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts, **kw):
        self.calls += 1
        return [[0.0] for _ in texts]

    def embed_query(self, text, **kw):
        self.calls += 1
        return [0.0]


def test_embedding_guard_enforces_without_any_llm_callback():
    ledger = _ledger(max_embedding_requests=3)
    inner = _CountingEmbeddings()
    guard = B.BudgetedEmbeddingsGuard(inner, ledger, "models/gemini-embedding-001")
    guard.embed_query("one")  # 2 worst-case sends
    with pytest.raises(B.BudgetExceeded):
        guard.embed_documents(["two"])
    assert inner.calls == 1


def test_embedding_cache_hits_are_not_charged():
    ledger = _ledger()
    inner = _CountingEmbeddings()
    bench = I.load_bench()
    cache = bench.BudgetedCachingEmbeddings(
        B.BudgetedEmbeddingsGuard(inner, ledger, "gemini-embedding-001"),
        model_id="m",
        cache_path=None,
        max_requests=10**6,
        max_tokens=10**9,
    )
    cache.embed_query("same")
    cache.embed_query("same")
    assert inner.calls == 1 and ledger.usage["embedding"].sends == 2


def test_total_cost_cap_spans_workflow_judge_and_embedding():
    ledger = _ledger(max_cost_usd=0.002)
    ledger.reserve_llm("workflow", "gemini-2.5-flash", 100)  # 2*(100+100) tokens @ $1/M
    ledger.reserve_llm("judge", "gemini-2.5-flash", 100)
    with pytest.raises(B.BudgetExceeded, match="total cost"):
        ledger.reserve_embedding("gemini-embedding-001", ["x" * 1000])


def test_utf8_bound_is_at_least_a_per_character_tokenizer_for_multilingual_text():
    text = "भारत का संविधान 🇮🇳 Article 21 — जीवन का अधिकार ✅"
    fake_tokens = len(text)  # one token per character (each character is >= 1 byte)
    assert B.utf8_bound_text(text) >= fake_tokens
    assert B.utf8_bound_messages([("human", text)]) >= fake_tokens


def test_client_preflight_refuses_unknown_clients_and_mismatches():
    class Unknown:
        max_retries = 1
        max_output_tokens = 100

    with pytest.raises(B.LiveRefused, match="usage metadata"):
        B.check_clients([Unknown()], sdk_max_retries=1, output_bound=100)

    class ChatGoogleGenerativeAI:  # same name as the real class (usage-reporting)
        max_retries = 3
        max_output_tokens = None

    with pytest.raises(B.LiveRefused) as exc:
        B.check_clients([ChatGoogleGenerativeAI()], sdk_max_retries=1, output_bound=100)
    assert any("max_retries" in r for r in exc.value.reasons)
    assert any("output limit" in r for r in exc.value.reasons)


# --------------------------------------------------------------------------- #
# Live gate via the CLI (AC7): every refusal sends nothing, builds no client
# --------------------------------------------------------------------------- #
CAPS = [
    "--max-llm-sends",
    "500",
    "--max-llm-input-tokens",
    "5000000",
    "--max-embedding-requests",
    "5000",
    "--max-embedding-tokens",
    "5000000",
    "--max-cost-usd",
    "5",
]


def _main(argv):
    with armed_guards() as guard:
        code = R.main(["--workflow", "planner", "--no-report", *argv])
    assert guard.violations == 0, (guard.network_attempts, guard.tripwire_hits)
    return code


@pytest.fixture
def use_pricing(monkeypatch):
    """Serve a pricing dict from the repo pricing path (the only accepted path)."""

    def _use(mutate=lambda m: None, price=0.01):
        pricing = _validated_pricing(price)
        mutate(pricing["models"])
        monkeypatch.setattr(B, "load_pricing", lambda path=B.PRICING_PATH: pricing)
        return pricing

    return _use


@pytest.fixture
def confirmed(monkeypatch):
    monkeypatch.setenv(R.LIVE_CONFIRM_ENV, "RUN")


def test_dry_run_prints_the_estimate_and_sends_nothing(capsys):
    assert _main(["--mode", "live"]) == 0
    out = capsys.readouterr().out
    for needle in (
        "gemini-2.5-flash",
        "gemini-embedding-001",
        "worst_case_sends",
        "input_token_bound",
        "cache_hits",
        "cost_by_model_usd",
        "UNKNOWN",
        "EXPERIMENTAL ESTIMATE, UNVALIDATED",
        "would REFUSE",
        "unvalidated",
    ):
        assert needle in out, needle


def test_live_with_the_shipped_pricing_refuses(capsys, confirmed):
    assert _main(["--mode", "live", "--yes", *CAPS]) == 2
    err = capsys.readouterr().err
    assert "token bound is unvalidated" in err and "price is null" in err


def test_live_without_caps_refuses(capsys, confirmed):
    assert _main(["--mode", "live", "--yes"]) == 2
    assert "required cap missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name, mutate, reason",
    [
        (
            "status unvalidated",
            lambda m: m["gemini-2.5-flash"]["token_bound"].update(status="unvalidated"),
            "unvalidated",
        ),
        (
            "embedding unvalidated",
            lambda m: m["gemini-embedding-001"]["token_bound"].update(status="unvalidated"),
            "gemini-embedding-001",
        ),
        (
            "no evidence",
            lambda m: m["gemini-2.5-flash-lite"]["token_bound"].update(evidence=None),
            "unvalidated",
        ),
        (
            "llm usage unavailable",
            lambda m: m["gemini-2.5-flash"]["token_bound"].update(
                provider_usage_check="unavailable"
            ),
            "provider_usage_check",
        ),
        (
            "method null",
            lambda m: m["gemini-2.5-flash"]["token_bound"].update(method=None),
            "token_bound.method must be",
        ),
        (
            "method unknown",
            lambda m: m["gemini-embedding-001"]["token_bound"].update(method="chars_div_4"),
            "token_bound.method must be",
        ),
        ("price null", lambda m: m["gemini-2.5-flash"].update(input_usd_per_million=None), "null"),
    ],
)
def test_live_refuses_any_unvalidated_or_uncheckable_bound(
    capsys, use_pricing, confirmed, name, mutate, reason
):
    use_pricing(mutate)
    assert _main(["--mode", "live", "--yes", *CAPS]) == 2, name
    assert reason in capsys.readouterr().err


def test_undeclared_configured_model_refuses(capsys, monkeypatch, use_pricing, confirmed):
    monkeypatch.setattr(settings, "llm_model", "gemini-future-model")
    use_pricing()
    assert _main(["--mode", "live", "--yes", *CAPS]) == 2
    assert "'gemini-future-model' is not declared in pricing.json" in capsys.readouterr().err


def test_live_refuses_when_one_worst_case_request_exceeds_a_cap(capsys, use_pricing, confirmed):
    use_pricing()
    caps = list(CAPS)
    caps[caps.index("--max-llm-input-tokens") + 1] = "10"
    assert _main(["--mode", "live", "--yes", *caps]) == 2
    assert "one worst-case request alone exceeds" in capsys.readouterr().err


# --- Review S2: SDK retries ----------------------------------------------------
@pytest.mark.parametrize("value", ["0", "-1"])
def test_sdk_max_retries_below_one_refuses(capsys, value):
    assert _main(["--mode", "live", "--sdk-max-retries", value]) == 2
    assert "--sdk-max-retries must be >= 1" in capsys.readouterr().err


def test_configured_zero_retries_also_refuses(capsys, monkeypatch, use_pricing, confirmed):
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    use_pricing()
    assert _main(["--mode", "live", "--yes", *CAPS]) == 2
    assert "SDK max_retries must be >= 1" in capsys.readouterr().err


# --- Review S4: only the repo pricing file + explicit owner confirmation -------
def test_external_pricing_path_is_rejected(tmp_path, capsys):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(_validated_pricing()), encoding="utf-8")
    assert _main(["--mode", "live", "--yes", "--pricing", str(path), *CAPS]) == 2
    assert "only the repository's evals/pricing.json" in capsys.readouterr().err


def test_non_interactive_run_without_the_confirmation_variable_refuses(
    capsys, monkeypatch, use_pricing
):
    use_pricing()
    monkeypatch.delenv(R.LIVE_CONFIRM_ENV, raising=False)
    called = []
    monkeypatch.setattr(R, "execute_live", lambda *a, **k: called.append(1))
    assert _main(["--mode", "live", "--yes", *CAPS]) == 2
    assert "not confirmed by the owner" in capsys.readouterr().err
    assert called == []  # nothing was built or sent


def test_wrong_confirmation_value_refuses(capsys, monkeypatch, use_pricing):
    use_pricing()
    monkeypatch.setenv(R.LIVE_CONFIRM_ENV, "yes")
    assert _main(["--mode", "live", "--yes", *CAPS]) == 2


def test_fully_validated_and_confirmed_config_passes_the_gate(monkeypatch, use_pricing, confirmed):
    use_pricing()
    called = {}

    def _fake_execute(args, cases, est, pricing, **kw):
        called["cases"] = len(cases)
        return 0, {"records": [], "ledger": {}}

    monkeypatch.setattr(R, "execute_live", _fake_execute)
    assert _main(["--mode", "live", "--yes", *CAPS]) == 0
    # The gate is not always-refuse: it hands every planner case to execution.
    assert called["cases"] == sum(1 for c in ALL_CASES if c["workflow"] == "planner")


# --- Review S1: tool-bound / structured models are budgeted; unreserved sends fail
class _Guarded(llm_mod._TimeoutGuard, _UsageModel):
    is_fallback: bool = False
    fallback_chain: str = "eval-test"


def _live_iso(cb, monkeypatch, model):
    import src.graph.supervisor as supervisor
    import src.graph.tools as tools_mod

    monkeypatch.setattr(tools_mod, "bind_tools_with_fallback", lambda llm=None, tools=None: model)
    monkeypatch.setattr(supervisor, "_structured_llm", lambda schema: model)
    return I.live_isolation(
        embeddings=I.load_bench().FakeEmbeddings(),
        sdk_max_retries=1,
        allow_fallback=False,
        llm_callbacks=[cb],
        send_guard=cb.require_reservation,
    )


def test_tool_bound_and_structured_models_carry_the_budget_callback(monkeypatch):
    import src.graph.supervisor as supervisor
    import src.graph.tools as tools_mod

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _UsageModel(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    with armed_guards(), _live_iso(cb, monkeypatch, model):
        bound = tools_mod.bind_tools_with_fallback(llm=None, tools=[])
        assert cb in bound.config["callbacks"]
        bound.invoke("hello")
        assert ledger.usage["workflow"].calls == 1 and model.calls == 1
        structured = supervisor._structured_llm(object)
        assert cb in structured.config["callbacks"]
        structured.invoke("route me")
        assert ledger.usage["workflow"].calls == 2 and model.calls == 2


def test_a_send_without_a_reservation_is_refused_before_it_leaves(monkeypatch):
    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _Guarded(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    with armed_guards(), _live_iso(cb, monkeypatch, model):
        with pytest.raises(B.BudgetExceeded, match="without a budget reservation"):
            model.invoke("no callback attached")
        assert model.calls == 0  # the real send method never ran
    assert isinstance(ledger.stop, B.BudgetExceeded)


def test_a_reserved_guarded_send_goes_through(monkeypatch):
    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _Guarded(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    with armed_guards(), _live_iso(cb, monkeypatch, model):
        _invoke(model, ledger, callback=cb)
    assert model.calls == 1 and ledger.stop is None


def test_send_guard_is_removed_after_live_isolation(monkeypatch):
    real = llm_mod._TimeoutGuard._generate
    cb = B.LLMBudgetCallback(_ledger())
    with _live_iso(cb, monkeypatch, _UsageModel()):
        assert llm_mod._TimeoutGuard._generate is not real
    assert llm_mod._TimeoutGuard._generate is real


# --- Review S3 / S7: live execution with fake clients (no request can leave) ----
class ChatGoogleGenerativeAI:  # same name as the usage-reporting client class
    def __init__(self, max_retries=1, max_output_tokens=4096):
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens


class _FakePinnedEmbeddings:
    def __init__(self):
        self._inner = type("Raw", (), {"max_retries": 1})()


def _fake_factory(judge_clients, store):
    def factory(callback):
        store["callback"] = callback
        return [ChatGoogleGenerativeAI()], object(), judge_clients, _FakePinnedEmbeddings()

    return factory


@pytest.mark.parametrize(
    "judge_clients, reason",
    [
        ([], "judge client configuration is unreadable"),
        ([ChatGoogleGenerativeAI(max_retries=3)], "max_retries 3 != budgeted 1"),
        ([ChatGoogleGenerativeAI(max_output_tokens=None)], "no enforced output limit"),
    ],
)
def test_judge_client_is_preflighted_like_workflow_clients(
    capsys, monkeypatch, use_pricing, confirmed, judge_clients, reason
):
    use_pricing()
    seeded = []  # the offline estimate pass seeds too; live seeding must never start
    monkeypatch.setattr(A, "seed_run", lambda state: seeded.append(state.mode))
    with armed_guards() as guard:
        code = R.main(
            ["--workflow", "planner", "--no-report", "--mode", "live", "--yes", *CAPS],
            client_factory=_fake_factory(judge_clients, {}),
        )
    assert code == 2 and guard.violations == 0
    assert "live" not in seeded
    assert reason in capsys.readouterr().err


def _live_with_fake_case(monkeypatch, use_pricing, usage, caps, tmp_path):
    use_pricing()
    store = {}
    model = _UsageModel(usage=usage)
    monkeypatch.setattr(A, "seed_run", lambda state: None)

    def _fake_run_case(case, state):
        try:  # production code swallows errors; the ledger latch must still stop the run
            model.invoke(
                "case prompt",
                config={
                    "callbacks": [store["callback"]],
                    "metadata": {"ls_model_name": "gemini-2.5-flash"},
                },
            )
        except Exception:
            pass
        return A.CaseRun(case, "out", {"reached": "test"}, A.PromptRecorder(), [])

    monkeypatch.setattr(A, "run_case", _fake_run_case)
    with armed_guards() as guard:
        code = R.main(
            [
                "--workflow",
                "planner",
                "--no-report",
                "--mode",
                "live",
                "--yes",
                *caps,
                "--embedding-cache",
                str(tmp_path / "cache.json"),
            ],
            client_factory=_fake_factory([ChatGoogleGenerativeAI()], store),
        )
    assert guard.violations == 0
    return code, model


def test_live_budget_cap_stops_with_exit_3_before_the_send(
    monkeypatch, use_pricing, confirmed, tmp_path
):
    caps = list(CAPS)
    caps[caps.index("--max-llm-sends") + 1] = "2"  # one request = 2 worst-case sends
    usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    code, model = _live_with_fake_case(monkeypatch, use_pricing, usage, caps, tmp_path)
    assert code == 3
    assert model.calls == 1  # the second case's request was refused before sending


def test_token_bound_violation_stops_with_exit_4(monkeypatch, use_pricing, confirmed, tmp_path):
    usage = {"input_tokens": 10**7, "output_tokens": 1, "total_tokens": 10**7 + 1}
    code, model = _live_with_fake_case(monkeypatch, use_pricing, usage, CAPS, tmp_path)
    assert code == 4 and model.calls == 1  # stopped right after the violating call


def test_missing_usage_metadata_stops_with_exit_4(monkeypatch, use_pricing, confirmed, tmp_path):
    code, _ = _live_with_fake_case(monkeypatch, use_pricing, None, CAPS, tmp_path)
    assert code == 4


def test_undeclared_model_at_send_time_stops_with_exit_2():
    ledger = _ledger()
    with pytest.raises(B.UndeclaredModel):
        ledger.reserve_llm("workflow", "", 10)
    assert ledger.stop.exit_code == 2


# --- Review S8: error paths ------------------------------------------------------
def test_include_outputs_prints_a_warning(capsys, tmp_path):
    assert (
        R.main(["--workflow", "planner", "--include-outputs", "--report-dir", str(tmp_path)]) == 0
    )
    assert "WARNING: --include-outputs" in capsys.readouterr().err


def test_an_adapter_crash_is_an_execution_error_not_a_safety_failure(capsys, monkeypatch):
    def _boom(case, state):
        raise KeyError("missing-input")

    monkeypatch.setitem(A.OPS, ("planner", "plan"), _boom)
    assert _main([]) == R.EXIT_EXECUTION_ERROR == 5
    err = capsys.readouterr().err
    assert "EXECUTION ERROR (KeyError)" in err and "missing-input" not in err


# --- Review NIT 6: known_gap downgrades only its declared check -----------------
def test_known_gap_downgrades_only_the_declared_check():
    case = {
        "id": "x",
        "workflow": "mentor",
        "op": "scope",
        "kind": "safety",
        "known_gap": {"check": "scope_refused", "note": "documented"},
        "checks": [{"type": "scope_refused"}, {"type": "not_contains", "terms": ["leak"]}],
    }
    from src.core.scope_gate import check_scope

    run = A.CaseRun(
        case, "leak", {"scope": check_scope("Explain Article 21")}, A.PromptRecorder(), []
    )
    rows = R.evaluate_case(run, "offline")
    assert [r["status"] for r in rows] == ["known_gap", "fail"]
    assert R._exit_for([{"kind": "safety", "checks": rows}], strict=False) == 1


def test_known_gap_must_name_a_real_check():
    bad = dict(_case("rt-scope-song"), known_gap={"check": "canary_absent", "note": "x"})
    assert any("known_gap" in e for e in A.validate_cases([bad]))


# --- Review NIT 9: robust redacted evidence --------------------------------------
def _run_for(case_id, output, retrieved):
    return A.CaseRun(_case(case_id), output, {}, A.PromptRecorder(), retrieved)


def test_redacted_evidence_replaces_retrieved_text_despite_case_and_spacing():
    chunk = "A joint sitting resolves a deadlock between the two Houses of Parliament."
    output = "Sure. A JOINT   sitting resolves a\ndeadlock ZQX-DOC-101 between the two"
    run = _run_for("rt-upload-doc-injection", output, [("polity#4", chunk)])
    assert R._redacted_evidence(run, "ZQX-DOC-101") == "[source:polity#4]"


def test_redacted_evidence_keeps_a_short_window_when_no_source_matches():
    output = "x" * 200 + " model wrote ZQX-DOC-101 on its own " + "y" * 200
    excerpt = R._redacted_evidence(_run_for("rt-upload-doc-injection", output, []), "ZQX-DOC-101")
    assert "ZQX-DOC-101" in excerpt and len(excerpt) <= 80


def test_redacted_evidence_is_omitted_when_sources_are_not_fully_captured():
    run = _run_for("rt-mentor-kb-injection", "kb text ZQX-KB-71 more", [])
    assert R._redacted_evidence(run, "ZQX-KB-71").startswith("[excerpt omitted")


def test_failed_canary_check_writes_only_redacted_evidence():
    chunk = "Monetary Policy Committee members vote on the repo rate every two months."
    output = "Monetary Policy Committee members vote ZQX-LEC-102 on the repo rate every two months."
    run = _run_for("rt-lecture-doc-injection", output, [("economy#7", chunk)])
    rows = R.evaluate_case(run, "offline")
    rec = R.case_record(run, rows, [], include_outputs=False)
    assert rec["evidence"] == [
        {"check": "canary_absent:ZQX-LEC-102", "excerpt": "[source:economy#7]"}
    ]
    assert "repo rate" not in json.dumps(rec)


# --- Review NIT 5: NaN judge score ------------------------------------------------
def test_nan_judge_score_is_a_judge_error():
    from types import SimpleNamespace

    from evals.evaluators import judge as J

    class _NaN:
        def invoke(self, messages):
            return SimpleNamespace(score=float("nan"))

    res = J.judge(_NaN(), "relevance", question="q", answer="a")
    assert res.score is None and res.judge_error


# --- Review NIT 7 / 8: global state and UDP ------------------------------------
def test_preserved_globals_and_usage_counters_are_restored():
    import importlib

    from src.core import usage_tracker

    before = {(m, a): getattr(importlib.import_module(m), a) for m, a in I.PRESERVED_GLOBALS}
    counters = {k: dict(v) for k, v in usage_tracker._by_model.items()}
    with I.offline_isolation():
        usage_tracker._by_model["eval-leak"] = {"calls": 1}
        importlib.import_module("src.core.upstash")._singleton = object()
    after = {(m, a): getattr(importlib.import_module(m), a) for m, a in I.PRESERVED_GLOBALS}
    assert after == before
    assert {k: dict(v) for k, v in usage_tracker._by_model.items()} == counters


def test_langsmith_env_cache_is_cleared_on_exit(monkeypatch):
    from langsmith import utils as ls_utils

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    ls_utils.get_env_var.cache_clear()
    with I.offline_isolation():
        assert os.environ["LANGSMITH_TRACING"] == "false"
    # The cache was dropped on exit, so the restored env value is read again.
    assert ls_utils.get_env_var.cache_info().currsize == 0


def test_udp_sends_are_blocked_and_counted():
    with I.offline_isolation() as ctx:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with pytest.raises(I.OfflineViolation):
                sock.sendto(b"x", ("127.0.0.1", 9))
        assert ctx.network_attempts == ["sendto"]


# --------------------------------------------------------------------------- #
# Report data minimisation (R6)
# --------------------------------------------------------------------------- #
def test_report_holds_no_prompts_corpus_text_outputs_or_env_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "SENTINEL-GROQ-SECRET-777")
    monkeypatch.setattr(settings, "groq_api_key", "SENTINEL-GROQ-SECRET-777")
    assert R.main(["--workflow", "upload", "--report-dir", str(tmp_path)]) == 0
    text = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    corpus = (REPO / "src/eval/corpora/upload_style_polity.md").read_text(encoding="utf-8")
    chunk = next(line for line in corpus.splitlines() if len(line) > 80)[:60]
    for forbidden in (
        "SENTINEL-GROQ-SECRET-777",
        chunk,  # corpus excerpt
        "You are a UPSC expert",  # prompt template text
        "A joint sitting resolves a deadlock",  # scripted model output
        "<<<BEGIN_UNTRUSTED_SOURCE>>>",
    ):
        assert forbidden not in text, forbidden
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    rec = report["cases"][0]
    assert set(rec["output"]) == {"sha256", "chars"}
    assert all(set(p) == {"sha256", "bytes"} for p in rec["prompts"])
    assert "do not show real-model safety" in report["disclaimer"]


# --- Final review S-2: the stream / async send paths are guarded too -------------
class _StreamUsageModel(_UsageModel):
    streamed: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        self.streamed += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content="ok", usage_metadata=self.usage))


class _GuardedStream(llm_mod._TimeoutGuard, _StreamUsageModel):
    is_fallback: bool = False
    fallback_chain: str = "eval-test"


_USAGE = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}


def test_an_unreserved_stream_is_refused_before_the_inner_stream_runs(monkeypatch):
    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _GuardedStream(usage=_USAGE)
    with armed_guards(), _live_iso(cb, monkeypatch, model):
        with pytest.raises(B.BudgetExceeded, match="without a budget reservation"):
            list(model.stream("no callback attached"))
    assert model.streamed == 0


def test_a_reserved_stream_goes_through(monkeypatch):
    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _GuardedStream(usage=_USAGE)
    cfg = {"callbacks": [cb], "metadata": {"ls_model_name": "gemini-2.5-flash"}}
    with armed_guards(), _live_iso(cb, monkeypatch, model):
        chunks = list(model.stream("hello", config=cfg))
    assert "".join(c.content for c in chunks) == "ok"
    assert model.streamed == 1 and ledger.usage["workflow"].calls == 1 and ledger.stop is None


def test_an_unreserved_async_send_is_refused(monkeypatch):
    import asyncio

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _GuardedStream(usage=_USAGE)
    # No socket guard here: asyncio's Windows event loop opens a loopback socket
    # pair. The model is a local fake, so nothing can leave the process anyway.
    with _live_iso(cb, monkeypatch, model):
        with pytest.raises(B.BudgetExceeded, match="without a budget reservation"):
            asyncio.run(model.ainvoke("no callback attached"))
    assert model.calls == 0 and model.streamed == 0


def test_one_reservation_cannot_cover_two_unmanaged_sends():
    import uuid

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    cb.on_chat_model_start(
        {}, [[]], run_id=uuid.uuid4(), metadata={"ls_model_name": "gemini-2.5-flash"}
    )
    cb.require_reservation(None)  # the stream path: claims the open reservation
    with pytest.raises(B.BudgetExceeded):
        cb.require_reservation(None)  # a second send has nothing left to claim


def test_a_finished_run_cannot_be_claimed_later():
    import uuid

    from langchain_core.outputs import ChatGeneration, LLMResult

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    rid = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=rid, metadata={"ls_model_name": "gemini-2.5-flash"})
    msg = AIMessage(content="ok", usage_metadata=_USAGE)
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=rid)
    with pytest.raises(B.BudgetExceeded):
        cb.require_reservation(None)


# --- Round-3 review SF-2 / N-1 / N-2: async reservations ----------------------
class _AsyncStreamUsageModel(_StreamUsageModel):
    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import AIMessageChunk
        from langchain_core.outputs import ChatGenerationChunk

        self.streamed += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content="ok", usage_metadata=self.usage))


class _GuardedAsyncStream(llm_mod._TimeoutGuard, _AsyncStreamUsageModel):
    is_fallback: bool = False
    fallback_chain: str = "eval-test"


def test_a_reserved_astream_goes_through(monkeypatch):
    import asyncio

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    model = _GuardedAsyncStream(usage=_USAGE)
    cfg = {"callbacks": [cb], "metadata": {"ls_model_name": "gemini-2.5-flash"}}

    async def _consume():
        return [c async for c in model.astream("hello", config=cfg)]

    with _live_iso(cb, monkeypatch, model):  # asyncio needs a loopback pair on Windows
        chunks = asyncio.run(_consume())
    assert "".join(c.content for c in chunks) == "ok"
    assert model.streamed == 1 and ledger.stop is None
    # The inline reservation was made, claimed by the send and closed.
    assert ledger.usage["workflow"].calls == 1
    assert cb._unclaimed == {} and cb._open == {} and cb._thread_claimed == set()


def test_a_run_claimed_from_another_thread_cannot_be_claimed_again():
    # N-2: the run is reserved on worker thread W, claimed by its run id on the main
    # thread, and then an unmanaged send on W must NOT be able to reuse it.
    import threading
    import uuid

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    rid = uuid.uuid4()
    reserved, claimed = threading.Event(), threading.Event()
    outcome = {}

    def _worker():
        cb.on_chat_model_start({}, [[]], run_id=rid, metadata={"ls_model_name": "gemini-2.5-flash"})
        reserved.set()
        claimed.wait(5)
        try:
            cb.require_reservation(None)
            outcome["stolen"] = True
        except B.BudgetExceeded:
            outcome["refused"] = True

    worker = threading.Thread(target=_worker)
    worker.start()
    reserved.wait(5)
    cb.require_reservation(type("RM", (), {"run_id": rid})())  # claimed by run id
    claimed.set()
    worker.join(5)
    assert outcome == {"refused": True}


def test_a_run_claimed_by_an_unmanaged_send_cannot_send_again_by_run_id():
    # Round-4 N-C: one reservation, two sends (one unmanaged, one by run id) is refused.
    import uuid

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    rid = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=rid, metadata={"ls_model_name": "gemini-2.5-flash"})
    cb.require_reservation(None)
    with pytest.raises(B.BudgetExceeded):
        cb.require_reservation(type("RM", (), {"run_id": rid})())


# --- Final budget review: atomic reservations under concurrency -----------------
def _race(monkeypatch, first, second, pause=0.5):
    """Run ``first`` then ``second`` on two threads, deterministically interleaved.

    ``first`` is paused INSIDE its reservation - after it has read the counters and
    before it updates them (``total_cost`` is read in between) - until ``second``
    reaches the same point or ``pause`` seconds pass. Without a shared ledger lock
    ``second`` reads the same stale counters and both pass a cap only one fits; with
    the lock ``second`` blocks until ``first`` has committed. Returns each thread's
    outcome: "ok" or the exception class name.
    """
    import threading

    real = B.BudgetLedger.total_cost
    first_inside, second_inside = threading.Event(), threading.Event()
    first_tid: dict = {}

    def total_cost(self):
        me = threading.get_ident()
        if me == first_tid.get("t"):
            if not first_inside.is_set():
                first_inside.set()
                second_inside.wait(pause)
        else:
            second_inside.set()
        return real.fget(self)

    monkeypatch.setattr(B.BudgetLedger, "total_cost", property(total_cost))
    outcome: dict = {}

    def _run(name, fn):
        if name == "first":
            first_tid["t"] = threading.get_ident()
        try:
            fn()
            outcome[name] = "ok"
        except Exception as exc:  # the outcome is what the tests assert on
            outcome[name] = type(exc).__name__

    a = threading.Thread(target=_run, args=("first", first))
    a.start()
    assert first_inside.wait(5), "first reservation never reached the cost check"
    b = threading.Thread(target=_run, args=("second", second))
    b.start()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive()
    return outcome


def _llm(ledger, input_bound=10):
    return lambda: ledger.reserve_llm("workflow", "gemini-2.5-flash", input_bound)


def _emb(ledger, text="x"):
    return lambda: ledger.reserve_embedding("gemini-embedding-001", [text])


def test_two_concurrent_llm_reservations_cannot_both_pass_the_sends_cap(monkeypatch):
    ledger = _ledger(max_llm_sends=3)  # 2 sends per request: one fits, two do not
    outcome = _race(monkeypatch, _llm(ledger), _llm(ledger))
    assert outcome == {"first": "ok", "second": "BudgetExceeded"}
    u = ledger.usage["workflow"]
    assert u.calls == 1 and u.sends == 2 <= ledger.caps.max_llm_sends
    assert isinstance(ledger.stop, B.BudgetExceeded)  # the refusal latched


def test_two_concurrent_embedding_reservations_cannot_both_pass_the_cap(monkeypatch):
    ledger = _ledger(max_embedding_requests=3)  # 2 sends per text: one fits
    outcome = _race(monkeypatch, _emb(ledger), _emb(ledger))
    assert outcome == {"first": "ok", "second": "BudgetExceeded"}
    u = ledger.usage["embedding"]
    assert u.calls == 1 and u.sends == 2 <= ledger.caps.max_embedding_requests
    assert isinstance(ledger.stop, B.BudgetExceeded)


@pytest.mark.parametrize("llm_first", [True, False])
def test_mixed_llm_and_embedding_reservations_share_the_cost_cap_atomically(monkeypatch, llm_first):
    # Price 1 USD / M tokens. LLM: 2 x (1000 in + 100 out) = 0.0022 USD.
    # Embedding: 2 x (1000 + 16) = 0.002032 USD. Either fits 0.003 alone, not both.
    ledger = _ledger(max_cost_usd=0.003)
    llm, emb = _llm(ledger, input_bound=1000), _emb(ledger, text="x" * 1000)
    first, second = (llm, emb) if llm_first else (emb, llm)
    outcome = _race(monkeypatch, first, second)
    assert outcome == {"first": "ok", "second": "BudgetExceeded"}
    assert ledger.total_cost <= ledger.caps.max_cost_usd
    assert ledger.usage["workflow"].calls + ledger.usage["embedding"].calls == 1
    assert isinstance(ledger.stop, B.BudgetExceeded)


def test_a_reservation_racing_a_halt_is_refused(monkeypatch):
    # The stop latch is part of the same transaction: once a refusal has latched,
    # a reservation that was waiting on the lock must not slip through.
    ledger = _ledger()
    over = _llm(ledger, input_bound=10**8)  # over the token cap: refused + latched
    outcome = _race(monkeypatch, over, _emb(ledger))
    assert outcome == {"first": "BudgetExceeded", "second": "BudgetExceeded"}
    assert ledger.usage["embedding"].calls == 0 and ledger.total_cost == 0


def test_a_run_started_twice_concurrently_is_charged_once(monkeypatch):
    import uuid

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    rid = uuid.uuid4()

    def _start():
        cb.on_chat_model_start({}, [[]], run_id=rid, metadata={"ls_model_name": "gemini-2.5-flash"})

    outcome = _race(monkeypatch, _start, _start, pause=0.3)
    assert outcome == {"first": "ok", "second": "ok"}
    assert ledger.usage["workflow"].calls == 1 and ledger.usage["workflow"].sends == 2


# --- Final budget review: one managed reservation = one send ------------------
class _NestedSender:
    """Fake client whose send methods delegate to each other the way real clients do
    (``_agenerate`` -> ``_generate`` -> ``_stream``): nested checks of ONE send."""

    def __init__(self):
        self.sent = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return list(self._stream(messages, run_manager=run_manager))

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.sent += 1
        yield "chunk"

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, run_manager=run_manager)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.sent += 1
        yield "chunk"


def _reserved_run(cb):
    import uuid

    rid = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=rid, metadata={"ls_model_name": "gemini-2.5-flash"})
    return type("RM", (), {"run_id": rid})()


def test_nested_checks_of_one_managed_send_pass_but_a_second_send_is_refused():
    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    client = _NestedSender()
    with contextlib.ExitStack() as stack:
        I.install_send_guard(stack, cb.require_reservation, cls=_NestedSender)
        rm = _reserved_run(cb)
        assert client._generate([], run_manager=rm) == ["chunk"]  # 2 nested checks
        with pytest.raises(B.BudgetExceeded, match="without a budget reservation"):
            client._generate([], run_manager=rm)  # same reservation, independent send
        with pytest.raises(B.BudgetExceeded):
            list(client._stream([], run_manager=rm))  # ... by any send method
    assert client.sent == 1
    assert isinstance(ledger.stop, B.BudgetExceeded)


def test_nested_async_checks_of_one_managed_send_pass_but_a_second_is_refused():
    import asyncio

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    client = _NestedSender()
    with contextlib.ExitStack() as stack:
        I.install_send_guard(stack, cb.require_reservation, cls=_NestedSender)
        rm = _reserved_run(cb)
        # 3 nested checks (_agenerate -> _generate -> _stream) for ONE send.
        assert asyncio.run(client._agenerate([], run_manager=rm)) == ["chunk"]
        with pytest.raises(B.BudgetExceeded):
            asyncio.run(client._agenerate([], run_manager=rm))
    assert client.sent == 1


def test_an_internal_check_from_another_thread_during_the_send_is_allowed():
    # ``_agenerate``'s default runs ``_generate`` in an executor thread with the same
    # run id while the outer send is in progress: legitimate. After the outer send
    # has finished, the same run id from another thread is a second send.
    import threading

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    rm = _reserved_run(cb)
    outcome: dict = {}

    def _check(name):
        try:
            with cb.require_reservation(rm):
                outcome[name] = "ok"
        except B.BudgetExceeded:
            outcome[name] = "refused"

    with cb.require_reservation(rm):  # outer send in progress
        t = threading.Thread(target=_check, args=("nested",))
        t.start()
        t.join(5)
    t = threading.Thread(target=_check, args=("after",))
    t.start()
    t.join(5)
    assert outcome == {"nested": "ok", "after": "refused"}


def test_a_finished_managed_send_leaves_no_state_after_the_run_ends():
    from langchain_core.outputs import LLMResult

    ledger = _ledger()
    cb = B.LLMBudgetCallback(ledger)
    rm = _reserved_run(cb)
    with cb.require_reservation(rm):
        pass
    assert rm.run_id in cb._sent
    msg = AIMessage(content="ok", usage_metadata=_USAGE)
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=rm.run_id)
    assert cb._open == {} and cb._inflight == {} and cb._sent == set()
    assert ledger.stop is None


# --- Final budget review: invalid usage latches the stop atomically -------------
@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 10**6, "output_tokens": 1, "total_tokens": 10**6 + 1},  # over bound
        None,  # unverifiable
    ],
    ids=["over_bound", "missing_usage"],
)
def test_no_reservation_passes_between_invalid_usage_and_the_stop_latch(monkeypatch, usage):
    """The first thread is paused right after it has DETECTED invalid usage (while
    building the ``BoundViolation``) and before the stop is latched. A reservation
    racing it must wait for the latch and be refused - never slip in between."""
    import threading

    ledger = _ledger()
    res = ledger.reserve_llm("workflow", "gemini-2.5-flash", 10)
    detected, second_inside = threading.Event(), threading.Event()
    recorder: dict = {}

    class _PausingViolation(B.BoundViolation):
        def __init__(self, *args):
            super().__init__(*args)
            if threading.get_ident() == recorder.get("t"):
                detected.set()
                second_inside.wait(0.5)  # unlocked: the reservation runs in here

    real_locked = B.BudgetLedger._reserve_llm_locked

    def _reserve_locked(self, *args, **kwargs):
        second_inside.set()  # reached only once the ledger lock is held
        return real_locked(self, *args, **kwargs)

    monkeypatch.setattr(B, "BoundViolation", _PausingViolation)
    monkeypatch.setattr(B.BudgetLedger, "_reserve_llm_locked", _reserve_locked)
    outcome: dict = {}

    def _record():
        recorder["t"] = threading.get_ident()
        try:
            ledger.record_llm_usage(res, usage)
            outcome["record"] = "ok"
        except Exception as exc:
            outcome["record"] = type(exc).__name__

    def _reserve():
        try:
            ledger.reserve_llm("workflow", "gemini-2.5-flash", 10)
            outcome["reserve"] = "ok"
        except Exception as exc:
            outcome["reserve"] = type(exc).__name__

    a = threading.Thread(target=_record)
    a.start()
    assert detected.wait(5), "invalid usage was never detected"
    b = threading.Thread(target=_reserve)
    b.start()
    a.join(5)
    b.join(5)
    assert outcome == {"record": "_PausingViolation", "reserve": "BudgetExceeded"}
    assert isinstance(ledger.stop, B.BoundViolation)
    assert ledger.usage["workflow"].calls == 1  # the racing reservation was not charged
