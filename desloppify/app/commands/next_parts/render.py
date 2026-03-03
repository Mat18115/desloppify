"""Terminal rendering helpers for the `next` command."""

from __future__ import annotations

from desloppify import scoring as scoring_mod
from desloppify.app.commands.scan.scan_reporting_subjective import (
    build_subjective_followup,
)
from desloppify.engine.work_queue import ATTEST_EXAMPLE
from desloppify.intelligence.integrity import (
    is_holistic_subjective_finding,
    unassessed_subjective_dimensions,
)
from desloppify.app.commands.next_parts.render_support import (
    is_auto_fix_command,
    render_cluster_item as _render_cluster_item,
    render_compact_item as _render_compact_item,
    render_gate_banner,
    render_grouped as _render_grouped,
    render_queue_header,
    scorecard_subjective,
    show_empty_queue,
    subjective_coverage_breakdown,
)
from desloppify.core.output_api import colorize, log
from desloppify.core.paths_api import read_code_snippet
from desloppify.scoring import compute_health_breakdown, compute_score_impact

def _render_workflow_stage(item: dict) -> None:
    """Render a triage workflow stage item."""
    blocked = item.get("is_blocked", False)
    stage = item.get("stage_name", "")
    tag = " [blocked]" if blocked else ""
    style = "dim" if blocked else "bold"
    print(colorize(f"  (Planning stage: {stage}{tag})", style))
    print(colorize("  " + "─" * 60, "dim"))
    print(f"  {colorize(item.get('summary', ''), 'yellow')}")
    detail = item.get("detail", {})
    total = detail.get("total_review_findings", 0)
    if total:
        print(colorize(f"  {total} review findings to analyze", "dim"))
    if blocked:
        blocked_by = item.get("blocked_by", [])
        deps = ", ".join(b.replace("triage::", "") for b in blocked_by)
        print(colorize(f"  Blocked by: {deps}", "dim"))
        first_dep = blocked_by[0] if blocked_by else ""
        dep_name = first_dep.replace("triage::", "")
        if dep_name:
            print(colorize(f"  Next step: desloppify plan triage --stage {dep_name}", "dim"))
    else:
        print(colorize(f"\n  Action: {item.get('primary_command', '')}", "cyan"))


def _render_workflow_action(item: dict) -> None:
    """Render a workflow action item (e.g. create-plan)."""
    print(colorize("  (Workflow step)", "bold"))
    print(colorize("  " + "─" * 60, "dim"))
    print(f"  {colorize(item.get('summary', ''), 'yellow')}")
    print(colorize(f"\n  Action: {item.get('primary_command', '')}", "cyan"))


def _render_subjective_dimension(item: dict, *, explain: bool) -> None:
    """Render a subjective dimension re-review item."""
    detail = item.get("detail", {})
    subjective_score = float(
        detail.get("strict_score", item.get("subjective_score", 100.0))
    )
    print(f"  Dimension: {detail.get('dimension_name', 'unknown')}")
    print(f"  Score: {subjective_score:.1f}%")
    print(
        colorize(
            f"  Action: {item.get('primary_command', 'desloppify review --prepare')}",
            "cyan",
        )
    )
    print(colorize(
        "  Note: re-review scores what it finds — scores can go down if issues are discovered.",
        "dim",
    ))
    if explain:
        reason = item.get("explain", {}).get(
            "policy",
            "subjective items sort after mechanical items at the same level.",
        )
        print(colorize(f"  explain: {reason}", "dim"))


