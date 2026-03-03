"""Scan preflight guard: warn and gate scan when queue has unfinished items."""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

from desloppify import state as state_mod
from desloppify.app.commands.helpers.queue_progress import plan_aware_queue_breakdown
from desloppify.app.commands.helpers.state import state_path
from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS, CommandError
from desloppify.core.output_api import colorize
from desloppify.engine.plan import load_plan, save_plan


def scan_queue_preflight(args) -> None:
    """Warn and gate scan when queue has unfinished items."""
    # CI profile always passes
    if getattr(args, "profile", None) == "ci":
        return

    # --force-rescan with valid attestation bypasses
    if getattr(args, "force_rescan", False):
        attest = getattr(args, "attest", None) or ""
        if "i understand" not in attest.lower():
            raise CommandError(
                '--force-rescan requires --attest "I understand this is not '
                "the intended workflow and I am intentionally skipping queue "
                'completion"'
            )
        print(
            colorize(
                "  --force-rescan: bypassing queue completion check. "
                "Plan-start score will be reset.",
                "yellow",
            )
        )
        # Clear plan_start_scores
        try:
            plan = load_plan()
            if plan.get("plan_start_scores"):
                plan["plan_start_scores"] = {}
                save_plan(plan)
        except PLAN_LOAD_EXCEPTIONS as exc:
            logging.debug("Plan score cleanup skipped: %s", exc)
        return

    # No plan = no gate (first scan, or user never uses plan)
    try:
        plan = load_plan()
    except PLAN_LOAD_EXCEPTIONS:
        _logger.debug("scan preflight plan load skipped", exc_info=True)
        return
    if not plan.get("plan_start_scores"):
        return  # No active cycle

    # Count plan-aware remaining items, excluding workflow actions (e.g.
    # "run scan") which would otherwise create a circular gate.
    try:
        state = state_mod.load_state(state_path(args))
        breakdown = plan_aware_queue_breakdown(state, plan)
        remaining = breakdown.actionable
    except PLAN_LOAD_EXCEPTIONS:
        _logger.debug("scan preflight queue breakdown skipped", exc_info=True)
        return
    if remaining <= 0:
        return  # Queue clear, scan allowed

    # GATE
    raise CommandError(
        f"{remaining} item{'s' if remaining != 1 else ''}"
        " remaining in your queue.\n"
        "  The intended workflow is to complete the queue before scanning.\n"
        "  Work through items with `desloppify next`, then scan when clear.\n\n"
        "  To force a rescan (resets your plan-start score):\n"
        '    desloppify scan --force-rescan --attest "I understand this is not '
        "the intended workflow and I am intentionally skipping queue "
        'completion"'
    )


__all__ = ["scan_queue_preflight"]
