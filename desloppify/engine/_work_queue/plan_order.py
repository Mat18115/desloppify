"""Plan-order and cluster-collapse helpers for work queues."""

from __future__ import annotations

from desloppify.state import StateModel


def new_item_ids(state: StateModel) -> set[str]:
    """Return finding IDs added in the most recent scan."""
    scan_history = state.get("scan_history", [])
    if not scan_history:
        return set()
    threshold = scan_history[-1].get("timestamp", "")
    if not threshold:
        return set()
    return {
        finding_id
        for finding_id, finding in state.get("findings", {}).items()
        if finding.get("first_seen", "") >= threshold
    }


def enrich_plan_metadata(items: list[dict], plan: dict) -> None:
    """Stamp plan description, note, and cluster info from overrides."""
    overrides: dict = plan.get("overrides", {})
    clusters: dict = plan.get("clusters", {})

    for item in items:
        override = overrides.get(item["id"], {})
        if override.get("description"):
            item["plan_description"] = override["description"]
        if override.get("note"):
            item["plan_note"] = override["note"]
        if override.get("cluster"):
            cluster_name = override["cluster"]
            cluster_data = clusters.get(cluster_name, {})
            item["plan_cluster"] = {
                "name": cluster_name,
                "description": cluster_data.get("description"),
                "total_items": len(cluster_data.get("finding_ids", [])),
            }


def stamp_plan_sort_keys(
    items: list[dict],
    plan: dict,
    new_ids: set[str],
) -> None:
    """Stamp ``_plan_position`` and ``_is_new`` on each item.

    These fields are consumed by :func:`item_sort_key` in ``ranking.py``
    to produce the correct final ordering in a single sort pass.
    """
    queue_order: list[str] = plan.get("queue_order", [])
    skipped_ids: set[str] = set(plan.get("skipped", {}).keys())

    position_map: dict[str, int] = {}
    for idx, finding_id in enumerate(queue_order):
        if finding_id not in skipped_ids:
            position_map[finding_id] = idx

    for item in items:
        item_id = item["id"]
        pos = position_map.get(item_id)
        item["_plan_position"] = pos  # None if not in queue_order
        item["_is_new"] = item_id in new_ids


def separate_skipped(
    items: list[dict],
    plan: dict,
) -> tuple[list[dict], list[dict]]:
    """Separate skipped items from the main list.

    Returns ``(non_skipped, skipped)`` so callers can optionally re-append.
    """
    skipped_ids: set[str] = set(plan.get("skipped", {}).keys())
    if not skipped_ids:
        return items, []
    non_skipped: list[dict] = []
    skipped: list[dict] = []
    for item in items:
        if item["id"] in skipped_ids:
            skipped.append(item)
        else:
            non_skipped.append(item)
    return non_skipped, skipped


def filter_cluster_focus(
    items: list[dict],
    plan: dict,
    cluster: str | None,
) -> list[dict]:
    """Filter to only cluster members when a cluster focus is active."""
    effective_cluster = cluster or plan.get("active_cluster")
    if not effective_cluster:
        return items
    clusters: dict = plan.get("clusters", {})
    cluster_data = clusters.get(effective_cluster, {})
    cluster_member_ids = set(cluster_data.get("finding_ids", []))
    if not cluster_member_ids:
        return items
    return [item for item in items if item["id"] in cluster_member_ids]


def stamp_positions(items: list[dict], plan: dict) -> None:
    """Stamp queue_position and plan_skipped metadata on each item."""
    skipped_map: dict = plan.get("skipped", {})
    skipped_ids: set[str] = set(skipped_map.keys())

    for position, item in enumerate(items):
        item["queue_position"] = position + 1
        if item["id"] in skipped_ids:
            item["plan_skipped"] = True
            skip_entry = skipped_map.get(item["id"])
            if skip_entry:
                item["plan_skip_kind"] = skip_entry.get("kind", "temporary")
                skip_reason = skip_entry.get("reason")
                if skip_reason:
                    item["plan_skip_reason"] = skip_reason


