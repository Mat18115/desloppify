"""Rendering and follow-up helpers for the status command."""

from __future__ import annotations

from desloppify.app.commands.helpers.rendering import print_agent_plan, print_ranked_actions
from desloppify.app.commands.helpers.subjective import print_subjective_followup
from desloppify.app.commands.scan import (
    scan_reporting_dimensions as reporting_dimensions_mod,
)
from desloppify.app.commands.status_parts.render_dimensions import (
    find_lowest_dimension as _find_lowest_dimension,
    open_review_issue_counts as _open_review_issue_counts,
    render_objective_dimensions as _render_objective_dimensions,
    render_subjective_dimensions as _render_subjective_dimensions,
    scorecard_subjective_entries_for_status as _scorecard_subjective_entries,
)
from desloppify.app.commands.status_parts.render_io import (
    show_ignore_summary,
    show_tier_progress_table,
    write_status_query,
)
from desloppify.app.commands.status_parts.render_structural import (
    build_area_rows as _build_area_rows,
    collect_structural_areas as _collect_structural_areas,
    render_area_workflow as _render_area_workflow,
)
from desloppify.app.commands.status_parts.summary import (
    print_open_scope_breakdown,
    print_scan_completeness,
    print_scan_metrics,
    score_summary_lines,
)
from desloppify.scoring import (
    DIMENSIONS,
    compute_score_impact,
    merge_potentials,
)
from desloppify.core.output_api import colorize, print_table

def _render_dimension_legend(
    scorecard_subjective: list[dict],
    state: dict | None = None,
    *,
    objective_backlog: int = 0,
) -> None:
    """Print the legend footer and, when actionable, the stale rerun command."""
    print(
        colorize("  Health = open penalized | Strict = open + wontfix penalized", "dim")
    )
    print(
        colorize(
            "  Action: fix=auto-fixer | move=reorganize | refactor=manual rewrite | manual=review & fix",
            "dim",
        )
    )
    stale_keys = [
        str(e.get("dimension_key"))
        for e in scorecard_subjective
        if e.get("stale") and e.get("dimension_key")
    ]
    if stale_keys:
        print(
            colorize("  [stale] = assessment outdated", "yellow")
        )
        if objective_backlog <= 0:
            n = len(stale_keys)
            dims_arg = ",".join(stale_keys)
            print(
                colorize(
                    f"  {n} stale dimension{'s' if n != 1 else ''}"
                    f": `desloppify review --prepare --dimensions {dims_arg} --force-review-rerun`",
                    "yellow",
                )
            )


def show_dimension_table(
    state: dict, dim_scores: dict, *, objective_backlog: int = 0,
) -> None:
    """Show dimension health table with dual scores and progress bars."""
    print()
    bar_len = 20
    print(
        colorize(
            f"  {'Dimension':<22} {'Checks':>7}  {'Health':>6}  {'Strict':>6}  {'Bar':<{bar_len + 2}} {'Tier'}  {'Action'}",
            "dim",
        )
    )
    print(colorize("  " + "─" * 86, "dim"))

    scorecard_subjective = _scorecard_subjective_entries(state, dim_scores)
    lowest_name = _find_lowest_dimension(dim_scores, scorecard_subjective)
    review_issue_counts = _open_review_issue_counts(state)

    _render_objective_dimensions(dim_scores, lowest_name=lowest_name, bar_len=bar_len)
    _render_subjective_dimensions(
        scorecard_subjective,
        lowest_name=lowest_name,
        bar_len=bar_len,
        review_issue_counts=review_issue_counts,
    )
    _render_dimension_legend(scorecard_subjective, state=state, objective_backlog=objective_backlog)
    print()


