"""Recovery tests for saved plan metadata when scan state is missing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import desloppify.app.commands.plan.queue_render as queue_render_mod
import desloppify.app.commands.plan.triage.workflow as workflow_mod
from desloppify.app.commands.helpers.runtime import CommandRuntime
from desloppify.engine._state.persistence import load_state


def test_load_state_recovers_runtime_state_from_saved_plan(tmp_path: Path) -> None:
    """Missing state file should recover current review issues from sibling plan.json."""
    plan = {
        "queue_order": ["review::src/foo.ts::abcd1234"],
        "clusters": {
            "cluster-a": {
                "issue_ids": [],
                "action_steps": [
                    {"title": "Fix", "issue_refs": ["review::src/foo.ts::abcd1234"]},
                ],
                "description": "Recovered cluster",
                "auto": False,
            }
        },
        "epic_triage_meta": {"triage_stages": {"observe": {"report": "done"}}},
        "skipped": {},
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan))

    state = load_state(tmp_path / "state-typescript.json")

    assert "review::src/foo.ts::abcd1234" in state["issues"]
    assert state["_saved_plan_recovery"]["mode"] == "queue_only"


def test_cmd_plan_queue_uses_recovered_runtime_state(monkeypatch, capsys) -> None:
    """Queue rendering should continue when runtime already carries recovered state."""
    captured_states: list[dict] = []
    plan = {
        "queue_order": ["review::src/foo.ts::abcd1234"],
        "clusters": {},
        "epic_triage_meta": {"triage_stages": {"observe": {"report": "done"}}},
        "skipped": {},
    }
    recovered_state = {
        "issues": {
            "review::src/foo.ts::abcd1234": {
                "id": "review::src/foo.ts::abcd1234",
                "status": "open",
                "detector": "review",
                "file": "src/foo.ts",
                "summary": "review::src/foo.ts::abcd1234",
                "confidence": "medium",
                "tier": 2,
                "detail": {"dimension": "unknown", "recovered_from_plan": True},
            }
        },
        "last_scan": None,
        "_saved_plan_recovery": {"active": True, "mode": "queue_only"},
    }

    monkeypatch.setattr(
        queue_render_mod,
        "command_runtime",
        lambda _args: CommandRuntime(config={}, state=recovered_state, state_path=None),
    )
    monkeypatch.setattr(queue_render_mod, "load_plan", lambda: plan)
    monkeypatch.setattr(queue_render_mod, "print_triage_guardrail_info", lambda **_kw: None)

    def _fake_build_execution_queue(state, *, options=None):
        del options
        captured_states.append(state)
        return {"items": [], "total": 0, "grouped": {}, "new_ids": set()}

    monkeypatch.setattr(queue_render_mod, "build_execution_queue", _fake_build_execution_queue)

    args = argparse.Namespace(top=30, cluster=None, include_skipped=False, sort="priority")
    queue_render_mod.cmd_plan_queue(args)

    out = capsys.readouterr().out
    assert "continuing from saved plan metadata only" in out
    assert captured_states
    assert "review::src/foo.ts::abcd1234" in captured_states[0]["issues"]


def test_run_triage_workflow_uses_recovered_runtime_state(monkeypatch, capsys) -> None:
    """Triage workflow should proceed when runtime already carries recovered state."""
    calls: list[dict] = []
    scan_gate_calls: list[dict] = []
    recovered_state = {
        "issues": {
            "review::src/foo.ts::abcd1234": {
                "id": "review::src/foo.ts::abcd1234",
                "status": "open",
                "detector": "review",
                "file": "src/foo.ts",
                "summary": "review::src/foo.ts::abcd1234",
                "confidence": "medium",
                "tier": 2,
                "detail": {"dimension": "unknown", "recovered_from_plan": True},
            }
        },
        "last_scan": None,
        "_saved_plan_recovery": {"active": True, "mode": "queue_only"},
    }

    class _Services:
        @staticmethod
        def command_runtime(_args):
            return CommandRuntime(
                config={},
                state=recovered_state,
                state_path=None,
            )

    monkeypatch.setattr(
        workflow_mod._display_mod,
        "cmd_triage_dashboard",
        lambda args, services=None: calls.append(services.command_runtime(args).state),
    )

    workflow_mod.run_triage_workflow(
        argparse.Namespace(
            stage_prompt=None,
            run_stages=False,
            start=False,
            confirm=None,
            complete=False,
            confirm_existing=False,
            stage=None,
            dry_run=False,
        ),
        services=_Services(),
        require_completed_scan_fn=lambda state: scan_gate_calls.append(state) or True,
    )

    assert scan_gate_calls
    assert scan_gate_calls[0] is recovered_state
    assert calls
    assert "review::src/foo.ts::abcd1234" in calls[0]["issues"]
