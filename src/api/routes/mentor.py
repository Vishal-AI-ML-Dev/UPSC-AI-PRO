"""
Mentor routes - UPSC guidance and Q&A.

Backed by the canonical tool-calling agent (``src/graph/tools.build_tool_agent``
via ``build_mentor_graph``): the model itself decides whether to call
``web_search`` / ``knowledge_base_search``. The previous hand-rolled
``mentor_reply`` / ``detect_intent`` pipeline was removed in the P1 dual-stack
merge, so both endpoints now run the same brain as ``/agent/chat``.

The graph is compiled once and reused; it is stateless (no checkpointer), so
conversation context is supplied per request by mapping ``chat_history`` onto
the agent's message list.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from src.graph.mentor_graph import build_mentor_graph
from src.schemas import MentorRequest, MentorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mentor", tags=["Mentor"])

# The mentor tool-agent's answer node. Streaming is filtered to this node so the
# model's internal tool-deciding turns (which carry no user-facing content) do
# not leak into the response.
_ANSWER_NODE = "agent"

# Message types that must NOT be streamed to the client even when they pass
# through the answer node: the seeded human question and tool/system messages.
_NON_STREAM_TYPES = ("human", "system", "tool")

_mentor_graph = None


def _get_mentor_graph():
    """Compile the canonical mentor brain once and reuse it (stateless)."""
    global _mentor_graph
    if _mentor_graph is None:
        _mentor_graph = build_mentor_graph()
    return _mentor_graph


def _chunk_text(content) -> str:
    """Normalise a message chunk's content to plain text.

    Most chat models yield string content, but some emit a list of content
    parts (e.g. multimodal blocks); flatten those to their text payloads.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, dict):
                parts.append(piece.get("text", ""))
            else:
                parts.append(str(piece))
        return "".join(parts)
    return str(content or "")


def _history_to_messages(chat_history):
    """Map prior ChatMessage turns onto LangChain messages for the agent state."""
    messages = []
    for m in chat_history or []:
        role = (m.get("role") or "").lower()
        content = m.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


def _build_state(request: MentorRequest) -> dict:
    """Assemble the shared AgentState inputs from a mentor request."""
    return {
        "question": request.question,
        "student_context": (
            request.student_context.model_dump() if request.student_context else None
        ),
        "messages": _history_to_messages(
            [m.model_dump() for m in request.chat_history] if request.chat_history else None
        ),
    }


@router.post("/chat")
async def chat(request: MentorRequest):
    """Chat with mentor (streaming, real tool-calling)."""
    graph = _get_mentor_graph()
    state = _build_state(request)

    def generate():
        try:
            for chunk, metadata in graph.stream(state, stream_mode="messages"):
                if metadata.get("langgraph_node") != _ANSWER_NODE:
                    continue
                # Only stream assistant tokens; skip the seeded human question
                # (and any tool/system messages) that share the answer node so
                # the user's own question is never echoed back mid-stream.
                if getattr(chunk, "type", "") in _NON_STREAM_TYPES:
                    continue
                text = _chunk_text(getattr(chunk, "content", ""))
                if text:
                    yield text
        except Exception:
            logger.exception("Mentor stream failed")
            yield "Something went wrong \u2014 please try again in a moment."

    return StreamingResponse(generate(), media_type="text/plain")


@router.post("/chat/sync", response_model=MentorResponse)
async def chat_sync(request: MentorRequest):
    """Chat with mentor (non-streaming, real tool-calling)."""
    graph = _get_mentor_graph()
    state = _build_state(request)
    try:
        result = graph.invoke(state)
        response = result.get("answer") or ""
    except Exception:
        logger.exception("Mentor sync failed")
        response = "Something went wrong \u2014 please try again in a moment."
    return {"response": response, "intent": None}
