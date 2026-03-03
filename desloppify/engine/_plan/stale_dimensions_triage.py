"""Triage sync — manage triage stage IDs in the plan queue."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from desloppify.engine._plan.schema import PlanModel, ensure_plan_defaults
from desloppify.engine._state.schema import StateModel


@dataclass
class TriageSyncResult:
    """What changed during a triage sync."""

    injected: bool = False
    pruned: bool = False

    @property
    def changes(self) -> int:
        return int(self.injected) + int(self.pruned)


def review_finding_snapshot_hash(state: StateModel) -> str:
    """Hash open review finding IDs to detect changes.

    Returns empty string when there are no open review findings.
    """
    findings = state.get("findings", {})
    review_ids = sorted(
        fid for fid, f in findings.items()
        if f.get("status") == "open"
        and f.get("detector") in ("review", "concerns")
    )
    if not review_ids:
        return ""
    return hashlib.sha256("|".join(review_ids).encode()).hexdigest()[:16]


def sync_triage_needed(
    plan: PlanModel,
    state: StateModel,
) -> TriageSyncResult:
    """Inject 4 triage stage IDs at front of queue when review findings change.

    Only injects stages not already confirmed in ``epic_triage_meta``.

    When stages are already present but all new findings have been resolved
    since injection, auto-prunes the stale stages and updates the hash.

    When findings are *resolved* (current IDs are a subset of previously
    triaged IDs), the snapshot hash is updated silently — no re-triage
    is needed since the user is working through the plan.
    """
    from desloppify.engine._plan.stale_dimensions import (
        TRIAGE_IDS,
        TRIAGE_STAGE_IDS,
        _after_promoted,
    )

    ensure_plan_defaults(plan)
    result = TriageSyncResult()
    order: list[str] = plan["queue_order"]
    meta = plan.get("epic_triage_meta", {})
    confirmed = set(meta.get("triage_stages", {}).keys())

    # Check if any triage stage is already in queue
    already_present = any(sid in order for sid in TRIAGE_IDS)

    current_hash = review_finding_snapshot_hash(state)
    last_hash = meta.get("finding_snapshot_hash", "")

    if already_present:
        # Stages present — check if the reason for injection still applies.
        # Only auto-prune when triage was completed before (hash exists),
        # all new findings have been resolved, and no triage work is in
        # progress.  This avoids pruning the initial triage or a
        # user-started triage session.
        if last_hash and not confirmed:
            findings = state.get("findings", {})
            current_review_ids = {
                fid for fid, f in findings.items()
                if f.get("status") == "open"
                and f.get("detector") in ("review", "concerns")
            }
            triaged_ids = set(meta.get("triaged_ids", []))
            new_since_triage = current_review_ids - triaged_ids

            if not new_since_triage:
                # No new findings remain — prune stale stages
                for sid in TRIAGE_STAGE_IDS:
                    while sid in order:
                        order.remove(sid)
                if current_hash:
                    meta["finding_snapshot_hash"] = current_hash
                    plan["epic_triage_meta"] = meta
                result.pruned = True
        return result

    if current_hash and current_hash != last_hash:
        # Distinguish "new findings appeared" from "findings were resolved".
        # Only re-triage when genuinely new findings exist.
        findings = state.get("findings", {})
        current_review_ids = {
            fid for fid, f in findings.items()
            if f.get("status") == "open"
            and f.get("detector") in ("review", "concerns")
        }
        triaged_ids = set(meta.get("triaged_ids", []))
        new_since_triage = current_review_ids - triaged_ids

        if new_since_triage:
            # New review findings appeared — re-triage needed
            insert_at = _after_promoted(order, plan)
            stage_names = ("observe", "reflect", "organize", "commit")
            existing = set(order)
            injected_count = 0
            for sid, name in zip(TRIAGE_STAGE_IDS, stage_names):
                if name not in confirmed and sid not in existing:
                    order.insert(insert_at + injected_count, sid)
                    injected_count += 1
            if injected_count:
                result.injected = True
        else:
            # Only resolved findings changed the hash — update silently
            meta["finding_snapshot_hash"] = current_hash
            plan["epic_triage_meta"] = meta

    return result


def compute_new_finding_ids(plan: PlanModel, state: StateModel) -> set[str]:
    """Return the set of open review/concerns finding IDs added since last triage.

    Returns an empty set when no prior triage has recorded ``triaged_ids``.
    """
    meta = plan.get("epic_triage_meta", {})
    triaged = set(meta.get("triaged_ids", meta.get("synthesized_ids", [])))
    current = {
        fid for fid, f in state.get("findings", {}).items()
        if f.get("status") == "open" and f.get("detector") in ("review", "concerns")
    }
    return current - triaged if triaged else set()


def is_triage_stale(plan: PlanModel, state: StateModel) -> bool:
    """Side-effect-free check: is triage needed?

    Returns True when genuinely *new* review findings appeared since the
    last triage.  Triage stage IDs being in the queue alone is not
    sufficient — the new findings that triggered injection may have been
    resolved since then.

    When findings are merely resolved (current IDs are a subset of
    previously triaged IDs), triage is NOT stale — the user is working
    through the plan.
    """
    from desloppify.engine._plan.stale_dimensions import TRIAGE_IDS

    ensure_plan_defaults(plan)
    meta = plan.get("epic_triage_meta", {})

    # Always check for genuinely new findings (same logic regardless of
    # whether triage stages are in the queue).
    findings = state.get("findings", {})
    current_review_ids = {
        fid for fid, f in findings.items()
        if f.get("status") == "open"
        and f.get("detector") in ("review", "concerns")
    }
    triaged_ids = set(meta.get("triaged_ids", []))
    new_since_triage = current_review_ids - triaged_ids
    if new_since_triage:
        return True

    # If triage stages are in queue but there's in-progress triage work,
    # still consider it stale so the user finishes what they started.
    confirmed = set(meta.get("triage_stages", {}).keys())
    if confirmed:
        order = set(plan.get("queue_order", []))
        if order & TRIAGE_IDS:
            return True

    return False
