"""Codex pipeline orchestration for triage stages."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from desloppify.app.commands.review.batches_runtime import make_run_log_writer
from desloppify.base.discovery.file_paths import safe_write_text
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.output.terminal import colorize

from ..services import TriageServices, default_triage_services
from .orchestrator_codex_observe import run_observe
from .orchestrator_codex_sense import run_sense_check
from .orchestrator_common import STAGES, ensure_triage_started, run_stamp
from .stage_prompts import build_stage_prompt
from .stage_validation import build_auto_attestation, validate_stage


def _is_full_stage_run(stages_to_run: list[str]) -> bool:
    """True when the pipeline was asked to run the full triage stage set."""
    return set(stages_to_run) == set(STAGES)


def _all_stage_results_successful(
    *,
    stages_to_run: list[str],
    stage_results: dict[str, dict],
) -> bool:
    """True when each requested stage is confirmed or already confirmed."""
    for stage in stages_to_run:
        status = str(stage_results.get(stage, {}).get("status", ""))
        if status not in {"confirmed", "skipped"}:
            return False
    return True


def _print_not_finalized_message(reason: str) -> None:
    """Emit a consistent next-step message when auto-completion is skipped/blocked."""
    print(colorize(f"\n  Stages complete, triage not finalized ({reason}).", "yellow"))
    print(
        colorize(
            '  Finalize manually: desloppify plan triage --complete --strategy "<execution plan>"',
            "dim",
        )
    )


def run_codex_pipeline(
    args: argparse.Namespace,
    *,
    stages_to_run: list[str],
    services: TriageServices | None = None,
) -> None:
    """Run triage stages via Codex subprocesses (automated pipeline)."""
    from .codex_runner import run_triage_stage

    resolved_services = services or default_triage_services()
    timeout_seconds = int(getattr(args, "stage_timeout_seconds", 1800) or 1800)
    dry_run = bool(getattr(args, "dry_run", False))

    repo_root = get_project_root()
    plan = resolved_services.load_plan()
    ensure_triage_started(plan, resolved_services)

    stamp = run_stamp()
    desloppify_dir = repo_root / ".desloppify"
    run_dir = desloppify_dir / "triage_runs" / stamp
    prompts_dir = run_dir / "prompts"
    output_dir = run_dir / "output"
    logs_dir = run_dir / "logs"
    for d in (prompts_dir, output_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    run_log_path = run_dir / "run.log"
    append_run_log = make_run_log_writer(run_log_path)
    append_run_log(
        f"run-start runner=codex stages={','.join(stages_to_run)} "
        f"timeout={timeout_seconds}s dry_run={dry_run}"
    )

    print(colorize(f"  Run artifacts: {run_dir}", "dim"))
    print(colorize(f"  Live run log:  {run_log_path}", "dim"))

    runtime = resolved_services.command_runtime(args)
    state = runtime.state

    prior_reports: dict[str, str] = {}
    stage_results: dict[str, dict] = {}
    pipeline_start = time.monotonic()

    for stage in stages_to_run:
        plan = resolved_services.load_plan()
        meta = plan.get("epic_triage_meta", {})
        stages = meta.get("triage_stages", {})

        if stage in stages and stages[stage].get("confirmed_at"):
            print(colorize(f"  Stage {stage}: already confirmed, skipping.", "green"))
            append_run_log(f"stage-skip stage={stage} reason=already_confirmed")
            stage_results[stage] = {"status": "skipped"}
            report = stages[stage].get("report", "")
            if report:
                prior_reports[stage] = report
            continue

        stage_start = time.monotonic()
        append_run_log(f"stage-start stage={stage}")

        si = resolved_services.collect_triage_input(plan, state)

        used_parallel = False
        if stage == "observe":
            parallel_ok, merged_report = run_observe(
                si=si,
                repo_root=repo_root,
                prompts_dir=prompts_dir,
                output_dir=output_dir,
                logs_dir=logs_dir,
                timeout_seconds=timeout_seconds,
                dry_run=dry_run,
                append_run_log=append_run_log,
            )
            if parallel_ok is True and dry_run:
                stage_results[stage] = {"status": "dry_run"}
                continue
            if parallel_ok is True and merged_report:
                from ..stage_flow_commands import cmd_stage_observe

                record_args = argparse.Namespace(
                    stage="observe",
                    report=merged_report,
                    state=getattr(args, "state", None),
                )
                cmd_stage_observe(record_args, services=resolved_services)
                used_parallel = True
            elif parallel_ok is False:
                elapsed = int(time.monotonic() - stage_start)
                print(colorize("  Observe: parallel execution failed. Aborting.", "red"))
                append_run_log(f"stage-failed stage=observe elapsed={elapsed}s reason=parallel_execution_failed")
                stage_results[stage] = {"status": "failed", "elapsed_seconds": elapsed}
                write_triage_run_summary(run_dir, stamp, stages_to_run, stage_results, append_run_log)
                return

        if stage == "sense-check":
            parallel_ok, merged_report = run_sense_check(
                plan=plan,
                repo_root=repo_root,
                prompts_dir=prompts_dir,
                output_dir=output_dir,
                logs_dir=logs_dir,
                timeout_seconds=timeout_seconds,
                dry_run=dry_run,
                append_run_log=append_run_log,
            )
            if parallel_ok is True and dry_run:
                stage_results[stage] = {"status": "dry_run"}
                continue
            if parallel_ok is True and merged_report:
                plan = resolved_services.load_plan()
                from ..stage_flow_commands import cmd_stage_sense_check

                record_args = argparse.Namespace(
                    stage="sense-check",
                    report=merged_report,
                    state=getattr(args, "state", None),
                )
                cmd_stage_sense_check(record_args, services=resolved_services)
                used_parallel = True
            elif parallel_ok is False:
                elapsed = int(time.monotonic() - stage_start)
                print(colorize("  Sense-check: parallel execution failed. Aborting.", "red"))
                append_run_log(f"stage-failed stage=sense-check elapsed={elapsed}s reason=parallel_execution_failed")
                stage_results[stage] = {"status": "failed", "elapsed_seconds": elapsed}
                write_triage_run_summary(run_dir, stamp, stages_to_run, stage_results, append_run_log)
                return

        if not used_parallel:
            prompt = build_stage_prompt(stage, si, prior_reports, repo_root=repo_root)

            prompt_file = prompts_dir / f"{stage}.md"
            safe_write_text(prompt_file, prompt)

            if dry_run:
                print(colorize(f"  Stage {stage}: prompt written to {prompt_file}", "cyan"))
                print(colorize("  [dry-run] Would execute codex subprocess.", "dim"))
                stage_results[stage] = {"status": "dry_run"}
                continue

            print(colorize(f"\n  Stage {stage}: launching codex subprocess...", "bold"))
            append_run_log(f"stage-subprocess-start stage={stage}")

            output_file = output_dir / f"{stage}.raw.txt"
            log_file = logs_dir / f"{stage}.log"

            exit_code = run_triage_stage(
                prompt=prompt,
                repo_root=repo_root,
                output_file=output_file,
                log_file=log_file,
                timeout_seconds=timeout_seconds,
            )

            elapsed = int(time.monotonic() - stage_start)
            append_run_log(f"stage-subprocess-done stage={stage} code={exit_code} elapsed={elapsed}s")

            if exit_code != 0:
                print(colorize(f"  Stage {stage}: codex subprocess failed (exit {exit_code}).", "red"))
                print(colorize(f"  Check log: {log_file}", "dim"))
                print(colorize("  Re-run to resume (confirmed stages are skipped).", "dim"))
                append_run_log(f"stage-failed stage={stage} elapsed={elapsed}s code={exit_code}")
                stage_results[stage] = {"status": "failed", "exit_code": exit_code, "elapsed_seconds": elapsed}
                write_triage_run_summary(run_dir, stamp, stages_to_run, stage_results, append_run_log)
                return

        plan = resolved_services.load_plan()

        ok, error_msg = validate_stage(stage, plan, state, repo_root, triage_input=si)
        if not ok:
            elapsed = int(time.monotonic() - stage_start)
            print(colorize(f"  Stage {stage}: validation failed: {error_msg}", "red"))
            print(colorize("  Re-run to resume.", "dim"))
            append_run_log(f"stage-validation-failed stage={stage} elapsed={elapsed}s error={error_msg}")
            stage_results[stage] = {"status": "validation_failed", "elapsed_seconds": elapsed, "error": error_msg}
            write_triage_run_summary(run_dir, stamp, stages_to_run, stage_results, append_run_log)
            return

        attestation = build_auto_attestation(stage, plan, si)

        confirm_args = argparse.Namespace(
            confirm=stage,
            attestation=attestation,
            state=getattr(args, "state", None),
        )

        from ..confirmations import _cmd_confirm_stage

        _cmd_confirm_stage(confirm_args, services=resolved_services)

        plan = resolved_services.load_plan()
        meta = plan.get("epic_triage_meta", {})
        stages_data = meta.get("triage_stages", {})
        elapsed = int(time.monotonic() - stage_start)
        if stage in stages_data and stages_data[stage].get("confirmed_at"):
            print(colorize(f"  Stage {stage}: confirmed ({elapsed}s).", "green"))
            append_run_log(f"stage-confirmed stage={stage} elapsed={elapsed}s")
            stage_results[stage] = {"status": "confirmed", "elapsed_seconds": elapsed}
        else:
            print(colorize(f"  Stage {stage}: auto-confirmation did not take effect.", "red"))
            print(colorize("  Re-run to resume.", "dim"))
            append_run_log(f"stage-confirm-failed stage={stage} elapsed={elapsed}s")
            stage_results[stage] = {"status": "confirm_failed", "elapsed_seconds": elapsed}
            write_triage_run_summary(run_dir, stamp, stages_to_run, stage_results, append_run_log)
            return

        report = stages_data.get(stage, {}).get("report", "")
        if report:
            prior_reports[stage] = report

    if dry_run:
        print(colorize("\n  [dry-run] All prompts generated. No stages executed.", "cyan"))
        write_triage_run_summary(run_dir, stamp, stages_to_run, stage_results, append_run_log)
        return

    plan = resolved_services.load_plan()
    meta = plan.get("epic_triage_meta", {})
    stages_data = meta.get("triage_stages", {})

    strategy_parts: list[str] = []
    for stage in STAGES:
        report = stages_data.get(stage, {}).get("report", "")
        if report:
            strategy_parts.append(f"[{stage}] {report[:200]}")
    strategy = " ".join(strategy_parts)
    if len(strategy) < 200:
        strategy = strategy + " " + "Automated triage via codex subagent pipeline. " * 3

    should_auto_complete = (
        _is_full_stage_run(stages_to_run)
        and _all_stage_results_successful(
            stages_to_run=stages_to_run,
            stage_results=stage_results,
        )
    )
    if not should_auto_complete:
        total_elapsed = int(time.monotonic() - pipeline_start)
        _print_not_finalized_message("partial stage run")
        append_run_log(f"run-finished elapsed={total_elapsed}s finalized=false reason=partial_stage_run")
        write_triage_run_summary(
            run_dir,
            stamp,
            stages_to_run,
            stage_results,
            append_run_log,
            finalized=False,
            finalization_reason="partial_stage_run",
        )
        return

    completed_before = meta.get("last_completed_at")
    print(colorize("\n  Completing triage...", "bold"))

    attestation = build_auto_attestation("sense-check", plan, si)
    complete_args = argparse.Namespace(
        complete=True,
        strategy=strategy[:2000],
        attestation=attestation,
        state=getattr(args, "state", None),
    )

    from ..stage_completion_commands import _cmd_triage_complete

    _cmd_triage_complete(complete_args, services=resolved_services)

    completed_after = (
        resolved_services.load_plan()
        .get("epic_triage_meta", {})
        .get("last_completed_at")
    )
    total_elapsed = int(time.monotonic() - pipeline_start)
    if not completed_after or completed_after == completed_before:
        _print_not_finalized_message("completion command blocked")
        append_run_log(
            f"run-finished elapsed={total_elapsed}s finalized=false reason=completion_blocked"
        )
        write_triage_run_summary(
            run_dir,
            stamp,
            stages_to_run,
            stage_results,
            append_run_log,
            finalized=False,
            finalization_reason="completion_blocked",
        )
        return

    print(colorize(f"\n  Triage pipeline complete ({total_elapsed}s).", "green"))
    append_run_log(f"run-finished elapsed={total_elapsed}s finalized=true")
    write_triage_run_summary(
        run_dir,
        stamp,
        stages_to_run,
        stage_results,
        append_run_log,
        finalized=True,
    )


def write_triage_run_summary(
    run_dir: Path,
    stamp: str,
    stages: list[str],
    stage_results: dict[str, dict],
    append_run_log,
    *,
    finalized: bool | None = None,
    finalization_reason: str | None = None,
) -> None:
    """Write a run_summary.json with per-stage results."""
    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_stamp": stamp,
        "runner": "codex",
        "stages_requested": stages,
        "stage_results": stage_results,
        "run_dir": str(run_dir),
    }
    if finalized is not None:
        summary["finalized"] = finalized
    if finalization_reason:
        summary["finalization_reason"] = finalization_reason
    summary_path = run_dir / "run_summary.json"
    safe_write_text(summary_path, json.dumps(summary, indent=2) + "\n")
    print(colorize(f"  Run summary: {summary_path}", "dim"))
    append_run_log(f"run-summary {summary_path}")


__all__ = ["run_codex_pipeline"]
