"""Shared Rust path, manifest, and import-resolution helpers."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_source_files
from desloppify.base.text_utils import strip_c_style_comments

RUST_FILE_EXCLUSIONS = ["target", ".git", "node_modules", "vendor"]
_USE_RE = re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([^;]+);")
_PUB_USE_RE = re.compile(r"(?m)^\s*pub(?:\([^)]*\))?\s+use\s+([^;]+);")
_MOD_RE = re.compile(r"(?m)^\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)\s*;")
_PUBLIC_ITEM_RE = re.compile(
    r"(?m)^\s*pub(?:\([^)]*\))?\s+(?:struct|enum|trait|type|fn|mod)\s+"
)
_RUST_LOG_RE = re.compile(r"^\s*(?:println!|eprintln!|dbg!|tracing::)", re.MULTILINE)


@dataclass(frozen=True)
class RustFileContext:
    """Filesystem context required to resolve a Rust source file's module paths."""

    source_file: Path
    manifest_dir: Path
    package_name: str | None
    crate_name: str | None
    source_root: Path
    root_files: tuple[Path, ...]
    module_segments: tuple[str, ...]


def normalize_crate_name(name: str | None) -> str | None:
    """Normalize Cargo package names to Rust crate names."""
    if not name:
        return None
    text = str(name).strip()
    if not text:
        return None
    return text.replace("-", "_")


def find_rust_files(path: Path | str) -> list[str]:
    """Find Rust source files under path."""
    return find_source_files(path, [".rs"], exclusions=RUST_FILE_EXCLUSIONS)


def strip_rust_comments(content: str) -> str:
    """Strip Rust line/block comments while preserving literals best-effort."""
    stripped = strip_c_style_comments(content)
    stripped = re.sub(r"(?m)^\s*///.*$", "", stripped)
    stripped = re.sub(r"(?m)^\s*//!\s?.*$", "", stripped)
    return stripped


def normalize_rust_body(body: str) -> str:
    """Normalize a Rust function body for duplicate detection."""
    stripped = strip_rust_comments(body)
    lines = []
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _RUST_LOG_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def has_public_api_markers(content: str) -> bool:
    """Return True when a file exposes public API surface."""
    return bool(_PUBLIC_ITEM_RE.search(strip_rust_comments(content)))


def iter_mod_declarations(content: str) -> list[str]:
    """Return `mod foo;` declarations from a file."""
    stripped = strip_rust_comments(content)
    return [match.group(1) for match in _MOD_RE.finditer(stripped)]


def iter_use_specs(content: str) -> list[str]:
    """Return normalized Rust `use` / `pub use` specs from a file."""
    stripped = strip_rust_comments(content)
    specs: list[str] = []
    for match in _USE_RE.finditer(stripped):
        specs.extend(_expand_use_tree(match.group(1)))
    return specs


def iter_pub_use_specs(content: str) -> list[str]:
    """Return normalized `pub use` specs from a file."""
    stripped = strip_rust_comments(content)
    specs: list[str] = []
    for match in _PUB_USE_RE.finditer(stripped):
        specs.extend(_expand_use_tree(match.group(1)))
    return specs


def find_manifest_dir(path: Path | str) -> Path | None:
    """Walk up from path to the nearest Cargo.toml root."""
    candidate = Path(resolve_path(str(path)))
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / "Cargo.toml").is_file():
            return current
    return None


