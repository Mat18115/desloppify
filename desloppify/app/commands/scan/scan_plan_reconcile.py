"""Post-scan plan reconciliation — sync plan queue metadata after a scan merge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from desloppify import state as state_mod
from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.core.output_api import colorize
from desloppify.engine.plan import (
    append_log_entry,
    auto_cluster_findings,
    load_plan,
    reconcile_plan_after_scan,
    save_plan,
    sync_create_plan_needed,
    sync_communicate_score_needed,
    sync_stale_dimensions,
    sync_triage_needed,
    sync_unscored_dimensions,
)

if TYPE_CHECKING:
    from desloppify.app.commands.scan.scan_workflow import ScanRuntime


def _plan_has_user_content(plan: dict[str, object]) -> bool:
    """Return True when the living plan has any user-managed queue metadata."""
    return bool(
        plan.get("queue_order")
        or plan.get("overrides")
        or plan.get("clusters")
        or plan.get("skipped")
    )


def _apply_plan_reconciliation(plan: dict[str, object], state: state_mod.StateModel, reconcile_fn) -> bool:
    """Apply standard post-scan plan reconciliation when user content exists."""
    if not _plan_has_user_content(plan):
        return False
    recon = reconcile_fn(plan, state)
    if recon.resurfaced:
        print(
            colorize(
                f"  Plan: {len(recon.resurfaced)} skipped item(s) re-surfaced after review period.",
                "cyan",
            )
        )
    return bool(recon.changes)


def _sync_unscored_dimensions(plan: dict[str, object], state: state_mod.StateModel, sync_fn) -> bool:
    """Sync unscored subjective dimensions into the plan queue."""
    sync = sync_fn(plan, state)
    if sync.injected:
        print(
            colorize(
                f"  Plan: {len(sync.injected)} unscored subjective dimension(s) queued for initial review.",
                "cyan",
            )
        )
    return bool(sync.changes)


def _sync_stale_dimensions(plan: dict[str, object], state: state_mod.StateModel, sync_fn) -> bool:
    """Sync stale subjective dimensions (prune refreshed + inject stale) in plan queue."""
    sync = sync_fn(plan, state)
    if sync.pruned:
        print(
            colorize(
                f"  Plan: {len(sync.pruned)} refreshed subjective dimension(s) removed from queue.",
                "cyan",
            )
        )
    if sync.injected:
        print(
            colorize(
                f"  Plan: {len(sync.injected)} subjective dimension(s) queued for review.",
                "cyan",
            )
        )
    return bool(sync.changes)


def _sync_auto_clusters(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    target_strict: float = 95.0,
    policy=None,
    cycle_just_completed: bool = False,
) -> bool:
    """Regenerate automatic task clusters after scan merge."""
    return bool(auto_cluster_findings(
        plan, state,
        target_strict=target_strict,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
    ))


def _seed_plan_start_scores(plan: dict[str, object], state: state_mod.StateModel) -> bool:
    """Set plan_start_scores when beginning a new queue cycle."""
    existing = plan.get("plan_start_scores")
    if existing and not isinstance(existing, dict):
        return False
    # Seed when empty OR when it's the reset sentinel ({"reset": True})
    if existing and not existing.get("reset"):
        return False
    scores = state_mod.score_snapshot(state)
    if scores.strict is None:
        return False
    plan["plan_start_scores"] = {
        "strict": scores.strict,
        "overall": scores.overall,
        "objective": scores.objective,
        "verified": scores.verified,
    }
    return True


def _clear_plan_start_scores_if_queue_empty(
    state: state_mod.StateModel, plan: dict[str, object]
) -> bool:
    """Clear plan-start score snapshot once the queue is fully drained."""
    if not plan.get("plan_start_scores"):
        return False

    try:
        from desloppify.app.commands.helpers.queue_progress import plan_aware_queue_breakdown

        breakdown = plan_aware_queue_breakdown(state, plan)
        queue_empty = breakdown.actionable == 0
    except PLAN_LOAD_EXCEPTIONS as exc:
        logging.debug("Plan operation skipped: %s", exc)
        return False
    if not queue_empty:
        return False
    state["_plan_start_scores_for_reveal"] = dict(plan["plan_start_scores"])
    plan["plan_start_scores"] = {}
    return True


def reconcile_plan_post_scan(runtime: "ScanRuntime") -> None:
    """Reconcile plan queue metadata and stale subjective review dimensions."""
    try:
        plan_path = runtime.state_path.parent / "plan.json" if runtime.state_path else None
        plan = load_plan(plan_path)
        dirty = False

        if _apply_plan_reconciliation(plan, runtime.state, reconcile_plan_after_scan):
            dirty = True

        unscored_changed = _sync_unscored_dimensions(plan, runtime.state, sync_unscored_dimensions)
        if unscored_changed:
            dirty = True
            append_log_entry(plan, "sync_unscored", actor="system",
                             detail={"changes": True})

        from desloppify.app.commands.helpers.score import target_strict_score_from_config
        _target_strict = target_strict_score_from_config(runtime.config, fallback=95.0)

        # Compute subjective visibility policy once for consistent gating
        from desloppify.engine.plan import compute_subjective_visibility
        _policy = compute_subjective_visibility(
            runtime.state,
            target_strict=_target_strict,
            plan=plan,
        )

        # Detect cycle completion: plan_start_scores is empty when the
        # previous cycle drained the queue and revealed scores.  In that
        # case stale subjective dimensions should be prioritized over new
        # objective findings so the user reviews before a new cycle begins.
        _cycle_just_completed = not plan.get("plan_start_scores")

        stale_changed = _sync_stale_dimensions(
            plan, runtime.state,
            lambda p, s: sync_stale_dimensions(
                p, s, policy=_policy, cycle_just_completed=_cycle_just_completed,
            ),
        )
        if stale_changed:
            dirty = True
            append_log_entry(plan, "sync_stale", actor="system",
                             detail={"changes": True})

        auto_changed = _sync_auto_clusters(
            plan, runtime.state, target_strict=_target_strict, policy=_policy,
            cycle_just_completed=_cycle_just_completed,
        )
        if auto_changed:
            dirty = True
            append_log_entry(plan, "auto_cluster", actor="system",
                             detail={"changes": True})

        triage_sync = sync_triage_needed(plan, runtime.state)
        if triage_sync.changes:
            dirty = True
            if triage_sync.injected:
                print(
                    colorize(
                        "  Plan: planning mode needed — review findings changed since last triage.",
                        "cyan",
                    )
                )
                append_log_entry(plan, "sync_triage", actor="system",
                                 detail={"injected": True})

        communicate_sync = sync_communicate_score_needed(plan, runtime.state, policy=_policy)
        if communicate_sync.changes:
            dirty = True
            append_log_entry(plan, "sync_communicate_score", actor="system",
                             detail={"injected": True})

        create_plan_sync = sync_create_plan_needed(plan, runtime.state, policy=_policy)
        if create_plan_sync.changes:
            dirty = True
            if create_plan_sync.injected:
                print(
                    colorize(
                        "  Plan: reviews complete — `workflow::create-plan` queued.",
                        "cyan",
                    )
                )
                append_log_entry(plan, "sync_create_plan", actor="system",
                                 detail={"injected": True})

        seeded = _seed_plan_start_scores(plan, runtime.state)
        if seeded:
            dirty = True
            append_log_entry(plan, "seed_start_scores", actor="system",
                             detail={})
        # Only clear scores that existed before this reconcile pass —
        # never clear scores we just seeded in the same scan.
        if not seeded and _clear_plan_start_scores_if_queue_empty(runtime.state, plan):
            dirty = True
            append_log_entry(plan, "clear_start_scores", actor="system",
                             detail={})

        if dirty:
            save_plan(plan, plan_path)
    except PLAN_LOAD_EXCEPTIONS as exc:
        logging.debug("Plan operation skipped: %s", exc)
