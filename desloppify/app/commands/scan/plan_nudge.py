"""Post-scan plan workflow nudge.

Dependency-light module that only imports plan/queue helpers — avoids
the import cycle that previously forced function-level imports in scan.py.
"""

from __future__ import annotations

import logging

from desloppify.app.commands.helpers.queue_progress import plan_aware_queue_breakdown
from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.core.output_api import colorize
from desloppify.engine.plan import load_plan

_logger = logging.getLogger(__name__)


def print_plan_workflow_nudge(state: dict) -> None:
    """Print a queue-count reminder when plan-start scores exist."""
    try:
        plan = load_plan()
        if not plan.get("plan_start_scores"):
            return
        breakdown = plan_aware_queue_breakdown(state, plan)
        queue_total = breakdown.actionable
    except PLAN_LOAD_EXCEPTIONS:
        _logger.debug("plan workflow nudge skipped", exc_info=True)
        return

    if queue_total <= 0:
        return
    print(
        colorize(
            f"  Workflow: {queue_total} queue item{'s' if queue_total != 1 else ''}."
            " Score is frozen until the queue is clear — use `desloppify next` to begin.",
            "dim",
        )
    )
