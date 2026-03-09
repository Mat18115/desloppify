"""Direct coverage tests for triage display helpers."""

from __future__ import annotations

import desloppify.app.commands.plan.triage.display as display_mod
import desloppify.app.commands.plan.triage.display_primitives as primitives_mod


def test_print_stage_progress_shows_enrichment_gap(monkeypatch, capsys) -> None:
    monkeypatch.setattr(primitives_mod, "colorize", lambda text, _style: text)
    monkeypatch.setattr(primitives_mod, "unenriched_clusters", lambda _plan: [("cluster-a", ["steps"])])
    monkeypatch.setattr(primitives_mod, "manual_clusters_with_issues", lambda _plan: ["cluster-a"])

    display_mod.print_stage_progress({"reflect": {}}, plan={"clusters": {}})

    out = capsys.readouterr().out
    assert "cluster(s) need enrichment" in out
    assert "cluster-a" in out


def test_print_progress_reports_unclustered_issues(monkeypatch, capsys) -> None:
    monkeypatch.setattr(display_mod, "colorize", lambda text, _style: text)
    monkeypatch.setattr(display_mod, "short_issue_id", lambda fid: fid.split("::")[-1])

    plan = {
        "clusters": {
            "cluster-a": {
                "issue_ids": ["review::aaa111"],
                "description": "desc",
                "action_steps": ["step 1"],
                "auto": False,
            }
        }
    }
    open_issues = {
        "review::aaa111": {"summary": "clustered", "detail": {"dimension": "design"}},
        "review::bbb222": {"summary": "unclustered", "detail": {"dimension": "tests"}},
    }

    display_mod.print_progress(plan, open_issues)

    out = capsys.readouterr().out
    assert "1 issues not yet in a cluster" in out
    assert "[bbb222] [tests] unclustered" in out
    assert "Current clusters:" in out
