"""Observe/reflect/organize command handlers for triage flow."""

from __future__ import annotations

import argparse

from desloppify.base.output.terminal import colorize
from desloppify.base.output.user_message import print_user_message
from desloppify.state import utc_now

from .display import print_organize_result, print_reflect_result
from .helpers import (
    cascade_clear_later_confirmations,
    count_log_activity_since,
    has_triage_in_queue,
    inject_triage_stages,
    print_cascade_clear_feedback,
)
from ._stage_records import (
    record_enrich_stage,
    record_observe_stage,
    record_organize_stage,
    resolve_reusable_report,
)
from ._stage_rendering import (
    _print_observe_report_requirement,
    _print_reflect_report_requirement,
)
from ._stage_validation import (
    _auto_confirm_observe_if_attested,
    _auto_confirm_reflect_for_organize,
    _clusters_enriched_or_error,
    _enrich_report_or_error,
    _manual_clusters_or_error,
    _organize_report_or_error,
    _require_organize_stage_for_enrich,
    _require_reflect_stage_for_organize,
    _steps_missing_issue_refs,
    _steps_with_bad_paths,
    _steps_with_vague_detail,
    _steps_without_effort,
    _underspecified_steps,
    _unclustered_review_issues_or_error,
    _validate_recurring_dimension_mentions,
)
from .services import TriageServices, default_triage_services
from .stage_flow_enrich_sense import (
    record_sense_check_stage as _record_sense_check_stage_impl,
    run_stage_enrich,
    run_stage_sense_check,
)


