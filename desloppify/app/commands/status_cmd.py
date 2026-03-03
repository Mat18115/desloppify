"""status command: score dashboard with per-tier progress."""

from __future__ import annotations

import argparse
import json
import logging

_logger = logging.getLogger(__name__)

from desloppify import state as state_mod
from desloppify.app.commands.helpers.lang import resolve_lang
from desloppify.app.commands.helpers.queue_progress import (
    format_queue_block,
    get_plan_start_strict,
    plan_aware_queue_breakdown,
    print_frozen_score_with_queue_context,
)
from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.app.commands.helpers.score import target_strict_score_from_config
from desloppify.app.commands.helpers.state import require_completed_scan
from desloppify.app.commands.helpers.guardrails import print_triage_guardrail_info
from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.app.commands.scan import (
    scan_reporting_dimensions as reporting_dimensions_mod,
)
from desloppify.app.commands.status_parts.render import (
    print_open_scope_breakdown,
    print_scan_completeness,
    print_scan_metrics,
    score_summary_lines,
    show_agent_plan,
    show_dimension_table,
    show_focus_suggestion,
    show_ignore_summary,
    show_review_summary,
    show_structural_areas,
    show_subjective_followup,
    show_tier_progress_table,
    write_status_query,
)
from desloppify.engine.plan import load_plan
from desloppify.engine.work_queue import queue_context
from desloppify.engine.planning.scorecard_projection import (
    scorecard_dimensions_payload,
)
from desloppify.core.output_api import colorize
from desloppify.core.skill_docs import check_skill_version
from desloppify.core.tooling import check_config_staleness
from desloppify.intelligence.narrative import NarrativeContext, compute_narrative
from desloppify.scoring import compute_health_breakdown


def cmd_status(args: argparse.Namespace) -> None:
    """Show score dashboard."""
    runtime = command_runtime(args)
    state = runtime.state
    config = runtime.config

    stats = state.get("stats", {})
    dim_scores = state.get("dimension_scores", {}) or {}
    scorecard_dims = scorecard_dimensions_payload(state, dim_scores=dim_scores)
    subjective_measures = [row for row in scorecard_dims if row.get("subjective")]
    suppression = state_mod.suppression_metrics(state)

    if getattr(args, "json", False):
        print(
            json.dumps(
                _status_json_payload(
                    state,
                    stats,
                    dim_scores,
                    scorecard_dims,
                    subjective_measures,
                    suppression,
                ),
                indent=2,
            )
        )
        return

    if not require_completed_scan(state):
        return

    skill_warning = check_skill_version()
    if skill_warning:
        print(colorize(f"  {skill_warning}", "yellow"))
    config_warning = check_config_staleness(config)
    if config_warning:
        print(colorize(f"  {config_warning}", "yellow"))

    scores = state_mod.score_snapshot(state)
    by_tier = stats.get("by_tier", {})
    target_strict_score = target_strict_score_from_config(config, fallback=95.0)

    lang = resolve_lang(args)
    lang_name = lang.name if lang else None

    # Load living plan for plan-aware rendering and narrative
    _plan = load_plan()
    _plan_active = _plan if (
        _plan.get("queue_order") or _plan.get("clusters")
    ) else None

    print_triage_guardrail_info(plan=_plan, state=state)

    narrative = compute_narrative(
        state,
        context=NarrativeContext(lang=lang_name, command="status", plan=_plan_active),
    )
    ignores = config.get("ignore", [])

    # Build unified context once — plan, target_strict, and policy all agree.
    _ctx = queue_context(
        state,
        config=config,
        plan=_plan_active,
        target_strict=target_strict_score,
    )

    _breakdown = _print_score_section(state, scores, _plan, target_strict_score, _ctx)
    print_scan_metrics(state)
    print_open_scope_breakdown(state)
    print_scan_completeness(state)

    _objective_backlog = _ctx.policy.objective_count

    if dim_scores:
        show_dimension_table(state, dim_scores, objective_backlog=_objective_backlog)
        reporting_dimensions_mod.show_score_model_breakdown(
            state,
            dim_scores=dim_scores,
        )
    else:
        show_tier_progress_table(by_tier)

    if dim_scores:
        show_focus_suggestion(dim_scores, state, plan=_plan_active)
        show_subjective_followup(
            state,
            dim_scores,
            target_strict_score=target_strict_score,
            objective_backlog=_objective_backlog,
        )

    show_review_summary(state)
    show_structural_areas(state)

    # Commit tracking reminder
    try:
        from desloppify.app.commands.next_parts.render_nudges import render_uncommitted_reminder
        render_uncommitted_reminder(_plan_active)
    except PLAN_LOAD_EXCEPTIONS:
        _logger.debug("commit tracking reminder skipped", exc_info=True)

    show_agent_plan(narrative, plan=_plan_active)

    if narrative.get("headline"):
        print(colorize(f"  -> {narrative['headline']}", "cyan"))
        print()

    if ignores:
        show_ignore_summary(ignores, suppression)

    review_age = config.get("review_max_age_days", 30)
    if review_age != 30:
        label = "never" if review_age == 0 else f"{review_age} days"
        print(colorize(f"  Review staleness: {label}", "dim"))
    print()

    write_status_query(
        state=state,
        stats=stats,
        by_tier=by_tier,
        dim_scores=dim_scores,
        scorecard_dims=scorecard_dims,
        subjective_measures=subjective_measures,
        suppression=suppression,
        narrative=narrative,
        ignores=ignores,
        overall_score=scores.overall,
        objective_score=scores.objective,
        strict_score=scores.strict,
        verified_strict_score=scores.verified,
        plan=_plan_active,
    )


