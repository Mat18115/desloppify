"""Subjective-dimension auto-clustering — sync unscored, stale, and under-target clusters."""

from __future__ import annotations

from desloppify.engine._plan.schema import PlanModel
from desloppify.engine._plan.stale_dimensions import (
    SUBJECTIVE_PREFIX,
    _current_stale_ids,
    current_under_target_ids,
    current_unscored_ids,
)
from desloppify.engine._plan.subjective_policy import (
    NON_OBJECTIVE_DETECTORS,
    SubjectiveVisibility,
)
from desloppify.engine._state.schema import StateModel

_MIN_CLUSTER_SIZE = 2
_MIN_UNSCORED_CLUSTER_SIZE = 1

_STALE_KEY = "subjective::stale"
_STALE_NAME = "auto/stale-review"
_UNSCORED_KEY = "subjective::unscored"
_UNSCORED_NAME = "auto/initial-review"
_UNDER_TARGET_KEY = "subjective::under-target"
_UNDER_TARGET_NAME = "auto/under-target-review"


def sync_subjective_clusters(
    plan: PlanModel,
    state: StateModel,
    findings: dict,
    clusters: dict,
    existing_by_key: dict[str, str],
    active_auto_keys: set[str],
    now: str,
    *,
    sync_auto_cluster,
    target_strict: float,
    policy: SubjectiveVisibility | None = None,
    cycle_just_completed: bool = False,
) -> int:
    """Sync unscored, stale, and under-target subjective dimension clusters."""
    changes = 0

    all_subjective_ids = sorted(
        fid for fid in plan.get("queue_order", [])
        if fid.startswith(SUBJECTIVE_PREFIX)
    )

    if policy is not None:
        unscored_state_ids = policy.unscored_ids
        stale_state_ids = policy.stale_ids
    else:
        unscored_state_ids = current_unscored_ids(state)
        stale_state_ids = _current_stale_ids(state)

    unscored_queue_ids = sorted(
        fid for fid in all_subjective_ids if fid in unscored_state_ids
    )
    stale_queue_ids = sorted(
        fid for fid in all_subjective_ids
        if fid in stale_state_ids and fid not in unscored_state_ids
    )

    # -- Initial review cluster (unscored, min size 1) ---------------------
    if len(unscored_queue_ids) >= _MIN_UNSCORED_CLUSTER_SIZE:
        active_auto_keys.add(_UNSCORED_KEY)
        cli_keys = [fid.removeprefix(SUBJECTIVE_PREFIX) for fid in unscored_queue_ids]
        description = f"Initial review of {len(unscored_queue_ids)} unscored subjective dimensions"
        action = f"desloppify review --prepare --dimensions {','.join(cli_keys)}"
        changes += sync_auto_cluster(
            plan, clusters, existing_by_key,
            cluster_key=_UNSCORED_KEY,
            cluster_name=_UNSCORED_NAME,
            member_ids=unscored_queue_ids,
            description=description,
            action=action,
            now=now,
        )

    # -- Stale review cluster (previously scored, min size 2) --------------
    if len(stale_queue_ids) >= _MIN_CLUSTER_SIZE:
        active_auto_keys.add(_STALE_KEY)
        cli_keys = [fid.removeprefix(SUBJECTIVE_PREFIX) for fid in stale_queue_ids]
        description = f"Re-review {len(stale_queue_ids)} stale subjective dimensions"
        action = (
            "desloppify review --prepare --dimensions "
            + ",".join(cli_keys)
        )
        changes += sync_auto_cluster(
            plan, clusters, existing_by_key,
            cluster_key=_STALE_KEY,
            cluster_name=_STALE_NAME,
            member_ids=stale_queue_ids,
            description=description,
            action=action,
            now=now,
        )

    # -- Under-target review cluster (optional, current but below target) ----
    if policy is not None:
        under_target_ids = policy.under_target_ids
    else:
        under_target_ids = current_under_target_ids(state, target_strict=target_strict)
    under_target_queue_ids = sorted(under_target_ids)

    # Prune: remove IDs that were previously in the under-target cluster
    # but are no longer under target (they've improved above threshold).
    prev_ut_cluster = clusters.get(_UNDER_TARGET_NAME, {})
    prev_ut_ids = set(prev_ut_cluster.get("finding_ids", []))
    order = plan.get("queue_order", [])
    _ut_prune = [
        fid for fid in prev_ut_ids
        if fid not in under_target_ids
        and fid not in stale_state_ids
        and fid not in unscored_state_ids
        and fid in order
    ]
    for fid in _ut_prune:
        order.remove(fid)
        changes += 1

    # Guard: only inject under-target items when no objective findings
    # remain open — mirror the guard used by sync_stale_dimensions().
    if policy is not None:
        has_objective_items = policy.has_objective_backlog
    else:
        has_objective_items = any(
            f.get("status") == "open"
            and f.get("detector") not in NON_OBJECTIVE_DETECTORS
            and not f.get("suppressed")
            for f in findings.values()
        )

    if not has_objective_items and len(under_target_queue_ids) >= _MIN_CLUSTER_SIZE:
        active_auto_keys.add(_UNDER_TARGET_KEY)
        cli_keys = [fid.removeprefix(SUBJECTIVE_PREFIX) for fid in under_target_queue_ids]
        description = (
            f"Consider re-reviewing {len(under_target_queue_ids)} "
            f"dimensions under target score"
        )
        action = (
            "desloppify review --prepare --dimensions "
            + ",".join(cli_keys)
        )
        changes += sync_auto_cluster(
            plan, clusters, existing_by_key,
            cluster_key=_UNDER_TARGET_KEY,
            cluster_name=_UNDER_TARGET_NAME,
            member_ids=under_target_queue_ids,
            description=description,
            action=action,
            now=now,
            optional=True,
        )

        # Ensure under-target IDs are in queue_order (at the back)
        order = plan.get("queue_order", [])
        existing_order = set(order)
        for fid in under_target_queue_ids:
            if fid not in existing_order:
                order.append(fid)

    # Evict under-target IDs from queue when objective backlog has returned
    # — but NOT after a completed cycle, where they should stay for review.
    if has_objective_items and not cycle_just_completed:
        _objective_evict = [
            fid for fid in order
            if fid in under_target_ids
        ]
        for fid in _objective_evict:
            order.remove(fid)
            changes += 1

    return changes
