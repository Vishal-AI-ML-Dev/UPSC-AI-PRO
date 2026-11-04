"""Evaluation attempt recording (Phase 08 Slice C1, ADR-017).

Basic and Mains evaluations each record one ``evaluation_attempts`` row for the
authenticated caller: mode, optional allow-listed subject, validated score and
max score — never the question or answer text. Recording is best-effort: a
failure leaves the evaluator response unchanged and logs a redacted warning.

Fully offline: the evaluator model is a ``RunnableLambda`` stand-in.
"""

import logging
import math
import pathlib

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import OperationalError

import src.agents.evaluator.graph as evaluator
import src.api.routes.evaluator as evaluator_routes
import src.core.evaluation_history as history
from src.core.db import SessionLocal
from src.core.models import EvaluationAttempt
from src.schemas import SUBJECTS, EvaluateRequest, MainsEvalRequest

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "/api/v1"
BASIC = f"{API}/evaluator/evaluate/sync"
MAINS = f"{API}/evaluator/mains/sync"
MODEL_ANSWER = f"{API}/evaluator/model-answer/sync"

QUESTION = "Discuss the role of the Gram Sabha in rural local governance (C1-Q-MARKER)."
ANSWER = " ".join(["C1-A-MARKER The Gram Sabha includes all adult voters of the village."] * 6)

EVAL_TEXT = "## Score: 6/10\n\n### What you did well\n- Clear introduction\n"
MAINS_TEXT = "## Score: 9/15\n**Verdict:** Above average\n\n### Strengths\n- Good structure\n"


def _model(content):
    return lambda: RunnableLambda(lambda _prompt: AIMessage(content=content))


def _rows(user_id):
    with SessionLocal() as db:
        return list(
            db.execute(
                select(EvaluationAttempt)
                .where(EvaluationAttempt.user_id == user_id)
                .order_by(EvaluationAttempt.created_at)
            ).scalars()
        )


def _basic(client, user, **extra):
    return client.post(
        BASIC, json={"question": QUESTION, "answer": ANSWER, **extra}, headers=user.headers
    )


def _mains(client, user, **extra):
    return client.post(
        MAINS, json={"question": QUESTION, "answer": ANSWER, **extra}, headers=user.headers
    )


# ------------------------------------------------------------------ AC-C1-1
def test_basic_records_one_row_with_subject(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))
    u = make_user("c1_basic")
    r = _basic(client, u, subject="Economy")
    assert r.status_code == 200
    rows = _rows(u.id)
    assert len(rows) == 1
    row = rows[0]
    assert (row.user_id, row.mode, row.subject, row.score, row.max_score) == (
        u.id,
        "basic",
        "Economy",
        6.0,
        10,
    )
    assert row.created_at is not None


def test_mains_records_one_row_with_requested_marks(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(MAINS_TEXT))
    u = make_user("c1_mains")
    r = _mains(client, u, marks=15, subject="Polity & Governance")
    assert r.status_code == 200
    rows = _rows(u.id)
    assert len(rows) == 1
    assert (rows[0].mode, rows[0].subject, rows[0].score, rows[0].max_score) == (
        "mains",
        "Polity & Governance",
        9.0,
        15,
    )


def test_no_subject_records_null_subject(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))
    u = make_user("c1_nosubj")
    assert _basic(client, u).status_code == 200
    assert _mains(client, u, subject=None).status_code == 200
    rows = _rows(u.id)
    assert sorted((r.mode, r.subject) for r in rows) == [("basic", None), ("mains", None)]


@pytest.mark.parametrize("endpoint", [BASIC, MAINS])
@pytest.mark.parametrize(
    "subject", ["Astrology", "economy", "Economy ", "", "x" * 61, 5, ["Economy"]]
)
def test_invalid_subject_is_422_and_writes_no_row(
    client, make_user, monkeypatch, endpoint, subject
):
    called = []
    monkeypatch.setattr(evaluator, "get_llm", lambda: called.append(1))
    u = make_user("c1_badsubj")
    r = client.post(
        endpoint,
        json={"question": QUESTION, "answer": ANSWER, "subject": subject},
        headers=u.headers,
    )
    assert r.status_code == 422
    assert not called  # rejected before the model is reached
    assert _rows(u.id) == []


