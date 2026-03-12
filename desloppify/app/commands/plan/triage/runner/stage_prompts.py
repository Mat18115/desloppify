"""Per-stage subagent prompt builders for triage runners."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.engine.plan_triage import (
    TriageInput,
    build_triage_prompt,
)

from ..services import TriageServices, default_triage_services
from .stage_prompts_instruction_blocks import _STAGE_INSTRUCTIONS
from .stage_prompts_instruction_shared import (
    _STAGES,
    PromptMode,
    render_cli_reference,
    triage_prompt_preamble,
)
from .stage_prompts_observe import (
    _observe_batch_instructions,
    build_observe_batch_prompt,
)
from .stage_prompts_sense import (
    build_sense_check_content_prompt,
    build_sense_check_structure_prompt,
)
from .stage_prompts_validation import _validation_requirements


def _required_issue_hashes(triage_input: TriageInput) -> list[str]:
    """Return sorted short hashes for open review issues."""
    return sorted(issue_id.rsplit("::", 1)[-1] for issue_id in triage_input.open_issues)


def _compact_issue_summary(triage_input: TriageInput) -> str:
    """Return a compact issue summary for later triage stages."""
    by_dim: Counter[str] = Counter()
    for issue in triage_input.open_issues.values():
        detail = issue.get("detail", {}) if isinstance(issue.get("detail"), dict) else {}
        by_dim[str(detail.get("dimension", "unknown"))] += 1
    dims = ", ".join(f"{name} ({count})" for name, count in sorted(by_dim.items()))
    parts = [
        "## Issue Summary",
        f"Open review issues: {len(triage_input.open_issues)}",
    ]
    if dims:
        parts.append(f"Open dimensions: {dims}")
    if triage_input.new_since_last:
        parts.append(f"New since last triage: {len(triage_input.new_since_last)}")
    if triage_input.resolved_since_last:
        parts.append(f"Resolved since last triage: {len(triage_input.resolved_since_last)}")
    return "\n".join(parts)


def _issue_context_for_stage(stage: str, triage_input: TriageInput) -> str:
    """Return the amount of issue context appropriate for a stage."""
    if stage in {"observe", "reflect"}:
        parts = ["## Issue Data\n\n" + build_triage_prompt(triage_input)]
        if stage == "reflect":
            short_ids = _required_issue_hashes(triage_input)
            parts.append(
                "## Required Issue Hashes\n"
                f"Total open review issues: {len(short_ids)}\n"
                "Every one of these hashes must appear exactly once in your cluster/skip blueprint.\n"
                "Do not repeat hashes outside that blueprint.\n"
                + ", ".join(short_ids)
            )
            parts.append(
                "## Coverage Ledger Template\n"
                "Your final report MUST contain a `## Coverage Ledger` section with one line per issue.\n"
                "Allowed forms:\n"
                '- `- abcd1234 -> cluster "cluster-name"`\n'
                '- `- abcd1234 -> skip "specific-reason-tag"`\n'
                "Do not mention hashes outside the `## Coverage Ledger` section.\n"
                + "\n".join(f"- {short_id} -> TODO" for short_id in short_ids)
            )
        return "\n\n".join(parts)
    return _compact_issue_summary(triage_input)


def _format_assessments_table(assessments: list[dict]) -> str:
    """Format structured observe assessments as a readable table for downstream stages."""
    if not assessments:
        return ""
    lines = ["### Structured Observe Assessments\n"]
    lines.append("| Hash | Verdict | Recommendation |")
    lines.append("|------|---------|----------------|")
    for a in assessments:
        h = a.get("hash", "?")
        v = a.get("verdict", "?")
        r = a.get("recommendation", "")
        # Truncate recommendation for table readability
        if len(r) > 80:
            r = r[:77] + "..."
        lines.append(f"| {h} | {v} | {r} |")
    return "\n".join(lines)


def _format_assessment_file_evidence(assessments: list[dict]) -> str:
    """Format file-level observe evidence for clustering-heavy downstream stages."""
    if not assessments:
        return ""
    lines = ["### Observe File Evidence\n"]
    for assessment in assessments:
        issue_hash = str(assessment.get("hash", "?"))
        files_read = [
            str(path).strip()
            for path in assessment.get("files_read", [])
            if str(path).strip()
        ]
        verdict = str(assessment.get("verdict", "?")).strip() or "?"
        reasoning = str(assessment.get("verdict_reasoning", "")).strip()
        recommendation = str(assessment.get("recommendation", "")).strip()
        lines.append(f"- {issue_hash}: {verdict}")
        if files_read:
            lines.append(f"  files_read: {', '.join(files_read)}")
        if reasoning:
            lines.append(f"  verdict_reasoning: {reasoning}")
        if recommendation:
            lines.append(f"  recommendation: {recommendation}")
    return "\n".join(lines)


def _relevant_prior_reports(
    stage: str,
    prior_reports: dict[str, str],
    stages_data: dict | None = None,
) -> list[tuple[str, str]]:
    """Return the stage reports that matter for the current stage."""
    wanted = {
        "reflect": ("observe",),
        "organize": ("reflect",),
        "enrich": ("organize",),
        "sense-check": ("organize", "enrich"),
    }.get(stage, tuple(prior_reports))
    result = [(name, prior_reports[name]) for name in wanted if name in prior_reports]

    # Append structured observe assessments for stages that need verdict data
    if stage in {"reflect", "organize"} and stages_data:
        observe_data = stages_data.get("observe", {})
        assessments = observe_data.get("assessments", [])
        if assessments:
            table = _format_assessments_table(assessments)
            if table:
                result.append(("observe-assessments", table))
        if stage == "organize":
            evidence = _format_assessment_file_evidence(assessments)
            if evidence:
                result.append(("observe-evidence", evidence))

    return result


def build_stage_prompt(
    stage: str,
    triage_input: TriageInput,
    prior_reports: dict[str, str],
    *,
    repo_root: Path,
    mode: PromptMode = "self_record",
    cli_command: str = "desloppify",
    stages_data: dict | None = None,
) -> str:
    """Build a complete subagent prompt for a triage stage."""
    parts: list[str] = []

    # Preamble
    parts.append(
        triage_prompt_preamble(mode).format(
            stage=stage.upper(),
            repo_root=repo_root,
            cli_command=cli_command,
        )
    )

    # Prior stage reports
    relevant_prior_reports = _relevant_prior_reports(stage, prior_reports, stages_data)
    if relevant_prior_reports:
        parts.append("## Prior Stage Reports\n")
        for prior_stage, report in relevant_prior_reports:
            parts.append(f"### {prior_stage.upper()} Report\n{report}\n")

    # Issue data / summary
    parts.append(_issue_context_for_stage(stage, triage_input))

    # Stage-specific instructions
    instruction_fn = _STAGE_INSTRUCTIONS.get(stage)
    if instruction_fn:
        parts.append(instruction_fn(mode))

    # CLI reference
    if mode == "self_record":
        parts.append(render_cli_reference(cli_command))

    # Validation requirements
    parts.append(_validation_requirements(stage))

    return "\n\n".join(parts)


def cmd_stage_prompt(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Print the current prompt for a triage stage, built from live plan data."""
    stage = args.stage_prompt
    resolved_services = services or default_triage_services()
    plan = resolved_services.load_plan()
    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    si = resolved_services.collect_triage_input(plan, state)
    repo_root = get_project_root()

    # Extract real prior reports from plan.json
    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})
    prior_reports: dict[str, str] = {}
    for prior_stage in _STAGES:
        if prior_stage == stage:
            break
        report = stages.get(prior_stage, {}).get("report", "")
        if report:
            prior_reports[prior_stage] = report

    prompt = build_stage_prompt(stage, si, prior_reports, repo_root=repo_root, stages_data=stages)
    print(prompt)


__all__ = [
    "build_observe_batch_prompt",
    "build_sense_check_content_prompt",
    "build_sense_check_structure_prompt",
    "build_stage_prompt",
    "cmd_stage_prompt",
    "_observe_batch_instructions",
    "_validation_requirements",
]
