"""TypedDict model definitions for persisted state payloads."""

from __future__ import annotations

from typing import Any, NotRequired, Required, TypedDict

from desloppify.engine._state.schema_types_issues import (
    DimensionScore,
    Issue,
    ScanHistoryEntry,
    ScoreConfidenceDetector,
    ScoreConfidenceModel,
    StateStats,
    TierStats,
)
from desloppify.engine._state.schema_types_review import (
    AssessmentImportAuditEntry,
    AttestationLogEntry,
    ConcernDismissal,
    IgnoreIntegrityModel,
    LangCapability,
    ReviewCacheModel,
    SubjectiveAssessment,
    SubjectiveAssessmentJudgment,
    SubjectiveIntegrity,
)
from desloppify.languages._framework.base.types import ScanCoverageRecord


class StateModel(TypedDict, total=False):
    version: Required[int]
    created: Required[str]
    last_scan: Required[str | None]
    scan_count: Required[int]
    overall_score: Required[float]
    objective_score: Required[float]
    strict_score: Required[float]
    verified_strict_score: Required[float]
    stats: Required[StateStats]
    issues: Required[dict[str, Issue]]
    dimension_scores: dict[str, DimensionScore]
    scan_path: str | None
    tool_hash: str
    scan_completeness: dict[str, str]
    potentials: dict[str, dict[str, int]]
    codebase_metrics: dict[str, dict[str, Any]]
    scan_coverage: dict[str, ScanCoverageRecord]
    score_confidence: ScoreConfidenceModel
    scan_history: list[ScanHistoryEntry]
    lang_capabilities: dict[str, LangCapability]
    zone_distribution: dict[str, int]
    review_cache: ReviewCacheModel
    reminder_history: dict[str, int]
    ignore_integrity: IgnoreIntegrityModel
    config: dict[str, Any]
    lang: str
    subjective_integrity: Required[SubjectiveIntegrity]
    subjective_assessments: Required[dict[str, SubjectiveAssessment]]
    custom_review_dimensions: list[str]
    assessment_import_audit: list[AssessmentImportAuditEntry]
    attestation_log: list[AttestationLogEntry]
    concern_dismissals: dict[str, ConcernDismissal]
    _plan_start_scores_for_reveal: dict[str, Any]


class ScanDiff(TypedDict):
    new: int
    auto_resolved: int
    reopened: int
    total_current: int
    suspect_detectors: list[str]
    chronic_reopeners: list[dict]
    skipped_other_lang: int
    resolved_out_of_scope: int
    ignored: int
    ignore_patterns: int
    raw_issues: int
    suppressed_pct: float
    skipped: NotRequired[int]
    skipped_details: NotRequired[list[dict]]


__all__ = [
    "ConcernDismissal",
    "AssessmentImportAuditEntry",
    "AttestationLogEntry",
    "Issue",
    "TierStats",
    "StateStats",
    "DimensionScore",
    "ScoreConfidenceDetector",
    "ScoreConfidenceModel",
    "ScanHistoryEntry",
    "SubjectiveAssessment",
    "SubjectiveAssessmentJudgment",
    "SubjectiveIntegrity",
    "LangCapability",
    "ReviewCacheModel",
    "IgnoreIntegrityModel",
    "StateModel",
    "ScanDiff",
]
