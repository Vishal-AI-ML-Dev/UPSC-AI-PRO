"""Offline tests for the query-complexity model router (pure, no keys/deps)."""

from src.core.model_router import (
    LITE,
    STRONG,
    STRONG_WORD_THRESHOLD,
    describe_route,
    route_model_tier,
)


# --------------------------------------------------------------------------- #
# LITE: clearly-trivial turns
# --------------------------------------------------------------------------- #
def test_empty_and_greetings_route_lite():
    assert route_model_tier("") == LITE
    assert route_model_tier("   ") == LITE
    assert route_model_tier("hi") == LITE
    assert route_model_tier("bhai motivate karo", has_tools=True) == LITE
    assert route_model_tier("thanks yaar", has_tools=True) == LITE


# --------------------------------------------------------------------------- #
# STRONG: reasoning / long-form
# --------------------------------------------------------------------------- #
def test_reasoning_keywords_route_strong():
    for q in [
        "explain fundamental rights",
        "critically analyse the DPSP vs FR debate",
        "evaluate my mains answer on federalism",
        "compare presidential and parliamentary systems",
        "why does inflation rise",
    ]:
        assert route_model_tier(q, has_tools=True) == STRONG, q


def test_hinglish_reasoning_cue_routes_strong():
    assert route_model_tier("samjhao photosynthesis kaise hota hai", has_tools=True) == STRONG


def test_long_query_routes_strong_even_without_keywords():
    long_q = " ".join(["topic"] * STRONG_WORD_THRESHOLD)
    assert route_model_tier(long_q) == STRONG


# --------------------------------------------------------------------------- #
# Lookup + tools interaction
# --------------------------------------------------------------------------- #
def test_volatile_lookup_needs_tools_to_route_strong():
    # With tools available, a date/result lookup should use the strong model for
    # dependable tool orchestration...
    assert route_model_tier("UPSC Prelims 2026 ki date kab hai", has_tools=True) == STRONG
    # ...but the same lookup with NO tools stays lite (nothing to orchestrate).
    assert route_model_tier("prelims date", has_tools=False) == LITE


# --------------------------------------------------------------------------- #
# force override + describe_route reasons
# --------------------------------------------------------------------------- #
def test_force_overrides_all_heuristics():
    assert route_model_tier("hi", force=STRONG) == STRONG
    assert route_model_tier("critically analyse everything", force=LITE) == LITE
    # invalid force value is ignored (falls through to heuristics)
    assert route_model_tier("hi", force="banana") == LITE


def test_describe_route_returns_tier_and_reason():
    tier, reason = describe_route("", has_tools=True)
    assert (tier, reason) == (LITE, "empty")
    tier, reason = describe_route("hello there", has_tools=True)
    assert tier == LITE and reason == "trivial"
    tier, reason = describe_route("explain osmosis", has_tools=True)
    assert tier == STRONG and reason.startswith("reasoning:")
    tier, reason = describe_route("latest result today", has_tools=True)
    assert tier == STRONG and reason.startswith("lookup:")
    tier, reason = describe_route(" ".join(["w"] * STRONG_WORD_THRESHOLD), has_tools=False)
    assert tier == STRONG and reason.startswith("long:")


def test_router_is_deterministic():
    q = "critically evaluate cooperative federalism in india"
    assert route_model_tier(q, has_tools=True) == route_model_tier(q, has_tools=True)
