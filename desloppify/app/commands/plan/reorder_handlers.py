"""Plan reorder subcommand handlers."""

from __future__ import annotations

import argparse

from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.app.commands.helpers.state import require_completed_scan
from desloppify.app.commands.plan._resolve import resolve_ids_from_patterns
from desloppify.core.output_api import colorize
from desloppify.engine.plan import append_log_entry, load_plan, move_items, save_plan


def resolve_target(plan: dict, target: str | None, position: str) -> str | None:
    """Resolve a cluster name used as a before/after target to a member ID."""
    if target is None:
        return None
    clusters = plan.get("clusters", {})
    if target not in clusters:
        return target
    member_ids = clusters[target].get("finding_ids", [])
    if not member_ids:
        return target
    order = plan.get("queue_order", [])
    member_set = set(member_ids)
    ordered = [fid for fid in order if fid in member_set]
    if not ordered:
        return member_ids[0]
    return ordered[0] if position == "before" else ordered[-1]


def cmd_plan_reorder(args: argparse.Namespace) -> None:
    """Reorder findings in the queue."""
    state = command_runtime(args).state
    if not require_completed_scan(state):
        return

    patterns: list[str] = getattr(args, "patterns", [])
    position: str = getattr(args, "position", "top")
    target: str | None = getattr(args, "target", None)

    if position in ("before", "after") and target is None:
        print(colorize(f"  '{position}' requires --target (-t). Example: plan reorder <pat> {position} -t <id>", "red"))
        return
    if position in ("up", "down") and target is None:
        print(colorize(f"  '{position}' requires --target (-t) with an integer offset. Example: plan reorder <pat> {position} -t 3", "red"))
        return

    plan = load_plan()

    target = resolve_target(plan, target, position)

    finding_ids = resolve_ids_from_patterns(state, patterns, plan=plan)
    if not finding_ids:
        print(colorize("  No matching findings found.", "yellow"))
        return

    offset: int | None = None
    if position in ("up", "down") and target is not None:
        try:
            offset = int(target)
        except (ValueError, TypeError):
            print(colorize(f"  Invalid offset: {target}", "red"))
            return
        target = None

    count = move_items(plan, finding_ids, position, target=target, offset=offset)
    append_log_entry(
        plan, "reorder", finding_ids=finding_ids, actor="user",
        detail={"position": position, "target": target, "offset": offset},
    )
    save_plan(plan)
    print(colorize(f"  Moved {count} item(s) to {position}.", "green"))


__all__ = ["cmd_plan_reorder", "resolve_target"]