def test_every_allow_listed_subject_is_accepted_and_fits_the_column():
    assert len(SUBJECTS) == 10
    for subject in SUBJECTS:
        assert len(subject) <= 60
        assert EvaluateRequest(question=QUESTION, answer=ANSWER, subject=subject).subject == subject
        assert (
            MainsEvalRequest(question=QUESTION, answer=ANSWER, subject=subject).subject == subject
        )
    with pytest.raises(ValidationError):
        EvaluateRequest(question=QUESTION, answer=ANSWER, subject="Maths")


def test_model_answer_does_not_record(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model("Intro. Body. Conclusion."))
    u = make_user("c1_model")
    r = client.post(MODEL_ANSWER, json={"question": QUESTION}, headers=u.headers)
    assert r.status_code == 200
    assert _rows(u.id) == []


def test_no_question_or_answer_text_is_stored():
    columns = {c.name for c in EvaluationAttempt.__table__.columns}
    assert columns == {"id", "user_id", "mode", "subject", "score", "max_score", "created_at"}


# ------------------------------------------------------------------ AC-C1-2
@pytest.mark.parametrize(
    "score",
    [-0.5, -1, 10.01, 11, math.nan, math.inf, -math.inf, None, "6", True, False],
)
def test_validated_score_rejects_invalid(score):
    assert history.validated_score(score, 10) is None


@pytest.mark.parametrize("score,expected", [(0, 0.0), (6, 6.0), (7.5, 7.5), (10, 10.0)])
def test_validated_score_accepts_bounds(score, expected):
    assert history.validated_score(score, 10) == expected


@pytest.mark.parametrize("max_score", [0, -1, -10, None, 10.0, "10", True, math.nan])
def test_valid_max_score_rejects(max_score):
    assert history.valid_max_score(max_score) is None


@pytest.mark.parametrize("score", [-1.0, 10.5, math.nan, math.inf])
def test_record_attempt_stores_invalid_score_as_null(make_user, score):
    u = make_user("c1_null")
    assert history.record_attempt(
        user_id=u.id, mode="basic", subject="Ethics", score=score, max_score=10
    )
    rows = _rows(u.id)
    assert len(rows) == 1 and rows[0].score is None and rows[0].max_score == 10


@pytest.mark.parametrize("max_score", [0, -5, None, 10.0])
def test_record_attempt_writes_no_row_without_positive_int_max(make_user, max_score):
    u = make_user("c1_nomax")
    assert (
        history.record_attempt(
            user_id=u.id, mode="mains", subject=None, score=5.0, max_score=max_score
        )
        is False
    )
    assert _rows(u.id) == []


def test_record_attempt_rejects_unknown_mode_and_subject(make_user):
    u = make_user("c1_allow")
    with pytest.raises(ValueError):
        history.record_attempt(user_id=u.id, mode="model", subject=None, score=1, max_score=10)
    with pytest.raises(ValueError):
        history.record_attempt(user_id=u.id, mode="basic", subject="Maths", score=1, max_score=10)
    with pytest.raises(ValueError):
        history.record_attempt(user_id="", mode="basic", subject=None, score=1, max_score=10)
    assert _rows(u.id) == []


def test_unparseable_evaluation_records_unscored_row(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model("Looks fine overall."))
    u = make_user("c1_unscored")
    r = _basic(client, u, subject="Geography")
    assert r.status_code == 200 and r.json()["structured"]["score"] is None
    rows = _rows(u.id)
    assert len(rows) == 1 and rows[0].score is None and rows[0].max_score == 10


def test_off_scale_mains_score_is_unscored(client, make_user, monkeypatch):
    # "16/10" exceeds the requested 10 marks -> parser and recorder both give None.
    monkeypatch.setattr(evaluator, "get_llm", _model("## Score: 16/10\n"))
    u = make_user("c1_offscale")
    assert _mains(client, u, marks=10).status_code == 200
    rows = _rows(u.id)
    assert len(rows) == 1 and rows[0].score is None and rows[0].max_score == 10


