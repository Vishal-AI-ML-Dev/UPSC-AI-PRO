"""Offline smoke checks for the 8 product workflows (Phase 01 baseline).

For one primary endpoint per workflow this asserts that:

* the route is mounted on the real app under ``/api/v1``;
* it refuses requests without a token, or with an invalid token (401) -- sent
  with a *valid* body so the 401 can only come from authentication;
* with a valid session it rejects an invalid body (422) -- so the 422 can only
  come from request validation.

No LLM, network, vector store or background job is reached: auth and validation
both fail before the handler runs. Happy paths are covered per workflow elsewhere
and are deliberately out of scope here.
"""

import pytest

API = "/api/v1"

# (workflow, method, path, query params, valid body, invalid body)
WORKFLOWS = [
    (
        "ai_mentor",
        "POST",
        "/agent/chat/stream",
        {},
        {"question": "Explain the basic structure doctrine."},
        {"question": ""},
    ),
    (
        "ncert_study",
        "POST",
        "/ncert/study",
        {},
        {"class_name": "Class 11", "subject": "Polity", "chapter": "Constitution"},
        {"class_name": "Class 11"},
    ),
    (
        "pyq",
        "POST",
        "/pyq/generate",
        {},
        {"topic": "Federalism"},
        {"topic": ""},
    ),
    (
        "answer_evaluator",
        "POST",
        "/evaluator/evaluate/sync",
        {},
        {"question": "Discuss federalism in India.", "answer": "x" * 60},
        {"question": "Discuss federalism in India.", "answer": "too short"},
    ),
    (
        "current_affairs",
        "POST",
        "/current-affairs/daily",
        {},
        {"date": "01 November 2026"},
        {},
    ),
    (
        "study_planner",
        "POST",
        "/planner/generate/sync",
        {},
        {"goal": "UPSC 2027"},
        {"hours": "6"},
    ),
    (
        "lecture_learning",
        "POST",
        "/lecture/process-text",
        {},
        {"transcript": "Today we discuss the Preamble."},
        {"transcript": ""},
    ),
    (
        "pdf_document_chat",
        "POST",
        "/upload/chat",
        {"pdf_hash": "0" * 32},
        {"question": "Summarise chapter one."},
        {"question": ""},
    ),
]

IDS = [w[0] for w in WORKFLOWS]


def _mounted(app, method: str, path: str) -> bool:
    # Use the generated OpenAPI schema: recent FastAPI versions wrap included
    # routers lazily, so ``app.routes`` no longer lists every APIRoute.
    return method.lower() in app.openapi()["paths"].get(path, {})


@pytest.mark.parametrize("name,method,path,params,valid,invalid", WORKFLOWS, ids=IDS)
def test_workflow_route_is_mounted(client, name, method, path, params, valid, invalid):
    assert _mounted(client.app, method, API + path), f"{name}: {method} {API + path} not mounted"


@pytest.mark.parametrize("name,method,path,params,valid,invalid", WORKFLOWS, ids=IDS)
def test_workflow_requires_token(client, name, method, path, params, valid, invalid):
    r = client.request(method, API + path, params=params, json=valid)
    assert r.status_code == 401, f"{name}: expected 401, got {r.status_code}"
    assert r.json()["detail"] == "Not authenticated"
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


@pytest.mark.parametrize("name,method,path,params,valid,invalid", WORKFLOWS, ids=IDS)
def test_workflow_rejects_invalid_token(client, name, method, path, params, valid, invalid):
    headers = {"Authorization": "Bearer not-a-real-token"}
    r = client.request(method, API + path, params=params, json=valid, headers=headers)
    assert r.status_code == 401, f"{name}: expected 401, got {r.status_code}"
    # Reached the real JWT/session check in src/api/deps.py (unauthorized("invalid")).
    assert r.json()["detail"] == "Invalid or expired token"
    assert 'error="invalid_token"' in r.headers.get("www-authenticate", "")


@pytest.mark.parametrize("name,method,path,params,valid,invalid", WORKFLOWS, ids=IDS)
def test_workflow_validates_body(client, make_user, name, method, path, params, valid, invalid):
    user = make_user("smoke")
    r = client.request(method, API + path, params=params, json=invalid, headers=user.headers)
    assert r.status_code == 422, f"{name}: expected 422, got {r.status_code}"
    errors = r.json()["detail"]
    assert isinstance(errors, list) and errors
    assert all(e["loc"][0] == "body" for e in errors), f"{name}: non-body error {errors}"


def test_upload_chat_requires_pdf_hash(client, make_user):
    """The PDF chat endpoint also needs its ``pdf_hash`` query parameter."""
    user = make_user("smoke")
    r = client.post(f"{API}/upload/chat", json={"question": "Summarise."}, headers=user.headers)
    assert r.status_code == 422
    assert any(e["loc"] == ["query", "pdf_hash"] for e in r.json()["detail"])