def _render_finding_detail(item: dict) -> dict:
    """Render plan overrides, file info, and detail fields. Returns parsed detail dict."""
    if item.get("plan_description"):
        print(colorize(f"  → {item['plan_description']}", "cyan"))
    plan_cluster = item.get("plan_cluster")
    if isinstance(plan_cluster, dict):
        cluster_name = plan_cluster.get("name", "")
        cluster_desc = plan_cluster.get("description") or ""
        total = plan_cluster.get("total_items", 0)
        desc_str = f' — "{cluster_desc}"' if cluster_desc else ""
        print(colorize(f"  Cluster: {cluster_name}{desc_str} ({total} items)", "dim"))
    if item.get("plan_note"):
        print(colorize(f"  Note: {item['plan_note']}", "dim"))

    print(f"  File: {item.get('file', '')}")
    print(colorize(f"  ID:   {item.get('id', '')}", "dim"))

    detail = item.get("detail", {})
    if isinstance(detail, str):
        detail = {"suggestion": detail}
    if isinstance(detail, dict):
        detail.setdefault("lines", [])
        detail.setdefault("line", None)
        detail.setdefault("category", None)
        detail.setdefault("importers", None)
        detail.setdefault("count", 0)
    if detail.get("lines"):
        print(f"  Lines: {', '.join(str(line_no) for line_no in detail['lines'][:8])}")
    if detail.get("category"):
        print(f"  Category: {detail['category']}")
    if detail.get("importers") is not None:
        print(f"  Active importers: {detail['importers']}")
    if detail.get("suggestion"):
        print(colorize(f"\n  Suggestion: {detail['suggestion']}", "dim"))

    target_line = detail.get("line") or (detail.get("lines", [None]) or [None])[0]
    if target_line and item.get("file") not in (".", ""):
        snippet = read_code_snippet(item["file"], target_line)
        if snippet:
            print(colorize("\n  Code:", "dim"))
            print(snippet)

    return detail


def _render_score_impact(
    item: dict, dim_scores: dict, potentials: dict | None,
) -> None:
    """Render dimension score context and impact estimates."""
    detector = item.get("detector", "")
    if dim_scores:
        dimension = scoring_mod.get_dimension_for_detector(detector)
        if dimension and dimension.name in dim_scores:
            dimension_score = dim_scores[dimension.name]
            strict_val = dimension_score.get("strict", dimension_score["score"])
            print(
                colorize(
                    f"\n  Dimension: {dimension.name} — {dimension_score['score']:.1f}% "
                    f"(strict: {strict_val:.1f}%) "
                    f"({dimension_score['issues']} of {dimension_score['checks']:,} checks failing)",
                    "dim",
                )
            )

    if potentials and detector and dim_scores:
        try:
            impact = compute_score_impact(dim_scores, potentials, detector, issues_to_fix=1)
            if impact > 0:
                print(colorize(f"  Impact: fixing this is worth ~+{impact:.1f} pts on overall score", "cyan"))
            else:
                dimension = scoring_mod.get_dimension_for_detector(detector)
                if dimension and dimension.name in dim_scores:
                    issues = dim_scores[dimension.name].get("issues", 0)
                    if issues > 1:
                        bulk = compute_score_impact(dim_scores, potentials, detector, issues_to_fix=issues)
                        if bulk > 0:
                            print(colorize(
                                f"  Impact: fixing all {issues} {detector} issues → ~+{bulk:.1f} pts",
                                "cyan",
                            ))
        except (ImportError, TypeError, ValueError, KeyError) as exc:
            log(f"  score impact estimate skipped: {exc}")
    elif detector == "review" and dim_scores:
        try:
            dim_key = item.get("detail", {}).get("dimension", "")
            if dim_key:
                breakdown = compute_health_breakdown(dim_scores)
                for entry in breakdown.get("entries", []):
                    if not isinstance(entry, dict):
                        continue
                    entry_key = str(entry.get("name", "")).lower().replace(" ", "_")
                    if entry_key == dim_key.lower().replace(" ", "_"):
                        drag = float(entry.get("overall_drag", 0) or 0)
                        if drag > 0.01:
                            print(colorize(
                                f"  Dimension drag: {entry['name']} costs -{drag:.2f} pts on overall score",
                                "cyan",
                            ))
                        break
        except (ImportError, TypeError, ValueError, KeyError) as exc:
            log(f"  dimension drag estimate skipped: {exc}")


