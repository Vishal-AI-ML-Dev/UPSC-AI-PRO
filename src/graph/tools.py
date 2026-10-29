"""Real tool-calling for the UPSC agent stack.

Until now the graph decided *for* the model whether to search the web (a single
boolean ``needs_web_search`` flag in ``mentor_graph`` that fired one hard-coded
search). This module gives the **model** genuine, structured tools and runs a
proper tool-execution loop, so the LLM itself chooses which tool to call, with
what arguments, and how many times.

Layers (kept deliberately separate so the useful bits are testable offline):

1. **Plain tool implementations** - ordinary Python functions with lazy heavy
   imports (``web_search_tool_fn``, ``knowledge_base_tool_fn``). Importing this
   module pulls in *no* LLM / vector / langchain dependency.
2. **Pure dispatch** - ``run_tool_calls`` maps model-emitted tool calls to
   result dicts, isolating unknown-tool and tool-exception handling from any
   LLM. This is the part covered by offline unit tests.
3. **LangChain binding** - ``get_structured_tools`` / ``bind_tools_with_fallback``
   build the ``@tool`` objects and bind them to the (fallback-wrapped) chat
   model. Imported lazily so step 1/2 stay dependency-free.
4. **Reusable agent** - ``build_tool_agent`` compiles a small ReAct-style
   LangGraph loop (model <-> tools) over the shared ``AgentState``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.core.grounding import (
    citations_from_tool_messages,
    filter_kb_citations_by_overlap,
    format_sources,
)
from src.core.model_router import describe_route

logger = logging.getLogger(__name__)

# Cap on model<->tools round-trips inside build_tool_agent, so a misbehaving
# model cannot loop forever calling tools.
DEFAULT_MAX_TOOL_LOOPS = 3

DEFAULT_TOOL_SYSTEM = (
    "You are Arjun, a UPSC preparation mentor. You have tools available. "
    "Call `web_search` ONLY for current/volatile facts (exam dates, "
    "notifications, results, cut-offs, vacancies, recent news). Call "
    "`knowledge_base_search` to ground concept explanations in verified "
    "material. Do NOT call a tool for greetings, motivation, or things you "
    "already know. For greetings, small talk, or vague one-liners such as "
    "'hi', 'hello', or 'hey', reply in ONE short, warm sentence and invite "
    "the student to pick a topic (study plan, NCERT/GS doubts, current "
    "affairs, PYQ practice, or mains answer evaluation); do NOT dump "
    "generic exam-pattern, syllabus, or eligibility information unless they "
    "actually ask for it. After using tools, answer concisely in the student's "
    "language and cite any source labels returned by the tools. "
    "CRITICAL - never fabricate time-sensitive facts: for exam dates, "
    "results, notifications, cut-offs or vacancies, rely ONLY on the "
    "web_search output. If web_search returns no results, an error, or "
    "nothing relevant, clearly tell the student you could NOT fetch live "
    "information right now and point them to the official source "
    "(upsc.gov.in). NEVER state or guess such dated facts from your own "
    "memory - your training data is stale and will be wrong. "
    "GRACEFUL FALLBACK - the knowledge base is a bonus, not a gate: if "
    "`knowledge_base_search` returns no passages or a NOTE that it is "
    "unavailable, still answer conceptual, timeless questions (definitions, "
    "concepts, history, polity, strategy) from your general knowledge; when such "
    "an answer gives specific article numbers, years, case names, figures or "
    "scheme details, add one short line saying these are from general knowledge "
    "and should be checked in a standard source. "
    "Do NOT reply that you cannot help or tell the student to try later for "
    "such questions - only do that for the time-sensitive facts listed above. "
    "Never mention internal tools, errors, or outages to the student."
)


# --------------------------------------------------------------------------- #
# 1. Plain tool implementations (lazy heavy imports)
# --------------------------------------------------------------------------- #
def web_search_tool_fn(query: str) -> str:
    """Live web search over trusted UPSC/news sources. Returns a text digest.

    Degrades gracefully: if the search backend is unconfigured (e.g. missing
    TAVILY_API_KEY) or returns nothing, we return an honest "no results"
    string instead of raising, so the model can admit it lacks live data
    rather than answering a dated fact from stale memory.
    """
    query = (query or "").strip()
    if not query:
        return "No query provided."
    try:
        from src.agents.mentor.graph import _fetch_search_context

        result = _fetch_search_context(query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("web_search unavailable: %s", exc)
        result = ""
    if result:
        # Live web text is UNTRUSTED - fence it before the model reads it.
        from src.core.prompt_safety import harden_untrusted

        return harden_untrusted(result, label="live web search results")
    return (
        "No live web results found (search backend unavailable or returned "
        "nothing). Do not answer dated facts from memory."
    )


# Jab KB me kuch na mile ya fail ho -- model ko clearly bolo ki apne knowledge se
# jawab de (apology mat de). Yahi "LLM fallback" hai: KB bonus hai, gate nahi.
KB_FALLBACK_NOTE = (
    "NOTE: no matching passages were found in the knowledge base for this query. "
    "If this is a conceptual, timeless, or explanatory question (definitions, "
    "polity, history, geography, economy, concepts, strategy, judgments), answer it "
    "from your general knowledge, keeping to well-established points. No source "
    "passage backs this answer, so if you give specific article numbers, years, "
    "case names, figures or scheme details, add one short line saying these are "
    "from general knowledge and should be checked in a standard source. Do NOT tell "
    "the student you cannot help, cannot access the knowledge base, or to try again "
    "later, and do NOT mention this note or any tool/outage. ONLY for time-sensitive "
    "facts (exam dates, results, cut-offs, vacancies, notifications) should you "
    "defer to official sources instead of answering from memory."
)

# Same fallback when the knowledge base itself cannot be searched.
KB_UNAVAILABLE_NOTE = (
    "NOTE: the knowledge base is temporarily unavailable. If this is a conceptual "
    "or timeless question, answer it from your general knowledge; if you give "
    "specific article numbers, years, case names, figures or scheme details, add "
    "one short line saying these are from general knowledge and should be checked "
    "in a standard source. Do not mention this outage."
)


def knowledge_base_tool_fn(query: str, persist_key: str = "") -> str:
    """Grounded retrieval.

    With a ``persist_key`` -> search that specific document collection
    (NCERT chapter / lecture / upload). Without one -> search the durable
    Mentor KB. Returns source-labelled context text.
    """
    query = (query or "").strip()
    if not query:
        return "No query provided."

    persist_key = (persist_key or "").strip()
    if not persist_key:
        from src.core import mentor_kb

        try:
            kb = mentor_kb.search_kb(query, k=4)
        except Exception as exc:  # noqa: BLE001
            logger.warning("knowledge_base_search (mentor KB) unavailable: %s", exc)
            return KB_UNAVAILABLE_NOTE
        context = kb.get("context")
        if not context:
            return KB_FALLBACK_NOTE
        # KB passages originate from ingested (untrusted) documents - fence them.
        from src.core.prompt_safety import harden_untrusted

        return harden_untrusted(context, label="knowledge base excerpt")

    from src.core.vector_store import load_vector_store

    db = load_vector_store(persist_key)
    if db is None:
        return KB_FALLBACK_NOTE
    try:
        scored = db.similarity_search_with_relevance_scores(query, k=4)
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge_base_search failed for %s: %s", persist_key, exc)
        return KB_FALLBACK_NOTE

    parts = []
    for doc, _score in scored:
        meta = getattr(doc, "metadata", None) or {}
        src = (
            meta.get("source_title")
            or meta.get("filename")
            or meta.get("source")
            or meta.get("chapter")
        )
        prefix = f"[{src}] " if src else ""
        parts.append(f"{prefix}{doc.page_content}")
    if not parts:
        return KB_FALLBACK_NOTE
    # Retrieved document text is untrusted - fence it before the model reads it.
    from src.core.prompt_safety import harden_untrusted

    return harden_untrusted("\n\n".join(parts), label="knowledge base excerpt")


# Registry: tool name -> plain callable. Names MUST match get_structured_tools().
TOOL_FUNCTIONS: dict[str, Callable[..., str]] = {
    "web_search": web_search_tool_fn,
    "knowledge_base_search": knowledge_base_tool_fn,
}


# --------------------------------------------------------------------------- #
# 2. Pure dispatch (offline-testable; no LLM, no langchain)
# --------------------------------------------------------------------------- #
def run_tool_calls(
    tool_calls: list[dict],
    registry: Optional[dict[str, Callable[..., str]]] = None,
) -> list[dict]:
    """Execute model-emitted tool calls against a registry.

    Args:
        tool_calls: list of ``{"name": str, "args": dict, "id": str}`` (the
            shape LangChain puts on ``AIMessage.tool_calls``).
        registry: name -> callable. Defaults to ``TOOL_FUNCTIONS``.

    Returns:
        One result dict per call: ``{"tool_call_id", "name", "content"}``.
        Unknown tools and tool exceptions are converted into ``ERROR: ...``
        content instead of raising, so the loop can hand them back to the model.
    """
    reg = TOOL_FUNCTIONS if registry is None else registry
    results: list[dict] = []
    for call in tool_calls or []:
        name = call.get("name", "") if isinstance(call, dict) else ""
        args = (call.get("args") if isinstance(call, dict) else None) or {}
        call_id = call.get("id", "") if isinstance(call, dict) else ""
        fn = reg.get(name)
        if fn is None:
            available = ", ".join(sorted(reg)) or "(none)"
            content = f"ERROR: unknown tool '{name}'. Available tools: {available}."
        elif not isinstance(args, dict):
            content = f"ERROR: tool '{name}' expects an object of arguments."
        else:
            try:
                content = str(fn(**args))
            except TypeError as exc:
                content = f"ERROR: bad arguments for tool '{name}': {exc}"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tool '%s' raised: %s", name, exc)
                # KB fail ho to bhi model apne knowledge se jawab de (apology nahi).
                if name == "knowledge_base_search":
                    content = KB_FALLBACK_NOTE
                else:
                    content = f"ERROR: tool '{name}' failed: {exc}"
        results.append({"tool_call_id": call_id, "name": name, "content": content})
    return results


def _content_to_text(content) -> str:
    """Flatten an LLM message ``content`` into plain text.

    Gemini (and other multimodal models) can return ``content`` as a list of
    block dicts like ``[{"type": "text", "text": "...", "extras": {...}}]``
    instead of a plain string. Concatenate the text parts so the final
    ``answer`` is always a clean string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                piece = block.get("text")
                if isinstance(piece, str) and piece:
                    parts.append(piece)
        return "".join(parts)
    return str(content)


