"""Offline tests for deterministic student-profile personalization helpers."""

from src.graph.profile import extract_student_profile_signals, merge_student_profile


def test_extracts_explicit_profile_signals():
    out = extract_student_profile_signals(
        "My optional subject: Sociology, target 2027, I study 6 hours and weak in polity. Prefer Hinglish."
    )
    assert out["target_year"] == "2027"
    assert out["study_hours"] == 6
    assert out["optional"] == "Sociology"
    assert out["preferred_language"] == "hinglish"
    assert out["weak_areas"] == ["polity"]


def test_merge_preserves_existing_and_appends_weak_areas():
    existing = {"target_year": "2026", "weak_areas": ["Economy"], "optional": "History"}
    updates = {"target_year": "2027", "weak_areas": ["Economy", "Polity"]}
    merged = merge_student_profile(existing, updates)
    assert merged["target_year"] == "2027"
    assert merged["optional"] == "History"
    assert merged["weak_areas"] == ["Economy", "Polity"]


def test_no_invention_from_generic_text():
    assert extract_student_profile_signals("Give me a plan for UPSC") == {}
