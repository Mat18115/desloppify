"""Predicates and small utilities for work queue items."""

from __future__ import annotations

import re
from fnmatch import fnmatch

from desloppify.core.enums import finding_status_tokens
from desloppify.core.registry import DETECTORS
from desloppify.intelligence.integrity import (
    is_holistic_subjective_finding,
)

ALL_STATUSES = set(finding_status_tokens(include_all=True))
ACTION_TYPE_PRIORITY = {"auto_fix": 0, "refactor": 1, "manual_fix": 2, "reorganize": 3}
ATTEST_EXAMPLE = (
    "I have actually [DESCRIBE THE CONCRETE CHANGE YOU MADE] "
    "and I am not gaming the score by resolving without fixing."
)


def detail_dict(item: dict) -> dict:
    """Return finding detail as a dict; tolerate legacy/non-dict payloads."""
    detail = item.get("detail")
    return detail if isinstance(detail, dict) else {}


def status_matches(item_status: str, status_filter: str) -> bool:
    return status_filter == "all" or item_status == status_filter


def is_subjective_finding(item: dict) -> bool:
    detector = item.get("detector")
    if detector in {"subjective_assessment"}:
        return True
    if detector == "holistic_review":
        return True
    return False


def is_review_finding(item: dict) -> bool:
    return item.get("detector") == "review"


def is_subjective_queue_item(item: dict) -> bool:
    """True for subjective work items, including collapsed subjective clusters."""
    if item.get("kind") == "subjective_dimension":
        return True
    if item.get("kind") == "cluster":
        members = item.get("members", [])
        return bool(members) and all(
            m.get("kind") == "subjective_dimension" for m in members
        )
    return False


def review_finding_weight(item: dict) -> float:
    """Return review issue weight aligned with issues list ordering."""
    confidence = str(item.get("confidence", "low")).lower()
    weight_by_confidence = {
        "high": 1.0,
        "medium": 0.7,
        "low": 0.3,
    }
    weight = weight_by_confidence.get(confidence, 0.3)
    if detail_dict(item).get("holistic"):
        weight *= 10.0
    return float(weight)


def scope_matches(item: dict, scope: str | None) -> bool:
    """Apply show-style pattern matching against a queue item."""
    if not scope:
        return True

    item_id = item.get("id", "")
    detector = item.get("detector", "")
    filepath = item.get("file", "")
    summary = item.get("summary", "")
    dimension = detail_dict(item).get("dimension_name", "")
    kind = item.get("kind", "")

    if "*" in scope:
        return any(
            fnmatch(candidate, scope)
            for candidate in (item_id, filepath, detector, dimension, summary)
        )

    if "::" in scope:
        return item_id.startswith(scope)

    lowered = scope.lower()
    if kind == "subjective_dimension":
        return (
            lowered in item_id.lower()
            or lowered in dimension.lower()
            or lowered in summary.lower()
        )

    # Hash suffix: 8+ hex chars matches the tail segment of a finding ID.
    if len(lowered) >= 8 and re.fullmatch(r"[0-9a-f]+", lowered):
        return item_id.lower().endswith("::" + lowered)

    return (
        detector == scope
        or filepath == scope
        or filepath.startswith(scope.rstrip("/") + "/")
    )


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def supported_fixers_for_item(state: dict, item: dict) -> set[str] | None:
    """Return supported fixers for an item's language when known."""
    lang = str(item.get("lang", "") or "").strip()
    if not lang:
        return None

    caps = state.get("lang_capabilities", {})
    if not isinstance(caps, dict):
        return None

    lang_caps = caps.get(lang, {})
    if not isinstance(lang_caps, dict):
        return None

    fixers = lang_caps.get("fixers")
    if not isinstance(fixers, list):
        return None
    return {fixer for fixer in fixers if isinstance(fixer, str)}


def primary_command_for_finding(
    item: dict, *, supported_fixers: set[str] | None = None
) -> str:
    detector = item.get("detector", "")
    meta = DETECTORS.get(detector)
    if meta and meta.action_type == "auto_fix" and meta.fixers:
        available_fixers = [
            fixer
            for fixer in meta.fixers
            if supported_fixers is not None and fixer in supported_fixers
        ]
        if available_fixers:
            return f"desloppify autofix {available_fixers[0]} --dry-run"
    if detector == "subjective_review":
        if is_holistic_subjective_finding(item):
            return "desloppify review --prepare"
        return "desloppify show subjective"
    return f'desloppify plan resolve "{item.get("id", "")}" --note "<what you did>" --confirm'


__all__ = [
    "ALL_STATUSES",
    "ATTEST_EXAMPLE",
    "detail_dict",
    "is_review_finding",
    "is_subjective_finding",
    "is_subjective_queue_item",
    "primary_command_for_finding",
    "review_finding_weight",
    "scope_matches",
    "slugify",
    "status_matches",
    "supported_fixers_for_item",
]
