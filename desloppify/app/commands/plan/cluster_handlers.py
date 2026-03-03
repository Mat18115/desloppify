"""Plan cluster subcommand handlers."""

from __future__ import annotations

import argparse
import re

from desloppify.app.commands.helpers.runtime import command_runtime
from desloppify.app.commands.helpers.state import require_completed_scan
from desloppify.app.commands.plan._resolve import resolve_ids_from_patterns
from desloppify.core.output_api import colorize
from desloppify.app.commands.plan.reorder_handlers import resolve_target
from desloppify.engine.plan import (
    add_to_cluster,
    append_log_entry,
    create_cluster,
    delete_cluster,
    load_plan,
    merge_clusters,
    move_items,
    remove_from_cluster,
    save_plan,
)


_LEADING_NUM_RE = re.compile(r'^\d+\.\s*')


def _print_pattern_hints() -> None:
    """Print valid pattern format hints after a no-match error."""
    print(colorize("  Valid patterns:", "dim"))
    print(colorize("    f41b3eb7              (8-char hash suffix from dashboard)", "dim"))
    print(colorize("    review::path::name    (ID prefix)", "dim"))
    print(colorize("    review                (all findings from detector)", "dim"))
    print(colorize("    src/foo.py            (all findings in file)", "dim"))
    print(colorize("    timing_attack         (finding name — last ::segment of ID)", "dim"))
    print(colorize("    review::*naming*      (glob pattern)", "dim"))
    print(colorize("    my-cluster            (cluster name — expands to members)", "dim"))


def _cmd_cluster_create(args: argparse.Namespace) -> None:
    name: str = getattr(args, "cluster_name", "")
    description: str | None = getattr(args, "description", None)
    action: str | None = getattr(args, "action", None)
    plan = load_plan()
    try:
        create_cluster(plan, name, description, action=action)
    except ValueError as ex:
        print(colorize(f"  {ex}", "red"))
        return
    append_log_entry(
        plan, "cluster_create", cluster_name=name, actor="user",
        detail={"description": description, "action": action},
    )
    save_plan(plan)
    print(colorize(f"  Created cluster: {name}", "green"))


def _cmd_cluster_add(args: argparse.Namespace) -> None:
    state = command_runtime(args).state
    if not require_completed_scan(state):
        return

    cluster_name: str = getattr(args, "cluster_name", "")
    patterns: list[str] = getattr(args, "patterns", [])

    plan = load_plan()
    finding_ids = resolve_ids_from_patterns(state, patterns, plan=plan)
    if not finding_ids:
        print(colorize("  No matching findings found.", "yellow"))
        _print_pattern_hints()
        return
    try:
        count = add_to_cluster(plan, cluster_name, finding_ids)
    except ValueError as ex:
        print(colorize(f"  {ex}", "red"))
        return

    # Check for overlap with other manual clusters
    member_set = set(finding_ids)
    for other_name, other_cluster in plan.get("clusters", {}).items():
        if other_name == cluster_name or other_cluster.get("auto"):
            continue
        other_ids = set(other_cluster.get("finding_ids", []))
        if not other_ids:
            continue
        overlap = member_set & other_ids
        if len(overlap) > len(other_ids) * 0.5:
            print(colorize(
                f"  Warning: {len(overlap)} finding(s) also in cluster '{other_name}' "
                f"({len(overlap)}/{len(other_ids)} = {int(len(overlap)/len(other_ids)*100)}% overlap).",
                "yellow",
            ))

    append_log_entry(
        plan, "cluster_add", finding_ids=finding_ids, cluster_name=cluster_name, actor="user",
    )
    save_plan(plan)
    print(colorize(f"  Added {count} item(s) to cluster {cluster_name}.", "green"))


def _cmd_cluster_remove(args: argparse.Namespace) -> None:
    state = command_runtime(args).state
    if not require_completed_scan(state):
        return

    cluster_name: str = getattr(args, "cluster_name", "")
    patterns: list[str] = getattr(args, "patterns", [])

    plan = load_plan()
    finding_ids = resolve_ids_from_patterns(state, patterns, plan=plan)
    if not finding_ids:
        print(colorize("  No matching findings found.", "yellow"))
        _print_pattern_hints()
        return
    try:
        count = remove_from_cluster(plan, cluster_name, finding_ids)
    except ValueError as ex:
        print(colorize(f"  {ex}", "red"))
        return
    append_log_entry(
        plan, "cluster_remove", finding_ids=finding_ids, cluster_name=cluster_name, actor="user",
    )
    save_plan(plan)
    print(colorize(f"  Removed {count} item(s) from cluster {cluster_name}.", "green"))


