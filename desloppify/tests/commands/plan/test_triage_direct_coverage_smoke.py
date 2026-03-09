"""Direct-coverage smoke tests for triage helper modules."""

from __future__ import annotations

import inspect

import desloppify.app.commands.plan.triage.helpers as triage_helpers_mod
import desloppify.app.commands.plan.triage.services as triage_services_mod
import desloppify.app.commands.plan.triage.stage_completion_commands as triage_completion_mod
import desloppify.app.commands.plan.triage.runner.codex_runner as triage_codex_runner_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_common as triage_orchestrator_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_codex_observe as triage_observe_mod
import desloppify.app.commands.plan.triage.runner.orchestrator_codex_parallel as triage_parallel_mod
import desloppify.app.commands.plan.triage.display as triage_display_mod
import desloppify.app.commands.plan.triage.display_layout as triage_display_layout_mod


def test_triage_helper_modules_direct_coverage_smoke() -> None:
    assert callable(triage_helpers_mod.has_triage_in_queue)
    assert callable(triage_helpers_mod.triage_coverage)
    assert callable(triage_helpers_mod.group_issues_into_observe_batches)

    services = triage_services_mod.default_triage_services()
    assert isinstance(services, triage_services_mod.TriageServices)
    assert callable(services.load_plan)
    assert callable(services.save_plan)

    assert callable(triage_completion_mod.cmd_triage_complete)
    assert callable(triage_completion_mod.cmd_confirm_existing)

    assert callable(triage_codex_runner_mod.run_triage_stage)
    assert callable(triage_codex_runner_mod._output_file_has_text)

    assert callable(triage_orchestrator_mod.parse_only_stages)
    assert triage_orchestrator_mod.parse_only_stages("observe,reflect") == [
        "observe",
        "reflect",
    ]

    codex_src = inspect.getsource(triage_codex_runner_mod)
    observe_src = inspect.getsource(triage_observe_mod)
    parallel_src = inspect.getsource(triage_parallel_mod)
    assert "app.commands.review._runner_process_types" not in codex_src
    assert "app.commands.review._runner_parallel_types" not in observe_src
    assert "app.commands.review._runner_parallel_types" not in parallel_src

    display_src = inspect.getsource(triage_display_mod)
    display_layout_src = inspect.getsource(triage_display_layout_mod)
    assert "from . import display as display_mod" not in display_layout_src
    assert "from .display_primitives import print_stage_progress" in display_src
