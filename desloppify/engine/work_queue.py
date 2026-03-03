"""Public work-queue API facade.

Work-queue internals live in ``desloppify.engine._work_queue``; this module
exposes the stable, non-private API used by commands, rendering helpers, and
test suites.
"""

from __future__ import annotations

# --- context: unified queue-resolution context -----------------------------
from desloppify.engine._work_queue.context import (
    QueueContext,
    queue_context,
)

# --- core: queue building & options ----------------------------------------
from desloppify.engine._work_queue.core import (
    ATTEST_EXAMPLE,
    QueueBuildOptions,
    WorkQueueResult,
    build_work_queue,
    group_queue_items,
)

# --- helpers: status/scope matching, item utilities ------------------------
from desloppify.engine._work_queue.helpers import (
    ALL_STATUSES,
    is_review_finding,
    is_subjective_finding,
    is_subjective_queue_item,
    primary_command_for_finding,
    review_finding_weight,
    scope_matches,
    slugify,
    status_matches,
    supported_fixers_for_item,
)

# --- synthetic: dimension items, triage stages, workflow builders ----------
from desloppify.engine._work_queue.synthetic import (
    build_subjective_items,
    subjective_strict_scores,
)

# --- issues: review-finding work queue -------------------------------------
from desloppify.engine._work_queue.issues import (
    expire_stale_holistic,
    impact_label,
    list_open_review_findings,
    update_investigation,
)

# --- ranking: sort keys, grouping ------------------------------------------
from desloppify.engine._work_queue.ranking import (
    build_finding_items,
    item_explain,
    item_sort_key,
    subjective_score_value,
)

__all__ = [
    # context
    "QueueContext",
    "queue_context",
    # core
    "ATTEST_EXAMPLE",
    "QueueBuildOptions",
    "WorkQueueResult",
    "build_work_queue",
    "group_queue_items",
    # helpers
    "ALL_STATUSES",
    "is_review_finding",
    "is_subjective_finding",
    "is_subjective_queue_item",
    "primary_command_for_finding",
    "review_finding_weight",
    "scope_matches",
    "slugify",
    "status_matches",
    "supported_fixers_for_item",
    # synthetic
    "build_subjective_items",
    "subjective_strict_scores",
    # ranking
    "build_finding_items",
    "item_explain",
    "item_sort_key",
    "subjective_score_value",
    # issues
    "expire_stale_holistic",
    "impact_label",
    "list_open_review_findings",
    "update_investigation",
]
