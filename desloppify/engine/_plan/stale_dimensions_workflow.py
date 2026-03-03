"""Workflow stage sync — inject workflow::* IDs into the plan queue."""

from __future__ import annotations

from dataclasses import dataclass

from desloppify.engine._plan.schema import PlanModel, ensure_plan_defaults
from desloppify.engine._plan.subjective_policy import (
    NON_OBJECTIVE_DETECTORS,
    SubjectiveVisibility,
)
from desloppify.engine._state.schema import StateModel

from desloppify.engine._plan.stale_dimensions import (
    SUBJECTIVE_PREFIX,
    TRIAGE_IDS,
    TRIAGE_PREFIX,
    WORKFLOW_COMMUNICATE_SCORE_ID,
    WORKFLOW_CREATE_PLAN_ID,
    WORKFLOW_IMPORT_SCORES_ID,
    WORKFLOW_PREFIX,
    WORKFLOW_SCORE_CHECKPOINT_ID,
    current_unscored_ids,
)


@dataclass
class CommunicateScoreSyncResult:
    """What changed during a communicate-score sync."""

    injected: bool = False

    @property
    def changes(self) -> int:
        return int(self.injected)


def sync_communicate_score_needed(
    plan: PlanModel,
    state: StateModel,
    *,
    policy: SubjectiveVisibility | None = None,
    scores_just_imported: bool = False,
) -> CommunicateScoreSyncResult:
    """Inject ``workflow::communicate-score`` when scores should be shown.

    Injects when either:
    - All initial subjective reviews are complete (no unscored dimensions), OR
    - Scores were just imported (trusted/attested/override)

    And ``workflow::communicate-score`` is not already in the queue.
    Positioned after subjective items but before triage/create-plan.
    """
    ensure_plan_defaults(plan)
    result = CommunicateScoreSyncResult()
    order: list[str] = plan["queue_order"]

    # Also treat legacy score-checkpoint as already-present
    if WORKFLOW_COMMUNICATE_SCORE_ID in order or WORKFLOW_SCORE_CHECKPOINT_ID in order:
        return result

    # Trigger 1: scores just imported
    should_inject = scores_just_imported

    # Trigger 2: all initial reviews complete (no unscored dimensions)
    if not should_inject:
        if policy is not None:
            should_inject = not policy.unscored_ids
        else:
            should_inject = not current_unscored_ids(state)

    if not should_inject:
        return result

    # Insert after any subjective items, before triage/workflow/findings
    insert_at = 0
    for i, fid in enumerate(order):
        if fid.startswith(SUBJECTIVE_PREFIX):
            insert_at = i + 1
    order.insert(insert_at, WORKFLOW_COMMUNICATE_SCORE_ID)
    result.injected = True
    return result


@dataclass
class CreatePlanSyncResult:
    """What changed during a create-plan sync."""

    injected: bool = False

    @property
    def changes(self) -> int:
        return int(self.injected)


def sync_create_plan_needed(
    plan: PlanModel,
    state: StateModel,
    *,
    policy: SubjectiveVisibility | None = None,
) -> CreatePlanSyncResult:
    """Inject ``workflow::create-plan`` when reviews complete + objective backlog exists.

    Only injects when:
    - No unscored (placeholder) subjective dimensions remain
    - At least one objective finding exists
    - ``workflow::create-plan`` is not already in the queue
    - No triage stages are pending
    """
    ensure_plan_defaults(plan)
    result = CreatePlanSyncResult()
    order: list[str] = plan["queue_order"]

    if WORKFLOW_CREATE_PLAN_ID in order:
        return result

    # Don't inject if triage stages are pending
    if any(sid in order for sid in TRIAGE_IDS):
        return result

    # Check that no unscored dimensions remain
    if policy is not None:
        if policy.unscored_ids:
            return result
        has_objective = policy.has_objective_backlog
    else:
        unscored = current_unscored_ids(state)
        if unscored:
            return result
        findings = state.get("findings", {})
        has_objective = any(
            f.get("status") == "open"
            and f.get("detector") not in NON_OBJECTIVE_DETECTORS
            for f in findings.values()
        )
    if not has_objective:
        return result

    # Insert after any subjective/workflow items, at the end of the
    # synthetic block (so create-plan comes after score-checkpoint).
    insert_at = 0
    for i, fid in enumerate(order):
        if fid.startswith(SUBJECTIVE_PREFIX) or fid.startswith(TRIAGE_PREFIX) or fid.startswith(WORKFLOW_PREFIX):
            insert_at = i + 1
    order.insert(insert_at, WORKFLOW_CREATE_PLAN_ID)
    result.injected = True
    return result


@dataclass
class ImportScoresSyncResult:
    """What changed during an import-scores sync."""

    injected: bool = False

    @property
    def changes(self) -> int:
        return int(self.injected)


def sync_import_scores_needed(
    plan: PlanModel,
    state: StateModel,
    *,
    assessment_mode: str | None = None,
) -> ImportScoresSyncResult:
    """Inject ``workflow::import-scores`` after findings-only import.

    Only injects when:
    - Assessment mode was ``findings_only`` (scores were skipped)
    - ``workflow::import-scores`` is not already in the queue
    - There are assessments in the payload that could be imported

    Positioned after score-checkpoint, before create-plan.
    """
    ensure_plan_defaults(plan)
    result = ImportScoresSyncResult()
    order: list[str] = plan["queue_order"]

    if WORKFLOW_IMPORT_SCORES_ID in order:
        return result

    # Only inject when scores were skipped (findings-only mode)
    if assessment_mode != "findings_only":
        return result

    # Insert after any subjective/workflow items
    insert_at = 0
    for i, fid in enumerate(order):
        if fid.startswith(SUBJECTIVE_PREFIX) or fid.startswith(WORKFLOW_PREFIX):
            insert_at = i + 1
    order.insert(insert_at, WORKFLOW_IMPORT_SCORES_ID)
    result.injected = True
    return result
