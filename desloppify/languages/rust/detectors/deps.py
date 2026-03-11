"""Rust dependency graph builder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.engine.detectors.graph import finalize_graph
from desloppify.languages.rust.support import (
    build_workspace_package_index,
    find_rust_files,
    iter_mod_declarations,
    iter_use_specs,
    resolve_mod_declaration,
    resolve_use_spec,
)


def build_dep_graph(
    path: Path,
    roslyn_cmd: str | None = None,
    *,
    include_mod_declarations: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build a Rust dependency graph from `mod` and `use` declarations."""
    del roslyn_cmd
    files = find_rust_files(path)
    graph = {filepath: {"imports": set(), "importers": set()} for filepath in files}
    if not graph:
        return {}

    file_set = set(graph.keys())
    package_index = build_workspace_package_index()
    for filepath in files:
        try:
            content = Path(resolve_path(filepath)).read_text(errors="replace")
        except OSError:
            continue

        if include_mod_declarations:
            for module_name in iter_mod_declarations(content):
                resolved = resolve_mod_declaration(module_name, filepath, file_set)
                if resolved and resolved != filepath:
                    graph[filepath]["imports"].add(resolved)
                    graph[resolved]["importers"].add(filepath)

        for spec in iter_use_specs(content):
            resolved = resolve_use_spec(
                spec,
                filepath,
                file_set,
                package_index,
                allow_crate_root_fallback=False,
            )
            if resolved and resolved != filepath:
                graph[filepath]["imports"].add(resolved)
                graph[resolved]["importers"].add(filepath)

    return finalize_graph(graph)


__all__ = ["build_dep_graph"]
