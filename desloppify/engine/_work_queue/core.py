"""Unified work-queue selection for next/show/plan views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from desloppify.engine._plan.subjective_policy import (
    SubjectiveVisibility,
    compute_subjective_visibility,
)
from desloppify.engine._work_queue.context import QueueContext
from desloppify.engine._work_queue.helpers import (
    ALL_STATUSES,
    ATTEST_EXAMPLE,
    scope_matches,
)
from desloppify.engine._work_queue.synthetic import (
    build_communicate_score_item,
    build_create_plan_item,
    build_import_scores_item,
    build_score_checkpoint_item,
    build_subjective_items,
    build_triage_stage_items,
)
from desloppify.engine._work_queue.ranking import (
    build_finding_items,
    enrich_with_impact,
    group_queue_items,
    item_explain,
    item_sort_key,
)
from desloppify.engine._work_queue.plan_order import (
    collapse_clusters,
    enrich_plan_metadata,
    filter_cluster_focus,
    new_item_ids as _new_item_ids,
    separate_skipped,
    stamp_plan_sort_keys,
    stamp_positions,
)
from desloppify.state import StateModel

# Sentinel: "read scan_path from state" (the safe default).
# Callers that want to override can pass an explicit str or None.
_SCAN_PATH_FROM_STATE = object()


@dataclass(frozen=True)
class QueueBuildOptions:
    """Configuration for queue construction.

    ``scan_path`` defaults to reading from ``state["scan_path"]`` so callers
    don't need to thread it manually.  Pass an explicit ``str`` or ``None``
    to override (``None`` disables scope filtering).
    """

    # Output control
    count: int | None = 1
    explain: bool = False

    # Scope filtering
    scan_path: str | None | object = _SCAN_PATH_FROM_STATE
    scope: str | None = None
    status: str = "open"
    chronic: bool = False

    # Subjective gating
    include_subjective: bool = True
    subjective_threshold: float = 100.0
    policy: SubjectiveVisibility | None = None

    # Plan integration
    plan: dict | None = None
    include_skipped: bool = False
    cluster: str | None = None

    # Pre-computed context (overrides plan/policy)
    context: QueueContext | None = None


class WorkQueueResult(TypedDict):
    """Typed shape of the dict returned by :func:`build_work_queue`."""

    items: list[dict]
    total: int
    grouped: dict[str, list[dict]]
    new_ids: set[str]


def build_work_queue(
    state: StateModel,
    *,
    options: QueueBuildOptions | None = None,
) -> WorkQueueResult:
    """Build a ranked work queue from state findings.

    Pipeline:
    1. Gather — finding items, subjective dimensions, workflow stages
    2. Score  — estimate impact from dimension headroom, apply floor
    3. Order  — stamp plan positions, sort, filter to cluster focus
    4. Limit  — truncate to count, optionally add explain metadata
    """
    opts = options or QueueBuildOptions()
    plan, scan_path, status, threshold = _resolve_inputs(opts, state)

    # 1. Gather
    items = build_finding_items(
        state, scan_path=scan_path, status_filter=status,
        scope=opts.scope, chronic=opts.chronic,
    )
    items += _gather_subjective_items(state, opts, plan, threshold)
    items += _gather_workflow_items(state, plan, status)

    # 2. Score & filter
    enrich_with_impact(items, state.get("dimension_scores", {}))
    items = [i for i in items if _passes_impact_floor(i)]

    # 3. Plan-aware ordering
    new_ids, skipped = _plan_presort(items, state, plan)
    items.sort(key=item_sort_key)
    _plan_postsort(items, skipped, plan, opts)

    # 4. Finalize
    if not items:
        items += _empty_queue_fallback(plan)
    total = len(items)
    if opts.count is not None and opts.count > 0:
        items = items[:opts.count]
    if opts.explain:
        for item in items:
            item["explain"] = item_explain(item)

    return {
        "items": items,
        "total": total,
        "grouped": group_queue_items(items, "item"),
        "new_ids": new_ids,
    }


# ---------------------------------------------------------------------------
# Pipeline helpers (private to this module)
# ---------------------------------------------------------------------------


def _resolve_inputs(
    opts: QueueBuildOptions, state: StateModel,
) -> tuple[dict | None, str | None, str, float]:
    """Resolve plan, scan_path, status, and subjective threshold from options."""
    ctx = opts.context
    plan = ctx.plan if ctx is not None else opts.plan

    scan_path: str | None = (
        state.get("scan_path")
        if opts.scan_path is _SCAN_PATH_FROM_STATE
        else opts.scan_path  # type: ignore[assignment]
    )

    status = opts.status
    if status not in ALL_STATUSES:
        raise ValueError(f"Unsupported status filter: {status}")

    try:
        threshold = float(opts.subjective_threshold)
    except (TypeError, ValueError):
        threshold = 100.0
    threshold = max(0.0, min(100.0, threshold))

    return plan, scan_path, status, threshold


def _gather_subjective_items(
    state: StateModel,
    opts: QueueBuildOptions,
    plan: dict | None,
    threshold: float,
) -> list[dict]:
    """Build synthetic subjective items, gated by SubjectiveVisibility policy."""
    if not opts.include_subjective:
        return []
    if opts.status not in {"open", "all"}:
        return []
    if opts.chronic:
        return []

    ctx = opts.context
    policy = (
        (ctx.policy if ctx is not None else None)
        or opts.policy
        or compute_subjective_visibility(state, plan=plan)
    )

    candidates = build_subjective_items(
        state, state.get("findings", {}), threshold=threshold,
    )

    # When a plan explicitly includes a subjective item in queue_order,
    # surface it regardless of policy — the plan is authoritative.
    plan_queue_set: set[str] = (
        set(plan.get("queue_order", []))
        if plan
        else set()
    )

    result: list[dict] = []
    for item in candidates:
        if not scope_matches(item, opts.scope):
            continue
        item_id = item.get("id", "")
        if not policy.should_surface(item) and item_id not in plan_queue_set:
            continue
        result.append(item)
    return result


def _gather_workflow_items(
    state: StateModel, plan: dict | None, status: str,
) -> list[dict]:
    """Inject triage stages, checkpoints, and create-plan when plan is active."""
    if not plan or status not in {"open", "all"}:
        return []

    items: list[dict] = list(build_triage_stage_items(plan, state))
    for builder in (
        build_score_checkpoint_item,
        build_import_scores_item,
        build_communicate_score_item,
    ):
        item = builder(plan, state)
        if item is not None:
            items.append(item)
    plan_item = build_create_plan_item(plan)
    if plan_item is not None:
        items.append(plan_item)
    return items


_MIN_STANDALONE_IMPACT = 0.05


def _passes_impact_floor(item: dict) -> bool:
    """Return True if item should survive the impact floor filter."""
    if item.get("kind") != "finding":
        return True
    if item.get("is_review") or item.get("is_subjective"):
        return True
    impact = item.get("estimated_impact")
    return not impact or impact >= _MIN_STANDALONE_IMPACT


def _plan_presort(
    items: list[dict], state: StateModel, plan: dict | None,
) -> tuple[set[str], list[dict]]:
    """Enrich plan metadata and stamp sort keys before sorting.

    Returns ``(new_ids, skipped)`` — skipped items are removed from
    ``items`` in place and returned separately for post-sort re-append.
    """
    if not plan:
        return set(), []

    new_ids = _new_item_ids(state)
    enrich_plan_metadata(items, plan)
    stamp_plan_sort_keys(items, plan, new_ids)
    remaining, skipped = separate_skipped(items, plan)
    items[:] = remaining
    return new_ids, skipped


def _plan_postsort(
    items: list[dict],
    skipped: list[dict],
    plan: dict | None,
    opts: QueueBuildOptions,
) -> None:
    """Re-append skipped items, stamp positions, filter to cluster focus."""
    if not plan:
        return

    if opts.include_skipped:
        items.extend(skipped)
    stamp_positions(items, plan)
    focused = filter_cluster_focus(items, plan, opts.cluster)
    items[:] = focused


def _empty_queue_fallback(plan: dict | None) -> list[dict]:
    """Return a 'run scan' nudge when an active plan cycle has cleared."""
    if not plan:
        return []
    plan_scores = plan.get("plan_start_scores", {})
    if plan_scores.get("strict") is None:
        return []
    return [{
        "id": "workflow::run-scan",
        "kind": "workflow_action",
        "summary": "Queue cleared \u2014 run scan to finalize and reveal your updated score.",
        "primary_command": "desloppify scan",
        "file": "",
        "detector": "workflow",
        "confidence": "high",
    }]


__all__ = [
    "ATTEST_EXAMPLE",
    "QueueBuildOptions",
    "QueueContext",
    "WorkQueueResult",
    "build_work_queue",
    "collapse_clusters",
    "group_queue_items",
]