def _cmd_cluster_delete(args: argparse.Namespace) -> None:
    cluster_name: str = getattr(args, "cluster_name", "")
    plan = load_plan()
    try:
        orphaned = delete_cluster(plan, cluster_name)
    except ValueError as ex:
        print(colorize(f"  {ex}", "red"))
        return
    append_log_entry(
        plan, "cluster_delete", finding_ids=orphaned, cluster_name=cluster_name, actor="user",
    )
    save_plan(plan)
    print(colorize(f"  Deleted cluster {cluster_name} ({len(orphaned)} items orphaned).", "green"))


def _cmd_cluster_reorder(args: argparse.Namespace) -> None:
    raw_names: str = getattr(args, "cluster_names", "") or getattr(args, "cluster_name", "")
    cluster_names: list[str] = [n.strip() for n in raw_names.split(",") if n.strip()]
    position: str = getattr(args, "position", "top")
    target: str | None = getattr(args, "target", None)

    plan = load_plan()
    clusters = plan.get("clusters", {})

    # Validate all names exist
    for name in cluster_names:
        if name not in clusters:
            print(colorize(f"  Cluster {name!r} does not exist.", "red"))
            return

    # Resolve cluster-name targets for before/after
    target = resolve_target(plan, target, position)

    offset: int | None = None
    if position in ("up", "down") and target is not None:
        try:
            offset = int(target)
        except (ValueError, TypeError):
            print(colorize(f"  Invalid offset: {target}", "red"))
            return
        target = None

    # Collect all member IDs from all clusters, preserving order, deduplicating
    seen: set[str] = set()
    all_member_ids: list[str] = []
    for name in cluster_names:
        for fid in clusters[name].get("finding_ids", []):
            if fid not in seen:
                seen.add(fid)
                all_member_ids.append(fid)

    if not all_member_ids:
        print(colorize("  No members in the specified cluster(s).", "yellow"))
        return

    count = move_items(plan, all_member_ids, position, target=target, offset=offset)
    append_log_entry(
        plan, "cluster_reorder", cluster_name=",".join(cluster_names), actor="user",
        detail={"position": position, "count": count},
    )
    save_plan(plan)
    label = ", ".join(cluster_names)
    print(colorize(f"  Moved cluster(s) {label} ({count} items) to {position}.", "green"))


def _print_cluster_member(idx: int, fid: str, finding: dict | None) -> None:
    """Print a single cluster member line with optional finding details."""
    print(f"    {idx}. {fid}")
    if not finding:
        return
    file = finding.get("file", "")
    lines = finding.get("detail", {}).get("lines", [])
    line_str = f" at lines: {', '.join(str(ln) for ln in lines)}" if lines else ""
    if file:
        print(colorize(f"       File: {file}{line_str}", "dim"))
    summary = finding.get("summary", "")
    if summary:
        print(colorize(f"       {summary}", "dim"))


def _load_findings_best_effort(args: argparse.Namespace) -> dict:
    """Load findings from state, returning empty dict on failure."""
    rt = command_runtime(args)
    return rt.state.get("findings", {})


def _cmd_cluster_show(args: argparse.Namespace) -> None:
    cluster_name: str = getattr(args, "cluster_name", "")
    plan = load_plan()
    cluster = plan.get("clusters", {}).get(cluster_name)
    if cluster is None:
        print(colorize(f"  Cluster {cluster_name!r} does not exist.", "red"))
        return

    # Header
    auto_tag = "Auto-generated" if cluster.get("auto") else "Manual"
    cluster_key = cluster.get("cluster_key", "")
    key_type = f" ({cluster_key.split('::', 1)[0]})" if cluster_key else ""
    print(colorize(f"  Cluster: {cluster_name}", "bold"))
    print(colorize(f"  Type: {auto_tag}{key_type}", "dim"))
    desc = cluster.get("description") or ""
    if desc:
        print(colorize(f"  Description: {desc}", "dim"))
    action = cluster.get("action") or ""
    if action:
        print(colorize(f"  Action: {action}", "dim"))

    # Members
    finding_ids = cluster.get("finding_ids", [])
    if not finding_ids:
        print(colorize("  Members: (none)", "dim"))
    else:
        findings = _load_findings_best_effort(args)
        print(colorize(f"  Members ({len(finding_ids)}):", "dim"))
        for idx, fid in enumerate(finding_ids, 1):
            _print_cluster_member(idx, fid, findings.get(fid))

    # Commands
    print()
    print(colorize("  Commands:", "dim"))
    print(colorize(f'    Resolve all:  desloppify plan resolve "{cluster_name}" --note "<what>" --attest "..."', "dim"))
    print(colorize(f"    Drill in:     desloppify next --cluster {cluster_name} --count 10", "dim"))
    print(colorize(f"    Skip:         desloppify plan skip {cluster_name}", "dim"))


