"""Direct coverage tests for split triage validation/orchestrator modules."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import desloppify.app.commands.plan.triage._stage_validation_completion_policy as completion_policy_mod
import desloppify.app.commands.plan.triage._stage_validation_completion_stages as completion_stages_mod
import desloppify.app.commands.plan.triage._stage_validation_enrich_checks as enrich_checks_mod
import desloppify.app.commands.plan.triage.confirmations_basic as confirmations_basic_mod
import desloppify.app.commands.plan.triage.confirmations_enrich as confirmations_enrich_mod
import desloppify.app.commands.plan.triage.confirmations_organize as confirmations_organize_mod
import desloppify.app.commands.plan.triage.display as triage_display_mod
import desloppify.app.commands.plan.triage.display_layout as display_layout_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_claude as orchestrator_claude_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_codex_observe as orchestrator_observe_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_codex_pipeline as orchestrator_pipeline_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_codex_sense as orchestrator_sense_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_common as orchestrator_common_mod


def test_completion_policy_helpers_cover_success_and_fail_paths(monkeypatch, capsys) -> None:
    monkeypatch.setattr(completion_policy_mod, "manual_clusters_with_issues", lambda _plan: ["c1"])
    monkeypatch.setattr(completion_policy_mod, "unenriched_clusters", lambda _plan: [])
    monkeypatch.setattr(completion_policy_mod, "unclustered_review_issues", lambda _plan, _state: [])
    assert completion_policy_mod._completion_clusters_valid({"clusters": {}}, state={}) is True

    assert completion_policy_mod._resolve_completion_strategy("keep", meta={}) == "keep"
    assert completion_policy_mod._resolve_completion_strategy(None, meta={}) is None
    assert completion_policy_mod._completion_strategy_valid("same") is True
    assert completion_policy_mod._completion_strategy_valid("x" * 220) is True
    assert completion_policy_mod._completion_strategy_valid("too short") is False

    assert completion_policy_mod._require_prior_strategy_for_confirm({"strategy_summary": "ok"}) is True
    assert completion_policy_mod._require_prior_strategy_for_confirm({}) is False
    assert completion_policy_mod._confirm_note_valid("x" * 100) is True
    assert completion_policy_mod._confirm_note_valid("short") is False

    assert (
        completion_policy_mod._resolve_confirm_existing_strategy(
            "same",
            has_only_additions=False,
            meta={},
        )
        == "same"
    )
    assert (
        completion_policy_mod._resolve_confirm_existing_strategy(
            None,
            has_only_additions=True,
            meta={},
        )
        == "same"
    )
    assert completion_policy_mod._confirm_strategy_valid("x" * 220) is True
    assert completion_policy_mod._confirm_strategy_valid("short") is False

    monkeypatch.setattr(
        completion_policy_mod,
        "extract_issue_citations",
        lambda _note, valid_ids: set(valid_ids),
    )
    si = SimpleNamespace(
        new_since_last={"review::a.py::id1"},
        open_issues={"review::a.py::id1": {}},
    )
    assert completion_policy_mod._note_cites_new_issues_or_error("review::a.py::id1", si) is True
    monkeypatch.setattr(completion_policy_mod, "extract_issue_citations", lambda _note, _ids: set())
    assert completion_policy_mod._note_cites_new_issues_or_error("no citation", si) is False

    out = capsys.readouterr().out
    assert "Strategy too short" in out


def test_completion_stage_helpers_include_gate_and_auto_confirm_defaults(monkeypatch, capsys) -> None:
    plan = {"clusters": {"a": {"issue_ids": ["id1"], "action_steps": []}}}

    assert (
        completion_stages_mod._require_organize_stage_for_complete(
            plan=plan,
            meta={},
            stages={},
        )
        is False
    )
    assert (
        completion_stages_mod._require_enrich_stage_for_complete(
            plan=plan,
            meta={},
            stages={"organize": {}},
        )
        is False
    )
    assert (
        completion_stages_mod._require_sense_check_stage_for_complete(
            plan=plan,
            meta={},
            stages={"enrich": {}},
        )
        is False
    )

    assert (
        completion_stages_mod._auto_confirm_organize_for_complete(
            plan=plan,
            stages={},
            attestation=None,
        )
        is False
    )
    assert (
        completion_stages_mod._auto_confirm_enrich_for_complete(
            plan=plan,
            stages={},
            attestation=None,
        )
        is False
    )
    assert (
        completion_stages_mod._auto_confirm_sense_check_for_complete(
            plan=plan,
            stages={},
            attestation=None,
        )
        is False
    )

    out = capsys.readouterr().out
    assert "Cannot complete" in out


def test_enrich_checks_helpers_cover_main_signals(tmp_path, capsys) -> None:
    plan = {
        "clusters": {
            "manual": {
                "issue_ids": ["i1", "i2", "i3"],
                "action_steps": [
                    {"title": "Fix A", "detail": "short", "issue_refs": []},
                    {
                        "title": "Fix B",
                        "detail": "edit src/missing/file.ts and update behavior",
                        "issue_refs": ["review::a.py::1"],
                    },
                    {
                        "title": "Fix C",
                        "detail": (
                            "update src/a/file.ts and src/b/file.ts and src/c/file.ts and "
                            "src/d/file.ts and src/e/file.ts and src/f/file.ts"
                        ),
                        "issue_refs": ["review::a.py::2"],
                        "effort": "small",
                    },
                ],
            }
        },
        "issues": {"review::a.py::2": {"status": "wontfix"}},
    }

    assert enrich_checks_mod._require_organize_stage_for_enrich({"observe": {}, "reflect": {}}) is False
    assert enrich_checks_mod._underspecified_steps(plan) == [("manual", 1, 3)]
    assert enrich_checks_mod._steps_without_effort(plan) == [("manual", 2, 3)]
    assert enrich_checks_mod._steps_missing_issue_refs(plan) == [("manual", 1, 3)]
    assert enrich_checks_mod._clusters_with_high_step_ratio(plan) == []

    bad_paths = enrich_checks_mod._steps_with_bad_paths(plan, tmp_path)
    assert bad_paths
    vague = enrich_checks_mod._steps_with_vague_detail(plan, tmp_path)
    assert vague
    stale_refs = enrich_checks_mod._steps_referencing_skipped_issues(plan)
    assert stale_refs == [("manual", 3, ["review::a.py::2"])]

    assert enrich_checks_mod._enrich_report_or_error("x" * 120) == "x" * 120
    assert enrich_checks_mod._enrich_report_or_error("short") is None

    out = capsys.readouterr().out
    assert "Report too short" in out


def test_confirmation_modules_stage_presence_guards(capsys) -> None:
    args = argparse.Namespace()
    confirmations_basic_mod.confirm_observe(args, {}, {}, None)
    confirmations_basic_mod.confirm_reflect(args, {}, {}, None)
    confirmations_enrich_mod.confirm_enrich(args, {}, {}, None)
    confirmations_enrich_mod.confirm_sense_check(args, {}, {}, None)
    confirmations_organize_mod.confirm_organize(args, {}, {}, None)
    out = capsys.readouterr().out
    assert "Cannot confirm" in out


def test_validate_attestation_rules() -> None:
    assert confirmations_basic_mod.validate_attestation("mentions naming", "observe", dimensions=["Naming"]) is None
    err = confirmations_basic_mod.validate_attestation(
        "generic text",
        "reflect",
        dimensions=["Naming"],
        cluster_names=["cluster-a"],
    )
    assert err is not None


def test_display_layout_renderers(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        triage_display_mod,
        "print_stage_progress",
        lambda _stages, _plan: print("stage-progress"),
    )

    si = SimpleNamespace(
        open_issues={
            "review::src/a.py::id1": {
                "summary": "Issue one",
                "detail": {"dimension": "naming", "suggestion": "rename value"},
            }
        },
        existing_epics=[],
        new_since_last={"review::src/a.py::id1"},
        resolved_since_last=set(),
    )
    stages = {"observe": {"report": "observe report"}, "reflect": {"report": "reflect report"}}
    plan = {
        "clusters": {
            "manual": {
                "issue_ids": ["review::src/a.py::id1"],
                "action_steps": [{"title": "Do thing"}],
                "description": "manual cluster",
            }
        },
        "queue_order": ["review::src/a.py::id1"],
    }
    state = {"issues": si.open_issues}

    display_layout_mod.print_dashboard_header(si, stages, {}, plan)
    display_layout_mod.print_action_guidance(stages, {}, si, plan)
    display_layout_mod.print_prior_stage_reports(stages)
    display_layout_mod.print_issues_by_dimension(si.open_issues)
    display_layout_mod.show_plan_summary(plan, state)

    out = capsys.readouterr().out
    assert "Epic triage" in out
    assert "stage-progress" in out
    assert "Review issues by dimension" in out
    assert "Coverage:" in out


def test_orchestrator_common_helpers(monkeypatch) -> None:
    assert orchestrator_common_mod.parse_only_stages(None) == list(orchestrator_common_mod.STAGES)
    assert orchestrator_common_mod.parse_only_stages("observe,reflect") == ["observe", "reflect"]
    with pytest.raises(ValueError):
        orchestrator_common_mod.parse_only_stages("invalid")

    stamp = orchestrator_common_mod.run_stamp()
    assert len(stamp) == 15

    saved: list[dict] = []
    monkeypatch.setattr(orchestrator_common_mod, "has_triage_in_queue", lambda _plan: False)
    monkeypatch.setattr(
        orchestrator_common_mod,
        "inject_triage_stages",
        lambda plan: plan.setdefault("queue_order", []).append("triage::observe"),
    )
    plan = {"queue_order": []}
    services = SimpleNamespace(save_plan=lambda p: saved.append(p))
    updated = orchestrator_common_mod.ensure_triage_started(plan, services)
    assert "triage::observe" in updated["queue_order"]
    assert saved


def test_orchestrator_claude_prints_instructions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(orchestrator_claude_mod, "ensure_triage_started", lambda _plan, _svc: None)
    services = SimpleNamespace(load_plan=lambda: {}, save_plan=lambda _plan: None)
    orchestrator_claude_mod.run_claude_orchestrator(argparse.Namespace(), services=services)
    out = capsys.readouterr().out
    assert "Claude triage orchestrator mode" in out


def test_orchestrator_observe_helpers_and_dry_run(monkeypatch, tmp_path, capsys) -> None:
    output_file = tmp_path / "observe.txt"
    output_file.write_text("batch output", encoding="utf-8")
    merged = orchestrator_observe_mod._merge_observe_outputs([(["naming"], output_file)])
    assert "Dimensions: naming" in merged

    monkeypatch.setattr(
        orchestrator_observe_mod,
        "group_issues_into_observe_batches",
        lambda _si: [(["naming"], [{"id": "1"}])],
    )
    monkeypatch.setattr(
        orchestrator_observe_mod,
        "build_observe_batch_prompt",
        lambda **_kwargs: "prompt",
    )

    ok, report = orchestrator_observe_mod.run_observe(
        si=SimpleNamespace(),
        repo_root=tmp_path,
        prompts_dir=tmp_path / "prompts",
        output_dir=tmp_path / "out",
        logs_dir=tmp_path / "logs",
        timeout_seconds=60,
        dry_run=True,
    )
    assert ok is True
    assert report == ""
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_orchestrator_sense_dry_run(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(orchestrator_sense_mod, "manual_clusters_with_issues", lambda _plan: ["cluster-a"])
    monkeypatch.setattr(
        orchestrator_sense_mod,
        "build_sense_check_content_prompt",
        lambda **_kwargs: "content prompt",
    )
    monkeypatch.setattr(
        orchestrator_sense_mod,
        "build_sense_check_structure_prompt",
        lambda **_kwargs: "structure prompt",
    )

    ok, report = orchestrator_sense_mod.run_sense_check(
        plan={"clusters": {"cluster-a": {"issue_ids": ["id1"]}}},
        repo_root=tmp_path,
        prompts_dir=tmp_path / "prompts",
        output_dir=tmp_path / "out",
        logs_dir=tmp_path / "logs",
        timeout_seconds=60,
        dry_run=True,
    )
    assert ok is True
    assert report == ""
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_orchestrator_pipeline_summary_writer(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    messages: list[str] = []

    orchestrator_pipeline_mod.write_triage_run_summary(
        run_dir,
        stamp="20260309_120000",
        stages=["observe"],
        stage_results={"observe": {"status": "confirmed"}},
        append_run_log=messages.append,
    )

    summary_path = run_dir / "run_summary.json"
    assert summary_path.exists()
    text = summary_path.read_text(encoding="utf-8")
    assert '"runner": "codex"' in text
    assert messages


def test_orchestrator_pipeline_entrypoint_is_exposed() -> None:
    assert callable(orchestrator_pipeline_mod.run_codex_pipeline)
