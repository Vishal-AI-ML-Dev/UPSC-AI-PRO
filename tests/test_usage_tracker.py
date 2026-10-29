"""Offline unit tests for the Cost-dashboard usage tracker (pure aggregator)."""

from src.core import usage_tracker as ut


def setup_function(_):
    ut.reset()


def test_empty_snapshot_is_well_shaped():
    snap = ut.snapshot()
    assert snap["totals"]["calls"] == 0
    assert snap["agents"] == []
    assert snap["tier_mix"]["lite_share"] == 0.0
    assert snap["cache"]["hit_rate"] == 0.0
    assert set(snap["totals"]) == {
        "cost_inr",
        "input_tokens",
        "output_tokens",
        "tokens",
        "calls",
        "avg_cost_per_call_inr",
    }


def test_records_usage_and_computes_strong_cost():
    ut.record_usage("gemini-2.5-flash", 1000, 500)
    snap = ut.snapshot()
    assert snap["totals"]["calls"] == 1
    assert snap["totals"]["input_tokens"] == 1000
    assert snap["totals"]["output_tokens"] == 500
    assert snap["totals"]["tokens"] == 1500
    # strong rates: 0.025 in, 0.100 out per 1k -> 1*0.025 + 0.5*0.100 = 0.075
    assert snap["totals"]["cost_inr"] == 0.075
    assert snap["tier_mix"]["strong"] == 1
    assert snap["tier_mix"]["lite"] == 0
    assert len(snap["agents"]) == 1
    assert snap["agents"][0]["agent"] == "gemini-2.5-flash"


def test_tier_classification_and_lite_share():
    ut.record_usage("gemini-2.5-flash-lite", 1000, 1000)  # lite
    ut.record_usage("llama-3.1-8b-instant", 1000, 1000)  # lite
    ut.record_usage("gemini-2.5-flash", 1000, 1000)  # strong
    snap = ut.snapshot()
    assert snap["tier_mix"]["lite"] == 2
    assert snap["tier_mix"]["strong"] == 1
    assert snap["tier_mix"]["lite_share"] == round(2 / 3, 4)
    assert len(snap["agents"]) == 3
    # agents sorted by cost desc
    costs = [a["cost_inr"] for a in snap["agents"]]
    assert costs == sorted(costs, reverse=True)


def test_cache_counters_and_hit_rate():
    ut.record_cache("hit_exact")
    ut.record_cache("hit_exact")
    ut.record_cache("hit_semantic")
    ut.record_cache("miss")
    ut.record_cache("skip")
    ut.record_cache("bogus")  # ignored
    c = ut.snapshot()["cache"]
    assert c["hit_exact"] == 2
    assert c["hit_semantic"] == 1
    assert c["miss"] == 1
    assert c["skip"] == 1
    # hits=3, lookups=hits+miss=4 -> 0.75 (skip excluded)
    assert c["hit_rate"] == 0.75


def test_negative_and_none_tokens_are_safe():
    ut.record_usage("gemini-2.5-flash", -50, None)
    snap = ut.snapshot()
    assert snap["totals"]["input_tokens"] == 0
    assert snap["totals"]["output_tokens"] == 0
    assert snap["totals"]["calls"] == 1
