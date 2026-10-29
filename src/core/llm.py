"""
LLM initialization - multi-provider with automatic fallback ("router").

Provider order:
  1. Google Gemini  (flash / flash-lite)   - primary, best quality
  2. Groq           (free tier, Llama 3.x) - fallback when Gemini 429s

get_llm() / get_fast_llm() return a Runnable that already has fallbacks
attached, so EVERY existing chain (PROMPT | get_llm()) and every
.invoke()/.stream() call transparently fails over to the next provider on
any error - including 429 RESOURCE_EXHAUSTED (daily free-tier quota).

No agent code needs to change. Set GROQ_API_KEY in .env to enable the
Groq fallback (free key from https://console.groq.com). If GROQ_API_KEY is
empty, the app behaves exactly as before (Gemini only).
"""

import logging
import os
import re
import threading
import traceback

from langchain_google_genai import ChatGoogleGenerativeAI

from src.core.config import settings
from src.core.observability import langchain_callbacks

logger = logging.getLogger(__name__)

_llm_instance = None
_fast_llm_instance = None


_NON_TEXT_BLOCKS = {"reasoning", "thinking", "redacted_thinking"}


def message_text(content) -> str:
    """Plain answer text from a chat-model ``content`` value.

    Gemini (and other models) may return a string OR a list of blocks such as
    ``[{"type": "text", "text": "..."}, {"type": "reasoning", ...}]``. Text
    blocks and bare strings are joined; reasoning/thinking blocks and blocks
    without text are ignored. Anything else yields "" (never a repr of a block).
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
            elif isinstance(block, dict) and block.get("type") not in _NON_TEXT_BLOCKS:
                piece = block.get("text")
                if isinstance(piece, str):
                    parts.append(piece)
        return "".join(parts)
    return ""


def first_word_yes_no(value) -> bool | None:
    """True / False from the FIRST word of a model's yes/no field, else None.

    "yes (study notes)" is yes and "No - an invoice" is no; anything else
    (blank, "unknown", "not sure") is None so the caller keeps its default. A
    substring test for "no" would misread words like "notes" or "economy".
    """
    words = re.findall(r"[a-z]+", str(value or "").lower())
    if words and words[0] in ("yes", "no"):
        return words[0] == "yes"
    return None


_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../src


def describe_exception(exc: BaseException) -> str:
    """Secret-safe one-line summary: exception classes, HTTP/RPC status, location.

    Never includes exception messages, provider response bodies, prompts or
    answers - only class names, a numeric status, an UPPER_CASE status token
    and file:line:function locations.
    """
    chain, seen, e = [], set(), exc
    while e is not None and id(e) not in seen and len(chain) < 6:
        seen.add(id(e))
        chain.append(e)
        e = e.__cause__ or e.__context__
    code = status = None
    for e in chain:
        for attr in ("code", "status_code"):
            value = getattr(e, attr, None)
            if code is None and isinstance(value, int) and 100 <= value <= 599:
                code = value
        value = getattr(e, "status", None)
        if status is None and isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z_]{2,40}", value):
            status = value
    src_frame = origin = None
    for e in reversed(chain):  # root cause first
        frames = traceback.extract_tb(e.__traceback__) if e.__traceback__ else []
        if origin is None and frames:
            last = frames[-1]
            origin = f"{os.path.basename(last.filename)}:{last.lineno}:{last.name}"
        for fr in reversed(frames):
            path = os.path.abspath(fr.filename)
            if path.startswith(_SRC_ROOT + os.sep):
                rel = "src/" + os.path.relpath(path, _SRC_ROOT).replace(os.sep, "/")
                src_frame = f"{rel}:{fr.lineno}:{fr.name}"
                break
        if src_frame:
            break
    names = ">".join(type(x).__name__ for x in chain)
    return (
        f"{names} http={code if code is not None else '-'} status={status or '-'} "
        f"at={src_frame or '-'} origin={origin or '-'}"
    )


class LLMTimeoutError(TimeoutError):
    """A model call exceeded LLM_REQUEST_TIMEOUT_SECONDS (no fallback was tried)."""


def is_llm_timeout(exc: BaseException | None) -> bool:
    """True if ``exc`` (or anything in its cause/context chain) is an LLM timeout.

    Covers our own LLMTimeoutError, httpx client timeouts and Gemini's
    server-side deadline responses (HTTP 408 / 504).
    """
    import httpx

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (LLMTimeoutError, httpx.TimeoutException)):
            return True
        if getattr(exc, "code", None) in (408, 504) and type(exc).__module__.startswith(
            "google.genai"
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


# Per-thread "a model in this chain just timed out" flags, keyed by chain name.
# Not a ContextVar: with_fallbacks runs each model in its own copied context, so
# a ContextVar set by the primary would be invisible to the fallback. Primary and
# fallbacks of one invocation run one after another on the same thread.
_flags = threading.local()


def _timeout_flags() -> dict:
    flags = getattr(_flags, "by_chain", None)
    if flags is None:
        flags = _flags.by_chain = {}
    return flags


class _TimeoutGuard:
    """Chat-model mixin that never lets a timeout reach the next fallback model.

    ``with_fallbacks`` retries on ANY exception. The primary model of a chain
    clears its chain's timeout flag before each call and sets it on timeout;
    fallback models (``is_fallback=True``) of the same chain raise
    LLMTimeoutError without a network call while the flag is set. Roles are
    assigned by ``_chain_with_fallbacks``. Subclassing keeps bind_tools /
    with_structured_output working.
    """

    def _before_call(self) -> None:
        flags = _timeout_flags()
        if not self.is_fallback:
            flags[self.fallback_chain] = False
        elif flags.get(self.fallback_chain):
            raise LLMTimeoutError("previous model call timed out; fallback skipped")

    def _on_error(self, exc: Exception) -> None:
        if is_llm_timeout(exc):
            _timeout_flags()[self.fallback_chain] = True
            if not isinstance(exc, LLMTimeoutError):
                raise LLMTimeoutError(
                    f"model call exceeded {settings.llm_request_timeout_seconds:g}s"
                ) from exc

    def _generate(self, *args, **kwargs):
        self._before_call()
        try:
            return super()._generate(*args, **kwargs)
        except Exception as exc:
            self._on_error(exc)
            raise

    async def _agenerate(self, *args, **kwargs):
        self._before_call()
        try:
            return await super()._agenerate(*args, **kwargs)
        except Exception as exc:
            self._on_error(exc)
            raise

    def _stream(self, *args, **kwargs):
        self._before_call()
        try:
            yield from super()._stream(*args, **kwargs)
        except Exception as exc:
            self._on_error(exc)
            raise

    async def _astream(self, *args, **kwargs):
        self._before_call()
        try:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
        except Exception as exc:
            self._on_error(exc)
            raise


class _GuardedGemini(_TimeoutGuard, ChatGoogleGenerativeAI):
    is_fallback: bool = False
    fallback_chain: str = ""


_guarded_groq_cls = None


def _guarded_groq():
    """ChatGroq with the same timeout guard (built lazily: langchain-groq is optional)."""
    global _guarded_groq_cls
    if _guarded_groq_cls is None:
        from langchain_groq import ChatGroq

        class _GuardedGroq(_TimeoutGuard, ChatGroq):
            is_fallback: bool = False
            fallback_chain: str = ""

        _guarded_groq_cls = _GuardedGroq
    return _guarded_groq_cls


def _with_tracing(model, model_name: str):
    """Attach usage-tracking + Langfuse callbacks; unchanged if none apply."""
    cbs = list(langchain_callbacks() or [])
    try:
        from src.core.usage_tracker import get_usage_callback

        cbs.append(get_usage_callback())
    except Exception:
        logger.debug("usage callback unavailable", exc_info=True)
    if cbs:
        return model.with_config({"callbacks": cbs, "tags": ["upsc-ai", model_name]})
    return model


def _make_gemini(model_name: str, temperature: float):
    """Build a traced Gemini model, or None if no key / init fails."""
    if not settings.google_api_key:
        return None
    try:
        model = _GuardedGemini(
            model=model_name,
            google_api_key=settings.google_api_key,
            temperature=temperature,
            max_retries=settings.llm_max_retries,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout=settings.llm_request_timeout_seconds,  # seconds (SDK converts to ms)
        )
        return _with_tracing(model, model_name)
    except Exception as e:
        logger.warning(f"Gemini init failed ({model_name}): {e}")
        return None


def _make_groq(model_name: str, temperature: float):
    """Build a traced Groq model, or None if no key / package / init fails."""
    if not settings.groq_api_key:
        return None
    try:
        guarded_groq = _guarded_groq()
    except ImportError:
        logger.warning(
            "langchain-groq not installed; Groq fallback disabled. Run: uv add langchain-groq"
        )
        return None
    try:
        model = guarded_groq(
            model=model_name,
            api_key=settings.groq_api_key,
            temperature=temperature,
            max_retries=1,
        )
        return _with_tracing(model, model_name)
    except Exception as e:
        logger.warning(f"Groq init failed ({model_name}): {e}")
        return None


def _chain_with_fallbacks(providers: list, chain: str = ""):
    """
    providers: ordered list of (label, runnable-or-None).
    Returns the first available provider with the rest attached as fallbacks.
    A timeout in any model ends the chain (no further fallback calls).
    """
    active = [(label, r) for label, r in providers if r is not None]
    if not active:
        raise ValueError(
            "No LLM provider available. Set GOOGLE_API_KEY and/or GROQ_API_KEY in .env."
        )
    order = " -> ".join(label for label, _ in active)
    logger.info(f"LLM chain ready: {order}")
    # Roles are assigned on the ACTUAL chain, so the first available model is
    # always the one that resets the chain's timeout flag.
    chain = chain or f"chain-{id(active[0][1])}"
    for i, (_, r) in enumerate(active):
        inner = getattr(r, "bound", r)  # _with_tracing may wrap in a RunnableBinding
        if isinstance(inner, _TimeoutGuard):
            inner.is_fallback = i > 0
            inner.fallback_chain = chain

    primary = active[0][1]
    fallbacks = [r for _, r in active[1:]]
    if not settings.enable_provider_fallback or not fallbacks:
        return primary
    return primary.with_fallbacks(fallbacks)


def get_llm():
    """
    Main LLM chain (quality first):
        Gemini flash -> Gemini flash-lite -> Groq
    Use for: notes, CA, mentor, planner, evaluator, chat, study aids.
    """
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = _chain_with_fallbacks(
            [
                (
                    "gemini/" + settings.llm_model,
                    _make_gemini(settings.llm_model, settings.llm_temperature),
                ),
                (
                    "gemini/" + settings.llm_fast_model,
                    _make_gemini(settings.llm_fast_model, settings.llm_temperature),
                ),
                (
                    "groq/" + settings.groq_model,
                    _make_groq(settings.groq_model, settings.llm_temperature),
                ),
            ],
            chain="main",
        )
    return _llm_instance


def get_fast_llm():
    """
    Fast LLM chain (cheap/quick first, preserves flash quota):
        Gemini flash-lite -> Groq fast -> Gemini flash
    Use for: mindmap, questions, topic detection, translation.
    """
    global _fast_llm_instance
    if _fast_llm_instance is None:
        _fast_llm_instance = _chain_with_fallbacks(
            [
                ("gemini/" + settings.llm_fast_model, _make_gemini(settings.llm_fast_model, 0.2)),
                ("groq/" + settings.groq_fast_model, _make_groq(settings.groq_fast_model, 0.2)),
                ("gemini/" + settings.llm_model, _make_gemini(settings.llm_model, 0.2)),
            ],
            chain="fast",
        )
    return _fast_llm_instance


def get_llm_for_tier(tier: str = "strong"):
    """Return the LLM chain for a complexity tier (see src.core.model_router).

        "lite"   -> get_fast_llm()  (flash-lite first: fast + cheap, saves quota)
        "strong" -> get_llm()       (flash first: best quality)

    Unknown/None tiers fall back to the strong chain (quality-first is the safe
    default). This reuses the existing cached singletons, so tier routing adds
    NO new model instances and NO extra cost - it just picks which chain runs.
    """
    return get_fast_llm() if str(tier).lower() == "lite" else get_llm()


def reset_llm() -> None:
    """Force re-initialize both LLM chains."""
    global _llm_instance, _fast_llm_instance
    _llm_instance = None
    _fast_llm_instance = None
    logger.info("LLM instances reset")