def _cmd_stage_observe(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Record the OBSERVE stage: agent analyses themes and root causes.

    No citation gate — the point is genuine analysis, not ID-stuffing.
    Just requires a 100-char report describing what the agent observed.
    """
    report: str | None = getattr(args, "report", None)

    resolved_services = services or default_triage_services()
    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    plan = resolved_services.load_plan()

    # Auto-start: inject triage stage IDs if not present
    # NOTE: preserve existing stage data — don't clear triage_stages.
    # Only _cmd_triage_start (explicit --start) should clear stages.
    if not has_triage_in_queue(plan):
        inject_triage_stages(plan)
        meta = plan.setdefault("epic_triage_meta", {})
        meta.setdefault("triage_stages", {})
        resolved_services.save_plan(plan)
        print(colorize("  Planning mode auto-started (6 stages queued).", "cyan"))

    meta = plan.setdefault("epic_triage_meta", {})
    stages = meta.setdefault("triage_stages", {})
    existing_stage = stages.get("observe")

    # Jump-back: reuse existing report if no --report provided
    report, is_reuse = resolve_reusable_report(report, existing_stage)
    if not report:
        _print_observe_report_requirement()
        return

    si = resolved_services.collect_triage_input(plan, state)
    issue_count = len(si.open_issues)

    # Edge case: 0 issues
    if issue_count == 0:
        cleared = record_observe_stage(
            stages,
            report=report,
            issue_count=0,
            cited_ids=[],
            existing_stage=existing_stage,
            is_reuse=is_reuse,
        )
        resolved_services.save_plan(plan)
        print(colorize("  Observe stage recorded (no issues to analyse).", "green"))
        if is_reuse:
            print(colorize("  Observe data preserved (no changes).", "dim"))
            if cleared:
                print_cascade_clear_feedback(cleared, stages)
        return

    # Validation: report length (no citation counting)
    min_chars = 50 if issue_count <= 3 else 100
    if len(report) < min_chars:
        print(colorize(f"  Report too short: {len(report)} chars (minimum {min_chars}).", "red"))
        print(colorize("  Describe themes, root causes, contradictions, and how issues relate.", "dim"))
        return

    # Save stage (still extract citations for analytics, but don't gate on them)
    valid_ids = set(si.open_issues.keys())
    cited = resolved_services.extract_issue_citations(report, valid_ids)

    cleared = record_observe_stage(
        stages,
        report=report,
        issue_count=issue_count,
        cited_ids=sorted(cited),
        existing_stage=existing_stage,
        is_reuse=is_reuse,
    )

    resolved_services.save_plan(plan)

    resolved_services.append_log_entry(
        plan,
        "triage_observe",
        actor="user",
        detail={"issue_count": issue_count, "cited_ids": sorted(cited), "reuse": is_reuse},
    )
    resolved_services.save_plan(plan)

    print(
        colorize(
            f"  Observe stage recorded: {issue_count} issues analysed.",
            "green",
        )
    )
    if is_reuse:
        print(colorize("  Observe data preserved (no changes).", "dim"))
        if cleared:
            print_cascade_clear_feedback(cleared, stages)
    else:
        print(colorize("  Now confirm your analysis.", "yellow"))
        print(colorize("    desloppify plan triage --confirm observe", "dim"))

    if not is_reuse:
        print_user_message(
            "Observe recorded. Before confirming — did the subagent"
            " verify every issue with code reads? Check: are there"
            " specific file/line citations in the report, or just"
            " restated issue titles? Each issue needs a verdict:"
            " genuine / false positive / exaggerated. Don't confirm"
            " until the analysis is backed by actual code evidence."
        )


def _cmd_stage_reflect(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Record the REFLECT stage: compare current issues against completed work.

    Forces the agent to consider what was previously resolved and whether
    similar issues are recurring. Requires a 100-char report (50 if ≤3 issues).
    If recurring patterns are detected, the report must mention at least one
    recurring dimension name.
    """
    report: str | None = getattr(args, "report", None)
    attestation: str | None = getattr(args, "attestation", None)

    resolved_services = services or default_triage_services()
    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    plan = resolved_services.load_plan()

    if not has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue — nothing to reflect on.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    # Jump-back: reuse existing report if no --report provided
    existing_stage = stages.get("reflect")
    is_reuse = False
    if not report and existing_stage and existing_stage.get("report"):
        report = existing_stage["report"]
        is_reuse = True
    elif not report:
        _print_reflect_report_requirement()
        return

    if "observe" not in stages:
        print(colorize("  Cannot reflect: observe stage not complete.", "red"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return

    si = resolved_services.collect_triage_input(plan, state)

    # Fold-confirm: auto-confirm observe if attestation provided
    if not _auto_confirm_observe_if_attested(
        plan=plan,
        stages=stages,
        attestation=attestation,
        triage_input=si,
        save_plan_fn=resolved_services.save_plan,
    ):
        return

    issue_count = len(si.open_issues)

    # Validation: report length
    min_chars = 50 if issue_count <= 3 else 100
    if len(report) < min_chars:
        print(colorize(f"  Report too short: {len(report)} chars (minimum {min_chars}).", "red"))
        print(colorize("  Describe how current issues relate to previously completed work.", "dim"))
        return

    # Detect recurring patterns
    recurring = resolved_services.detect_recurring_patterns(
        si.open_issues,
        si.resolved_issues,
    )
    recurring_dims = sorted(recurring.keys())

    # If recurring patterns exist, report must mention at least one dimension
    if not _validate_recurring_dimension_mentions(
        report=report,
        recurring_dims=recurring_dims,
        recurring=recurring,
    ):
        return

    # Save stage
    stages = meta.setdefault("triage_stages", {})
    reflect_stage = {
        "stage": "reflect",
        "report": report,
        "cited_ids": [],
        "timestamp": utc_now(),
        "issue_count": issue_count,
    }
    reflect_stage["recurring_dims"] = recurring_dims
    stages["reflect"] = reflect_stage

    # Jump-back: preserve or clear confirmation
    if is_reuse and existing_stage and existing_stage.get("confirmed_at"):
        stages["reflect"]["confirmed_at"] = existing_stage["confirmed_at"]
        stages["reflect"]["confirmed_text"] = existing_stage.get("confirmed_text", "")
    cleared = cascade_clear_later_confirmations(stages, "reflect")

    resolved_services.save_plan(plan)

    reflect_detail = {
        "issue_count": issue_count,
        "reuse": is_reuse,
    }
    reflect_detail["recurring_dims"] = recurring_dims
    resolved_services.append_log_entry(
        plan,
        "triage_reflect",
        actor="user",
        detail=reflect_detail,
    )
    resolved_services.save_plan(plan)

    print_reflect_result(
        issue_count=issue_count,
        recurring_dims=recurring_dims,
        recurring=recurring,
        report=report,
        is_reuse=is_reuse,
        cleared=cleared,
        stages=stages,
    )


def _cmd_stage_organize(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Record the ORGANIZE stage: validates cluster enrichment.

    Instead of gating on a text report, validates that the plan data
    itself has been enriched: each manual cluster needs description +
    action_steps. This forces the agent to actually think about each
    cluster's execution plan.
    """
    report: str | None = getattr(args, "report", None)
    attestation: str | None = getattr(args, "attestation", None)

    resolved_services = services or default_triage_services()
    plan = resolved_services.load_plan()

    if not has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue — nothing to organize.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    # Jump-back: reuse existing report if no --report provided
    existing_stage = stages.get("organize")
    is_reuse = False
    if not report and existing_stage and existing_stage.get("report"):
        report = existing_stage["report"]
        is_reuse = True

    if not _require_reflect_stage_for_organize(stages):
        return

    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    triage_input = resolved_services.collect_triage_input(plan, state)

    # Fold-confirm: auto-confirm reflect if attestation provided
    if not _auto_confirm_reflect_for_organize(
        args=args,
        plan=plan,
        stages=stages,
        attestation=attestation,
        triage_input=triage_input,
        detect_recurring_patterns_fn=resolved_services.detect_recurring_patterns,
        save_plan_fn=resolved_services.save_plan,
    ):
        return

    # Validate: at least 1 manual cluster with issues
    manual_clusters = _manual_clusters_or_error(plan)
    if manual_clusters is None:
        return

    # Validate: all manual clusters are enriched
    if not _clusters_enriched_or_error(plan):
        return

    # Validate: all review issues are in a manual cluster (or wontfixed)
    if not _unclustered_review_issues_or_error(plan, state):
        return

    # Warn if few/no cluster operations since reflect
    reflect_ts = stages.get("reflect", {}).get("timestamp", "")
    if reflect_ts and not is_reuse:
        activity = count_log_activity_since(plan, reflect_ts)
        cluster_ops = sum(
            activity.get(k, 0)
            for k in ("cluster_create", "cluster_add", "cluster_update", "cluster_remove")
        )
        if cluster_ops == 0:
            print(colorize("  Warning: no cluster operations logged since reflect.", "yellow"))
            print(colorize("  Did you create clusters, add issues, and enrich them?", "yellow"))
            print(colorize("  The organize stage should reflect real work — not just recording a report.", "dim"))
            print()
        elif cluster_ops < 3:
            print(colorize(f"  Note: only {cluster_ops} cluster operation(s) logged since reflect.", "yellow"))
            print(colorize("  Make sure all clusters are properly set up before proceeding.", "dim"))
            print()

    report = _organize_report_or_error(report)
    if report is None:
        return

    stages = meta.setdefault("triage_stages", {})
    cleared = record_organize_stage(
        stages,
        report=report,
        issue_count=len(manual_clusters),
        existing_stage=existing_stage,
        is_reuse=is_reuse,
    )

    resolved_services.save_plan(plan)

    resolved_services.append_log_entry(
        plan,
        "triage_organize",
        actor="user",
        detail={"cluster_count": len(manual_clusters), "reuse": is_reuse},
    )
    resolved_services.save_plan(plan)

    print_organize_result(
        manual_clusters=manual_clusters,
        plan=plan,
        report=report,
        is_reuse=is_reuse,
        cleared=cleared,
        stages=stages,
    )


def _cmd_stage_enrich(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public wrapper for enrich stage with patchable module dependencies."""
    run_stage_enrich(
        args,
        services=services,
        has_triage_in_queue_fn=has_triage_in_queue,
        require_organize_stage_for_enrich_fn=_require_organize_stage_for_enrich,
        underspecified_steps_fn=_underspecified_steps,
        steps_with_bad_paths_fn=_steps_with_bad_paths,
        steps_without_effort_fn=_steps_without_effort,
        enrich_report_or_error_fn=_enrich_report_or_error,
        resolve_reusable_report_fn=resolve_reusable_report,
        record_enrich_stage_fn=record_enrich_stage,
        colorize_fn=colorize,
        print_user_message_fn=print_user_message,
        print_cascade_clear_feedback_fn=print_cascade_clear_feedback,
    )


def _record_sense_check_stage(
    stages: dict,
    *,
    report: str,
    existing_stage: dict | None,
    is_reuse: bool,
) -> list[str]:
    return _record_sense_check_stage_impl(
        stages,
        report=report,
        existing_stage=existing_stage,
        is_reuse=is_reuse,
        utc_now_fn=utc_now,
        cascade_clear_later_confirmations_fn=cascade_clear_later_confirmations,
    )


def _cmd_stage_sense_check(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public wrapper for sense-check stage with patchable module dependencies."""
    run_stage_sense_check(
        args,
        services=services,
        has_triage_in_queue_fn=has_triage_in_queue,
        resolve_reusable_report_fn=resolve_reusable_report,
        record_sense_check_stage_fn=_record_sense_check_stage,
        colorize_fn=colorize,
        print_cascade_clear_feedback_fn=print_cascade_clear_feedback,
        underspecified_steps_fn=_underspecified_steps,
        steps_missing_issue_refs_fn=_steps_missing_issue_refs,
        steps_with_bad_paths_fn=_steps_with_bad_paths,
        steps_with_vague_detail_fn=_steps_with_vague_detail,
        steps_without_effort_fn=_steps_without_effort,
    )


def cmd_stage_observe(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for observe stage recording."""
    _cmd_stage_observe(args, services=services)


def cmd_stage_reflect(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for reflect stage recording."""
    _cmd_stage_reflect(args, services=services)


def cmd_stage_enrich(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for enrich stage recording."""
    _cmd_stage_enrich(args, services=services)


def cmd_stage_organize(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for organize stage recording."""
    _cmd_stage_organize(args, services=services)


def cmd_stage_sense_check(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for sense-check stage recording."""
    _cmd_stage_sense_check(args, services=services)


__all__ = [
    "cmd_stage_enrich",
    "cmd_stage_observe",
    "cmd_stage_organize",
    "cmd_stage_reflect",
    "cmd_stage_sense_check",
    "_cmd_stage_enrich",
    "_cmd_stage_observe",
    "_cmd_stage_organize",
    "_cmd_stage_reflect",
    "_cmd_stage_sense_check",
]
