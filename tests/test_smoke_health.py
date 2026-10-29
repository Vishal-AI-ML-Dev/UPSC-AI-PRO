"""Smoke: the app boots and public/protected routing behaves."""


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert "version" in body


def test_root_ok(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "message" in r.json()


def test_protected_route_requires_auth(client):
    # /pyq/* is mounted behind get_current_user -> no token must give 401.
    r = client.post("/api/v1/pyq/parse", json={"text": "1. dummy?"})
    assert r.status_code == 401