def _message_type(message) -> str:
    """Duck-typed message kind ("human"/"ai"/"tool"/...) without importing
    langchain, so this stays offline-testable."""
    return getattr(message, "type", "") or ""


def _should_seed_question(history: list, question) -> bool:
    """Decide whether to inject ``question`` as a fresh human turn.

    Handles the checkpointed-thread case: a NEW question arriving on a thread
    that already has prior messages must still be added. Rules:
      * no question -> False
      * empty history -> True
      * just came back from a tool (last msg is a tool message) -> False
      * question already the latest human message -> False
      * otherwise -> True
    """
    if not question:
        return False
    if not history:
        return True
    if _message_type(history[-1]) == "tool":
        return False
    for message in reversed(history):
        if _message_type(message) == "human":
            return _content_to_text(getattr(message, "content", "")) != question
    return True


def to_tool_messages(results: list[dict]) -> list:
    """Convert ``run_tool_calls`` output into LangChain ``ToolMessage`` objects."""
    from langchain_core.messages import ToolMessage

    return [
        ToolMessage(
            content=r["content"],
            tool_call_id=r.get("tool_call_id", ""),
            name=r.get("name", ""),
        )
        for r in results
    ]


# --------------------------------------------------------------------------- #
# 3. LangChain binding (lazy)
# --------------------------------------------------------------------------- #
def get_structured_tools() -> list:
    """Build the LangChain ``@tool`` objects the model can be bound to.

    Tool names/args here are the contract the model sees; their bodies delegate
    to the plain functions above so behaviour is identical to ``run_tool_calls``.
    """
    from langchain_core.tools import tool

    @tool
    def web_search(query: str) -> str:
        """Search trusted UPSC and news sources for current, time-sensitive facts
        such as exam dates, notifications, results, cut-offs, or vacancies."""
        return web_search_tool_fn(query)

    @tool
    def knowledge_base_search(query: str, persist_key: str = "") -> str:
        """Retrieve grounded, source-labelled passages. Pass persist_key to
        search a specific document (NCERT chapter/lecture/upload); leave it empty
        to search the verified Mentor knowledge base."""
        return knowledge_base_tool_fn(query, persist_key)

    return [web_search, knowledge_base_search]


