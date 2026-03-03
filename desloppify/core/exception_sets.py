"""Shared exception tuples used across command/render flows."""

from __future__ import annotations


class CommandError(Exception):
    """Raised by command helpers to signal a user-facing CLI error.

    The CLI entrypoint catches this and prints ``message`` to stderr
    before exiting with ``exit_code``.  Helpers should raise this instead
    of calling ``sys.exit()`` directly so that the error path is testable
    and composable.
    """

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


PLAN_LOAD_EXCEPTIONS = (
    ImportError,
    AttributeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
)

__all__ = ["CommandError", "PLAN_LOAD_EXCEPTIONS"]
