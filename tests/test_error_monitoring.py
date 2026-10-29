"""Unit tests for Sentry error monitoring (src.core.error_monitoring).

Fully offline & side-effect-free: sentry_sdk.init is spied (never creates a real
client), so no events are buffered and the suite never hangs flushing at exit.
"""

from __future__ import annotations

import src.core.error_monitoring as em

_FAKE_DSN = "https://public@o1.ingest.sentry.io/1"


def _clear():
    em.sentry_enabled.cache_clear()


def test_disabled_when_no_dsn(monkeypatch):
    monkeypatch.setattr(em.settings, "sentry_dsn", "")
    _clear()
    assert em.sentry_enabled() is False
    assert em.init_sentry() is False


def test_enabled_flag_with_dsn(monkeypatch):
    monkeypatch.setattr(em.settings, "sentry_dsn", _FAKE_DSN)
    _clear()
    assert em.sentry_enabled() is True


def test_init_calls_sdk_and_returns_true(monkeypatch):
    monkeypatch.setattr(em.settings, "sentry_dsn", _FAKE_DSN)
    monkeypatch.setattr(em.settings, "sentry_environment", "test")
    _clear()

    captured = {}

    import sentry_sdk

    def _spy_init(**kwargs):
        # Spy: record args, never create a real client (no network, no
        # buffered events, no exit-time flush hang).
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", _spy_init)

    assert em.init_sentry() is True
    assert captured["dsn"] == _FAKE_DSN
    assert captured["environment"] == "test"
    assert captured["send_default_pii"] is False


def test_init_failopen_on_error(monkeypatch):
    monkeypatch.setattr(em.settings, "sentry_dsn", _FAKE_DSN)
    _clear()
    import sentry_sdk

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sentry_sdk, "init", _boom)
    # Fail-open: error is swallowed, returns False, never raises.
    assert em.init_sentry() is False


def test_capture_exception_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(em.settings, "sentry_dsn", "")
    _clear()
    # Must not raise even though Sentry is off.
    em.capture_exception(ValueError("ignored"))
