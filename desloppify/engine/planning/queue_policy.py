"""Queue-policy helpers shared by planning render/select modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desloppify.engine.work_queue import (
    QueueBuildOptions,
    WorkQueueResult,
    build_work_queue,
)


def _subjective_threshold(state: dict[str, Any], *, default: float = 95.0) -> float:
    config = state.get("config", {})
    raw_target = default
    if isinstance(config, dict):
        raw_target = config.get("target_strict_score", default)
    try:
        value = float(raw_target)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(100.0, value))


@dataclass(frozen=True, slots=True)
class OpenPlanQueuePolicy:
    count: int | None = None
    scan_path: str | None = None
    include_subjective: bool = True


def build_open_plan_queue(
    state: dict[str, Any],
    policy: OpenPlanQueuePolicy | None = None,
) -> WorkQueueResult:
    """Build one open-status queue with consistent planning policy defaults."""
    policy = policy or OpenPlanQueuePolicy()
    # When policy.scan_path is explicitly set, override the auto-default.
    # Otherwise let QueueBuildOptions read from state automatically.
    scan_path_kwarg: dict = (
        {"scan_path": policy.scan_path} if policy.scan_path is not None else {}
    )
    return build_work_queue(
        state,
        options=QueueBuildOptions(
            count=policy.count,
            status="open",
            include_subjective=policy.include_subjective,
            subjective_threshold=_subjective_threshold(state),
            **scan_path_kwarg,
        ),
    )


__all__ = ["OpenPlanQueuePolicy", "build_open_plan_queue"]
