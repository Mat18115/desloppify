"""Scan coverage confidence — annotate dimension scores with coverage metadata."""

from __future__ import annotations

from desloppify.core.coercions_api import coerce_confidence
from desloppify.engine._state.schema import StateModel
from desloppify.languages._framework.base.types import ScanCoverageRecord


def active_scan_coverage(state: StateModel) -> ScanCoverageRecord:
    """Resolve the active scan-coverage record for the current language."""
    scan_coverage = state.get("scan_coverage", {})
    if not isinstance(scan_coverage, dict) or not scan_coverage:
        return {}

    lang_name = state.get("lang")
    if isinstance(lang_name, str) and lang_name:
        payload = scan_coverage.get(lang_name, {})
        return payload if isinstance(payload, dict) else {}

    if len(scan_coverage) == 1:
        only = next(iter(scan_coverage.values()))
        return only if isinstance(only, dict) else {}
    return {}


def apply_scan_coverage_to_dimension_scores(
    state: StateModel,
    *,
    dimension_scores: dict[str, dict],
) -> None:
    """Annotate dimension scores with scan-coverage confidence metadata.

    Walks each dimension's detectors, checks which have reduced scan
    coverage, and stamps ``coverage_status``, ``coverage_confidence``,
    and ``coverage_impacts`` onto the dimension data.  Also updates
    ``state["score_confidence"]`` with a summary.
    """
    coverage_payload = active_scan_coverage(state)
    detectors_payload = coverage_payload.get("detectors", {})
    if not isinstance(detectors_payload, dict):
        state["score_confidence"] = {
            "status": "full",
            "confidence": 1.0,
            "detectors": [],
            "dimensions": [],
        }
        return

    reduced_detectors: dict[str, dict[str, object]] = {}
    for detector, raw in detectors_payload.items():
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "full")).lower()
        confidence = coerce_confidence(raw.get("confidence"), default=1.0)
        if status != "reduced" and confidence >= 1.0:
            continue
        reduced_detectors[str(detector)] = dict(raw)

    reduced_dimensions: list[str] = []
    score_confidence_detectors: list[dict[str, object]] = []
    for detector, payload in reduced_detectors.items():
        score_confidence_detectors.append(
            {
                "detector": detector,
                "status": str(payload.get("status", "reduced")),
                "confidence": round(
                    coerce_confidence(payload.get("confidence"), default=1.0), 2
                ),
                "summary": str(payload.get("summary", "") or ""),
                "impact": str(payload.get("impact", "") or ""),
                "remediation": str(payload.get("remediation", "") or ""),
                "tool": str(payload.get("tool", "") or ""),
                "reason": str(payload.get("reason", "") or ""),
            }
        )

    for dim_name, dim_data in dimension_scores.items():
        if not isinstance(dim_data, dict):
            continue
        detectors = dim_data.get("detectors", {})
        if not isinstance(detectors, dict):
            continue

        impacts: list[dict[str, object]] = []
        for detector_name, detector_meta in detectors.items():
            reduced = reduced_detectors.get(str(detector_name))
            if not isinstance(detector_meta, dict):
                continue
            if reduced is None:
                detector_meta.pop("coverage_status", None)
                detector_meta.pop("coverage_confidence", None)
                detector_meta.pop("coverage_summary", None)
                continue
            confidence = coerce_confidence(reduced.get("confidence"), default=1.0)
            status = str(reduced.get("status", "reduced"))
            summary = str(reduced.get("summary", "") or "")
            detector_meta["coverage_status"] = status
            detector_meta["coverage_confidence"] = round(confidence, 2)
            detector_meta["coverage_summary"] = summary
            impacts.append(
                {
                    "detector": str(detector_name),
                    "status": status,
                    "confidence": round(confidence, 2),
                    "summary": summary,
                }
            )

        if not impacts:
            dim_data.pop("coverage_status", None)
            dim_data.pop("coverage_confidence", None)
            dim_data.pop("coverage_impacts", None)
            continue

        reduced_dimensions.append(str(dim_name))
        dim_data["coverage_status"] = "reduced"
        dim_data["coverage_confidence"] = round(
            min(coerce_confidence(item.get("confidence"), default=1.0) for item in impacts),
            2,
        )
        dim_data["coverage_impacts"] = impacts

    if not score_confidence_detectors:
        state["score_confidence"] = {
            "status": "full",
            "confidence": 1.0,
            "detectors": [],
            "dimensions": [],
        }
        return

    state["score_confidence"] = {
        "status": "reduced",
        "confidence": round(
            min(
                coerce_confidence(item.get("confidence"), default=1.0)
                for item in score_confidence_detectors
            ),
            2,
        ),
        "detectors": score_confidence_detectors,
        "dimensions": sorted(set(reduced_dimensions)),
    }
