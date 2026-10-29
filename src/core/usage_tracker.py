"""In-process LLM usage + cache tracker for the Cost dashboard.

Aggregates token usage and estimated INR cost from every LLM call (via a
fail-open LangChain callback) plus response-cache hit/miss counters. All state
lives in process memory (thread-safe) and resets on restart -- the Cost UI says
so explicitly. The aggregator functions are pure and offline-testable; only the
callback adapter touches LangChain (guarded so imports never hard-fail).
"""

from __future__ import annotations

import threading

from src.core.config import settings

_LOCK = threading.Lock()

# model_name -> {"input_tokens", "output_tokens", "calls"}
_by_model: dict[str, dict[str, int]] = {}
_cache = {"hit_exact": 0, "hit_semantic": 0, "miss": 0, "skip": 0}

# Substrings that mark a cheap/fast ("lite") model; everything else is
# "strong". NOTE: no "mini" -- it collides with "gemini".
_LITE_MARKERS = ("lite", "8b", "instant", "gemma", "flash-8b")


def _tier_for(model_name: str) -> str:
    name = (model_name or "").lower()
    return "lite" if any(k in name for k in _LITE_MARKERS) else "strong"


def _rates() -> dict:
    return {
        "lite": {
            "input": settings.price_lite_input_inr,
            "output": settings.price_lite_output_inr,
        },
        "strong": {
            "input": settings.price_strong_input_inr,
            "output": settings.price_strong_output_inr,
        },
    }


def _cost_for(model_name: str, input_tokens: int, output_tokens: int) -> float:
    r = _rates()[_tier_for(model_name)]
    return (input_tokens / 1000.0) * r["input"] + (output_tokens / 1000.0) * r["output"]


def record_usage(model_name: str, input_tokens: int, output_tokens: int) -> None:
    """Accumulate one LLM call's token usage (thread-safe, fail-open)."""
    try:
        model_name = model_name or "unknown"
        it = max(int(input_tokens or 0), 0)
        ot = max(int(output_tokens or 0), 0)
        with _LOCK:
            row = _by_model.setdefault(
                model_name, {"input_tokens": 0, "output_tokens": 0, "calls": 0}
            )
            row["input_tokens"] += it
            row["output_tokens"] += ot
            row["calls"] += 1
    except Exception:
        pass


def record_cache(kind: str) -> None:
    """Count one cache outcome: hit_exact | hit_semantic | miss | skip."""
    try:
        with _LOCK:
            if kind in _cache:
                _cache[kind] += 1
    except Exception:
        pass


def reset() -> None:
    """Clear all counters (used by tests and manual resets)."""
    with _LOCK:
        _by_model.clear()
        for k in _cache:
            _cache[k] = 0


def snapshot() -> dict:
    """Return the Cost dashboard payload (matches the CostOverview shape)."""
    with _LOCK:
        by_model = {m: dict(v) for m, v in _by_model.items()}
        cache = dict(_cache)

    agents = []
    tot_in = tot_out = tot_calls = 0
    lite_calls = strong_calls = 0
    tot_cost = 0.0
    for model, v in by_model.items():
        it, ot, calls = v["input_tokens"], v["output_tokens"], v["calls"]
        cost = _cost_for(model, it, ot)
        agents.append(
            {
                "agent": model,
                "input_tokens": it,
                "output_tokens": ot,
                "tokens": it + ot,
                "calls": calls,
                "cost_inr": round(cost, 4),
            }
        )
        tot_in += it
        tot_out += ot
        tot_calls += calls
        tot_cost += cost
        if _tier_for(model) == "lite":
            lite_calls += calls
        else:
            strong_calls += calls

    agents.sort(key=lambda a: a["cost_inr"], reverse=True)
    tier_total = lite_calls + strong_calls
    lite_share = (lite_calls / tier_total) if tier_total else 0.0

    hits = cache["hit_exact"] + cache["hit_semantic"]
    lookups = hits + cache["miss"]
    hit_rate = (hits / lookups) if lookups else 0.0
    avg_cost = (tot_cost / tot_calls) if tot_calls else 0.0
    savings = hits * avg_cost

    return {
        "estimated": True,
        "currency": "INR",
        "totals": {
            "cost_inr": round(tot_cost, 4),
            "input_tokens": tot_in,
            "output_tokens": tot_out,
            "tokens": tot_in + tot_out,
            "calls": tot_calls,
            "avg_cost_per_call_inr": round(avg_cost, 4),
        },
        "agents": agents,
        "tier_mix": {
            "lite": lite_calls,
            "strong": strong_calls,
            "lite_share": round(lite_share, 4),
        },
        "cache": {
            "hit_exact": cache["hit_exact"],
            "hit_semantic": cache["hit_semantic"],
            "miss": cache["miss"],
            "skip": cache["skip"],
            "hit_rate": round(hit_rate, 4),
            "estimated_savings_inr": round(savings, 4),
        },
        "rates_inr_per_1k": _rates(),
    }


# --- LangChain callback adapter (fail-open, optional dependency) ----------
try:
    from langchain_core.callbacks import BaseCallbackHandler as _BaseCB
except Exception:  # pragma: no cover - langchain always present in prod
    _BaseCB = object


def _extract_model_name(response, kwargs) -> str:
    try:
        out = getattr(response, "llm_output", None) or {}
        for key in ("model_name", "model", "model_id"):
            if out.get(key):
                return str(out[key])
    except Exception:
        pass
    try:
        params = kwargs.get("invocation_params") or {}
        for key in ("model", "model_name", "model_id"):
            if params.get(key):
                return str(params[key])
    except Exception:
        pass
    return "unknown"


class UsageCallbackHandler(_BaseCB):
    """Read token usage from each LLM result and record it. Never raises."""

    def on_llm_end(self, response, **kwargs) -> None:
        try:
            model_name = _extract_model_name(response, kwargs)
            input_tokens = output_tokens = 0
            for gen_list in getattr(response, "generations", []) or []:
                for gen in gen_list or []:
                    msg = getattr(gen, "message", None)
                    um = getattr(msg, "usage_metadata", None) if msg else None
                    if um:
                        input_tokens += int(um.get("input_tokens", 0) or 0)
                        output_tokens += int(um.get("output_tokens", 0) or 0)
            if input_tokens == 0 and output_tokens == 0:
                tu = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
                input_tokens = int(tu.get("prompt_tokens", 0) or 0)
                output_tokens = int(tu.get("completion_tokens", 0) or 0)
            if input_tokens or output_tokens:
                record_usage(model_name, input_tokens, output_tokens)
        except Exception:
            pass


_cb_singleton = None


def get_usage_callback():
    """Return the process-wide usage callback handler singleton."""
    global _cb_singleton
    if _cb_singleton is None:
        _cb_singleton = UsageCallbackHandler()
    return _cb_singleton
