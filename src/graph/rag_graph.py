"""Reusable RAG subgraph factory with CRAG self-correction.

Where it runs: ONLY in the LLM eval harness (``src/eval/llm_eval.py``, ADR-003).
No HTTP route reaches it: NCERT, Lecture, Upload and PYQ answer through their own
functions in ``src/agents/<feature>/graph.py``, and the Mentor chat supervisor has
no ``rag`` route since Phase 05 (ADR-014). The caller places a Qdrant collection
key on ``state['persist_key']``; the subgraph loads that collection and answers
from it. Any future HTTP caller must build that key server-side from the
authenticated user (ADR-007), never take it from the client or the model.

Graph flow (Corrective RAG / CRAG)::

    START -> retrieve -> grade -> (web_search?) -> generate -> verify_grounding -> END

Nodes:
    retrieve   Load the vector collection for ``state['persist_key']`` and run
               a relevance-scored search via ``similarity_search_with_sources``
               (re-using the existing 0.3 threshold + ``grounded`` flag).
    grade      Structured-output LLM judges whether the retrieved context can
               actually answer the question (the corrective step that catches
               weak/irrelevant retrievals before they reach generation).
    web_search Optional fallback to live web context when retrieval is not
               relevant, re-using the mentor module's search helper. The live
               web text is untrusted, so it is wrapped with ``harden_untrusted``
               before it is ever placed on the state / shown to the model.
    generate   Produce a grounded answer, instructed to never fabricate facts.
    verify_grounding  Check the final answer against available evidence and append
               a compact trust note (confidence + sources) without fabricating citations.

The default prompt is a generic grounded-RAG template. To preserve an agent's
bespoke prompt (e.g. NCERT's subject/paper framing), pass that agent's
``ChatPromptTemplate`` as ``generate_prompt`` together with an ``input_builder``
that maps the state into that prompt's input variables.

Citation / trust-note formatting lives in ``src.core.grounding`` (pure, offline
tested) so the RAG subgraph and the mentor tool-agent share one implementation.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Literal, Optional

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

# Re-use the mentor module's web search helper for the CRAG fallback.
from src.agents.mentor.graph import _fetch_search_context
from src.core.grounding import (
    derive_confidence,
    extract_web_citations,
)
from src.core.grounding import (
    format_sources as _format_sources,
)
from src.core.grounding import (
    format_trust_note as _format_trust_note,
)

# Citation + trust-note formatting is centralised in src.core.grounding so the
# RAG subgraph and the mentor tool-agent share exactly one implementation. The
# private aliases keep the in-graph call sites (and existing tests that import
# ``_format_sources`` from this module) unchanged.
from src.core.llm import get_fast_llm, get_llm, message_text
from src.core.prompt_safety import harden_untrusted
from src.core.vector_store import (
    load_vector_store,
    similarity_search_with_sources,
)
from src.graph.reflection import make_reflect_node
from src.graph.state import AgentState

logger = logging.getLogger(__name__)


# ============================ Default grounded prompt ==========================
GENERIC_RAG_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a precise UPSC study assistant. Answer ONLY using the "
            "provided context and web results. If neither contains the answer, "
            "clearly say you do not have enough grounded information and advise "
            "verifying with a standard source. Never invent facts, dates, names, "
            "or article numbers.",
        ),
        (
            "human",
            "Question:\n{question}\n\n"
            "Retrieved context:\n{context}\n\n"
            "Web results (may be empty):\n{web_context}\n\n"
            "Write a clear, syllabus-focused answer with key points.",
        ),
    ]
)


def _default_input_builder(state: AgentState) -> dict:
    """Map graph state into the variables expected by ``GENERIC_RAG_PROMPT``."""
    return {
        "question": state.get("question", ""),
        "context": state.get("kb_context") or "No grounded context found.",
        "web_context": state.get("search_results") or "No web results available.",
    }


class GroundingCheck(BaseModel):
    """Post-generation grounding check for the final answer."""

    confidence: Literal["high", "medium", "low"] = Field(
        ...,
        description="High only when the answer is fully supported by supplied evidence; low when evidence is weak or unavailable.",
    )
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="Specific factual claims in the answer that are not supported by the supplied evidence.",
    )


_VERIFY_SYS = (
    "You are a strict grounding verifier for a UPSC RAG assistant. Compare the "
    "ANSWER against the supplied EVIDENCE. Mark confidence high only when the "
    "answer is directly supported. Mark medium for partial support or minor "
    "general UPSC framing. Mark low when evidence is missing, weak, or the answer "
    "contains unsupported factual claims. List only concrete unsupported claims; "
    "do not penalize harmless wording, structure, or study advice."
)


# ============================ Structured grader ================================
class RelevanceGrade(BaseModel):
    """CRAG grade: can the retrieved context answer the question?"""

    relevant: bool = Field(
        ...,
        description=(
            "True only if the retrieved context contains enough information to "
            "directly answer the question. False if it is empty, off-topic, or "
            "insufficient."
        ),
    )


_GRADE_SYS = (
    "You are a strict retrieval grader for a UPSC assistant. Decide whether the "
    "retrieved context is sufficient and on-topic to answer the question. Be "
    "conservative: if the context is empty or only loosely related, mark it not "
    "relevant so the system can fall back to a web search."
)


def _structured_llm(schema):
    """Return a structured-output runnable that tolerates fallback wrappers.

    ``get_fast_llm()`` may return either a raw chat model or a
    ``RunnableWithFallbacks`` (which does not expose ``with_structured_output``).
    In the latter case, structured output is applied to the primary runnable and
    each fallback individually.
    """
    base = get_fast_llm()
    if hasattr(base, "with_structured_output"):
        return base.with_structured_output(schema)
    primary = base.runnable.with_structured_output(schema)
    fallbacks = [r.with_structured_output(schema) for r in base.fallbacks]
    return primary.with_fallbacks(fallbacks)


# ============================ Factory ==========================================
def build_rag_subgraph(
    *,
    label: str = "rag",
    generate_prompt: ChatPromptTemplate = GENERIC_RAG_PROMPT,
    input_builder: Callable[[AgentState], dict] = _default_input_builder,
    k: int = 5,
    allow_web_fallback: bool = True,
    checkpointer=None,
    enable_reflection: Optional[bool] = None,
):
    """Build and compile a reusable Corrective-RAG subgraph.

    Args:
        label: Short tag used for retrieval observability metrics.
        generate_prompt: Prompt used for answer generation. Defaults to a
            generic grounded-RAG template.
        input_builder: Maps the graph state into ``generate_prompt``'s input
            variables. Override this when supplying an agent-specific prompt.
        k: Number of chunks to retrieve.
        allow_web_fallback: When True, irrelevant retrievals fall back to a live
            web search before generation.
        checkpointer: Optional LangGraph checkpointer for conversation memory.
    """

    # Resolve reflection settings lazily (graph build must not require app env).
    reflect_min_score, reflect_max_revisions = 7, 1
    _reflect = enable_reflection
    try:
        from src.core.config import settings as _settings

        if _reflect is None:
            _reflect = bool(_settings.reflection_enabled)
        reflect_min_score = int(_settings.reflection_min_score)
        reflect_max_revisions = int(_settings.reflection_max_revisions)
    except Exception:  # pragma: no cover - settings present in app
        if _reflect is None:
            _reflect = False

    def _evidence_of(state: AgentState) -> str:
        return "\n\n".join(
            part for part in (state.get("kb_context", ""), state.get("search_results", "")) if part
        )

    def retrieve_node(state: AgentState) -> dict:
        """Load the active vector collection and run a relevance-scored search."""
        persist_key = state.get("persist_key")
        if not persist_key:
            logger.info("[%s] no persist_key on state; skipping retrieval", label)
            return {"kb_context": "", "grounded": False, "citations": []}

        db = load_vector_store(persist_key)
        if db is None:
            logger.info("[%s] no vector store found for key: %s", label, persist_key)
            return {"kb_context": "", "grounded": False, "citations": []}

        result = similarity_search_with_sources(db, state["question"], k=k, label=label)
        citations = result.get("chunks", [])
        return {
            "kb_context": result.get("context", ""),
            "grounded": result.get("grounded", False),
            "citations": citations,
        }

    def grade_node(state: AgentState) -> dict:
        """CRAG corrective step: judge whether context answers the question."""
        context = state.get("kb_context", "")
        if not context.strip():
            return {"rag_relevant": False}
        try:
            grader = _structured_llm(RelevanceGrade)
            grade = grader.invoke(
                [
                    ("system", _GRADE_SYS),
                    (
                        "human",
                        f"Question:\n{state['question']}\n\nRetrieved context:\n{context[:4000]}",
                    ),
                ]
            )
            return {"rag_relevant": bool(grade.relevant)}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[%s] grade failed (%s); treating context as unverified", label, exc)
            return {
                "rag_relevant": False,
                "grounding_warning": "Retrieval relevance grading failed; context was treated as unverified.",
            }

    def route_after_grade(state: AgentState) -> Literal["web_search", "generate"]:
        """Fall back to web search only when context is not relevant."""
        if state.get("rag_relevant"):
            return "generate"
        if allow_web_fallback:
            return "web_search"
        return "generate"

    def web_search_node(state: AgentState) -> dict:
        """Fetch live web context as a corrective fallback.

        Live web text is UNTRUSTED: wrap it with ``harden_untrusted`` before it
        reaches the generation prompt / grounding verifier, and surface the
        source URLs as citations so a web-only answer is still attributed.
        """
        raw = _fetch_search_context(state["question"]) or ""
        web_citations = extract_web_citations(raw)
        existing = list(state.get("citations") or [])
        return {
            "search_results": (
                harden_untrusted(raw, label="live web search results") if raw else ""
            ),
            "citations": existing + web_citations,
        }

    def generate_node(state: AgentState) -> dict:
        """Generate a grounded answer from context (and any web fallback)."""
        chain = generate_prompt | get_llm()
        resp = chain.invoke(input_builder(state))
        # Gemini may return content as a list of blocks; keep only answer text.
        answer = message_text(resp.content) if hasattr(resp, "content") else str(resp)
        return {"answer": answer}

    def verify_grounding_node(state: AgentState) -> dict:
        """Verify final answer grounding and append compact source/trust notes."""
        answer = state.get("answer", "")
        evidence = "\n\n".join(
            part for part in (state.get("kb_context", ""), state.get("search_results", "")) if part
        )
        citations = state.get("citations", []) or []
        has_evidence = bool(evidence.strip())
        confidence = derive_confidence(
            grounded=bool(state.get("grounded")),
            relevant=bool(state.get("rag_relevant")),
            has_evidence=has_evidence,
        )
        unsupported_claims: list[str] = []

        if has_evidence:
            try:
                verifier = _structured_llm(GroundingCheck)
                check = verifier.invoke(
                    [
                        ("system", _VERIFY_SYS),
                        (
                            "human",
                            f"QUESTION:\n{state.get('question', '')}\n\n"
                            f"EVIDENCE:\n{evidence[:6000]}\n\n"
                            f"ANSWER:\n{answer[:3000]}",
                        ),
                    ]
                )
                confidence = check.confidence
                unsupported_claims = list(check.unsupported_claims or [])
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[%s] grounding verification failed (%s)", label, exc)
                confidence = "medium" if has_evidence else "low"

        final_answer = (
            answer + _format_sources(citations) + _format_trust_note(confidence, unsupported_claims)
        )
        return {
            "answer": final_answer,
            "messages": [AIMessage(content=final_answer)],
            "grounding_confidence": confidence,
            "unsupported_claims": unsupported_claims,
        }

    graph = StateGraph(AgentState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade", grade_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("generate", generate_node)
    if _reflect:
        graph.add_node(
            "reflect",
            make_reflect_node(
                evidence_getter=_evidence_of,
                min_score=reflect_min_score,
                max_revisions=reflect_max_revisions,
            ),
        )
    graph.add_node("verify_grounding", verify_grounding_node)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        route_after_grade,
        {"web_search": "web_search", "generate": "generate"},
    )
    graph.add_edge("web_search", "generate")
    if _reflect:
        graph.add_edge("generate", "reflect")
        graph.add_edge("reflect", "verify_grounding")
    else:
        graph.add_edge("generate", "verify_grounding")
    graph.add_edge("verify_grounding", END)
    return graph.compile(checkpointer=checkpointer)


# ============================ Local smoke test =================================
if __name__ == "__main__":
    from src.core.config import settings

    rag_graph = build_rag_subgraph(label="ncert", checkpointer=InMemorySaver())

    # Try to find an existing NCERT collection so we can test grounded retrieval.
    base = settings.chroma_persist_dir
    existing = (
        [d for d in os.listdir(base) if d.startswith("ncert")] if os.path.exists(base) else []
    )
    persist_key = existing[0] if existing else ""
    print("Using persist_key:", persist_key or "(none - will test web fallback path)")

    config = {"configurable": {"thread_id": "rag-test-1"}}
    result = rag_graph.invoke(
        {
            "question": "Explain the main idea of this chapter.",
            "persist_key": persist_key,
        },
        config,
    )
    print("\n" + "=" * 60)
    print(
        "grounded:",
        result.get("grounded"),
        "| relevant:",
        result.get("rag_relevant"),
        "| citations:",
        result.get("citations"),
    )
    print("ANSWER:\n", result.get("answer"))
