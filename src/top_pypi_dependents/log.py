"""Progress and outcome logging.

Logs go to stderr so stdout stays the command's result: `extract --dry-run`
reports bytes there, and `build` its snapshot summary. A caller can redirect one
without losing the other.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

LEVELS = ("debug", "info", "warning", "error")

_PACKAGE = "top_pypi_dependents"


def configure(level: str = "info") -> None:
    """Attach one stderr handler to the package logger and set its level.

    Idempotent: the handler is added once, so calling this twice in a session --
    as the tests do -- does not double every line.
    """
    logger = logging.getLogger(_PACKAGE)
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)


def _fields(values: dict[str, object]) -> str:
    """Render outcome values as ` (key=value, key=value)`, or nothing."""
    if not values:
        return ""
    rendered = ", ".join(f"{key}={value}" for key, value in values.items())
    return f" ({rendered})"


@contextmanager
def stage(logger: logging.Logger, name: str) -> Iterator[dict[str, object]]:
    """Log a stage's start, duration, and outcome.

    Yields a dict the body fills in with whatever the stage turned out to have
    done -- rows written, bytes scanned -- which is logged on the way out. A
    duration alone says how long something took but never whether it worked.

    On failure it logs which stage died and why before re-raising, so an
    unattended monthly run leaves one greppable line naming the culprit.
    """
    outcome: dict[str, object] = {}
    logger.info("%s: started", name)
    started = time.monotonic()
    try:
        yield outcome
    except Exception as error:
        # Deliberately not `logger.exception`: the traceback is already on its
        # way to stderr as the exception propagates, and printing it twice
        # buries the one line that names the failing stage.
        logger.error(  # noqa: TRY400
            "%s: failed after %.1fs (%s)", name, time.monotonic() - started, error
        )
        raise
    logger.info(
        "%s: done in %.1fs%s", name, time.monotonic() - started, _fields(outcome)
    )