def read_package_name(manifest_dir: Path) -> str | None:
    """Read package name from Cargo.toml, if present."""
    manifest = manifest_dir / "Cargo.toml"
    if not manifest.is_file():
        return None
    try:
        data = tomllib.loads(manifest.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    package = data.get("package")
    if not isinstance(package, dict):
        return None
    return normalize_crate_name(package.get("name"))


def build_workspace_package_index(scan_root: Path | None = None) -> dict[str, Path]:
    """Return local crate-name -> Cargo manifest dir for the active project root."""
    root = find_workspace_root(scan_root) if scan_root is not None else get_project_root()
    packages: dict[str, Path] = {}
    for manifest in root.rglob("Cargo.toml"):
        if any(part in RUST_FILE_EXCLUSIONS for part in manifest.parts):
            continue
        name = read_package_name(manifest.parent)
        if name:
            packages[name] = manifest.parent.resolve()
    return packages


def find_workspace_root(path: Path | str | None) -> Path:
    """Return the outermost Cargo workspace root for a file/dir when present."""
    if path is None:
        return get_project_root()

    candidate = Path(resolve_path(str(path))).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    manifest_dir = find_manifest_dir(candidate) or candidate
    workspace_root = manifest_dir
    for current in (manifest_dir, *manifest_dir.parents):
        manifest = current / "Cargo.toml"
        if not manifest.is_file():
            continue
        try:
            data = tomllib.loads(manifest.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        workspace = data.get("workspace")
        if isinstance(workspace, dict):
            workspace_root = current
    return workspace_root.resolve()


def describe_rust_file(source_file: str | Path) -> RustFileContext:
    """Build resolution context for a Rust source file."""
    source = Path(resolve_path(str(source_file))).resolve()
    manifest_dir = find_manifest_dir(source) or get_project_root()
    package_name = read_package_name(manifest_dir)
    try:
        rel_to_manifest = source.relative_to(manifest_dir)
    except ValueError:
        rel_to_manifest = source.name
        rel_to_manifest = Path(rel_to_manifest)

    parts = rel_to_manifest.parts
    if rel_to_manifest == Path("build.rs"):
        return RustFileContext(
            source_file=source,
            manifest_dir=manifest_dir,
            package_name=package_name,
            crate_name=normalize_crate_name(package_name or manifest_dir.name),
            source_root=manifest_dir,
            root_files=(manifest_dir / "build.rs",),
            module_segments=(),
        )

    if parts[:2] == ("src", "bin") and len(parts) >= 3:
        bin_name = Path(parts[2]).stem
        bin_dir = manifest_dir / "src" / "bin" / bin_name
        root_files = (
            manifest_dir / "src" / "bin" / f"{bin_name}.rs",
            bin_dir / "main.rs",
        )
        if len(parts) == 3:
            module_segments: tuple[str, ...] = ()
        else:
            module_segments = _module_segments_from_rel(Path(*parts[3:]))
        return RustFileContext(
            source_file=source,
            manifest_dir=manifest_dir,
            package_name=package_name,
            crate_name=normalize_crate_name(bin_name),
            source_root=bin_dir,
            root_files=root_files,
            module_segments=module_segments,
        )

    if parts[:1] == ("src",):
        return RustFileContext(
            source_file=source,
            manifest_dir=manifest_dir,
            package_name=package_name,
            crate_name=package_name,
            source_root=manifest_dir / "src",
            root_files=(
                manifest_dir / "src" / "lib.rs",
                manifest_dir / "src" / "main.rs",
            ),
            module_segments=_module_segments_from_rel(Path(*parts[1:])),
        )

    if parts[:1] in {("tests",), ("examples",), ("benches",)} and len(parts) >= 2:
        root_name = Path(parts[1]).stem
        bucket = parts[0]
        root_dir = manifest_dir / bucket / root_name
        return RustFileContext(
            source_file=source,
            manifest_dir=manifest_dir,
            package_name=package_name,
            crate_name=normalize_crate_name(root_name),
            source_root=root_dir,
            root_files=(
                manifest_dir / bucket / f"{root_name}.rs",
                root_dir / "main.rs",
            ),
            module_segments=() if len(parts) == 2 else _module_segments_from_rel(Path(*parts[2:])),
        )

    return RustFileContext(
        source_file=source,
        manifest_dir=manifest_dir,
        package_name=package_name,
        crate_name=package_name,
        source_root=source.parent,
        root_files=(source,),
        module_segments=(),
    )


def resolve_mod_declaration(
    module_name: str,
    source_file: str | Path,
    production_files: set[str],
) -> str | None:
    """Resolve `mod foo;` to `foo.rs` or `foo/mod.rs` relative to the file's module dir."""
    source = Path(resolve_path(str(source_file))).resolve()
    base_dir = (
        source.parent
        if source.name in {"lib.rs", "main.rs", "mod.rs", "build.rs"}
        else source.with_suffix("")
    )
    for candidate in (base_dir / f"{module_name}.rs", base_dir / module_name / "mod.rs"):
        matched = _candidate_matches(candidate, production_files)
        if matched:
            return matched
    return None


def resolve_use_spec(
    spec: str,
    source_file: str | Path,
    production_files: set[str],
    package_index: dict[str, Path] | None = None,
    *,
    allow_crate_root_fallback: bool = True,
) -> str | None:
    """Resolve a Rust `use` spec to a local module file when possible."""
    cleaned = _normalize_use_spec(spec)
    if not cleaned:
        return None

    package_index = package_index or build_workspace_package_index()
    context = describe_rust_file(source_file)
    segments = [segment for segment in cleaned.split("::") if segment]
    if not segments:
        return None

    candidates: list[str | None] = []
    if cleaned.startswith("crate::"):
        candidates.append(
            _resolve_from_source_root(
                context.source_root,
                context.root_files,
                segments[1:],
                production_files,
                allow_root_fallback=allow_crate_root_fallback,
            )
        )
    elif segments[0] in {"self", "super"}:
        resolved_segments = _resolve_relative_segments(context.module_segments, segments)
        candidates.append(
            _resolve_from_source_root(
                context.source_root,
                context.root_files,
                resolved_segments,
                production_files,
                allow_root_fallback=allow_crate_root_fallback,
            )
        )
    else:
        first = segments[0]
        manifest_dir = package_index.get(first)
        if manifest_dir is not None:
            lib_root = manifest_dir / "src"
            candidates.append(
                _resolve_from_source_root(
                    lib_root,
                    (manifest_dir / "src" / "lib.rs", manifest_dir / "src" / "main.rs"),
                    segments[1:],
                    production_files,
                    allow_root_fallback=allow_crate_root_fallback,
                )
            )
        candidates.append(
            _resolve_from_source_root(
                context.source_root,
                context.root_files,
                list(context.module_segments) + segments,
                production_files,
                allow_root_fallback=False,
            )
        )
        candidates.append(
            _resolve_from_source_root(
                context.source_root,
                context.root_files,
                segments,
                production_files,
                allow_root_fallback=allow_crate_root_fallback,
            )
        )

    for resolved in candidates:
        if resolved:
            return resolved
    return None


def resolve_barrel_targets(
    filepath: str | Path,
    production_files: set[str],
    package_index: dict[str, Path] | None = None,
) -> set[str]:
    """Resolve `pub use` / `pub mod` targets from a Rust facade file."""
    try:
        content = Path(resolve_path(str(filepath))).read_text(errors="replace")
    except OSError:
        return set()

    package_index = package_index or build_workspace_package_index()
    targets: set[str] = set()
    for spec in iter_pub_use_specs(content):
        resolved = resolve_use_spec(
            spec,
            filepath,
            production_files,
            package_index,
            allow_crate_root_fallback=False,
        )
        if resolved:
            targets.add(resolved)
    for module_name in iter_mod_declarations(content):
        resolved = resolve_mod_declaration(module_name, filepath, production_files)
        if resolved:
            targets.add(resolved)
    return targets


def _module_segments_from_rel(rel_path: Path) -> tuple[str, ...]:
    parts = list(rel_path.parts)
    if not parts:
        return ()
    filename = parts[-1]
    if filename in {"lib.rs", "main.rs"} and len(parts) == 1:
        return ()
    if filename == "mod.rs":
        return tuple(parts[:-1])
    if filename.endswith(".rs"):
        return tuple(parts[:-1] + [Path(filename).stem])
    return tuple(parts)


def _resolve_relative_segments(
    module_segments: tuple[str, ...],
    segments: list[str],
) -> list[str]:
    resolved = list(module_segments)
    remaining = list(segments)
    while remaining and remaining[0] in {"self", "super"}:
        head = remaining.pop(0)
        if head == "super" and resolved:
            resolved.pop()
    resolved.extend(remaining)
    return resolved


def _resolve_from_source_root(
    source_root: Path,
    root_files: tuple[Path, ...],
    segments: list[str],
    production_files: set[str],
    *,
    allow_root_fallback: bool,
) -> str | None:
    if not segments:
        return _match_root_files(root_files, production_files)

    for width in range(len(segments), 0, -1):
        module_parts = segments[:width]
        if not module_parts:
            continue
        file_candidate = source_root.joinpath(*module_parts).with_suffix(".rs")
        mod_candidate = source_root.joinpath(*module_parts, "mod.rs")
        for candidate in (file_candidate, mod_candidate):
            matched = _candidate_matches(candidate, production_files)
            if matched:
                return matched

    if allow_root_fallback:
        return _match_root_files(root_files, production_files)
    return None


def _match_root_files(root_files: tuple[Path, ...], production_files: set[str]) -> str | None:
    for root_file in root_files:
        matched = _candidate_matches(root_file, production_files)
        if matched:
            return matched
    return None


def _candidate_matches(candidate: Path, production_files: set[str]) -> str | None:
    resolved_candidate = candidate.resolve()
    project_root = get_project_root()
    candidate_abs = str(resolved_candidate)
    try:
        candidate_rel = rel(resolved_candidate, project_root=project_root)
    except (TypeError, ValueError, OSError):
        candidate_rel = None

    for production_file in production_files:
        prod_path = Path(production_file)
        if prod_path.is_absolute():
            normalized = str(prod_path.resolve())
        else:
            normalized = str((project_root / prod_path).resolve())
        if normalized == candidate_abs:
            return production_file
        if candidate_rel is not None and production_file == candidate_rel:
            return production_file
    return None


def match_production_candidate(candidate: Path, production_files: set[str]) -> str | None:
    """Public wrapper for matching a resolved candidate to the production-file set."""
    return _candidate_matches(candidate, production_files)


def _split_top_level(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char in "{([":
            depth += 1
        elif char in "})]":
            depth = max(0, depth - 1)
        if char == delimiter and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _expand_use_tree(spec: str) -> list[str]:
    spec = spec.strip()
    if not spec:
        return []

    alias_split = re.split(r"\s+as\s+", spec, maxsplit=1)
    spec = alias_split[0].strip()
    if not spec:
        return []

    open_index = spec.find("{")
    if open_index == -1:
        normalized = _normalize_use_spec(spec)
        return [normalized] if normalized else []

    close_index = _find_matching_brace(spec, open_index)
    if close_index is None:
        normalized = _normalize_use_spec(spec)
        return [normalized] if normalized else []

    prefix = spec[:open_index].rstrip(":")
    suffix = spec[close_index + 1 :].strip()
    inner = spec[open_index + 1 : close_index]
    expanded: list[str] = []
    for part in _split_top_level(inner):
        combined = part if not prefix else f"{prefix}::{part}"
        if suffix:
            combined = f"{combined}{suffix}"
        expanded.extend(_expand_use_tree(combined))
    return expanded


def _find_matching_brace(text: str, start_index: int) -> int | None:
    depth = 0
    for index in range(start_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _normalize_use_spec(spec: str) -> str | None:
    normalized = spec.strip().replace(" ", "")
    if not normalized:
        return None
    normalized = normalized.removeprefix("::")
    normalized = normalized.replace("::{self}", "")
    normalized = normalized.replace("::self", "")
    normalized = normalized.replace("::*", "")
    normalized = normalized.strip(":")
    return normalized or None


__all__ = [
    "RUST_FILE_EXCLUSIONS",
    "RustFileContext",
    "build_workspace_package_index",
    "describe_rust_file",
    "find_manifest_dir",
    "find_rust_files",
    "find_workspace_root",
    "has_public_api_markers",
    "iter_mod_declarations",
    "iter_pub_use_specs",
    "iter_use_specs",
    "match_production_candidate",
    "normalize_crate_name",
    "normalize_rust_body",
    "read_package_name",
    "resolve_barrel_targets",
    "resolve_mod_declaration",
    "resolve_use_spec",
    "strip_rust_comments",
]