def _render_item(
    item: dict, dim_scores: dict, findings_scoped: dict, explain: bool,
    potentials: dict | None = None,
) -> None:
    if item.get("kind") == "cluster":
        _render_cluster_item(item)
        return
    if item.get("kind") == "workflow_stage":
        _render_workflow_stage(item)
        return
    if item.get("kind") == "workflow_action":
        _render_workflow_action(item)
        return

    confidence = item.get("confidence", "medium")
    print(colorize(f"  ({confidence} confidence)", "bold"))
    print(colorize("  " + "─" * 60, "dim"))
    print(f"  {colorize(item.get('summary', ''), 'yellow')}")

    if item.get("detector") == "review":
        print(colorize("  Type: Design review (requires judgment)", "dim"))
    elif is_auto_fix_command(item.get("primary_command")):
        print(colorize("  Type: Auto-fixable", "dim"))

    if item.get("kind", "finding") == "subjective_dimension":
        _render_subjective_dimension(item, explain=explain)
        return

    detail = _render_finding_detail(item)
    _render_score_impact(item, dim_scores, potentials)

    detector_name = item.get("detector", "")
    auto_fix_command = item.get("primary_command")
    if is_auto_fix_command(auto_fix_command):
        similar_count = sum(
            1
            for finding in findings_scoped.values()
            if finding.get("detector") == detector_name and finding["status"] == "open"
        )
        if similar_count > 1:
            print(
                colorize(
                    f"\n  Auto-fixable: {similar_count} similar findings. "
                    f"Run `{auto_fix_command}` to fix all at once.",
                    "cyan",
                )
            )
    if explain:
        explanation = item.get("explain", {})
        count_weight = explanation.get("count", int(detail.get("count", 0) or 0))
        detector = item.get("detector", "")
        base = (
            f"ranked by confidence={confidence}, "
            f"count={count_weight}, id={item.get('id', '')}"
        )
        if dim_scores and detector:
            dimension = scoring_mod.get_dimension_for_detector(detector)
            if dimension and dimension.name in dim_scores:
                ds = dim_scores[dimension.name]
                base += (
                    f". Dimension: {dimension.name} at {ds['score']:.1f}% "
                    f"({ds['issues']} open issues)"
                )
        if item.get("detector") == "review" and dim_scores:
            dim_key = item.get("detail", {}).get("dimension", "")
            if dim_key:
                for ds_name, ds_data in dim_scores.items():
                    if ds_name.lower().replace(" ", "_") == dim_key.lower().replace(" ", "_"):
                        score_val = ds_data.get("score", "?")
                        score_str = f"{score_val:.1f}" if isinstance(score_val, (int, float)) else str(score_val)
                        base += f". Subjective dimension: {ds_name} at {score_str}%"
                        break
        policy = explanation.get("policy")
        if policy:
            base = f"{base}. {policy}"
        print(colorize(f"  explain: {base}", "dim"))


def render_terminal_items(
    items: list[dict],
    dim_scores: dict,
    findings_scoped: dict,
    *,
    group: str,
    explain: bool,
    potentials: dict | None = None,
    plan: dict | None = None,
    cluster_filter: str | None = None,
) -> None:
    # Show focus header if plan has active cluster
    if plan and plan.get("active_cluster"):
        cluster_name = plan["active_cluster"]
        clusters = plan.get("clusters", {})
        cluster_data = clusters.get(cluster_name, {})
        total = len(cluster_data.get("finding_ids", []))
        print(colorize(f"\n  Focused on: {cluster_name} ({len(items)} of {total} remaining)", "cyan"))

    if group != "item":
        _render_grouped(items, group)
        return

    # Detect cluster drill-in: multiple items with cluster focus active
    is_cluster_drill = len(items) > 1 and (
        cluster_filter or (plan and plan.get("active_cluster"))
    )

    for idx, item in enumerate(items):
        if idx > 0:
            print()
        # Full card for first item, compact for rest in cluster drill-in
        if is_cluster_drill and idx > 0:
            _render_compact_item(item, idx, len(items))
            continue
        queue_pos = item.get("queue_position")
        if queue_pos and len(items) > 1:
            label = f"  [#{queue_pos}]"
        elif len(items) > 1:
            label = f"  [{idx + 1}/{len(items)}]"
        else:
            pos_str = f"  (#{ queue_pos} in queue)" if queue_pos else ""
            label = f"  Next item{pos_str}"
        print(colorize(label, "bold"))
        _render_item(item, dim_scores, findings_scoped, explain=explain, potentials=potentials)


