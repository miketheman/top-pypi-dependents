import logging

import pytest

from top_pypi_dependents import log

LOGGER = logging.getLogger("top_pypi_dependents.test")


def test_stage_logs_the_start_and_the_finish(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO), log.stage(LOGGER, "extract"):
        pass
    messages = [record.message for record in caplog.records]
    assert messages[0] == "extract: started"
    assert messages[1].startswith("extract: done in ")


def test_stage_reports_elapsed_seconds(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO), log.stage(LOGGER, "build"):
        pass
    assert caplog.records[-1].message.endswith("s")
    assert caplog.records[-1].levelno == logging.INFO


def test_stage_reports_outcome_fields_collected_in_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stage that says only how long it took cannot say whether it worked."""
    with caplog.at_level(logging.INFO), log.stage(LOGGER, "extract") as outcome:
        outcome["rows"] = 1003087
    assert "rows=1003087" in caplog.records[-1].message


def test_stage_logs_an_error_before_letting_the_exception_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unattended monthly run has to say which stage died, in one line."""
    error = ValueError("boom")
    with (
        caplog.at_level(logging.INFO),
        pytest.raises(ValueError, match="boom"),
        log.stage(LOGGER, "build"),
    ):
        raise error
    failure = caplog.records[-1]
    assert failure.levelno == logging.ERROR
    assert failure.message.startswith("build: failed after ")
    assert "boom" in failure.message


def test_configure_sets_the_requested_level() -> None:
    log.configure("debug")
    assert logging.getLogger("top_pypi_dependents").level == logging.DEBUG
    log.configure("info")
    assert logging.getLogger("top_pypi_dependents").level == logging.INFO


def test_configure_is_idempotent() -> None:
    """`main` may be called more than once in a test session."""
    log.configure("info")
    log.configure("info")
    assert len(logging.getLogger("top_pypi_dependents").handlers) == 1
