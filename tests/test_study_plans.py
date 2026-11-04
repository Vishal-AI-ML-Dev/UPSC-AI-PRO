"""Phase 08 Slice B: latest-study-plan persistence (spec R-B1..R-B5, AC-B1..AC-B6).

Offline: the real app on the throwaway SQLite DB (``tests/conftest.py``); the
plan metadata helper is replaced so no web search is attempted. Migration checks
use throwaway SQLite files, like ``tests/test_migrations.py``.
"""

import pathlib
import sqlite3
import tempfile
import threading
import uuid

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from src.core import study_plans
from src.core.db import SessionLocal
from src.core.models import StudyPlan
from src.schemas import StudyPlanMeta

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLAN = "/api/v1/planner/plan"
PROGRESS = "/api/v1/planner/plan/progress"
SAVE_UNAVAILABLE = "Could not save your study plan right now. Please try again."


@pytest.fixture(autouse=True)
def _offline_meta(monkeypatch):
    import src.api.routes.planner as planner_routes

    monkeypatch.setattr(
        planner_routes,
        "get_plan_meta",
        lambda goal: StudyPlanMeta(attempt_year=2027, months_left=7, timeline_msg="t"),
    )


def _body(goal="UPSC 2027", plan_md="## Week 1\n- Polity basics", **inputs):
    return {"goal": goal, "inputs": {"hours": "6", **inputs}, "plan_md": plan_md}


def _rows(user_id: str) -> list[StudyPlan]:
    with SessionLocal() as db:
        return list(db.scalars(select(StudyPlan).where(StudyPlan.user_id == user_id)))


