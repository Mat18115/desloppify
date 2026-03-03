"""Fix command option and fixer resolution helpers."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from desloppify.app.commands.helpers.lang import resolve_lang
from desloppify.core.exception_sets import CommandError
from desloppify.languages._framework.base.types import FixerConfig, LangConfig
from desloppify.core.output_api import colorize

_COMMAND_POST_FIX: dict[str, Callable[..., None]] = {}


def _load_fixer(args, fixer_name: str) -> tuple[LangConfig, FixerConfig]:
    """Resolve fixer from language plugin registry, or exit."""
    lang = resolve_lang(args)
    if not lang:
        raise CommandError("Could not detect language. Use --lang to specify.")
    if not lang.fixers:
        raise CommandError(f"No auto-fixers available for {lang.name}.")
    if fixer_name not in lang.fixers:
        available = ", ".join(sorted(lang.fixers.keys()))
        raise CommandError(
            f"Unknown fixer: {fixer_name}\n  Available: {available}"
        )
    fc = lang.fixers[fixer_name]
    if fixer_name in _COMMAND_POST_FIX and not fc.post_fix:
        fc = dataclasses.replace(fc, post_fix=_COMMAND_POST_FIX[fixer_name])
    return lang, fc