# ------------------------------------------------------------------ AC-C1-3
@pytest.mark.parametrize(
    "endpoint,text,send",
    [(BASIC, EVAL_TEXT, _basic), (MAINS, MAINS_TEXT, _mains)],
)
def test_record_failure_keeps_response_and_logs_redacted_warning(
    client, make_user, monkeypatch, caplog, endpoint, text, send
):
    monkeypatch.setattr(evaluator, "get_llm", _model(text))
    u = make_user("c1_fail")
    baseline = send(client, u, subject="Economy")
    assert baseline.status_code == 200

    secret = "db-password-in-exception-message"

    def boom(**_kwargs):
        raise RuntimeError(f"{secret} {QUESTION} {ANSWER}")

    monkeypatch.setattr(evaluator_routes, "record_attempt", boom)
    with caplog.at_level(logging.WARNING, logger=evaluator_routes.__name__):
        r = send(client, u, subject="Economy")
    assert r.status_code == 200
    assert r.json() == baseline.json()

    records = [rec for rec in caplog.records if rec.name == evaluator_routes.__name__]
    assert len(records) == 1
    rec = records[0]
    assert rec.getMessage() == "evaluation_attempt_persist_failed"
    assert rec.event == "evaluation_attempt_persist_failed"
    assert rec.mode == ("basic" if endpoint == BASIC else "mains")
    assert rec.error_type == "RuntimeError"
    assert rec.exc_info is None
    # Exactly the approved fields are attached to the record - nothing else.
    # (request_id is added to every record by the app-wide request-id log filter.)
    standard = logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "request_id"}
    extra = {k: v for k, v in rec.__dict__.items() if k not in standard}
    assert extra == {
        "event": "evaluation_attempt_persist_failed",
        "mode": rec.mode,
        "error_type": "RuntimeError",
    }
    for leaked in (secret, "C1-Q-MARKER", "C1-A-MARKER", u.email):
        assert leaked not in caplog.text
    # The pre-existing src.core.observability INFO line logs the parsed score
    # (not C1 code), so the score text is checked on the C1 warning only.
    c1_text = rec.getMessage() + repr(extra)
    for leaked in ("6/10", "9/15", "6.0", "9.0"):
        assert leaked not in c1_text
    assert len(_rows(u.id)) == 1  # only the baseline call recorded


def test_real_db_failure_keeps_response(client, make_user, monkeypatch, caplog):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))

    def broken_session():
        raise OperationalError("INSERT INTO evaluation_attempts", {}, Exception("db down"))

    monkeypatch.setattr(history, "SessionLocal", broken_session)
    u = make_user("c1_dbdown")
    with caplog.at_level(logging.WARNING, logger=evaluator_routes.__name__):
        r = _basic(client, u)
    assert r.status_code == 200
    assert r.json()["structured"]["score"] == 6.0
    assert "evaluation_attempt_persist_failed" in caplog.text
    assert "db down" not in caplog.text
    monkeypatch.undo()
    assert _rows(u.id) == []


# ------------------------------------------------------------------ AC-C1-4
def test_two_users_rows_are_attributed_to_the_caller(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))
    u1, u2 = make_user("c1_u1"), make_user("c1_u2")
    assert _basic(client, u1, subject="Economy").status_code == 200
    monkeypatch.setattr(evaluator, "get_llm", _model(MAINS_TEXT))
    assert _mains(client, u2, marks=15, subject="Ethics").status_code == 200

    r1, r2 = _rows(u1.id), _rows(u2.id)
    assert [(r.mode, r.subject) for r in r1] == [("basic", "Economy")]
    assert [(r.mode, r.subject) for r in r2] == [("mains", "Ethics")]


