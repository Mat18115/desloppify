"""Unified queue-resolution context.

A frozen ``QueueContext`` computed once per operation replaces the scattered
``plan`` / ``target_strict`` / ``policy`` threading through function chains.
Callers build one context and pass it everywhere — makes the wrong thing
impossible.
"""

from __future__ import annotations

from dataclasses import dataclass

from desloppify.engine._plan.subjective_policy import (
    SubjectiveVisibility,
    compute_subjective_visibility,
)
from desloppify.engine._state.schema import StateModel

# Sentinel: "auto-load plan from disk" (the default).
_PLAN_AUTO_LOAD = object()


@dataclass(frozen=True)
class QueueContext:
    """Immutable snapshot of resolved queue parameters.

    Built once via :func:`queue_context`, then threaded through
    ``build_work_queue``, ``plan_aware_queue_breakdown``, and command
    helpers so every call site agrees on plan, target, and policy.
    """

    plan: dict | None
    target_strict: float
    policy: SubjectiveVisibility


def queue_context(
    state: StateModel,
    *,
    config: dict | None = None,
    plan: dict | None | object = _PLAN_AUTO_LOAD,
    target_strict: float | None = None,
) -> QueueContext:
    """Build a :class:`QueueContext` with all parameters resolved.

    Resolution order:

    1. **plan** — explicit value wins; sentinel ``_PLAN_AUTO_LOAD`` triggers
       ``load_plan()`` (guarded by ``PLAN_LOAD_EXCEPTIONS``).
    2. **target_strict** — explicit float wins; ``None`` reads from *config*
       via ``target_strict_score_from_config``; final fallback is ``95.0``.
    3. **policy** — ``compute_subjective_visibility(state, ...)`` with the
       resolved plan and target_strict so every downstream consumer sees
       the same objective-vs-subjective balance.
    """
    # --- resolve plan ---
    if plan is _PLAN_AUTO_LOAD:
        from desloppify.core.exception_sets import PLAN_LOAD_EXCEPTIONS
        from desloppify.engine.plan import load_plan

        try:
            resolved_plan: dict | None = load_plan()
        except PLAN_LOAD_EXCEPTIONS:
            resolved_plan = None
    else:
        resolved_plan = plan  # type: ignore[assignment]

    # --- resolve target_strict ---
    if target_strict is not None:
        resolved_target = target_strict
    elif config is not None:
        from desloppify.app.commands.helpers.score import (
            target_strict_score_from_config,
        )

        resolved_target = target_strict_score_from_config(config, fallback=95.0)
    else:
        resolved_target = 95.0

    # --- resolve policy ---
    resolved_policy = compute_subjective_visibility(
        state,
        target_strict=resolved_target,
        plan=resolved_plan,
    )

    return QueueContext(
        plan=resolved_plan,
        target_strict=resolved_target,
        policy=resolved_policy,
    )


__all__ = [
    "QueueContext",
    "queue_context",
]
