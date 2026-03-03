"""exclude command: add path patterns and clean stale queue/state entries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from desloppify import state as state_mod
from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.core import config as config_mod
from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS, CommandError
from desloppify.core.file_paths import matches_exclusion
from desloppify.core.output_api import colorize
from desloppify.core.tooling import check_config_staleness
from desloppify.engine.plan import (
    load_plan,
    plan_path_for_state,
    purge_ids,
    save_plan,
)


def _state_file_for_runtime(runtime) -> Path:
    """Resolve the effective state file path for a command runtime."""
    state_file = runtime.state_path
    if isinstance(state_file, Path):
        return state_file
    return state_mod.STATE_FILE


def _prune_excluded_findings(state: dict, pattern: str) -> list[str]:
    """Drop findings whose file path matches a new exclusion pattern."""
    findings = state.get("findings")
    if not isinstance(findings, dict):
        return []

    removed_ids = [
        finding_id
        for finding_id, finding in findings.items()
        if isinstance(finding, dict)
        and matches_exclusion(str(finding.get("file", "")), pattern)
    ]
    for finding_id in removed_ids:
        findings.pop(finding_id, None)
    return removed_ids


def _purge_removed_ids_from_plan(state_file: Path, removed_ids: list[str]) -> int:
    """Remove pruned finding IDs from queue/skips/clusters in plan.json."""
    if not removed_ids:
        return 0
    plan_file = plan_path_for_state(state_file)
    plan = load_plan(plan_file)
    purged = purge_ids(plan, removed_ids)
    if purged:
        save_plan(plan, plan_file)
    return purged


def cmd_exclude(args: argparse.Namespace) -> None:
    """Add a path pattern to the exclude list and clean cached queue/state."""
    runtime = command_runtime(args)
    config = runtime.config
    state = runtime.state
    state_file = _state_file_for_runtime(runtime)

    config_mod.add_exclude_pattern(config, args.pattern)
    config["needs_rescan"] = True
    try:
        config_mod.save_config(config)
    except OSError as exc:
        raise CommandError(f"could not save config: {exc}") from exc

    removed_ids: list[str] = []
    plan_purged = 0
    if state_file.exists():
        removed_ids = _prune_excluded_findings(state, args.pattern)
        if removed_ids:
            try:
                state_mod.save_state(state, state_file)
            except OSError as exc:
                raise CommandError(f"could not save state: {exc}") from exc

            try:
                plan_purged = _purge_removed_ids_from_plan(state_file, removed_ids)
            except PLAN_LOAD_EXCEPTIONS:
                print(
                    colorize("  Warning: could not update living plan.", "yellow"),
                    file=sys.stderr,
                )

    print(colorize(f"Added exclude pattern: {args.pattern}", "green"))
    if removed_ids:
        print(f"  Removed {len(removed_ids)} matching findings from state.")
        if plan_purged:
            print(
                colorize(
                    f"  Plan updated: {plan_purged} item(s) removed from queue.",
                    "dim",
                )
            )
    config_warning = check_config_staleness(config)
    if config_warning:
        print(colorize(f"  {config_warning}", "yellow"))


__all__ = ["cmd_exclude"]
