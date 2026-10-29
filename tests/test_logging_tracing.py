"""Offline tests for request-ID tracing + structured JSON logging."""

import json
import logging
import sys

from src.core.log_formatters import JsonFormatter, RequestIdFilter
from src.core.request_context import (
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)


def test_request_id_lifecycle():
    assert get_request_id() is None
    rid = new_request_id()
    assert len(rid) == 32 and all(c in "0123456789abcdef" for c in rid)
    assert new_request_id() != new_request_id()
    token = set_request_id(rid)
    assert get_request_id() == rid
    reset_request_id(token)
    assert get_request_id() is None


def test_request_id_filter_injects():
    f = RequestIdFilter()
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
    f.filter(rec)
    assert rec.request_id == "-"
    token = set_request_id("abc123")
    try:
        rec2 = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
        f.filter(rec2)
        assert rec2.request_id == "abc123"
    finally:
        reset_request_id(token)


def test_json_formatter_basic():
    fmt = JsonFormatter()
    rec = logging.LogRecord("mylogger", logging.WARNING, __file__, 10, "hi %s", ("x",), None)
    rec.request_id = "rid-1"
    obj = json.loads(fmt.format(rec))
    assert obj["level"] == "WARNING"
    assert obj["logger"] == "mylogger"
    assert obj["msg"] == "hi x"
    assert obj["request_id"] == "rid-1"
    assert "ts" in obj


def test_json_formatter_includes_extra_and_exc():
    fmt = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord("l", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    rec.user_id = 42
    obj = json.loads(fmt.format(rec))
    assert obj["user_id"] == 42
    assert "exc" in obj and "ValueError" in obj["exc"]


def test_json_formatter_non_serializable_extra():
    fmt = JsonFormatter()
    rec = logging.LogRecord("l", logging.INFO, __file__, 1, "m", None, None)
    rec.obj = object()
    obj = json.loads(fmt.format(rec))
    assert "obj" in obj and isinstance(obj["obj"], str)