def render_uncommitted_reminder(plan: dict | None) -> None:
    """Show a subtle reminder if there are uncommitted resolved findings."""
    if plan is None:
        return
    try:
        from desloppify.core.config import load_config

        config = load_config()
        if not config.get("commit_tracking_enabled", True):
            return

        uncommitted = plan.get("uncommitted_findings", [])
        if not uncommitted:
            return

        count = len(uncommitted)
        print(colorize(
            f"\n  {count} resolved finding{'s' if count != 1 else ''} uncommitted"
            " — `desloppify plan commit-log` to review",
            "dim",
        ))
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        log(f"  uncommitted reminder skipped: {exc}")


def render_single_item_resolution_hint(items: list[dict]) -> None:
    if len(items) != 1:
        return
    kind = items[0].get("kind", "finding")
    if kind in ("cluster", "workflow_stage", "workflow_action"):
        return  # These kinds have their own resolution hints
    if kind != "finding":
        return
    item = items[0]
    detector_name = item.get("detector", "")
    if detector_name == "subjective_review":
        print(colorize("\n  Review with:", "dim"))
        primary = item.get(
            "primary_command", "desloppify show subjective"
        )
        print(f"    {primary}")
        if is_holistic_subjective_finding(item):
            print("    desloppify review --prepare")
        return

    primary = item.get("primary_command", "")
    if is_auto_fix_command(primary):
        print(colorize("\n  Fix with:", "dim"))
        print(f"    {primary}")
        print(colorize("  Or resolve individually:", "dim"))
    else:
        print(colorize("\n  Resolve with:", "dim"))

    print(
        f'    desloppify plan resolve "{item["id"]}" --note "<what you did>" --confirm'
    )
    print(
        f'    desloppify plan skip --permanent "{item["id"]}" --note "<why>" '
        f'--attest "{ATTEST_EXAMPLE}"'
    )