def test_client_cannot_choose_the_owner(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))
    victim, attacker = make_user("c1_victim"), make_user("c1_attacker")
    r = _basic(client, attacker, user_id=victim.id, mode="mains", score=10, max_score=10)
    assert r.status_code == 200  # unknown fields are ignored, never trusted
    assert _rows(victim.id) == []
    rows = _rows(attacker.id)
    assert len(rows) == 1 and (rows[0].mode, rows[0].score) == ("basic", 6.0)


@pytest.mark.parametrize("endpoint", [BASIC, MAINS])
def test_unauthenticated_is_401_and_writes_nothing(client, monkeypatch, endpoint):
    called = []
    monkeypatch.setattr(evaluator_routes, "record_attempt", lambda **k: called.append(k))
    r = client.post(endpoint, json={"question": QUESTION, "answer": ANSWER, "subject": "Economy"})
    assert r.status_code == 401
    assert not called


def test_response_shapes_unchanged(client, make_user, monkeypatch):
    monkeypatch.setattr(evaluator, "get_llm", _model(EVAL_TEXT))
    u = make_user("c1_shape")
    body = _basic(client, u, subject="Economy").json()
    assert set(body) == {"response", "structured"}
    assert set(body["structured"]) == {"score", "max_score", "did_well", "missing", "improvements"}
    monkeypatch.setattr(evaluator, "get_llm", _model(MAINS_TEXT))
    body = _mains(client, u).json()
    assert set(body) == {"response", "structured"}
    assert set(body["structured"]) == {
        "score",
        "max_marks",
        "verdict",
        "strengths",
        "gaps",
        "improvements",
    }


# ------------------------------------------------------------- migration 0006
def _alembic_cfg(db_url: str):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def test_migration_0006_upgrade_indexes_cascade_and_downgrade(tmp_path):
    from alembic import command

    db_url = f"sqlite:///{(tmp_path / 'mig_0006.db').as_posix()}"
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0006_evaluation_attempts")
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "evaluation_attempts" in insp.get_table_names()
        cols = {c["name"]: c for c in insp.get_columns("evaluation_attempts")}
        assert set(cols) == {"id", "user_id", "mode", "subject", "score", "max_score", "created_at"}
        assert cols["subject"]["nullable"] and cols["score"]["nullable"]
        assert not cols["user_id"]["nullable"] and not cols["max_score"]["nullable"]
        indexes = {i["name"]: i["column_names"] for i in insp.get_indexes("evaluation_attempts")}
        assert indexes["ix_evaluation_attempts_user_id"] == ["user_id"]
        assert indexes["ix_evaluation_attempts_user_subject_created"] == [
            "user_id",
            "subject",
            "created_at",
        ]
        fks = insp.get_foreign_keys("evaluation_attempts")
        assert fks[0]["referred_table"] == "users" and fks[0]["constrained_columns"] == ["user_id"]
        assert fks[0]["options"].get("ondelete") == "CASCADE"

        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.execute(
                text(
                    "INSERT INTO users (id, email, name, hashed_password, email_verified, created_at)"
                    " VALUES ('u1', 'm@example.com', 'M', 'x', 1, '2026-11-04')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO evaluation_attempts (id, user_id, mode, subject, score, max_score,"
                    " created_at) VALUES ('a', 'u1', 'basic', NULL, NULL, 10, '2026-11-04')"
                )
            )
            conn.execute(text("DELETE FROM users WHERE id='u1'"))
            assert conn.execute(text("SELECT COUNT(*) FROM evaluation_attempts")).scalar() == 0
            conn.execute(
                text(
                    "INSERT INTO users (id, email, name, hashed_password, email_verified, created_at)"
                    " VALUES ('u2', 'k@example.com', 'K', 'x', 1, '2026-11-04')"
                )
            )

        command.downgrade(cfg, "0005_study_plans")
        insp = inspect(engine)
        assert "evaluation_attempts" not in insp.get_table_names()
        assert "study_plans" in insp.get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT email FROM users WHERE id='u2'")).scalar() == (
                "k@example.com"
            )

        command.upgrade(cfg, "head")
        assert "evaluation_attempts" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
