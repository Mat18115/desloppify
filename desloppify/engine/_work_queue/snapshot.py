"""Canonical queue snapshot for phase and visibility decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from desloppify.base.config import DEFAULT_TARGET_STRICT_SCORE
from desloppify.engine._plan.constants import (
    WORKFLOW_DEFERRED_DISPOSITION_ID,
    WORKFLOW_RUN_SCAN_ID,
)
from desloppify.engine._plan.policy.subjective import NON_OBJECTIVE_DETECTORS
from desloppify.engine._plan.schema import (
    planned_objective_ids as _planned_objective_ids,
)
from desloppify.engine._state.filtering import path_scoped_issues
from desloppify.engine._work_queue.ranking import build_issue_items
from desloppify.engine._work_queue.synthetic import (
    build_subjective_items,
    build_triage_stage_items,
)
from desloppify.engine._work_queue.synthetic_workflow import (
    build_communicate_score_item,
    build_create_plan_item,
    build_deferred_disposition_item,
    build_import_scores_item,
    build_run_scan_item,
    build_score_checkpoint_item,
)
from desloppify.engine._work_queue.types import WorkQueueItem
from desloppify.engine._state.schema import StateModel

PHASE_REVIEW_INITIAL = "review_initial"
PHASE_EXECUTE = "execute"
PHASE_SCAN = "scan"
PHASE_REVIEW_POSTFLIGHT = "review_postflight"
PHASE_WORKFLOW_POSTFLIGHT = "workflow_postflight"
PHASE_TRIAGE_POSTFLIGHT = "triage_postflight"


@dataclass(frozen=True)
class QueueSnapshot:
    """Canonical queue facts and partitions for one invocation."""

    phase: str
    all_objective_items: tuple[WorkQueueItem, ...]
    all_initial_review_items: tuple[WorkQueueItem, ...]
    all_postflight_review_items: tuple[WorkQueueItem, ...]
    all_scan_items: tuple[WorkQueueItem, ...]
    all_postflight_workflow_items: tuple[WorkQueueItem, ...]
    all_postflight_triage_items: tuple[WorkQueueItem, ...]
    execution_items: tuple[WorkQueueItem, ...]
    backlog_items: tuple[WorkQueueItem, ...]
    objective_in_scope_count: int
    planned_objective_count: int
    objective_execution_count: int
    objective_backlog_count: int
    subjective_initial_count: int
    subjective_postflight_count: int
    workflow_postflight_count: int
    triage_pending_count: int
    has_unplanned_objective_blockers: bool


def _option_value(options: object | None, name: str, default: Any) -> Any:
    if options is None:
        return default
    return getattr(options, name, default)


def _resolved_scan_path(options: object | None, state: StateModel) -> str | None:
    scan_path = _option_value(options, "scan_path", state.get("scan_path"))
    if hasattr(scan_path, "__class__") and scan_path.__class__.__name__ == "_ScanPathFromState":
        return state.get("scan_path")
    return scan_path


def _is_fresh_boundary(plan: dict | None) -> bool:
    if not isinstance(plan, dict):
        return True
    scores = plan.get("plan_start_scores")
    if not scores:
        return True
    return isinstance(scores, dict) and bool(scores.get("reset"))


def _is_objective_item(item: WorkQueueItem, *, skipped_ids: set[str]) -> bool:
    return (
        item.get("kind") in {"issue", "cluster"}
        and item.get("detector", "") not in NON_OBJECTIVE_DETECTORS
        and item.get("id", "") not in skipped_ids
    )


def _review_issue_items(items: Iterable[WorkQueueItem]) -> list[WorkQueueItem]:
    return [
        item for item in items
        if item.get("detector", "") in {"review", "concerns", "subjective_review"}
    ]


def _subjective_partitions(
    state: StateModel,
    *,
    scoped_issues: dict[str, dict],
    threshold: float,
) -> tuple[list[WorkQueueItem], list[WorkQueueItem]]:
    candidates = build_subjective_items(state, scoped_issues, threshold=threshold)
    initial = [item for item in candidates if item.get("initial_review")]
    postflight = [item for item in candidates if not item.get("initial_review")]
    return initial, postflight


def _workflow_partitions(
    plan: dict | None,
    state: StateModel,
) -> tuple[list[WorkQueueItem], list[WorkQueueItem], list[WorkQueueItem]]:
    if not isinstance(plan, dict):
        return [], [], []
    scan_items = [
        item
        for item in (
            build_deferred_disposition_item(plan),
            build_run_scan_item(plan),
        )
        if item is not None
    ]
    postflight_workflow = [
        item
        for item in (
            build_score_checkpoint_item(plan, state),
            build_import_scores_item(plan, state),
            build_communicate_score_item(plan, state),
            build_create_plan_item(plan),
        )
        if item is not None
    ]
    triage_items = build_triage_stage_items(plan, state)
    return scan_items, postflight_workflow, triage_items


def _phase_for_snapshot(
    *,
    fresh_boundary: bool,
    initial_review_items: list[WorkQueueItem],
    objective_items: list[WorkQueueItem],
    scan_items: list[WorkQueueItem],
    postflight_review_items: list[WorkQueueItem],
    postflight_workflow_items: list[WorkQueueItem],
    triage_items: list[WorkQueueItem],
) -> str:
    if fresh_boundary and initial_review_items:
        return PHASE_REVIEW_INITIAL
    if objective_items:
        return PHASE_EXECUTE
    if scan_items:
        return PHASE_SCAN
    if postflight_review_items:
        return PHASE_REVIEW_POSTFLIGHT
    if postflight_workflow_items:
        return PHASE_WORKFLOW_POSTFLIGHT
    if triage_items:
        return PHASE_TRIAGE_POSTFLIGHT
    return PHASE_SCAN


def _execution_items_for_phase(
    phase: str,
    *,
    objective_items: list[WorkQueueItem],
    initial_review_items: list[WorkQueueItem],
    scan_items: list[WorkQueueItem],
    postflight_review_items: list[WorkQueueItem],
    postflight_workflow_items: list[WorkQueueItem],
    triage_items: list[WorkQueueItem],
) -> list[WorkQueueItem]:
    if phase == PHASE_REVIEW_INITIAL:
        return initial_review_items
    if phase == PHASE_EXECUTE:
        return objective_items
    if phase == PHASE_SCAN:
        deferred_items = [
            item for item in scan_items
            if item.get("id") == WORKFLOW_DEFERRED_DISPOSITION_ID
        ]
        if deferred_items:
            return deferred_items
        return [
            item for item in scan_items
            if item.get("id") == WORKFLOW_RUN_SCAN_ID
        ]
    if phase == PHASE_REVIEW_POSTFLIGHT:
        return postflight_review_items
    if phase == PHASE_WORKFLOW_POSTFLIGHT:
        return postflight_workflow_items
    if phase == PHASE_TRIAGE_POSTFLIGHT:
        return triage_items
    return []


def build_queue_snapshot(
    state: StateModel,
    *,
    options: object | None = None,
    plan: dict | None = None,
    target_strict: float = DEFAULT_TARGET_STRICT_SCORE,
) -> QueueSnapshot:
    """Build the canonical queue snapshot for the current state."""
    context = _option_value(options, "context", None)
    effective_plan = context.plan if context is not None else (
        plan if plan is not None else _option_value(options, "plan", None)
    )
    scan_path = _resolved_scan_path(options, state)
    skipped_ids = set((effective_plan or {}).get("skipped", {}).keys())
    scoped_issues = path_scoped_issues(state.get("issues", {}), scan_path)
    scope = _option_value(options, "scope", None)
    chronic = bool(_option_value(options, "chronic", False))

    all_issue_items = build_issue_items(
        state,
        scan_path=scan_path,
        status_filter="open",
        scope=scope,
        chronic=chronic,
    )
    objective_items = [
        item for item in all_issue_items
        if _is_objective_item(item, skipped_ids=skipped_ids)
    ]
    planned_ids = _planned_objective_ids(
        {item.get("id", "") for item in objective_items},
        effective_plan,
    )
    planned_objective_items = [
        item for item in objective_items
        if item.get("id", "") in planned_ids
    ]
    review_issue_items = _review_issue_items(all_issue_items)
    initial_review_items, subjective_postflight_items = _subjective_partitions(
        state,
        scoped_issues=scoped_issues,
        threshold=target_strict,
    )
    postflight_review_items = [*subjective_postflight_items, *review_issue_items]
    scan_items, postflight_workflow_items, triage_items = _workflow_partitions(
        effective_plan,
        state,
    )

    fresh_boundary = _is_fresh_boundary(effective_plan)
    phase = _phase_for_snapshot(
        fresh_boundary=fresh_boundary,
        initial_review_items=initial_review_items,
        objective_items=planned_objective_items,
        scan_items=scan_items,
        postflight_review_items=postflight_review_items,
        postflight_workflow_items=postflight_workflow_items,
        triage_items=triage_items,
    )
    execution_items = _execution_items_for_phase(
        phase,
        objective_items=planned_objective_items,
        initial_review_items=initial_review_items,
        scan_items=scan_items,
        postflight_review_items=postflight_review_items,
        postflight_workflow_items=postflight_workflow_items,
        triage_items=triage_items,
    )

    execution_ids = {item.get("id", "") for item in execution_items}
    backlog_items = [
        item for item in (
            [*planned_objective_items, *initial_review_items, *postflight_review_items, *scan_items, *postflight_workflow_items, *triage_items]
        )
        if item.get("id", "") not in execution_ids
    ]
    objective_backlog_count = sum(
        1 for item in planned_objective_items if item.get("id", "") not in execution_ids
    )
    has_unplanned_objective_blockers = len(planned_objective_items) < len(objective_items)

    return QueueSnapshot(
        phase=phase,
        all_objective_items=tuple(objective_items),
        all_initial_review_items=tuple(initial_review_items),
        all_postflight_review_items=tuple(postflight_review_items),
        all_scan_items=tuple(scan_items),
        all_postflight_workflow_items=tuple(postflight_workflow_items),
        all_postflight_triage_items=tuple(triage_items),
        execution_items=tuple(execution_items),
        backlog_items=tuple(backlog_items),
        objective_in_scope_count=len(objective_items),
        planned_objective_count=len(planned_objective_items),
        objective_execution_count=sum(
            1
            for item in execution_items
            if item.get("kind") in {"issue", "cluster"}
            and item.get("detector", "") not in NON_OBJECTIVE_DETECTORS
        ),
        objective_backlog_count=objective_backlog_count,
        subjective_initial_count=len(initial_review_items),
        subjective_postflight_count=len(postflight_review_items),
        workflow_postflight_count=len(postflight_workflow_items),
        triage_pending_count=len(triage_items),
        has_unplanned_objective_blockers=has_unplanned_objective_blockers,
    )


def coarse_phase_name(phase: str) -> str:
    """Map internal queue phases to the persisted coarse lifecycle value."""
    if phase == PHASE_REVIEW_INITIAL or phase == PHASE_REVIEW_POSTFLIGHT:
        return "review"
    if phase == PHASE_WORKFLOW_POSTFLIGHT:
        return "workflow"
    if phase == PHASE_TRIAGE_POSTFLIGHT:
        return "triage"
    return phase


__all__ = [
    "PHASE_EXECUTE",
    "PHASE_REVIEW_INITIAL",
    "PHASE_REVIEW_POSTFLIGHT",
    "PHASE_SCAN",
    "PHASE_TRIAGE_POSTFLIGHT",
    "PHASE_WORKFLOW_POSTFLIGHT",
    "QueueSnapshot",
    "build_queue_snapshot",
    "coarse_phase_name",
]