def render_followup_nudges(
    state: dict,
    dim_scores: dict,
    findings_scoped: dict,
    *,
    strict_score: float | None,
    target_strict_score: float,
    queue_total: int = 0,
    plan_start_strict: float | None = None,
    breakdown: "QueueBreakdown | None" = None,
) -> None:
    from desloppify.app.commands.helpers.queue_progress import (
        format_queue_block,
    )

    subjective_threshold = target_strict_score
    subjective_entries = scorecard_subjective(state, dim_scores)
    followup = build_subjective_followup(
        state,
        subjective_entries,
        threshold=subjective_threshold,
        max_quality_items=3,
        max_integrity_items=5,
    )
    unassessed_subjective = unassessed_subjective_dimensions(dim_scores)
    # Show frozen plan-start score + queue block when in an active cycle
    if queue_total > 0 and plan_start_strict is not None and breakdown is not None:
        frozen = plan_start_strict
        block = format_queue_block(breakdown, frozen_score=frozen, live_score=strict_score)
        print()
        for text, style in block:
            print(colorize(text, style))
        print(colorize(
            "  Score will not update until the queue is clear and you run `desloppify scan`.",
            "dim",
        ))
    elif queue_total > 0 and plan_start_strict is not None:
        from desloppify.app.commands.helpers.queue_progress import format_plan_delta
        delta_str = format_plan_delta(strict_score, plan_start_strict) if strict_score is not None else ""
        if delta_str:
            score_line = f"\n  Score: strict {strict_score:.1f}/100 (plan start: {plan_start_strict:.1f}, {delta_str})"
        else:
            score_line = f"\n  Score (frozen at plan start): strict {plan_start_strict:.1f}/100"
        print(colorize(score_line, "cyan"))
        print(
            colorize(
                f"  Queue: {queue_total} item{'s' if queue_total != 1 else ''}"
                " remaining. Score will not update until the queue is clear and you run `desloppify scan`.",
                "dim",
            )
        )
    elif strict_score is not None:
        gap = round(float(target_strict_score) - float(strict_score), 1)
        if gap > 0:
            print(
                colorize(
                    f"\n  North star: strict {strict_score:.1f}/100 → target {target_strict_score:.1f} (+{gap:.1f} needed)",
                    "cyan",
                )
            )
        else:
            print(
                colorize(
                    f"\n  North star: strict {strict_score:.1f}/100 meets target {target_strict_score:.1f}",
                    "green",
                )
            )
    # Show queue block after north star when no frozen score
    if breakdown is not None and queue_total > 0 and plan_start_strict is None:
        block = format_queue_block(breakdown)
        for text, style in block:
            print(colorize(text, style))

    # Subjective bottleneck banner — only shown when the objective queue is
    # clear.  While objective items remain, the queue is the single authority
    # on what to work on next; no need to distract with subjective advice.
    _objective_remaining = max(
        0,
        (breakdown.queue_total - breakdown.subjective) if breakdown else queue_total,
    )
    if strict_score is not None and dim_scores and _objective_remaining <= 0:
        try:
            health_breakdown = compute_health_breakdown(dim_scores)
            subjective_drag = sum(
                float(e.get("overall_drag", 0) or 0)
                for e in health_breakdown.get("entries", [])
                if isinstance(e, dict) and e.get("component") == "subjective"
            )
            mechanical_drag = sum(
                float(e.get("overall_drag", 0) or 0)
                for e in health_breakdown.get("entries", [])
                if isinstance(e, dict) and e.get("component") != "subjective"
            )
            if subjective_drag > mechanical_drag and subjective_drag > 5.0:
                print(colorize(
                    f"\n  Subjective dimensions are the main bottleneck "
                    f"(-{subjective_drag:.0f} pts vs -{mechanical_drag:.0f} pts mechanical).",
                    "yellow",
                ))
                print(colorize(
                    "  Code fixes alone won't close the gap — run "
                    "`desloppify review --run-batches --runner codex --parallel --scan-after-import` "
                    "to re-score.",
                    "yellow",
                ))
        except (ImportError, TypeError, ValueError, KeyError) as exc:
            log(f"  subjective bottleneck banner skipped: {exc}")

    # Integrity penalty/warn lines preserved (anti-gaming safeguard, must remain visible).
    for style, message in followup.integrity_lines:
        print(colorize(f"\n  {message}", style))

    # Rescan nudge after structural work
    if queue_total > 10:
        print(colorize(
            "\n  Tip: after structural fixes (splitting files, moving code), rescan to "
            "let cascade effects settle: `desloppify scan --path .`",
            "dim",
        ))

    # Collapsed subjective summary.
    coverage_open, _coverage_reasons, _holistic_reasons = subjective_coverage_breakdown(
        findings_scoped
    )
    parts: list[str] = []
    low_dims = len(followup.low_assessed)
    unassessed_count = len(unassessed_subjective)
    stale_count = sum(1 for e in subjective_entries if e.get("stale"))
    open_review = [
        f for f in findings_scoped.values()
        if f.get("status") == "open" and f.get("detector") == "review"
    ]
    if low_dims:
        parts.append(f"{low_dims} dimension{'s' if low_dims != 1 else ''} below target")
    if stale_count:
        parts.append(f"{stale_count} stale")
    if unassessed_count:
        parts.append(f"{unassessed_count} unassessed")
    if len(open_review):
        parts.append(f"{len(open_review)} review finding{'s' if len(open_review) != 1 else ''} open")
    if coverage_open > 0:
        parts.append(f"{coverage_open} file{'s' if coverage_open != 1 else ''} need review")

    if parts:
        print(colorize(f"\n  Subjective: {', '.join(parts)}.", "cyan"))
        print(colorize("  Run `desloppify show subjective` for details.", "dim"))


__all__ = [
    "is_auto_fix_command",
    "render_followup_nudges",
    "render_gate_banner",
    "render_queue_header",
    "render_single_item_resolution_hint",
    "render_terminal_items",
    "render_uncommitted_reminder",
    "scorecard_subjective",
    "show_empty_queue",
    "subjective_coverage_breakdown",
]
