"""Shared triage display primitives used by multiple rendering modules."""

from __future__ import annotations

from desloppify.base.output.terminal import colorize
from desloppify.engine._plan.triage_playbook import (
    TRIAGE_CMD_CLUSTER_ENRICH_COMPACT,
    TRIAGE_STAGE_DEPENDENCIES,
    TRIAGE_STAGE_LABELS,
)

from .helpers import manual_clusters_with_issues
from .stage_helpers import unenriched_clusters


def print_stage_progress(stages: dict, plan: dict | None = None) -> None:
    """Print the triage-stage progress indicator."""
    print(colorize("  Stages:", "dim"))
    for stage_name, label in TRIAGE_STAGE_LABELS:
        if stage_name in stages:
            if stages[stage_name].get("confirmed_at"):
                print(colorize(f"    ✓ {label} (confirmed)", "green"))
            else:
                print(colorize(f"    ✓ {label} (needs confirm)", "yellow"))
        elif TRIAGE_STAGE_DEPENDENCIES[stage_name].issubset(stages):
            print(colorize(f"    → {label} (current)", "yellow"))
        else:
            print(colorize(f"    ○ {label}", "dim"))

    if plan and "reflect" in stages and "organize" not in stages:
        gaps = unenriched_clusters(plan)
        manual = manual_clusters_with_issues(plan)
        if not manual:
            print(colorize("\n    No manual clusters yet. Create clusters and enrich them.", "yellow"))
        elif gaps:
            print(colorize(f"\n    {len(gaps)} cluster(s) need enrichment:", "yellow"))
            for name, missing in gaps:
                print(colorize(f"      {name}: missing {', '.join(missing)}", "yellow"))
            print(colorize(f"      Fix: {TRIAGE_CMD_CLUSTER_ENRICH_COMPACT}", "dim"))
        else:
            print(colorize(f"\n    All {len(manual)} manual cluster(s) enriched.", "green"))


__all__ = ["print_stage_progress"]