def _put(client, user, **kw):
    r = client.put(PLAN, json=_body(**kw), headers=user.headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- auth (AC-B2)
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", PLAN, None),
        ("put", PLAN, _body()),
        ("patch", PROGRESS, {"plan_id": "a" * 32, "progress": {}}),
        ("delete", PLAN, None),
    ],
)
def test_every_endpoint_requires_auth(client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 401


# ---------------------------------------------------------- happy path (AC-B2)
def test_put_get_patch_get_delete_get(client, make_user):
    u = make_user("plan_happy")
    assert client.get(PLAN, headers=u.headers).json() == {"plan": None}

    saved = _put(client, u, goal="UPSC 2027", weak="Economy")
    assert len(saved["id"]) == 32 and saved["updated_at"]

    plan = client.get(PLAN, headers=u.headers).json()["plan"]
    assert plan["id"] == saved["id"]
    assert plan["goal"] == "UPSC 2027"
    assert plan["plan_md"] == "## Week 1\n- Polity basics"
    assert plan["inputs"]["weak"] == "Economy" and plan["inputs"]["hours"] == "6"
    assert plan["meta"] == {
        "attempt_year": 2027,
        "months_left": 7,
        "timeline_msg": "t",
        "prelims_date": None,
    }
    assert plan["progress"] == {}

    ticks = {"pabc:0-week-1:0": True, "pabc:0-week-1:1": False}
    r = client.patch(PROGRESS, json={"plan_id": saved["id"], "progress": ticks}, headers=u.headers)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == saved["id"] and r.json()["progress"] == ticks

    assert client.get(PLAN, headers=u.headers).json()["plan"]["progress"] == ticks

    assert client.delete(PLAN, headers=u.headers).status_code == 204
    assert client.get(PLAN, headers=u.headers).json() == {"plan": None}
    assert _rows(u.id) == []
    # Idempotent.
    assert client.delete(PLAN, headers=u.headers).status_code == 204


def test_patch_without_plan_is_404(client, make_user):
    u = make_user("plan_none")
    r = client.patch(PROGRESS, json={"plan_id": "a" * 32, "progress": {}}, headers=u.headers)
    assert r.status_code == 404
    assert r.json()["detail"] == "No saved study plan."


def test_patch_with_unknown_plan_id_is_409(client, make_user):
    u = make_user("plan_wrong_id")
    saved = _put(client, u)
    r = client.patch(
        PROGRESS,
        json={"plan_id": uuid.uuid4().hex, "progress": {"p:a:0": True}},
        headers=u.headers,
    )
    assert r.status_code == 409
    assert _rows(u.id)[0].progress == "{}"
    assert _rows(u.id)[0].id == saved["id"]


# ------------------------------------------------------------ isolation (AC-B3)
def test_users_cannot_read_or_change_each_others_plan(client, make_user):
    u1, u2 = make_user("plan_iso1"), make_user("plan_iso2")
    s1 = _put(client, u1, goal="U1 goal", plan_md="U1 plan")
    _ = client.patch(
        PROGRESS, json={"plan_id": s1["id"], "progress": {"p:a:0": True}}, headers=u1.headers
    )

    # U2 has no plan: U1's plan is invisible and U1's id is not addressable.
    assert client.get(PLAN, headers=u2.headers).json() == {"plan": None}
    r = client.patch(
        PROGRESS, json={"plan_id": s1["id"], "progress": {"p:a:0": False}}, headers=u2.headers
    )
    assert r.status_code == 404

    # U2 with its own plan still cannot address U1's id.
    s2 = _put(client, u2, goal="U2 goal", plan_md="U2 plan")
    r = client.patch(
        PROGRESS, json={"plan_id": s1["id"], "progress": {"p:a:0": False}}, headers=u2.headers
    )
    assert r.status_code == 409
    assert client.get(PLAN, headers=u2.headers).json()["plan"]["id"] == s2["id"]

    # U2 deleting its plan never touches U1's.
    assert client.delete(PLAN, headers=u2.headers).status_code == 204
    p1 = client.get(PLAN, headers=u1.headers).json()["plan"]
    assert p1["id"] == s1["id"]
    assert p1["goal"] == "U1 goal" and p1["plan_md"] == "U1 plan"
    assert p1["progress"] == {"p:a:0": True}


def test_client_cannot_supply_a_user_id(client, make_user):
    u1, u2 = make_user("plan_uid1"), make_user("plan_uid2")
    r = client.put(PLAN, json={**_body(), "user_id": u1.id}, headers=u2.headers)
    assert r.status_code == 422
    assert _rows(u1.id) == [] and _rows(u2.id) == []


# ---------------------------------------------------------- latest only (AC-B4)
def test_second_put_replaces_with_new_id_and_resets_progress(client, make_user):
    u = make_user("plan_replace")
    first = _put(client, u, goal="first", plan_md="first plan")
    client.patch(
        PROGRESS, json={"plan_id": first["id"], "progress": {"p:a:0": True}}, headers=u.headers
    )

    second = _put(client, u, goal="second", plan_md="second plan")
    assert second["id"] != first["id"]

    rows = _rows(u.id)
    assert len(rows) == 1
    assert rows[0].id == second["id"]
    assert rows[0].goal == "second" and rows[0].plan_md == "second plan"
    assert rows[0].progress == "{}"

    r = client.patch(
        PROGRESS, json={"plan_id": first["id"], "progress": {"p:a:1": True}}, headers=u.headers
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "This study plan was replaced by a newer one. Reload to see it."
    assert client.get(PLAN, headers=u.headers).json()["plan"]["progress"] == {}


# ------------------------------------------------------------ validation (AC-B5)
@pytest.mark.parametrize(
    "body",
    [
        _body(plan_md="x" * 100_001),
        _body(plan_md=""),
        _body(goal="g" * 201),
        {"goal": "g", "inputs": {"hours": "h" * 201}, "plan_md": "x"},
        {"goal": "g", "inputs": {"attempt_number": "1" * 21}, "plan_md": "x"},
        {"goal": "g", "inputs": {"unknown": "x"}, "plan_md": "x"},
        {"goal": "g"},
    ],
    ids=[
        "plan_md_too_long",
        "plan_md_empty",
        "goal_too_long",
        "hours_too_long",
        "attempt_too_long",
        "unknown_input",
        "plan_md_missing",
    ],
)
def test_put_rejects_invalid_payloads(client, make_user, body):
    u = make_user("plan_bad_put")
    assert client.put(PLAN, json=body, headers=u.headers).status_code == 422
    assert _rows(u.id) == []


def test_put_accepts_maximum_sizes(client, make_user):
    u = make_user("plan_max")
    saved = _put(client, u, goal="g" * 200, plan_md="x" * 100_000)
    assert len(_rows(u.id)[0].plan_md) == 100_000
    assert saved["id"] == _rows(u.id)[0].id


@pytest.mark.parametrize(
    "progress",
    [
        {f"p:a:{i}": True for i in range(1001)},
        {"bad key": True},
        {"p:a:b": True},
        {"p:a:12345": True},
        {"p:a:1\n": True},
        {"p" * 157 + ":a:1": True},
        {"p:a:1": 1},
        {"p:a:1": "true"},
        {"p:a:1": None},
    ],
    ids=[
        "too_many",
        "space",
        "non_digit_index",
        "index_too_long",
        "newline",
        "key_too_long",
        "int_value",
        "string_value",
        "null_value",
    ],
)
def test_patch_rejects_invalid_progress(client, make_user, progress):
    u = make_user("plan_bad_patch")
    saved = _put(client, u)
    r = client.patch(
        PROGRESS, json={"plan_id": saved["id"], "progress": progress}, headers=u.headers
    )
    assert r.status_code == 422
    assert _rows(u.id)[0].progress == "{}"


def test_patch_accepts_1000_entries_and_rejects_bad_plan_id(client, make_user):
    u = make_user("plan_patch_bounds")
    saved = _put(client, u)
    full = {f"p:a:{i}": i % 2 == 0 for i in range(1000)}
    r = client.patch(PROGRESS, json={"plan_id": saved["id"], "progress": full}, headers=u.headers)
    assert r.status_code == 200 and r.json()["progress"] == full
    for bad in ("not-hex", saved["id"].upper(), saved["id"] + "0"):
        r = client.patch(PROGRESS, json={"plan_id": bad, "progress": {}}, headers=u.headers)
        assert r.status_code == 422


# ----------------------------------------------------- persistence (AC-B1)
def test_saved_plan_survives_a_restart(client, make_user):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core.db import engine

    u = make_user("plan_restart")
    saved = _put(client, u, goal="persist me", plan_md="persisted plan")
    client.patch(
        PROGRESS, json={"plan_id": saved["id"], "progress": {"p:a:0": True}}, headers=u.headers
    )

    # Drop every pooled connection, then read through a brand-new session and client.
    engine.dispose()
    with SessionLocal() as db:
        assert study_plans.get_plan(db, u.id)["id"] == saved["id"]
    fresh = TestClient(app)
    plan = fresh.get(PLAN, headers=u.headers).json()["plan"]
    assert plan["id"] == saved["id"]
    assert plan["plan_md"] == "persisted plan"
    assert plan["progress"] == {"p:a:0": True}


# ----------------------------------------------------- concurrency (AC-B6)
def test_sqlite_supports_on_conflict_and_returning():
    assert sqlite3.sqlite_version_info >= (3, 35, 0)


def test_concurrent_puts_leave_one_complete_row(client, make_user):
    u = make_user("plan_race")
    for rnd in range(20):
        barrier = threading.Barrier(2)
        results: dict[str, object] = {}

        def save(tag: str) -> None:
            body = _body(goal=f"goal-{tag}-{rnd}", plan_md=f"plan-{tag}-{rnd}", weak=tag)
            barrier.wait()
            try:
                r = client.put(PLAN, json=body, headers=u.headers)
                results[tag] = (r.status_code, r.json())
            except Exception as exc:  # pragma: no cover - surfaced by the assert below
                results[tag] = exc

        threads = [threading.Thread(target=save, args=(t,)) for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert set(results) == {"A", "B"}, f"round {rnd}: {results}"
        for tag, res in results.items():
            assert not isinstance(res, Exception), f"round {rnd} {tag}: {res!r}"
            assert res[0] in (200, 503), f"round {rnd} {tag}: {res}"
            if res[0] == 503:
                assert res[1] == {"detail": SAVE_UNAVAILABLE}

        rows = _rows(u.id)
        assert len(rows) == 1, f"round {rnd}: {len(rows)} rows"
        row = rows[0]
        winner = row.goal.split("-")[1]
        assert winner in ("A", "B")
        # The whole row comes from one request ...
        assert row.goal == f"goal-{winner}-{rnd}"
        assert row.plan_md == f"plan-{winner}-{rnd}"
        assert f'"weak": "{winner}"' in row.inputs
        assert row.progress == "{}"
        # ... and its id is the id returned to that same request.
        status, body = results[winner]
        assert status == 200 and body["id"] == row.id
        assert client.get(PLAN, headers=u.headers).json()["plan"]["id"] == row.id


def test_conflict_path_updates_in_place(client, make_user):
    u = make_user("plan_conflict")
    with SessionLocal() as db:
        first = study_plans.upsert_plan(db, u.id, "g1", {"hours": "6"}, "p1", None)
        study_plans.update_progress(db, u.id, first["id"], {"p:a:0": True})
        created = db.scalar(select(StudyPlan.created_at).where(StudyPlan.user_id == u.id))
        second = study_plans.upsert_plan(db, u.id, "g2", {"hours": "8"}, "p2", {"m": 1})
        count = db.scalar(
            select(func.count()).select_from(StudyPlan).where(StudyPlan.user_id == u.id)
        )
        db.expire_all()
        row = db.scalars(select(StudyPlan).where(StudyPlan.user_id == u.id)).one()
    assert count == 1
    assert second["id"] != first["id"] and row.id == second["id"]
    assert (row.goal, row.plan_md, row.inputs, row.meta) == (
        "g2",
        "p2",
        '{"hours": "8"}',
        '{"m": 1}',
    )
    assert row.progress == "{}"
    assert row.created_at == created  # the row was updated, not re-inserted


def test_upsert_is_one_statement_for_postgresql_and_sqlite():
    from sqlalchemy.dialects import postgresql, sqlite

    values = {
        "id": "a" * 32,
        "user_id": "u",
        "goal": "g",
        "inputs": "{}",
        "plan_md": "p",
        "meta": None,
        "progress": "{}",
    }
    for mod in (postgresql, sqlite):
        sql = str(study_plans.upsert_statement(mod.insert, values).compile(dialect=mod.dialect()))
        assert sql.startswith("INSERT INTO study_plans")
        assert "ON CONFLICT (user_id) DO UPDATE SET" in sql
        returning = sql.split(" RETURNING ")[1]
        assert returning.replace("study_plans.", "") == "id, updated_at"
        assert "created_at = " not in sql.split("DO UPDATE SET")[1]


def test_unsupported_dialect_fails_closed():
    class _Bind:
        class dialect:  # noqa: N801
            name = "mysql"

    class _Db:
        def get_bind(self):
            return _Bind()

    with pytest.raises(study_plans.PlanStoreUnavailable):
        study_plans._dialect_insert(_Db())


def _locked():
    return OperationalError("UPSERT", {}, Exception("database is locked"))


def test_put_retries_once_after_a_db_error(client, make_user, monkeypatch):
    u = make_user("plan_retry")
    real = study_plans._upsert_once
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _locked()
        return real(*args, **kwargs)

    monkeypatch.setattr(study_plans, "_upsert_once", flaky)
    saved = _put(client, u)
    assert calls["n"] == 2
    assert [r.id for r in _rows(u.id)] == [saved["id"]]


@pytest.mark.parametrize("error", [_locked(), IntegrityError("UPSERT", {}, Exception("dup"))])
def test_put_returns_fixed_503_after_the_retry_fails(client, make_user, monkeypatch, error):
    u = make_user("plan_503")
    calls = {"n": 0}

    def broken(*args, **kwargs):
        calls["n"] += 1
        raise error

    monkeypatch.setattr(study_plans, "_upsert_once", broken)
    r = client.put(PLAN, json=_body(), headers=u.headers)
    assert r.status_code == 503
    assert r.json() == {"detail": SAVE_UNAVAILABLE}
    assert "locked" not in r.text and "dup" not in r.text
    assert calls["n"] == 2
    assert _rows(u.id) == []


def test_put_other_db_error_is_503_without_retry_or_logged_plan_text(
    client, make_user, monkeypatch, caplog
):
    u = make_user("plan_dataerror")
    calls = {"n": 0}

    def broken(*args, **kwargs):
        calls["n"] += 1
        raise DataError("UPSERT", {"plan_md": "SECRET-PLAN-TEXT"}, Exception("value too long"))

    monkeypatch.setattr(study_plans, "_upsert_once", broken)
    with caplog.at_level("DEBUG"):
        r = client.put(PLAN, json=_body(plan_md="SECRET-PLAN-TEXT"), headers=u.headers)
    assert r.status_code == 503
    assert r.json() == {"detail": SAVE_UNAVAILABLE}
    assert calls["n"] == 1
    assert "SECRET-PLAN-TEXT" not in caplog.text
    assert _rows(u.id) == []


def test_put_when_meta_fails_still_saves(client, make_user, monkeypatch):
    import src.api.routes.planner as planner_routes

    def boom(goal):
        raise RuntimeError("search down")

    monkeypatch.setattr(planner_routes, "get_plan_meta", boom)
    u = make_user("plan_meta_fail")
    saved = _put(client, u)
    plan = client.get(PLAN, headers=u.headers).json()["plan"]
    assert plan["id"] == saved["id"] and plan["meta"] is None


# ------------------------------------------------------------- migration 0005
def _alembic_cfg(db_url: str):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def test_migration_0005_upgrade_constraints_and_downgrade():
    from alembic import command

    db_url = f"sqlite:///{(pathlib.Path(tempfile.mkdtemp()) / 'mig_0005.db').as_posix()}"
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0005_study_plans")
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "study_plans" in insp.get_table_names()
        uniques = insp.get_unique_constraints("study_plans")
        assert any(c["column_names"] == ["user_id"] for c in uniques)
        fks = insp.get_foreign_keys("study_plans")
        assert fks[0]["referred_table"] == "users" and fks[0]["constrained_columns"] == ["user_id"]
        assert fks[0]["options"].get("ondelete") == "CASCADE"

        insert_plan = text(
            "INSERT INTO study_plans (id, user_id, goal, inputs, plan_md, progress, created_at,"
            " updated_at) VALUES (:id, 'u1', 'g', '{}', 'p', '{}', '2026-11-03', '2026-11-03')"
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, name, hashed_password, email_verified, created_at)"
                    " VALUES ('u1', 'm@example.com', 'M', 'x', 1, '2026-11-03')"
                )
            )
            conn.execute(insert_plan, {"id": "a" * 32})
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert_plan, {"id": "b" * 32})

        command.downgrade(cfg, "0004_single_active_session")
        insp = inspect(engine)
        assert "study_plans" not in insp.get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT email FROM users WHERE id='u1'")).scalar() == (
                "m@example.com"
            )

        command.upgrade(cfg, "head")
        assert "study_plans" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
