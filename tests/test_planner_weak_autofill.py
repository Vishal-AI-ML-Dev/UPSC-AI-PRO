"""Phase 08 Slice A (AC-A3): Planner weak-area autofill from the caller's profile.

When ``weak`` is blank or "Not specified", the planner routes fill it from the
authenticated caller's long-term Mentor profile (``weak_areas``). An explicit
value always wins; a missing or failing store falls back to "Not specified".
The model is a RunnableLambda stand-in that captures the rendered prompt.
"""

from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.store.memory import InMemoryStore

import src.agents.planner.graph as planner
import src.api.routes.planner as planner_routes
from src.graph.memory import save_student_profile

API = "/api/v1"
SYNC = f"{API}/planner/generate/sync"
STREAM = f"{API}/planner/generate"
U1_AREAS = ["Zebrafish Ecology", "Quokka Studies"]
U2_AREAS = ["Platypus Mapping"]


@pytest.fixture
def prompts(monkeypatch):
    seen: list[str] = []

    def _f(prompt_value):
        seen.append(prompt_value.to_string())
        return AIMessage(content="PLAN")

    monkeypatch.setattr(planner, "get_llm", lambda: RunnableLambda(_f))
    monkeypatch.setattr(planner, "_fetch_upsc_prelims_date", lambda year: "")
    monkeypatch.setattr(planner.mentor_kb, "kb_exists", lambda: False)
    return seen


@pytest.fixture
def store(monkeypatch):
    s = InMemoryStore()
    monkeypatch.setattr(planner_routes, "get_store", lambda: s)
    return s


@pytest.fixture
def users(make_user, store):
    u1, u2 = make_user("weak1"), make_user("weak2")
    save_student_profile(store, u1.id, {"weak_areas": U1_AREAS})
    save_student_profile(store, u2.id, {"weak_areas": U2_AREAS})
    return u1, u2


def _generate(client, url, user, **body):
    r = client.post(url, json={"goal": "UPSC 2027", **body}, headers=user.headers)
    assert r.status_code == 200
    return r


@pytest.mark.parametrize("url", [SYNC, STREAM], ids=["sync", "stream"])
@pytest.mark.parametrize(
    "body",
    [{}, {"weak": ""}, {"weak": "   "}, {"weak": "Not specified"}, {"weak": "not specified "}],
    ids=["omitted", "empty", "spaces", "not-specified", "not-specified-case"],
)
def test_blank_weak_uses_only_callers_profile(client, users, prompts, url, body):
    u1, u2 = users
    _generate(client, url, u1, **body)
    _generate(client, url, u2, **body)
    p1, p2 = prompts
    assert "Zebrafish Ecology, Quokka Studies" in p1
    assert "Platypus Mapping" not in p1
    assert "Platypus Mapping" in p2
    assert "Zebrafish" not in p2 and "Quokka" not in p2


@pytest.mark.parametrize("url", [SYNC, STREAM], ids=["sync", "stream"])
def test_explicit_weak_always_wins(client, users, prompts, url):
    u1, _ = users
    _generate(client, url, u1, weak="Narwhal History")
    assert "Narwhal History" in prompts[0]
    assert "Zebrafish" not in prompts[0]


def test_explicit_weak_does_not_read_the_store(client, make_user, prompts, monkeypatch):
    def _boom():
        raise AssertionError("store must not be read when weak is explicit")

    monkeypatch.setattr(planner_routes, "get_store", _boom)
    _generate(client, SYNC, make_user("weak3"), weak="Axolotl Topics")
    assert "Axolotl Topics" in prompts[0]


@pytest.mark.parametrize("url", [SYNC, STREAM], ids=["sync", "stream"])
def test_store_unavailable_fails_open(client, make_user, prompts, monkeypatch, caplog, url):
    def _down():
        raise ConnectionError("db host=secret.example password=hunter2")

    monkeypatch.setattr(planner_routes, "get_store", _down)
    with caplog.at_level(logging.WARNING, logger=planner_routes.__name__):
        r = _generate(client, url, make_user("weak4"))
    assert "PLAN" in r.text
    assert "Not specified" in prompts[0]
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "ConnectionError" in logged
    assert "hunter2" not in logged and "secret.example" not in logged


def test_store_none_fails_open(client, make_user, prompts, monkeypatch):
    monkeypatch.setattr(planner_routes, "get_store", lambda: None)
    _generate(client, SYNC, make_user("weak5"))
    assert "Not specified" in prompts[0]


@pytest.mark.parametrize(
    "profile",
    [{}, {"weak_areas": []}, {"weak_areas": ["  "]}, {"weak_areas": "Zebrafish"}],
    ids=["no-profile", "empty-list", "blank-items", "not-a-list"],
)
def test_unusable_profile_falls_back(client, make_user, prompts, store, profile):
    user = make_user("weak6")
    if profile:
        save_student_profile(store, user.id, profile)
    _generate(client, SYNC, user)
    assert "Not specified" in prompts[0]


def test_autofilled_value_is_bounded(client, make_user, prompts, store):
    from src.schemas import SHORT_TEXT_MAX

    user = make_user("weak7")
    areas = [f"Marker{i:02d} " + "x" * 70 for i in range(12)]
    save_student_profile(store, user.id, {"weak_areas": areas})
    assert planner_routes._resolve_weak("", user.id) == ", ".join(areas)[:SHORT_TEXT_MAX]
    _generate(client, SYNC, user)
    assert "Marker00" in prompts[0]
    assert "Marker11" not in prompts[0]


