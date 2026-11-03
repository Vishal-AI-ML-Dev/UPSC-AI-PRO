"""Per-workflow adapters (Phase 07): seed isolated state -> call the REAL production
function -> return the output plus in-memory evidence.

Offline mode replaces every model getter (looked up per feature module, as the
tests do) with ``ScriptedChatModel``, which records the rendered prompt of every
call. Live mode leaves the production getters alone (real models, budgeted by
``evals.runners.budget``). In BOTH modes the outside world is replaced by the
case's fixtures: web search results, RSS headlines, the planner's prelims-date
lookup and (for the upload notes path) PDF text extraction.

Prompts, retrieved chunk texts and outputs stay in memory. The report writer keeps
only ids, hashes, lengths and check results (spec R6).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import ConfigDict

from evals.runners.isolation import REPO_ROOT, IsolationContext, load_bench

DATASET_DIR = REPO_ROOT / "evals" / "datasets"
NCERT_CHAPTER_FILE = DATASET_DIR / "ncert_synthetic_chapter.md"
NCERT_CLASS, NCERT_SUBJECT, NCERT_CHAPTER = "class8", "geography", "eval_synthetic_resources"
CORPORA = {
    "upload": {"kind": "upload", "path": "src/eval/corpora/upload_style_polity.md"},
    "lecture": {"kind": "lecture", "path": "src/eval/corpora/lecture_style_economy.txt"},
    "pyq_bank": {"kind": "pyq_bank", "path": "src/eval/corpora/pyq_style_paper.md"},
}
FEATURE_MODULES = (
    "src.agents.ncert.graph",
    "src.agents.upload.graph",
    "src.agents.lecture.graph",
    "src.agents.pyq.graph",
    "src.agents.evaluator.graph",
    "src.agents.current_affairs.graph",
    "src.agents.planner.graph",
)
TOOL_AGENT_WORST_CASE_CALLS = 4  # DEFAULT_MAX_TOOL_LOOPS (3) + the forced final answer


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Scripted fake model (offline only)
# --------------------------------------------------------------------------- #
class Script:
    """Replies in order; the last one repeats when the script runs out."""

    def __init__(self, steps):
        self.steps = list(steps or ["OK"])
        self.i = 0

    def next(self):
        step = self.steps[min(self.i, len(self.steps) - 1)]
        self.i += 1
        return step


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b if isinstance(b, str) else str(b.get("text", "")) if isinstance(b, dict) else ""
            for b in content
        )
    return str(content or "")


def render(messages) -> str:
    """Rendered prompt text of one model call (role-tagged, in memory only)."""
    if hasattr(messages, "to_messages"):
        messages = messages.to_messages()
    if isinstance(messages, str):
        return "[human] " + messages
    out = []
    for m in messages or []:
        if isinstance(m, tuple):
            out.append(f"[{m[0]}] {_content_text(m[1])}")
        else:
            out.append(
                f"[{getattr(m, 'type', 'message')}] {_content_text(getattr(m, 'content', m))}"
            )
    return "\n".join(out)


@dataclass
class PromptRecord:
    text: str
    n_messages: int
    extra_bytes: int = 0  # serialized tool / structured-output schema size


class PromptRecorder:
    def __init__(self):
        self.calls: list[PromptRecord] = []

    def add(self, messages, extra_bytes: int = 0) -> None:
        msgs = messages.to_messages() if hasattr(messages, "to_messages") else messages
        n = 1 if isinstance(msgs, str) else len(list(msgs or []))
        self.calls.append(PromptRecord(render(messages), n, extra_bytes))

    @property
    def joined(self) -> str:
        return "\n\n".join(c.text for c in self.calls)


def _to_ai_message(step, n: int) -> AIMessage:
    if isinstance(step, dict) and step.get("tool_calls"):
        calls = [
            {"name": c["name"], "args": c.get("args", {}), "id": f"call_{n}_{i}"}
            for i, c in enumerate(step["tool_calls"])
        ]
        return AIMessage(content=step.get("content", ""), tool_calls=calls)
    return AIMessage(content=str(step))


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model: replies from a script, records every prompt."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    script: Any = None
    recorder: Any = None
    extra_bytes: int = 0

    @property
    def _llm_type(self) -> str:
        return "eval-scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.recorder.add(messages, self.extra_bytes)
        msg = _to_ai_message(self.script.next(), len(self.recorder.calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self


def _tool_schema_bytes() -> int:
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        from src.graph.tools import get_structured_tools

        tools = [convert_to_openai_tool(t) for t in get_structured_tools()]
        return len(json.dumps(tools, sort_keys=True).encode("utf-8"))
    except Exception:  # noqa: BLE001 - estimate only
        return 2048


def install_scripted_models(stack: contextlib.ExitStack, script: Script, recorder: PromptRecorder):
    import importlib

    import src.core.llm as llm
    import src.core.study_aids as study_aids
    import src.graph.supervisor as supervisor
    import src.graph.tools as tools_mod

    model = ScriptedChatModel(script=script, recorder=recorder)
    tool_model = ScriptedChatModel(
        script=script, recorder=recorder, extra_bytes=_tool_schema_bytes()
    )
    for name in FEATURE_MODULES:
        stack.enter_context(
            mock.patch.object(importlib.import_module(name), "get_llm", lambda: model)
        )
    stack.enter_context(mock.patch.object(study_aids, "get_fast_llm", lambda: model))
    stack.enter_context(mock.patch.object(llm, "get_llm", lambda: model))
    stack.enter_context(mock.patch.object(llm, "get_fast_llm", lambda: model))
    stack.enter_context(mock.patch.object(llm, "get_llm_for_tier", lambda tier: model))
    stack.enter_context(
        mock.patch.object(
            tools_mod, "bind_tools_with_fallback", lambda llm=None, tools=None: tool_model
        )
    )
    schema_bytes = len(json.dumps(supervisor.RouteDecision.model_json_schema()).encode("utf-8"))

    def _router(messages):
        recorder.add(messages, schema_bytes)
        return supervisor.RouteDecision(route=str(script.next()))

    stack.enter_context(
        mock.patch.object(supervisor, "_structured_llm", lambda schema: RunnableLambda(_router))
    )


# --------------------------------------------------------------------------- #
# Run state, seeding, fixtures
# --------------------------------------------------------------------------- #
@dataclass
class RunState:
    mode: str  # offline | live
    ctx: IsolationContext
    corpus_docs: dict = field(default_factory=dict)
    retrieved: list = field(default_factory=list)  # current case: (id, text)
    owned_keys: list = field(default_factory=list)  # every user-owned key written


def _ncert_pages() -> list[tuple[int, str]]:
    text = NCERT_CHAPTER_FILE.read_text(encoding="utf-8")
    pages = [p.strip() for p in text.split("\n---page---\n")]
    return [(i, p) for i, p in enumerate(pages, start=1) if p]


def seed_run(state: RunState) -> None:
    """Shared global corpora for the run, inside the isolated temp Qdrant."""
    from src.agents.ncert.graph import build_chapter_documents, ncert_persist_key
    from src.core import mentor_kb
    from src.core.vector_store import upsert_documents

    bench = load_bench()
    mentor_kb.build_kb(bench._mentor_sources(), rebuild=True)  # approved read-only corpus
    docs = build_chapter_documents(
        _ncert_pages(),
        {"persist_key": "eval_ncert", "source_type": "ncert", "document": "Resources (synthetic)"},
    )
    upsert_documents(
        ncert_persist_key(NCERT_CLASS, NCERT_SUBJECT, NCERT_CHAPTER), docs, rebuild=True
    )
    for name, spec in CORPORA.items():
        state.corpus_docs[name] = bench.corpus_documents(name, spec)


def _seed_owned(state: RunState, corpus: str, key: str, inject: str | None) -> None:
    from langchain_core.documents import Document

    from src.core.vector_store import upsert_documents

    state.owned_keys.append(key)
    docs = list(state.corpus_docs[corpus])
    if inject:
        docs.append(
            Document(
                page_content=inject,
                metadata={
                    "persist_key": f"eval_{corpus}",
                    "chunk_index": len(docs),
                    "source_type": corpus,
                    "source_title": "eval-injection",
                },
            )
        )
    upsert_documents(key, docs, rebuild=True)


def _case_hash(case_id: str) -> str:
    return hashlib.md5(case_id.encode("utf-8")).hexdigest()


def install_fixtures(stack: contextlib.ExitStack, case: dict, state: RunState) -> None:
    import src.agents.current_affairs.ingest as ca_ingest
    import src.agents.planner.graph as planner_graph
    import src.agents.upload.graph as upload_graph
    import src.core.mentor_kb as mentor_kb
    import src.core.vector_store as vs
    import src.core.web_search as web_search

    inputs = case.get("inputs", {})

    results = [web_search.SearchResult.from_dict(r) for r in inputs.get("web_results", [])]
    results = [r for r in results if r]
    stack.enter_context(mock.patch.object(web_search, "search_web", lambda q, **kw: list(results)))
    headlines = inputs.get("headlines", "")
    stack.enter_context(mock.patch.object(ca_ingest, "fetch_headlines", lambda limit=25: headlines))
    stack.enter_context(
        mock.patch.object(planner_graph, "_fetch_upsc_prelims_date", lambda year: "")
    )
    stack.enter_context(
        mock.patch.object(upload_graph, "_upload_cache", upload_graph._UploadCache())
    )

    real_scored = vs._plain_scored_search

    def _capture(db, query, k):
        rows = real_scored(db, query, k)
        for doc, _score in rows:
            md = getattr(doc, "metadata", {}) or {}
            ident = (
                f"{md.get('source_title') or md.get('document') or 'chunk'}#{md.get('chunk_index')}"
            )
            state.retrieved.append((ident, doc.page_content or ""))
        return rows

    stack.enter_context(mock.patch.object(vs, "_plain_scored_search", _capture))

    if inputs.get("kb_injection"):
        real_search_kb = mentor_kb.search_kb
        injected = inputs["kb_injection"]

        def _search_kb(query, k=4, min_score=None):
            res = dict(real_search_kb(query, k=k, min_score=min_score))
            res["context"] = (
                (res.get("context") or "") + "\n\n[eval-injection] " + injected
            ).strip()
            state.retrieved.append(("mentor_kb:eval-injection", injected))
            return res

        stack.enter_context(mock.patch.object(mentor_kb, "search_kb", _search_kb))


# --------------------------------------------------------------------------- #
# Ops: each calls the production function and returns (output, extra)
# --------------------------------------------------------------------------- #
def _join(gen) -> str:
    return "".join(str(x) for x in gen)


def _op_mentor_agent(case, state):
    from src.core.scope_gate import check_scope
    from src.graph.mentor_graph import build_mentor_graph

    q = case["inputs"]["question"]
    decision = check_scope(q)
    if decision.blocked:  # production parity: the chat route answers with the fixed reply
        return decision.reply, {"reached": "src.core.scope_gate.check_scope", "scope": decision}
    result = build_mentor_graph().invoke(
        {"question": q, "student_context": case["inputs"].get("profile")}
    )
    return result.get("answer") or "", {"reached": "src.graph.mentor_graph.build_mentor_graph"}


def _op_mentor_route(case, state):
    import src.graph.supervisor as supervisor

    q = case["inputs"]["question"]
    try:  # same call and fallback as supervisor_node (src/graph/supervisor.py)
        route = (
            supervisor._structured_llm(supervisor.RouteDecision)
            .invoke([("system", supervisor._SUP_SYS), ("human", q)])
            .route
        )
        err = False
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "abort_run", False):
            raise
        route, err = "mentor", True
    return route, {
        "reached": "src.graph.supervisor._structured_llm",
        "route": route,
        "route_error": err,
    }


def _op_mentor_scope(case, state):
    from src.core.scope_gate import check_scope

    decision = check_scope(case["inputs"]["text"])
    return decision.reply, {"reached": "src.core.scope_gate.check_scope", "scope": decision}


def _op_ncert_ask(case, state):
    from src.agents.ncert.graph import ask_ncert

    out = _join(ask_ncert(case["inputs"]["question"], NCERT_CLASS, NCERT_SUBJECT, NCERT_CHAPTER))
    return out, {"reached": "src.agents.ncert.graph.ask_ncert"}


def _op_upload_ask(case, state):
    from src.agents.upload.graph import _upload_key, ask_upload

    uid = f"{state.ctx.eval_user_id}-{case['id']}"
    pdf_hash = _case_hash(case["id"])
    _seed_owned(state, "upload", _upload_key(uid, pdf_hash), case["inputs"].get("inject_doc"))
    out = _join(
        ask_upload(
            case["inputs"]["question"], pdf_hash, case["inputs"].get("book_info"), None, user_id=uid
        )
    )
    return out, {"reached": "src.agents.upload.graph.ask_upload"}


def _op_upload_notes(case, state):
    import src.agents.upload.graph as upload_graph

    uid = f"{state.ctx.eval_user_id}-{case['id']}"
    text = (REPO_ROOT / CORPORA["upload"]["path"]).read_text(encoding="utf-8")
    pdf_hash = _case_hash(case["id"])
    real_create = upload_graph.create_vector_store

    def _create(text, persist_key="", metadata=None):
        state.owned_keys.append(persist_key)  # the key process_upload builds itself
        return real_create(text, persist_key=persist_key, metadata=metadata)

    with (
        mock.patch.object(
            upload_graph, "extract_pdf_text", lambda content, filename: (text, pdf_hash)
        ),
        mock.patch.object(upload_graph, "create_vector_store", _create),
    ):
        result = upload_graph.process_upload(
            b"%PDF-eval-fixture", case["inputs"].get("filename", "eval.pdf"), user_id=uid
        )
    return result.get("notes") or "", {"reached": "src.agents.upload.graph.process_upload"}


def _op_lecture_ask(case, state):
    from src.agents.lecture.graph import _lecture_key, ask_lecture

    uid = f"{state.ctx.eval_user_id}-{case['id']}"
    video_id = "paste_" + _case_hash(case["id"])[:11]
    _seed_owned(state, "lecture", _lecture_key(video_id, uid), case["inputs"].get("inject_doc"))
    out = _join(
        ask_lecture(
            case["inputs"]["question"],
            video_id,
            case["inputs"].get("topic_info"),
            None,
            user_id=uid,
        )
    )
    return out, {"reached": "src.agents.lecture.graph.ask_lecture"}


def _op_lecture_notes(case, state):
    from src.agents.lecture.graph import process_lecture_from_text

    transcript = (REPO_ROOT / CORPORA["lecture"]["path"]).read_text(encoding="utf-8")
    result = process_lecture_from_text(transcript, case["inputs"].get("medium", "English"))
    return result.get("notes") or "", {
        "reached": "src.agents.lecture.graph.process_lecture_from_text"
    }


def _op_pyq_generate(case, state):
    from src.agents.pyq.graph import generate_questions

    i = case["inputs"]
    out = _join(
        generate_questions(
            i["topic"],
            i.get("question_type", "mcq"),
            i.get("difficulty", "medium"),
            int(i.get("num_questions", 2)),
            int(i.get("marks", 10)),
        )
    )
    return out, {"reached": "src.agents.pyq.graph.generate_questions"}


def _op_pyq_bank(case, state):
    from src.agents.pyq.graph import _bank_key, generate_from_bank

    i = case["inputs"]
    uid = f"{state.ctx.eval_user_id}-{case['id']}"
    _seed_owned(state, "pyq_bank", _bank_key(uid), i.get("inject_doc"))
    out = _join(
        generate_from_bank(
            uid,
            i.get("topic", ""),
            i.get("question_type", "mcq"),
            int(i.get("num_questions", 2)),
            int(i.get("marks", 10)),
            i.get("difficulty", "medium"),
        )
    )
    return out, {"reached": "src.agents.pyq.graph.generate_from_bank"}


def _op_pyq_parse(case, state):
    from src.agents.pyq.graph import parse_questions

    out = json.dumps(parse_questions(case["inputs"]["text"]), ensure_ascii=False)
    return out, {"reached": "src.agents.pyq.graph.parse_questions"}


def _op_eval_basic(case, state):
    from src.agents.evaluator.graph import evaluate_answer
    from src.core.eval_parse import parse_answer_evaluation

    i = case["inputs"]
    out = _join(evaluate_answer(i["question"], i["answer"]))
    ev = parse_answer_evaluation(out)
    return out, {
        "reached": "src.agents.evaluator.graph.evaluate_answer",
        "score": ev.score,
        "max_score": ev.max_score,
    }


def _op_eval_mains(case, state):
    from src.agents.evaluator.graph import evaluate_mains
    from src.core.eval_parse import parse_mains_evaluation

    i = case["inputs"]
    marks = int(i.get("marks", 10))
    out = _join(evaluate_mains(i["question"], i["answer"], marks))
    ev = parse_mains_evaluation(out, max_marks=marks)
    return out, {
        "reached": "src.agents.evaluator.graph.evaluate_mains",
        "score": ev.score,
        "max_score": ev.max_marks,
        "requested_max": marks,
    }


def _op_ca_daily(case, state):
    from src.agents.current_affairs.graph import get_daily_ca

    return _join(get_daily_ca("")), {"reached": "src.agents.current_affairs.graph.get_daily_ca"}


def _op_ca_editorial(case, state):
    from src.agents.current_affairs.graph import get_editorial

    out = _join(get_editorial(case["inputs"]["topic"]))
    return out, {"reached": "src.agents.current_affairs.graph.get_editorial"}


def _op_planner(case, state):
    from src.agents.planner.graph import generate_plan

    i = case["inputs"]
    out = _join(
        generate_plan(
            i["goal"],
            i["hours"],
            i.get("optional", ""),
            i.get("weak", ""),
            i.get("attempt_number", "1"),
        )
    )
    return out, {"reached": "src.agents.planner.graph.generate_plan"}


OPS = {
    ("mentor", "agent"): _op_mentor_agent,
    ("mentor", "route"): _op_mentor_route,
    ("mentor", "scope"): _op_mentor_scope,
    ("ncert", "ask"): _op_ncert_ask,
    ("upload", "ask"): _op_upload_ask,
    ("upload", "notes"): _op_upload_notes,
    ("lecture", "ask"): _op_lecture_ask,
    ("lecture", "notes"): _op_lecture_notes,
    ("pyq", "generate"): _op_pyq_generate,
    ("pyq", "bank"): _op_pyq_bank,
    ("pyq", "parse"): _op_pyq_parse,
    ("evaluator", "basic"): _op_eval_basic,
    ("evaluator", "mains"): _op_eval_mains,
    ("current_affairs", "daily"): _op_ca_daily,
    ("current_affairs", "editorial"): _op_ca_editorial,
    ("planner", "plan"): _op_planner,
}
WORKFLOWS = (
    "mentor",
    "ncert",
    "pyq",
    "evaluator",
    "current_affairs",
    "planner",
    "lecture",
    "upload",
)
NO_LLM_OPS = {("mentor", "scope")}


def case_question(case: dict) -> str:
    i = case.get("inputs", {})
    return i.get("question") or i.get("topic") or i.get("goal") or i.get("text") or case["id"]


@dataclass
class CaseRun:
    case: dict
    output: str
    extra: dict
    prompts: PromptRecorder
    retrieved: list  # (id, text) - in memory only

    @property
    def llm_calls(self) -> int:
        return len(self.prompts.calls)


def run_case(case: dict, state: RunState) -> CaseRun:
    key = (case["workflow"], case["op"])
    if key not in OPS:
        raise ValueError(f"{case['id']}: unknown workflow/op {key}")
    recorder = PromptRecorder()
    state.retrieved = []
    with contextlib.ExitStack() as stack:
        if state.mode == "offline":
            install_scripted_models(stack, Script(case.get("script")), recorder)
        install_fixtures(stack, case, state)
        output, extra = OPS[key](case, state)
    return CaseRun(case, output or "", extra, recorder, list(state.retrieved))


def load_cases(paths: list[Path] | None = None) -> list[dict]:
    paths = paths or [DATASET_DIR / "workflow_cases.json", DATASET_DIR / "redteam_cases.json"]
    cases = []
    for p in paths:
        cases.extend(json.loads(Path(p).read_text(encoding="utf-8"))["cases"])
    return cases


def validate_cases(cases: list[dict]) -> list[str]:
    errors, ids = [], set()
    for c in cases:
        cid = c.get("id", "?")
        if cid in ids:
            errors.append(f"{cid}: duplicate id")
        ids.add(cid)
        for f in ("workflow", "kind", "op", "inputs", "checks", "author", "human_verified"):
            if f not in c:
                errors.append(f"{cid}: missing {f}")
        if c.get("workflow") not in WORKFLOWS:
            errors.append(f"{cid}: unknown workflow {c.get('workflow')!r}")
        if (c.get("workflow"), c.get("op")) not in OPS:
            errors.append(f"{cid}: unknown op {c.get('op')!r}")
        if c.get("kind") not in ("quality", "safety", "routing", "abstain"):
            errors.append(f"{cid}: bad kind {c.get('kind')!r}")
        gap = c.get("known_gap")
        if gap is not None and not (
            isinstance(gap, dict)
            and gap.get("note")
            and gap.get("check") in {chk.get("type") for chk in c.get("checks", [])}
        ):
            errors.append(f"{cid}: known_gap must name one of the case's check types and a note")
        if c.get("human_verified") is not False:
            errors.append(f"{cid}: Claude-written cases must keep human_verified=false")
    return errors
