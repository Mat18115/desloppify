"""Rust detector package."""

from .custom import (
    detect_doctest_hygiene,
    detect_error_boundaries,
    detect_feature_hygiene,
    detect_future_proofing,
    detect_import_hygiene,
    detect_public_api_conventions,
    detect_thread_safety_contracts,
)
from .deps import build_dep_graph

__all__ = [
    "build_dep_graph",
    "detect_doctest_hygiene",
    "detect_error_boundaries",
    "detect_feature_hygiene",
    "detect_future_proofing",
    "detect_import_hygiene",
    "detect_public_api_conventions",
    "detect_thread_safety_contracts",
]