def action_type_for_detector(detector: str) -> str:
    """Look up the action_type for a detector from the registry."""
    try:
        from desloppify.core.registry import DETECTORS

        meta = DETECTORS.get(detector)
        if meta:
            return meta.action_type
    except ImportError as exc:
        _ = exc
    return "manual_fix"


def _build_cluster_meta(
    cluster_name: str, members: list[dict], cluster_data: dict
) -> dict:
    """Build a cluster meta-item from its member items."""
    detector = members[0].get("detector", "") if members else ""
    action = cluster_data.get("action") or ""
    if "desloppify autofix" in action:
        action_type = "auto_fix"
    elif "desloppify move" in action:
        action_type = "reorganize"
    else:
        action_type = action_type_for_detector(detector)
        if action_type == "auto_fix" and "desloppify autofix" not in action:
            action_type = "refactor"

    stored_desc = cluster_data.get("description") or ""
    total_in_cluster = len(cluster_data.get("finding_ids", []))
    if stored_desc and total_in_cluster != len(members):
        summary = stored_desc.replace(str(total_in_cluster), str(len(members)))
    else:
        summary = stored_desc or f"{len(members)} findings"

    primary_command = cluster_data.get("action")
    if not primary_command:
        primary_command = f"desloppify next --cluster {cluster_name} --count 10"

    estimated_impact = max(
        (m.get("estimated_impact", 0.0) for m in members), default=0.0
    )

    return {
        "id": cluster_name,
        "kind": "cluster",
        "action_type": action_type,
        "summary": summary,
        "members": members,
        "member_count": len(members),
        "primary_command": primary_command,
        "cluster_name": cluster_name,
        "cluster_auto": True,
        "cluster_optional": bool(cluster_data.get("optional")),
        "confidence": "high",
        "detector": detector,
        "file": "",
        "estimated_impact": estimated_impact,
    }


def collapse_clusters(items: list[dict], plan: dict) -> list[dict]:
    """Replace cluster member items with single cluster meta-items.

    Walks the list in order: the first member of each collapsed cluster is
    replaced with its meta-item, subsequent members are skipped.  This
    preserves the ordering established by sort + plan-order.
    """
    clusters = plan.get("clusters", {})
    if not clusters:
        return items

    fid_to_cluster: dict[str, str] = {}
    for name, cluster in clusters.items():
        if not cluster.get("auto"):
            continue
        for finding_id in cluster.get("finding_ids", []):
            fid_to_cluster[finding_id] = name

    if not fid_to_cluster:
        return items

    # Collect members per cluster (preserving encounter order)
    cluster_members: dict[str, list[dict]] = {}
    for item in items:
        cname = fid_to_cluster.get(item.get("id", ""))
        if cname:
            cluster_members.setdefault(cname, []).append(item)

    # Build meta-items only for clusters with 2+ members in the queue
    meta_items: dict[str, dict] = {}
    for cname, members in cluster_members.items():
        if len(members) < 2:
            continue
        meta_items[cname] = _build_cluster_meta(
            cname, members, clusters.get(cname, {})
        )

    # Walk in order: replace first member of each collapsed cluster
    # with its meta-item, skip subsequent members
    seen_clusters: set[str] = set()
    result: list[dict] = []
    for item in items:
        cname = fid_to_cluster.get(item.get("id", ""))
        if cname and cname in meta_items:
            if cname not in seen_clusters:
                seen_clusters.add(cname)
                result.append(meta_items[cname])
            # skip individual member
        else:
            result.append(item)
    return result


__all__ = [
    "collapse_clusters",
    "enrich_plan_metadata",
    "filter_cluster_focus",
    "new_item_ids",
    "separate_skipped",
    "stamp_plan_sort_keys",
    "stamp_positions",
]