def show_focus_suggestion(
    dim_scores: dict, state: dict, *, plan: dict | None = None
) -> None:
    """Show the lowest-scoring dimension as the focus area."""
    # When plan has an active focus cluster, show that instead
    if plan and plan.get("active_cluster"):
        cluster_name = plan["active_cluster"]
        cluster = plan.get("clusters", {}).get(cluster_name, {})
        remaining = len(cluster.get("finding_ids", []))
        desc = cluster.get("description") or ""
        desc_str = f" — {desc}" if desc else ""
        print(
            colorize(
                f"  Focus: {cluster_name} ({remaining} items remaining){desc_str}",
                "cyan",
            )
        )
        print()
        return

    scorecard_subjective = _scorecard_subjective_entries(state, dim_scores)
    lowest_name = _find_lowest_dimension(dim_scores, scorecard_subjective)
    if not lowest_name:
        return

    # Determine kind, score, and issue count from the resolved name
    lowest_kind = None
    lowest_score = 101.0
    lowest_issues = 0
    for dim in DIMENSIONS:
        if dim.name == lowest_name:
            ds = dim_scores.get(dim.name)
            if ds:
                lowest_score = float(ds.get("strict", ds["score"]))
                lowest_kind = "mechanical"
                lowest_issues = int(ds.get("failing", 0))
            break
    else:
        for entry in scorecard_subjective:
            if entry.get("name") == lowest_name:
                lowest_score = float(entry.get("strict", entry.get("score", 100.0)))
                lowest_kind = "subjective"
                lowest_issues = 0
                break

    if lowest_name and lowest_score < 100:
        if lowest_kind == "subjective":
            print(
                colorize(
                    f"  Focus: {lowest_name} ({lowest_score:.1f}%) — re-review to improve",
                    "cyan",
                )
            )
            print()
            return

        potentials = merge_potentials(state.get("potentials", {}))
        target_dim = next((d for d in DIMENSIONS if d.name == lowest_name), None)
        if target_dim:
            impact = 0.0
            for det in target_dim.detectors:
                impact = compute_score_impact(
                    {
                        k: {
                            "score": v["score"],
                            "tier": v.get("tier", 3),
                            "detectors": v.get("detectors", {}),
                        }
                        for k, v in dim_scores.items()
                        if "score" in v
                    },
                    potentials,
                    det,
                    lowest_issues,
                )
                if impact > 0:
                    break

            impact_str = f" for +{impact:.1f} pts" if impact > 0 else ""
            print(
                colorize(
                    f"  Focus: {lowest_name} ({lowest_score:.1f}%) — "
                    f"fix {lowest_issues} items{impact_str}",
                    "cyan",
                )
            )
            print()


def show_subjective_followup(
    state: dict,
    dim_scores: dict,
    *,
    target_strict_score: float,
    objective_backlog: int = 0,
) -> None:
    """Show concrete subjective follow-up commands when applicable."""
    if not dim_scores:
        return

    subjective = _scorecard_subjective_entries(state, dim_scores)
    if not subjective:
        return

    followup = reporting_dimensions_mod.build_subjective_followup(
        state,
        subjective,
        threshold=target_strict_score,
        max_quality_items=3,
        max_integrity_items=5,
    )
    if print_subjective_followup(followup, objective_backlog=objective_backlog):
        print()


def show_agent_plan(narrative: dict, *, plan: dict | None = None) -> None:
    """Show concise action plan derived from narrative.actions.

    When a living *plan* is active, renders plan focus/progress instead.
    """
    if plan and (plan.get("queue_order") or plan.get("clusters")):
        print_agent_plan(
            [],
            plan=plan,
            header="  AGENT PLAN (use `desloppify next` to see your next task):",
        )
        return

    actions = narrative.get("actions", [])
    if not actions:
        return

    print(
        colorize(
            "  AGENT PLAN (use `desloppify next --count 20` to inspect more items):",
            "yellow",
        )
    )
    top = actions[0]
    print(colorize(f"  Agent focus: `{top['command']}` — {top['description']}", "cyan"))

    if print_ranked_actions(actions):
        print()


def show_structural_areas(state: dict):
    """Show structural debt grouped by area when T3/T4 debt is significant."""
    sorted_areas = _collect_structural_areas(state)
    if sorted_areas is None:
        return

    print(colorize("\n  ── Structural Debt by Area ──", "bold"))
    print(
        colorize(
            "  Create a task doc for each area → farm to sub-agents for decomposition",
            "dim",
        )
    )
    print()

    rows = _build_area_rows(sorted_areas)
    print_table(
        ["Area", "Items", "Tiers", "Open", "Debt", "Weight"], rows, [42, 6, 10, 5, 5, 7]
    )

    _render_area_workflow(sorted_areas)


def show_review_summary(state: dict):
    """Show review findings summary if any exist."""
    findings = state.get("findings", {})
    review_open = [
        f
        for f in findings.values()
        if f.get("status") == "open" and f.get("detector") == "review"
    ]
    if not review_open:
        return
    uninvestigated = sum(
        1 for f in review_open if not f.get("detail", {}).get("investigation")
    )
    parts = [f"{len(review_open)} finding{'s' if len(review_open) != 1 else ''} open"]
    if uninvestigated:
        parts.append(f"{uninvestigated} uninvestigated")
    print(colorize(f"  Review: {', '.join(parts)} — `desloppify show review --status open`", "cyan"))
    dim_scores = state.get("dimension_scores", {})
    if "Test health" in dim_scores:
        print(
            colorize(
                "  Test health tracks coverage + review; review findings track issues found.",
                "dim",
            )
        )
    print()


__all__ = [
    "print_open_scope_breakdown",
    "print_scan_completeness",
    "print_scan_metrics",
    "score_summary_lines",
    "show_agent_plan",
    "show_dimension_table",
    "show_focus_suggestion",
    "show_ignore_summary",
    "show_review_summary",
    "show_structural_areas",
    "show_subjective_followup",
    "show_tier_progress_table",
    "write_status_query",
]
