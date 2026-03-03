"""Queue-order enforcement helpers for resolve command flows."""

from __future__ import annotations

import logging

from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.core.output_api import colorize
from desloppify.engine.plan import has_living_plan, load_plan
from desloppify.engine._work_queue.plan_order import collapse_clusters
from desloppify.engine.work_queue import QueueBuildOptions, build_work_queue, queue_context

_logger = logging.getLogger(__name__)


def _check_queue_order_guard(
    state: dict,
    patterns: list[str],
    status: str,
) -> bool:
    """Warn and block if resolving items not at the front of the plan queue."""
    if status != "fixed":
        return False
    try:
        if not has_living_plan():
            return False
        plan = load_plan()
        queue_order = plan.get("queue_order", [])
        if not queue_order:
            return False

        ctx = queue_context(state, plan=plan)
        result = build_work_queue(
            state,
            options=QueueBuildOptions(
                count=None,
                include_subjective=True,
                context=ctx,
            ),
        )
        items = result["items"]
        if not plan.get("active_cluster"):
            items = collapse_clusters(items, plan)
        if not items:
            return False

        front_item = items[0]
        front_id = front_item["id"]

        front_ids: set[str] = set()
        if front_item.get("kind") == "cluster":
            for member in front_item.get("members", []):
                front_ids.add(member["id"])
            front_ids.add(front_id)
        else:
            front_ids.add(front_id)

        clusters = plan.get("clusters", {})
        resolved_ids: set[str] = set()
        for pattern in patterns:
            if pattern in clusters:
                resolved_ids.update(clusters[pattern].get("finding_ids", []))
                resolved_ids.add(pattern)
            else:
                resolved_ids.add(pattern)

        findings = state.get("findings", {})
        resolved_ids = {
            finding_id
            for finding_id in resolved_ids
            if finding_id in clusters
            or (finding_id in findings and findings[finding_id].get("status") == "open")
        }
        if not resolved_ids:
            return False

        out_of_order = resolved_ids - front_ids
        for cluster_id in list(out_of_order):
            if cluster_id in clusters:
                alive_members = {
                    finding_id
                    for finding_id in clusters[cluster_id].get("finding_ids", [])
                    if finding_id in findings and findings[finding_id].get("status") == "open"
                }
                if alive_members and alive_members <= front_ids:
                    out_of_order.discard(cluster_id)
        if not out_of_order:
            return False

        print(colorize("\n  Queue order violation: these items are not next in the plan queue:\n", "yellow"))
        for finding_id in sorted(out_of_order):
            print(f"    {finding_id}")
        print(colorize(f"\n  The current next item is: {front_id}", "dim"))
        print(colorize("  Items must be resolved in plan order. If you need to reprioritize:", "dim"))
        print(colorize("    desloppify plan reorder <pattern> top            # move to front", "dim"))
        print(colorize("    desloppify plan skip <pattern> --reason '...'    # skip for now", "dim"))
        print(colorize("    desloppify next                                  # see what's next\n", "dim"))
        return True
    except PLAN_LOAD_EXCEPTIONS:
        _logger.debug("queue order guard skipped", exc_info=True)
        return False


__all__ = ["_check_queue_order_guard"]