def _weak_line(prompt: str) -> str:
    """The rendered ``Weak area(s) : ...`` value (the template itself names GS subjects)."""
    for line in prompt.splitlines():
        if line.startswith("Weak area(s)"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("weak-area line missing from the planner prompt")


# --------------------------------------------------------------------------- #
# Phase 08 Slice C2 (AC-C2-4, R-C2-8 / Amendment C2-1): weak subjects first,
# then profile weak_areas; caller-only; each source fails open on its own.
# --------------------------------------------------------------------------- #
def _weak_attempts(user_id: str, subject: str, n: int = 3, score: float = 2) -> None:
    from datetime import datetime, timedelta, timezone

    from src.core.db import SessionLocal
    from src.core.models import EvaluationAttempt

    base = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    with SessionLocal() as db:
        for i in range(n):
            db.add(
                EvaluationAttempt(
                    user_id=user_id,
                    mode="basic",
                    subject=subject,
                    score=score,
                    max_score=10,
                    created_at=base + timedelta(minutes=i),
                )
            )
        db.commit()


@pytest.mark.parametrize("url", [SYNC, STREAM], ids=["sync", "stream"])
def test_weak_subjects_come_before_profile_areas_caller_only(client, users, prompts, url):
    u1, u2 = users
    _weak_attempts(u1.id, "Economy", score=1)
    _weak_attempts(u1.id, "Ethics", score=3)
    _generate(client, url, u1)
    _generate(client, url, u2)
    p1, p2 = prompts
    assert _weak_line(p1) == "Economy, Ethics, Zebrafish Ecology, Quokka Studies"
    assert _weak_line(p2) == "Platypus Mapping"


def test_weak_subjects_used_without_a_profile(client, make_user, prompts, store):
    user = make_user("weak-c2-a")
    _weak_attempts(user.id, "Geography")
    _generate(client, SYNC, user)
    assert _weak_line(prompts[0]) == "Geography"


def test_not_yet_weak_subjects_are_not_used(client, make_user, prompts, store):
    user = make_user("weak-c2-b")
    _weak_attempts(user.id, "Geography", n=2)  # 2 attempts: below WEAK_MIN_ATTEMPTS
    _weak_attempts(user.id, "Ethics", score=6)  # exactly 60 %: not weak
    _generate(client, SYNC, user)
    assert planner_routes._resolve_weak("", user.id) == ""
    assert _weak_line(prompts[0]) == "Not specified"


def test_duplicates_are_removed_case_insensitively(client, make_user, prompts, store):
    user = make_user("weak-c2-c")
    _weak_attempts(user.id, "Economy")
    save_student_profile(store, user.id, {"weak_areas": ["economy", "Quokka Studies"]})
    assert planner_routes._resolve_weak("", user.id) == "Economy, Quokka Studies"


def test_explicit_weak_reads_neither_source(client, make_user, prompts, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        planner_routes.evaluation_history,
        "weak_subject_names",
        lambda uid: calls.append("subjects") or ["Economy"],
    )
    monkeypatch.setattr(planner_routes, "get_store", lambda: calls.append("store"))
    user = make_user("weak-c2-d")
    _weak_attempts(user.id, "Economy")
    _generate(client, SYNC, user, weak="Axolotl Topics")
    assert calls == []
    assert _weak_line(prompts[0]) == "Axolotl Topics"


@pytest.mark.parametrize("url", [SYNC, STREAM], ids=["sync", "stream"])
def test_weak_subject_failure_falls_back_to_profile(
    client, users, prompts, monkeypatch, caplog, url
):
    def _down(user_id):
        raise ConnectionError("db host=secret.example password=hunter2")

    monkeypatch.setattr(planner_routes.evaluation_history, "weak_subject_names", _down)
    u1, _ = users
    with caplog.at_level(logging.WARNING, logger=planner_routes.__name__):
        r = _generate(client, url, u1)
    assert "PLAN" in r.text
    assert _weak_line(prompts[0]) == "Zebrafish Ecology, Quokka Studies"
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert "Planner weak-subject autofill unavailable (ConnectionError)" in logged
    assert "hunter2" not in logged and "secret.example" not in logged


def test_store_failure_still_uses_weak_subjects(client, make_user, prompts, monkeypatch):
    def _down():
        raise ConnectionError("store down")

    monkeypatch.setattr(planner_routes, "get_store", _down)
    user = make_user("weak-c2-e")
    _weak_attempts(user.id, "Ethics")
    _generate(client, SYNC, user)
    assert _weak_line(prompts[0]) == "Ethics"


def test_both_sources_down_fall_back_to_not_specified(client, make_user, prompts, monkeypatch):
    def _down(*_args):
        raise ConnectionError("down")

    monkeypatch.setattr(planner_routes.evaluation_history, "weak_subject_names", _down)
    monkeypatch.setattr(planner_routes, "get_store", _down)
    r = _generate(client, SYNC, make_user("weak-c2-f"))
    assert "PLAN" in r.text
    assert _weak_line(prompts[0]) == "Not specified"