def bind_tools_with_fallback(llm: Any = None, tools: Optional[list] = None):
    """Bind tools to the chat model, tolerating the fallback wrapper.

    ``get_llm()`` may return a ``RunnableWithFallbacks`` which does not expose
    ``bind_tools``. Mirror the project's structured-output pattern: bind to the
    primary runnable and each fallback individually, then re-attach fallbacks.
    """
    from src.core.llm import get_llm

    base = llm if llm is not None else get_llm()
    tools = tools if tools is not None else get_structured_tools()

    if hasattr(base, "bind_tools"):
        return base.bind_tools(tools)
    # RunnableWithFallbacks: bind on primary + each fallback.
    primary = base.runnable.bind_tools(tools)
    fallbacks = [r.bind_tools(tools) for r in base.fallbacks]
    return primary.with_fallbacks(fallbacks)


# --------------------------------------------------------------------------- #
# 4. Reusable ReAct-style tool-calling agent (LangGraph)
# --------------------------------------------------------------------------- #
def _compose_system(system_prompt: str, extra) -> str:
    """Fold an optional per-request context block into the base system prompt.

    Pure + offline-testable: the tool agent calls this to merge caller-supplied
    context (e.g. a student profile) into the system message, so callers need
    not know the base prompt. Empty/whitespace extra returns the base unchanged.
    """
    text = extra if isinstance(extra, str) else (str(extra) if extra else "")
    text = text.strip()
    if not text:
        return system_prompt
    return f"{system_prompt}\n\n{text}"


