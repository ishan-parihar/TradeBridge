from __future__ import annotations

import logging
import re

import pytest

from mt5_mcp.observability.logging import (
    CorrelationFilter,
    correlation_id,
    intent_id,
    request_id,
    set_correlation_id,
    set_intent_id,
    set_request_id,
)


@pytest.fixture(autouse=True)
def reset_contextvars():
    correlation_id.set(None)
    intent_id.set(None)
    request_id.set(None)
    yield


class TestCorrelationFilter:
    def test_adds_correlation_id_to_record(self):
        correlation_id.set("corr-123")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        f = CorrelationFilter()
        assert f.filter(record) is True
        assert record.correlation_id == "corr-123"

    def test_adds_intent_id_to_record(self):
        intent_id.set("intent-456")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        f = CorrelationFilter()
        assert f.filter(record) is True
        assert record.intent_id == "intent-456"

    def test_adds_request_id_to_record(self):
        request_id.set("req-789")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        f = CorrelationFilter()
        assert f.filter(record) is True
        assert record.request_id == "req-789"

    def test_defaults_to_dash_when_not_set(self):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        f = CorrelationFilter()
        assert f.filter(record) is True
        assert record.correlation_id == "-"
        assert record.intent_id == "-"
        assert record.request_id == "-"

    def test_all_fields_set_together(self):
        correlation_id.set("c1")
        intent_id.set("i1")
        request_id.set("r1")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        f = CorrelationFilter()
        f.filter(record)
        assert record.correlation_id == "c1"
        assert record.intent_id == "i1"
        assert record.request_id == "r1"


class TestContextVarSetters:
    def test_set_correlation_id(self):
        set_correlation_id("test-corr")
        assert correlation_id.get() == "test-corr"

    def test_set_intent_id(self):
        set_intent_id("test-intent")
        assert intent_id.get() == "test-intent"

    def test_set_request_id(self):
        set_request_id("test-req")
        assert request_id.get() == "test-req"


class TestContextIsolation:
    def test_setting_one_context_does_not_affect_others(self):
        set_correlation_id("corr-a")
        assert correlation_id.get() == "corr-a"
        assert intent_id.get() is None
        assert request_id.get() is None

    def test_reset_isolates_context(self):
        set_correlation_id("corr-b")
        correlation_id.set(None)
        assert correlation_id.get() is None

    def test_context_independent_across_tests(self, reset_contextvars):
        set_correlation_id("isolated-corr")
        set_intent_id("isolated-intent")
        set_request_id("isolated-req")
        assert correlation_id.get() == "isolated-corr"
        assert intent_id.get() == "isolated-intent"
        assert request_id.get() == "isolated-req"


class TestLogOutputFormat:
    def test_format_includes_correlation_fields(self):
        root = logging.getLogger()
        original_level = root.level
        original_handlers = root.handlers[:]

        try:
            root.handlers = []
            root.setLevel(logging.DEBUG)

            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s "
                    "[corr:%(correlation_id)s] [intent:%(intent_id)s] [req:%(request_id)s] %(message)s"
                )
            )
            handler.addFilter(CorrelationFilter())
            root.addHandler(handler)

            set_correlation_id("fmt-corr")
            set_intent_id("fmt-intent")
            set_request_id("fmt-req")

            import io

            stream = io.StringIO()
            handler.stream = stream

            logger = logging.getLogger("mt5_mcp.test")
            logger.info("test message")

            output = stream.getvalue()
            assert "[corr:fmt-corr]" in output
            assert "[intent:fmt-intent]" in output
            assert "[req:fmt-req]" in output
            assert "test message" in output
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

    def test_format_shows_dash_for_unset_fields(self):
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_level = root.level

        try:
            root.handlers = []
            root.setLevel(logging.DEBUG)

            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s "
                    "[corr:%(correlation_id)s] [intent:%(intent_id)s] [req:%(request_id)s] %(message)s"
                )
            )
            handler.addFilter(CorrelationFilter())
            root.addHandler(handler)

            import io

            stream = io.StringIO()
            handler.stream = stream

            logger = logging.getLogger("mt5_mcp.test")
            logger.info("test message")

            output = stream.getvalue()
            assert "[corr:-]" in output
            assert "[intent:-]" in output
            assert "[req:-]" in output
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)
