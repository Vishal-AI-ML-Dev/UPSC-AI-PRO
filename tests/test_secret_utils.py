"""Tests for JWT secret hardening (pure, offline)."""

import pytest

from src.core.secret_utils import MIN_JWT_SECRET_LEN, resolve_jwt_secret


def test_strong_secret_passthrough():
    s = "x" * MIN_JWT_SECRET_LEN
    assert resolve_jwt_secret(s, is_production=True) == s


def test_empty_secret_fails_fast_in_production():
    with pytest.raises(ValueError):
        resolve_jwt_secret("", is_production=True)


def test_weak_secret_fails_fast_in_production():
    with pytest.raises(ValueError):
        resolve_jwt_secret("short", is_production=True)


def test_whitespace_only_is_treated_as_empty():
    with pytest.raises(ValueError):
        resolve_jwt_secret("   ", is_production=True)


def test_dev_generates_ephemeral_secret():
    out = resolve_jwt_secret("", is_production=False)
    assert len(out) >= MIN_JWT_SECRET_LEN
    # Two dev calls yield different ephemeral secrets.
    assert out != resolve_jwt_secret("", is_production=False)
