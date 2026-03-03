"""CLI parser definition for the ``review`` subcommand."""

from __future__ import annotations

import argparse


def _add_review_parser(
    sub,
    *,
    deprecated_action_cls: type[argparse.Action],
    deprecated_bool_cls: type[argparse.Action],
) -> None:
    p_review = sub.add_parser(
        "review",
        help="Prepare or import holistic subjective review",
        description="Run holistic subjective reviews using LLM-based analysis.",
        epilog="""\
examples:
  desloppify review --prepare
  desloppify review --run-batches --runner codex --parallel --scan-after-import
  desloppify review --external-start --external-runner claude
  desloppify review --external-submit --session-id <id> --import findings.json
  desloppify review --merge --similarity 0.8""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # -- core options --
    g_core = p_review.add_argument_group("core options")
    g_core.add_argument("--path", type=str, default=None, help="Project root directory (default: auto-detected)")
    g_core.add_argument("--state", type=str, default=None, help="Path to state file")
    g_core.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare review data (output to query.json)",
    )
    g_core.add_argument(
        "--import",
        dest="import_file",
        type=str,
        metavar="FILE",
        help="Import review findings from JSON file",
    )
    g_core.add_argument(
        "--validate-import",
        dest="validate_import_file",
        type=str,
        metavar="FILE",
        help="Validate review import payload and selected trust mode without mutating state",
    )
    g_core.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Allow partial review import when invalid findings are skipped "
            "(default: fail on any skipped finding)"
        ),
    )
    g_core.add_argument(
        "--dimensions",
        type=str,
        default=None,
        help="Comma-separated dimensions to evaluate",
    )
    g_core.add_argument(
        "--retrospective",
        action="store_true",
        help=(
            "Include historical review issue status/note context in the packet "
            "to support root-cause vs symptom analysis during review"
        ),
    )
    g_core.add_argument(
        "--retrospective-max-issues",
        type=int,
        default=30,
        help="Max recent historical issues to include in review context (default: 30)",
    )
    g_core.add_argument(
        "--retrospective-max-batch-items",
        type=int,
        default=20,
        help="Max history items included per batch focus slice (default: 20)",
    )
    g_core.add_argument(
        "--force-review-rerun",
        action="store_true",
        help="Bypass the objective-plan-drained gate for review reruns",
    )

    # -- external review --
    g_external = p_review.add_argument_group("external review")
    g_external.add_argument(
        "--external-start",
        action="store_true",
        help=(
            "Start a cloud external review session (generates blind packet, "
            "session id/token, and reviewer template)"
        ),
    )
    g_external.add_argument(
        "--external-submit",
        action="store_true",
        help=(
            "Submit external reviewer JSON via a started session; "
            "CLI injects canonical provenance before import"
        ),
    )
    g_external.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="External review session id for --external-submit",
    )
    g_external.add_argument(
        "--external-runner",
        choices=["claude"],
        default="claude",
        help="External reviewer runner for --external-start (default: claude)",
    )
    g_external.add_argument(
        "--session-ttl-hours",
        type=int,
        default=24,
        help="External review session expiration in hours (default: 24)",
    )

    # -- batch execution --
    g_batch = p_review.add_argument_group("batch execution")
    g_batch.add_argument(
        "--run-batches",
        action="store_true",
        help="Run holistic investigation batches with subagents and merge/import output",
    )
    g_batch.add_argument(
        "--runner",
        choices=["codex"],
        default="codex",
        help="Subagent runner backend (default: codex)",
    )
    g_batch.add_argument(
        "--parallel", action="store_true", help="Run selected batches in parallel"
    )
    g_batch.add_argument(
        "--max-parallel-batches",
        type=int,
        default=3,
        help=(
            "Max concurrent subagent batches when --parallel is enabled "
            "(default: 3)"
        ),
    )
    g_batch.add_argument(
        "--batch-timeout-seconds",
        type=int,
        default=20 * 60,
        help="Per-batch runner timeout in seconds (default: 1200)",
    )
    g_batch.add_argument(
        "--batch-max-retries",
        type=int,
        default=1,
        help=(
            "Retries per failed batch for transient runner/network errors "
            "(default: 1)"
        ),
    )
    g_batch.add_argument(
        "--batch-retry-backoff-seconds",
        type=float,
        default=2.0,
        help=(
            "Base backoff delay for transient batch retries in seconds "
            "(default: 2.0)"
        ),
    )
    g_batch.add_argument(
        "--batch-heartbeat-seconds",
        type=float,
        default=15.0,
        help=(
            "Progress heartbeat interval during parallel batch runs in seconds "
            "(default: 15.0)"
        ),
    )
    g_batch.add_argument(
        "--batch-stall-warning-seconds",
        type=int,
        default=0,
        help=(
            "Emit warning when a running batch exceeds this elapsed time "
            "(0 disables warnings; does not terminate the batch)"
        ),
    )
    g_batch.add_argument(
        "--batch-stall-kill-seconds",
        type=int,
        default=120,
        help=(
            "Terminate a batch when output state is unchanged and runner streams are idle "
            "for this many seconds (default: 120; 0 disables kill recovery)"
        ),
    )
    g_batch.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate packet/prompts only (skip runner/import)",
    )
    g_batch.add_argument(
        "--run-log-file",
        type=str,
        default=None,
        help=(
            "Optional explicit path for live run log output "
            "(overrides default run artifacts path)"
        ),
    )
    g_batch.add_argument(
        "--packet",
        type=str,
        default=None,
        help="Use an existing immutable packet JSON instead of preparing a new one",
    )
    g_batch.add_argument(
        "--only-batches",
        type=str,
        default=None,
        help="Comma-separated 1-based batch indexes to run (e.g. 1,3,5)",
    )
    g_batch.add_argument(
        "--scan-after-import",
        action="store_true",
        help="Run `scan` after successful merged import",
    )
    g_batch.add_argument(
        "--import-run",
        dest="import_run_dir",
        type=str,
        metavar="DIR",
        default=None,
        help=(
            "Re-import results from a completed run directory "
            "(replays merge+import when the original pipeline was interrupted)"
        ),
    )

    # -- trust & attestation --
    g_trust = p_review.add_argument_group("trust & attestation")
    g_trust.add_argument(
        "--manual-override",
        action="store_true",
        help=(
            "Allow untrusted assessment score imports. Findings always import; "
            "scores require trusted blind provenance unless this override is set."
        ),
    )
    g_trust.add_argument(
        "--attested-external",
        action="store_true",
        help=(
            "Accept external blind-run assessments as durable scores when "
            "paired with --attest and valid blind packet provenance "
            "(intended for cloud Claude subagent workflows)."
        ),
    )
    g_trust.add_argument(
        "--attest",
        type=str,
        default=None,
        help=(
            "Required with --manual-override or --attested-external. "
            "For attested external imports include both phrases "
            "'without awareness' and 'unbiased'."
        ),
    )

    # -- post-processing --
    g_post = p_review.add_argument_group("post-processing")
    g_post.add_argument(
        "--merge",
        action="store_true",
        help="Merge conceptually duplicate open review findings",
    )
    g_post.add_argument(
        "--similarity",
        type=float,
        default=0.8,
        help="Summary similarity threshold for merge (0-1, default: 0.8)",
    )

    # -- deprecated --
    g_deprecated = p_review.add_argument_group("deprecated")
    g_deprecated.add_argument(
        "--max-age",
        type=int,
        default=None,
        action=deprecated_action_cls,
        help="Deprecated in holistic-only mode (ignored)",
    )
    g_deprecated.add_argument(
        "--max-files",
        type=int,
        default=None,
        action=deprecated_action_cls,
        help="Deprecated in holistic-only mode (ignored)",
    )
    g_deprecated.add_argument(
        "--refresh",
        action=deprecated_bool_cls,
        help="Deprecated in holistic-only mode (ignored)",
    )
    g_deprecated.add_argument(
        "--holistic",
        action=deprecated_bool_cls,
        help="Deprecated: holistic is now the only review mode",
    )
    g_deprecated.add_argument(
        "--save-run-log",
        action="store_true",
        help=(
            "Deprecated no-op: run logs are now always saved while running "
            "(default location: run artifacts dir)"
        ),
    )