def _print_score_section(state, scores, plan, target_strict_score, ctx=None):
    """Print score header: frozen plan-start or live score with queue breakdown."""
    plan_start_strict = get_plan_start_strict(plan)
    breakdown = None
    queue_remaining = 0
    if plan_start_strict is not None:
        try:
            breakdown = plan_aware_queue_breakdown(state, plan, context=ctx)
            queue_remaining = breakdown.queue_total
        except PLAN_LOAD_EXCEPTIONS as exc:
            _logger.debug("Plan-aware queue count failed: %s", exc)
            queue_remaining = 0
    if plan_start_strict is not None and queue_remaining > 0:
        print_frozen_score_with_queue_context(
            plan, queue_remaining, breakdown=breakdown,
        )
    else:
        for line, style in score_summary_lines(
            overall_score=scores.overall,
            objective_score=scores.objective,
            strict_score=scores.strict,
            verified_strict_score=scores.verified,
            target_strict=target_strict_score,
        ):
            print(colorize(line, style))
        # Show queue breakdown even without frozen score
        if breakdown is None:
            try:
                breakdown = plan_aware_queue_breakdown(state, plan, context=ctx)
            except PLAN_LOAD_EXCEPTIONS:
                breakdown = None
        if breakdown is not None and breakdown.queue_total > 0:
            block = format_queue_block(breakdown)
            for text, style in block:
                print(colorize(text, style))
    return breakdown


def _status_json_payload(
    state: dict,
    stats: dict,
    dim_scores: dict,
    scorecard_dims: list[dict],
    subjective_measures: list[dict],
    suppression: dict,
) -> dict:
    scores = state_mod.score_snapshot(state)
    findings = state.get("findings", {})
    open_scope = (
        state_mod.open_scope_breakdown(findings, state.get("scan_path"))
        if isinstance(findings, dict)
        else None
    )
    return {
        "overall_score": scores.overall,
        "objective_score": scores.objective,
        "strict_score": scores.strict,
        "verified_strict_score": scores.verified,
        "dimension_scores": dim_scores,
        "score_breakdown": compute_health_breakdown(dim_scores) if dim_scores else None,
        "scorecard_dimensions": scorecard_dims,
        "subjective_measures": subjective_measures,
        "potentials": state.get("potentials"),
        "codebase_metrics": state.get("codebase_metrics"),
        "stats": stats,
        "open_scope": open_scope,
        "suppression": suppression,
        "scan_count": state.get("scan_count", 0),
        "last_scan": state.get("last_scan"),
    }

__all__ = [
    "cmd_status",
    "show_dimension_table",
    "show_focus_suggestion",
    "show_ignore_summary",
    "show_structural_areas",
    "show_subjective_followup",
]