def _cmd_cluster_list(args: argparse.Namespace) -> None:
    plan = load_plan()
    clusters = plan.get("clusters", {})
    active = plan.get("active_cluster")
    if not clusters:
        print("  No clusters defined.")
        return
    print(colorize("  Clusters:", "bold"))
    for name, cluster in clusters.items():
        member_count = len(cluster.get("finding_ids", []))
        desc = cluster.get("description") or ""
        marker = " (focused)" if name == active else ""
        desc_str = f" — {desc}" if desc else ""
        auto_tag = " [auto]" if cluster.get("auto") else ""
        print(f"    {name}: {member_count} items{auto_tag}{desc_str}{marker}")


def _cmd_cluster_update(args: argparse.Namespace) -> None:
    """Update cluster description and/or action_steps."""
    cluster_name: str = getattr(args, "cluster_name", "")
    description: str | None = getattr(args, "description", None)
    steps: list[str] | None = getattr(args, "steps", None)

    if description is None and steps is None:
        print(colorize("  Nothing to update. Use --description and/or --steps.", "yellow"))
        return

    plan = load_plan()
    cluster = plan.get("clusters", {}).get(cluster_name)
    if cluster is None:
        print(colorize(f"  Cluster {cluster_name!r} does not exist.", "red"))
        return

    if description is not None:
        cluster["description"] = description
    if steps is not None:
        cluster["action_steps"] = list(steps)
        print(colorize(f"  Stored {len(steps)} action step(s):", "dim"))
        for i, step in enumerate(steps, 1):
            clean = _LEADING_NUM_RE.sub('', step)
            print(colorize(f"    {i}. {clean}", "dim"))
        if len(steps) == 0:
            print(colorize("  Warning: 0 steps stored. Did you forget the --steps values?", "yellow"))
        elif len(steps) == 1 and len(steps[0]) > 100:
            print(colorize("  Warning: only 1 step stored and it's quite long. Check shell quoting.", "yellow"))
    cluster["user_modified"] = True

    from desloppify.state import utc_now

    cluster["updated_at"] = utc_now()
    append_log_entry(
        plan, "cluster_update", cluster_name=cluster_name, actor="user",
        detail={"description": description, "steps": steps},
    )
    save_plan(plan)
    print(colorize(f"  Updated cluster: {cluster_name}", "green"))


def _cmd_cluster_merge(args: argparse.Namespace) -> None:
    """Merge source cluster into target cluster."""
    source: str = getattr(args, "source", "")
    target: str = getattr(args, "target", "")

    plan = load_plan()
    try:
        added, source_ids = merge_clusters(plan, source, target)
    except ValueError as ex:
        print(colorize(f"  {ex}", "red"))
        return

    append_log_entry(
        plan, "cluster_merge", finding_ids=source_ids,
        cluster_name=target, actor="user",
        detail={"source": source, "added": added},
    )
    save_plan(plan)
    print(colorize(
        f"  Merged cluster {source!r} into {target!r}: "
        f"{added} finding(s) added, {len(source_ids)} total moved. Source deleted.",
        "green",
    ))


def cmd_cluster_dispatch(args: argparse.Namespace) -> None:
    """Route cluster subcommands."""
    cluster_action = getattr(args, "cluster_action", None)
    dispatch = {
        "create": _cmd_cluster_create,
        "add": _cmd_cluster_add,
        "remove": _cmd_cluster_remove,
        "delete": _cmd_cluster_delete,
        "reorder": _cmd_cluster_reorder,
        "show": _cmd_cluster_show,
        "list": _cmd_cluster_list,
        "update": _cmd_cluster_update,
        "merge": _cmd_cluster_merge,
    }
    handler = dispatch.get(cluster_action)
    if handler is None:
        _cmd_cluster_list(args)
        return
    handler(args)


__all__ = ["cmd_cluster_dispatch"]