def _today_line() -> str:
    """Current-date context so the model uses correct past/future tense.

    The model has no inherent sense of "today", so it can describe an exam that
    has already happened as if it were still upcoming. We inject the current
    date (IST, fixed UTC+5:30 - India has no DST) on every turn and instruct the
    model to compare event dates against it.
    """
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist)
    return (
        f"Today's date is {today:%A, %d %B %Y} (IST). When you mention any "
        "date-bound event (exam, result, notification, deadline, cut-off), "
        "compare its date to today and use the correct tense: if it is before "
        "today, state clearly that it has ALREADY happened (past tense); if it "
        "is after today, say it is upcoming. Never describe a past event as if "
        "it is still going to happen."
    )


def build_tool_agent(
    tools: Optional[list] = None,
    checkpointer=None,
    max_tool_loops: int = DEFAULT_MAX_TOOL_LOOPS,
    system_prompt: str = DEFAULT_TOOL_SYSTEM,
    extra_context: Optional[Callable] = None,
):
    """Compile a model<->tools loop over the shared ``AgentState``.

    Flow::

        START -> agent -> (tool_calls? -> tools -> agent)* -> END

    The agent seeds the conversation from ``state['messages']`` (or
    ``state['question']`` on the first turn), lets the model optionally emit
    tool calls, executes them via ``run_tool_calls``, and loops back until the
    model answers with no tool calls or ``max_tool_loops`` is reached. The final
    answer is written to ``state['answer']``.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import END, START, StateGraph

    from src.graph.state import AgentState
    # NOTE: keep AgentState OFF the inner node/branch signatures below.
    # With `from __future__ import annotations` LangGraph's schema inference
    # runs get_type_hints() against MODULE globals, where this locally-imported
    # name does not exist -> NameError. StateGraph(AgentState) already sets it.

    tools = tools if tools is not None else get_structured_tools()

    # Resolve the LLM lazily on first invoke rather than at graph-build time.
    # Building the graph must NOT require an LLM provider / API keys: otherwise
    # merely constructing the app (supervisor startup, or smoke tests that boot
    # the app with no GOOGLE_API_KEY/GROQ_API_KEY) crashes with "No LLM provider
    # available". Models are cached after first resolution -> built once per graph.
    # Cache (bound, base) model pair PER tier - resolved lazily on first use of
    # that tier. Building the graph still needs no API key; each tier's chain is
    # only created when a request actually routes to it, then reused.
    _models: dict = {}

    def _resolve_models(tier: str):
        if tier not in _models:
            from src.core.llm import get_llm_for_tier

            base = get_llm_for_tier(tier)  # tool-free model for the forced final answer
            _models[tier] = (bind_tools_with_fallback(llm=base, tools=tools), base)
        return _models[tier]

    def agent_node(state) -> dict:
        question = state.get("question")
        # Route by query complexity: trivial turns -> lite (fast/cheap), reasoning
        # or volatile-lookup turns -> strong. Tools are exposed here, so
        # date/result/news lookups get the strong model for dependable tool use.
        tier, why = describe_route(question or "", has_tools=True)
        logger.info("mentor model tier=%s (%s)", tier, why)
        model, base_model = _resolve_models(tier)
        history = list(state.get("messages") or [])
        new_messages = []
        if _should_seed_question(history, question):
            # Fresh user turn (incl. a NEW question on an existing
            # checkpointed thread): inject it so the model actually sees it.
            # Do NOT re-seed when returning from a tool call.
            human = HumanMessage(content=question)
            new_messages.append(human)
            history = history + [human]
        rounds = sum(1 for m in history if getattr(m, "tool_calls", None))
        # At the loop cap, answer WITHOUT tools so we never terminate on a
        # dangling tool call with no final answer.
        active = model if rounds < max_tool_loops else base_model
        # Always give the model today's date so it phrases past vs. upcoming
        # events with the correct tense (it has no built-in sense of "today").
        sys_text = _compose_system(system_prompt, _today_line())
        if extra_context is not None:
            try:
                sys_text = _compose_system(sys_text, extra_context(state))
            except Exception as exc:  # noqa: BLE001
                logger.warning("extra_context failed: %s", exc)
        resp = active.invoke([SystemMessage(content=sys_text), *history])
        new_messages.append(resp)
        out: dict = {"messages": new_messages}
        if not getattr(resp, "tool_calls", None):
            answer = _content_to_text(getattr(resp, "content", resp))
            # Attribute the answer to the sources the model actually used
            # (web_search URLs / knowledge_base_search labels). Deterministic,
            # no extra LLM call - citations land on every grounded answer.
            citations = citations_from_tool_messages(history)
            # Honesty guard: only keep a KB source if the answer actually used
            # its content (prevents a strategy note being cited under an answer
            # the model wrote from its own knowledge).
            citations = filter_kb_citations_by_overlap(answer, history, citations)
            if citations:
                answer = answer + format_sources(citations)
            out["answer"] = answer
            out["citations"] = citations
        return out

    def tools_node(state) -> dict:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        results = run_tool_calls(
            [{"name": c["name"], "args": c.get("args", {}), "id": c.get("id", "")} for c in calls]
        )
        return {"messages": to_tool_messages(results)}

    def should_continue(state) -> str:
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        if last is None or not getattr(last, "tool_calls", None):
            return END
        # Count how many tool-execution rounds already happened; stop at the cap.
        rounds = sum(1 for m in msgs if getattr(m, "tool_calls", None))
        if rounds > max_tool_loops:
            logger.warning("Tool loop cap (%s) reached; ending.", max_tool_loops)
            return END
        return "tools"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


# ============================ Local smoke test ================================
if __name__ == "__main__":  # pragma: no cover
    from langgraph.checkpoint.memory import InMemorySaver

    app = build_tool_agent(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "tool-agent-1"}}
    for q in [
        "Motivate me, I'm feeling low today.",  # -> no tool
        "When is the UPSC Prelims 2026 exam scheduled?",  # -> web_search
    ]:
        result = app.invoke({"question": q}, cfg)
        print(f"\nQ: {q}\nA: {result.get('answer')}\n")
