"""Phase 02 — Personal PYQ bank ownership regression (spec AC6, R15).

The bank key is built server-side from the authenticated user
(``pyqbank_<user id>``). User B's status / generate / clear calls must only ever
touch B's own collection, never A's. Vector store is faked.
"""

import pytest

import src.core.vector_store as vector_store

API = "/api/v1"


@pytest.fixture
def bank(monkeypatch):
    touched: list[str] = []
    existing: set[str] = set()

    def exists(key):
        touched.append(key)
        return key in existing

    def load(key):
        touched.append(key)
        return object() if key in existing else None

    def persist_dir(key):
        touched.append(key)
        return "Z:/definitely/not/a/real/dir/" + key

    monkeypatch.setattr(vector_store, "vector_store_exists", exists)
    monkeypatch.setattr(vector_store, "load_vector_store", load)
    monkeypatch.setattr(vector_store, "_use_qdrant", lambda: False)
    monkeypatch.setattr(vector_store, "persist_dir_for", persist_dir)
    return touched, existing


def test_user_b_cannot_reach_user_a_bank(client, make_user, bank):
    touched, existing = bank
    a, b = make_user("bank_a"), make_user("bank_b")
    a_key, b_key = f"pyqbank_{a.id}".lower(), f"pyqbank_{b.id}".lower()
    existing.add(a_key)  # A has built a bank

    status = client.get(f"{API}/pyq/bank/status", headers=b.headers)
    assert status.status_code == 200
    assert status.json()["exists"] is False

    gen = client.post(f"{API}/pyq/bank/generate", json={"topic": "polity"}, headers=b.headers)
    assert gen.status_code == 200
    assert "haven't uploaded any question paper" in gen.text

    clear = client.post(f"{API}/pyq/bank/clear", headers=b.headers)
    assert clear.status_code == 200
    assert clear.json() == {"cleared": False}

    assert touched and set(touched) == {b_key}
    assert a_key not in touched


def test_owner_sees_own_bank(client, make_user, bank):
    touched, existing = bank
    a = make_user("bank_owner")
    existing.add(f"pyqbank_{a.id}".lower())
    r = client.get(f"{API}/pyq/bank/status", headers=a.headers)
    assert r.json()["exists"] is True
    assert set(touched) == {f"pyqbank_{a.id}".lower()}


def test_bank_key_is_server_side_only():
    from src.agents.pyq.graph import _bank_key

    assert _bank_key("abc") == "pyqbank_abc"
    # BankGenRequest carries no key / user field a client could use to pick a bank.
    from src.schemas import BankGenRequest

    assert not {"user_id", "persist_key", "bank_key"} & set(BankGenRequest.model_fields)
