"""Markdown plan rendering."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.core.registry import dimension_action_type
from desloppify.engine.planning.types import PlanState
from desloppify.engine.work_queue import QueueBuildOptions, build_work_queue
from desloppify.engine.planning.render_sections import (
    addressed_section as _addressed_section,
    plan_skipped_section as _plan_skipped_section,
    plan_superseded_section as _plan_superseded_section,
    plan_user_ordered_section as _plan_user_ordered_section,
    summary_lines as _summary_lines,
)
from desloppify.scoring import DIMENSIONS, DISPLAY_NAMES
from desloppify.state import get_objective_score, get_overall_score, get_strict_score
from desloppify.core.output_api import LOC_COMPACT_THRESHOLD


def _plan_header(state: PlanState, stats: dict) -> list[str]:
    """Build the plan header: title, score line, and codebase metrics."""
    overall_score = get_overall_score(state)
    objective_score = get_objective_score(state)
    strict_score = get_strict_score(state)

    if (
        overall_score is not None
        and objective_score is not None
        and strict_score is not None
    ):
        header_score = (
            f"**Health:** overall {overall_score:.1f}/100 | "
            f"objective {objective_score:.1f}/100 | "
            f"strict {strict_score:.1f}/100"
        )
    elif overall_score is not None:
        header_score = f"**Score: {overall_score:.1f}/100**"
    else:
        header_score = "**Scores unavailable**"

    metrics = state.get("codebase_metrics", {})
    total_files = sum(metric.get("total_files", 0) for metric in metrics.values())
    total_loc = sum(metric.get("total_loc", 0) for metric in metrics.values())
    total_dirs = sum(metric.get("total_directories", 0) for metric in metrics.values())

    lines = [
        f"# Desloppify Plan — {date.today().isoformat()}",
        "",
        f"{header_score} | "
        f"{stats.get('open', 0)} open | "
        f"{stats.get('fixed', 0)} fixed | "
        f"{stats.get('wontfix', 0)} wontfix | "
        f"{stats.get('auto_resolved', 0)} auto-resolved",
        "",
    ]

    if total_files:
        loc_str = (
            f"{total_loc:,}"
            if total_loc < LOC_COMPACT_THRESHOLD
            else f"{total_loc // 1000}K"
        )
        lines.append(
            f"\n{total_files} files · {loc_str} LOC · {total_dirs} directories\n"
        )

    return lines


def _plan_dimension_table(state: PlanState) -> list[str]:
    """Build the dimension health table rows (empty list when no data)."""
    dim_scores = state.get("dimension_scores", {})
    if not dim_scores:
        return []

    lines = [
        "## Health by Dimension",
        "",
        "| Dimension | Tier | Checks | Issues | Health | Strict | Action |",
        "|-----------|------|--------|--------|--------|--------|--------|",
    ]
    static_names: set[str] = set()
    rendered_names: set[str] = set()
    subjective_display_names = {
        display.lower() for display in DISPLAY_NAMES.values()
    }

    def _looks_subjective(name: str, data: dict) -> bool:
        detectors = data.get("detectors", {})
        if "subjective_assessment" in detectors:
            return True
        lowered = name.strip().lower()
        return lowered in subjective_display_names or lowered.startswith("elegance")

    for dim in DIMENSIONS:
        ds = dim_scores.get(dim.name)
        if not ds:
            continue
        static_names.add(dim.name)
        rendered_names.add(dim.name)
        checks = ds.get("checks", 0)
        issues = ds.get("issues", 0)
        score_val = ds.get("score", 100)
        strict_val = ds.get("strict", score_val)
        bold = "**" if score_val < 93 else ""
        action = dimension_action_type(dim.name)
        lines.append(
            f"| {bold}{dim.name}{bold} | T{dim.tier} | "
            f"{checks:,} | {issues} | {score_val:.1f}% | {strict_val:.1f}% | {action} |"
        )

    from desloppify.engine.planning.dimension_rows import scorecard_dimension_rows

    scorecard_rows = scorecard_dimension_rows(state)
    scorecard_subjective_rows = [
        (name, ds) for name, ds in scorecard_rows if _looks_subjective(name, ds)
    ]
    scorecard_subjective_names = {name for name, _ in scorecard_subjective_rows}

    # Show custom dimensions not present in scorecard.png in the main table.
    custom_non_subjective_rows: list[tuple[str, dict]] = []
    for name, ds in sorted(dim_scores.items(), key=lambda item: str(item[0]).lower()):
        if name in rendered_names or not isinstance(ds, dict):
            continue
        if _looks_subjective(name, ds):
            continue
        custom_non_subjective_rows.append((name, ds))
        rendered_names.add(name)

    for name, ds in custom_non_subjective_rows:
        checks = ds.get("checks", 0)
        issues = ds.get("issues", 0)
        score_val = ds.get("score", 100)
        strict_val = ds.get("strict", score_val)
        tier = int(ds.get("tier", 3) or 3)
        bold = "**" if score_val < 93 else ""
        action = dimension_action_type(name)
        lines.append(
            f"| {bold}{name}{bold} | T{tier} | "
            f"{checks:,} | {issues} | {score_val:.1f}% | {strict_val:.1f}% | {action} |"
        )

    extra_subjective_rows = [
        (name, ds)
        for name, ds in sorted(
            dim_scores.items(), key=lambda item: str(item[0]).lower()
        )
        if (
            isinstance(ds, dict)
            and name not in scorecard_subjective_names
            and name.strip().lower() not in subjective_display_names
            and name.strip().lower() not in {"elegance", "elegance (combined)"}
            and _looks_subjective(name, ds)
        )
    ]
    subjective_rows = [*scorecard_subjective_rows, *extra_subjective_rows]

    if subjective_rows:
        lines.append("| **Subjective Measures (matches scorecard.png)** | | | | | | |")
        for name, ds in subjective_rows:
            issues = ds.get("issues", 0)
            score_val = ds.get("score", 100)
            strict_val = ds.get("strict", score_val)
            tier = ds.get("tier", 4)
            bold = "**" if score_val < 93 else ""
            lines.append(
                f"| {bold}{name}{bold} | T{tier} | "
                f"— | {issues} | {score_val:.1f}% | {strict_val:.1f}% | review |"
            )

    lines.append("")
    return lines


def _plan_item_sections(findings: dict, *, state: PlanState | None = None) -> list[str]:
    """Build per-file sections from the shared work-queue backend."""

    queue_state: PlanState | dict = state or {"findings": findings}
    raw_target = (
        (state or {}).get("config", {}).get("target_strict_score", 95)
        if isinstance(state, dict)
        else 95
    )
    try:
        subjective_threshold = float(raw_target)
    except (TypeError, ValueError):
        subjective_threshold = 95.0
    subjective_threshold = max(0.0, min(100.0, subjective_threshold))
    if "findings" not in queue_state:
        queue_state = {**queue_state, "findings": findings}

    queue = build_work_queue(
        queue_state,
        options=QueueBuildOptions(
            count=None,
            status="open",
            include_subjective=True,
            subjective_threshold=subjective_threshold,
        ),
    )
    open_items = queue.get("items", [])
    by_file: dict[str, list] = defaultdict(list)
    for item in open_items:
        by_file[item.get("file", ".")].append(item)

    lines: list[str] = []
    total_count = len(open_items)
    if not open_items:
        return lines

    lines.extend([
        "---",
        f"## Open Items ({total_count})",
        "",
    ])

    sorted_files = sorted(
        by_file.items(), key=lambda item: (-len(item[1]), item[0])
    )
    for filepath, file_items in sorted_files:
        display_path = "Codebase-wide" if filepath == "." else filepath
        lines.append(f"### `{display_path}` ({len(file_items)} findings)")
        lines.append("")
        for item in file_items:
            if item.get("kind") == "subjective_dimension":
                lines.append(f"- [ ] [subjective] {item.get('summary', '')}")
                lines.append(f"      `{item.get('id', '')}`")
                if item.get("primary_command"):
                    lines.append(f"      action: `{item['primary_command']}`")
                continue

            conf_badge = f"[{item.get('confidence', 'medium')}]"
            lines.append(f"- [ ] {conf_badge} {item.get('summary', '')}")
            lines.append(f"      `{item.get('id', '')}`")
        lines.append("")

    return lines


def generate_plan_md(state: PlanState, plan: dict | None = None) -> str:
    """Generate a prioritized markdown plan from state.

    When *plan* is provided (or auto-loaded from disk), user-ordered
    items, clusters, skipped, and superseded sections are rendered.
    When no plan exists, output is identical to the previous behavior.
    """
    findings = state["findings"]
    stats = state.get("stats", {})

    # Auto-load plan if not provided
    if plan is None:
        try:
            from desloppify.engine.plan import load_plan
            plan = load_plan()
        except PLAN_LOAD_EXCEPTIONS:
            plan = {}
    if not isinstance(plan, dict):
        plan = {}
    plan.setdefault("queue_order", [])
    plan.setdefault("skipped", {})
    plan.setdefault("clusters", {})

    has_plan = bool(
        plan
        and (
            plan.get("queue_order")
            or plan.get("skipped")
            or plan.get("clusters")
        )
    )

    lines = _plan_header(state, stats)
    lines.extend(_plan_dimension_table(state))
    lines.extend(_summary_lines(stats))

    if has_plan:
        # Build full queue for item lookup
        queue = build_work_queue(
            state,
            options=QueueBuildOptions(
                count=None,
                status="open",
                include_subjective=True,
            ),
        )
        all_items = queue.get("items", [])
        lines.extend(_plan_user_ordered_section(all_items, plan))

        # Remaining: items NOT in queue_order or skipped
        ordered_ids = set(plan.get("queue_order", []))
        skipped_ids = set(plan.get("skipped", {}).keys())
        plan_ids = ordered_ids | skipped_ids
        remaining = [item for item in all_items if item.get("id") not in plan_ids]
        if remaining:
            lines.append("---")
            lines.append(f"## Remaining (mechanical order, {len(remaining)} items)")
            lines.append("")

        lines.extend(_plan_item_sections(findings, state=state))
        lines.extend(_plan_skipped_section(all_items, plan))
        lines.extend(_plan_superseded_section(plan))
    else:
        lines.extend(_plan_item_sections(findings, state=state))

    lines.extend(_addressed_section(findings))

    return "\n".join(lines)
