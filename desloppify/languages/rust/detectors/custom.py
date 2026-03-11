"""Rust-specific API, manifest, and documentation policy detectors."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.languages.rust.support import (
    describe_rust_file,
    find_rust_files,
    has_public_api_markers,
    strip_rust_comments,
)

_USE_STATEMENT_RE = re.compile(
    r"(?ms)^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+(.+?);"
)
_PUB_FN_RE = re.compile(
    r"(?m)^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?fn\s+([A-Za-z_]\w*)\b"
)
_PUBLIC_TYPE_RE = re.compile(
    r"(?m)^\s*pub(?:\([^)]*\))?\s+(struct|enum)\s+([A-Za-z_]\w*)\b"
)
_FEATURE_REF_RE = re.compile(r'feature\s*=\s*"([^"\n]+)"')
_README_RUST_FENCE_RE = re.compile(
    r"(?ms)^```(?:rust|no_run|ignore|compile_fail|should_panic)\b.*?^```"
)
_GETTER_RE = re.compile(r"^get_[A-Za-z_]\w*$")
_INTO_RE = re.compile(r"^into_[A-Za-z_]\w*$")
_PUBLIC_ERROR_RE = re.compile(
    r"\b(?:anyhow|eyre|color_eyre)::Result\b"
    r"|Box\s*<\s*dyn\s+(?:std::error::)?Error\b"
    r"|Result\s*<[^>]*\b(?:anyhow|eyre|color_eyre)::Error\b",
    re.DOTALL,
)
_PANICY_RE = re.compile(r"\.\s*(?:unwrap|expect)\s*\(|\b(?:panic|todo|unimplemented)!\s*\(")
_NON_EXHAUSTIVE_RE = re.compile(r"#\s*\[\s*non_exhaustive\s*\]")
_PUBLIC_FIELD_RE = re.compile(r"(?m)^\s*pub\s+[A-Za-z_]\w*\s*:")
_ENUM_VARIANT_RE = re.compile(r"(?m)^\s*[A-Z][A-Za-z0-9_]*\s*(?:\(|\{|,)")
_THREAD_SENSITIVE_RE = re.compile(
    r"\b(?:UnsafeCell|Cell|RefCell|Rc)\s*<|(?<!\w)\*(?:const|mut)\b"
)
_THREAD_ASSERT_RE = re.compile(
    r"\b(?:Send|Sync|assert_send|assert_sync|assert_impl_all|static_assertions)\b"
)


@dataclass(frozen=True)
class PublicFnBlock:
    """Best-effort extracted Rust public function or method block."""

    name: str
    line: int
    attrs: str
    signature: str
    body: str
    receiver: str | None


@dataclass(frozen=True)
class PublicTypeBlock:
    """Best-effort extracted Rust public type block."""

    kind: str
    name: str
    line: int
    attrs: str
    preamble: str
    body: str


def detect_import_hygiene(path: Path) -> tuple[list[dict], int]:
    """Flag same-crate imports that should use `crate::...`."""
    entries: list[dict] = []
    files = find_rust_files(path)
    for filepath in files:
        absolute = Path(resolve_path(filepath))
        try:
            content = absolute.read_text(errors="replace")
        except OSError:
            continue

        context = describe_rust_file(absolute)
        crate_name = context.package_name
        if not crate_name or not _is_internal_module(context):
            continue

        stripped = strip_rust_comments(content)
        for match in _USE_STATEMENT_RE.finditer(stripped):
            statement = match.group(1).strip()
            if f"{crate_name}::" not in statement:
                continue
            line = _line_number(stripped, match.start())
            entries.append(
                {
                    "file": rel(absolute),
                    "line": line,
                    "name": f"crate_import::{line}",
                    "summary": (
                        f"Use `crate::` for same-crate imports instead of `{crate_name}::`"
                    ),
                    "detail": {
                        "crate_name": crate_name,
                        "statement": statement,
                    },
                    "tier": 2,
                    "confidence": "high",
                }
            )
    return entries, len(files)


def detect_feature_hygiene(path: Path) -> tuple[list[dict], int]:
    """Flag referenced cfg features that are missing from Cargo.toml."""
    entries: list[dict] = []
    by_manifest = _group_files_by_manifest(path)
    for manifest_dir, files in by_manifest.items():
        manifest_path = manifest_dir / "Cargo.toml"
        declared = _declared_features(manifest_path)
        seen: set[str] = set()
        for filepath in files:
            absolute = Path(resolve_path(filepath))
            try:
                stripped = strip_rust_comments(absolute.read_text(errors="replace"))
            except OSError:
                continue
            for match in _FEATURE_REF_RE.finditer(stripped):
                feature = match.group(1).strip()
                if not feature or feature in declared or feature in seen:
                    continue
                seen.add(feature)
                entries.append(
                    {
                        "file": rel(manifest_path),
                        "line": 1,
                        "name": feature,
                        "summary": (
                            f"Feature `{feature}` is referenced in Rust cfgs but not declared in Cargo.toml"
                        ),
                        "detail": {
                            "feature": feature,
                            "manifest": rel(manifest_path),
                            "source_file": rel(absolute),
                            "source_line": _line_number(stripped, match.start()),
                        },
                        "tier": 2,
                        "confidence": "high",
                    }
                )
    return entries, len(find_rust_files(path))


def detect_doctest_hygiene(path: Path) -> tuple[list[dict], int]:
    """Flag library crates whose README examples are not wired into doctests."""
    entries: list[dict] = []
    by_manifest = _group_files_by_manifest(path)
    for manifest_dir in by_manifest:
        lib_rs = manifest_dir / "src" / "lib.rs"
        readme = manifest_dir / "README.md"
        if not lib_rs.is_file() or not readme.is_file():
            continue
        try:
            readme_text = readme.read_text(errors="replace")
            lib_text = lib_rs.read_text(errors="replace")
        except OSError:
            continue
        if not _README_RUST_FENCE_RE.search(readme_text):
            continue
        if _has_readme_doctest_harness(lib_text):
            continue
        if _has_inline_rust_doc_examples(lib_text):
            continue
        entries.append(
            {
                "file": rel(lib_rs),
                "line": 1,
                "name": "readme_doctests",
                "summary": "README Rust examples are not included in crate doctests",
                "detail": {
                    "manifest": rel(manifest_dir / "Cargo.toml"),
                    "readme": rel(readme),
                },
                "tier": 2,
                "confidence": "high",
            }
        )
    return entries, len(find_rust_files(path))


def detect_public_api_conventions(path: Path) -> tuple[list[dict], int]:
    """Flag high-confidence public API naming mismatches."""
    entries: list[dict] = []
    files = find_rust_files(path)
    for filepath in files:
        absolute = Path(resolve_path(filepath))
        try:
            content = absolute.read_text(errors="replace")
        except OSError:
            continue
        context = describe_rust_file(absolute)
        if not _is_library_api_file(context) or not has_public_api_markers(content):
            continue

        for block in _iter_public_functions(content):
            if _has_python_binding_attrs(block.attrs):
                continue
            if _GETTER_RE.match(block.name) and _looks_like_plain_getter(block):
                entries.append(
                    _entry(
                        absolute,
                        line=block.line,
                        name=f"getter::{block.name}",
                        summary=(
                            f"Public getter `{block.name}` uses a `get_` prefix; idiomatic Rust getters are usually bare names"
                        ),
                        tier=3,
                        confidence="medium",
                    )
                )
            elif (
                _INTO_RE.match(block.name)
                and block.receiver in {"&self", "&mut self"}
                and _argument_count(block.signature) == 1
            ):
                entries.append(
                    _entry(
                        absolute,
                        line=block.line,
                        name=f"into_ref::{block.name}",
                        summary=(
                            f"Public method `{block.name}` is named `into_*` but borrows `self`; `into_*` is usually by-value in Rust"
                        ),
                        tier=3,
                        confidence="medium",
                    )
                )
    return entries, len(files)


def detect_error_boundaries(path: Path) -> tuple[list[dict], int]:
    """Flag public API error boundaries that lean on app-style error handling."""
    entries: list[dict] = []
    files = find_rust_files(path)
    for filepath in files:
        absolute = Path(resolve_path(filepath))
        try:
            content = absolute.read_text(errors="replace")
        except OSError:
            continue
        context = describe_rust_file(absolute)
        if not _is_library_api_file(context):
            continue

        for block in _iter_public_functions(content):
            if _PUBLIC_ERROR_RE.search(block.signature):
                entries.append(
                    _entry(
                        absolute,
                        line=block.line,
                        name=f"error_type::{block.name}",
                        summary=(
                            f"Public function `{block.name}` exposes an app-style error boundary; prefer a crate-specific error type on public APIs"
                        ),
                        tier=2,
                        confidence="medium",
                    )
                )
            if block.body and _has_public_panic_path(block.body):
                entries.append(
                    _entry(
                        absolute,
                        line=block.line,
                        name=f"panic_path::{block.name}",
                        summary=(
                            f"Public function `{block.name}` contains `unwrap`/`expect`/panic-style control flow on a public path"
                        ),
                        tier=2,
                        confidence="medium",
                    )
                )
    return entries, len(files)


def detect_future_proofing(path: Path) -> tuple[list[dict], int]:
    """Flag brittle public structs/enums that may want `#[non_exhaustive]`."""
    entries: list[dict] = []
    files = find_rust_files(path)
    for filepath in files:
        absolute = Path(resolve_path(filepath))
        try:
            content = absolute.read_text(errors="replace")
        except OSError:
            continue
        context = describe_rust_file(absolute)
        if not _is_library_api_file(context):
            continue

        for block in _iter_public_types(content):
            if _NON_EXHAUSTIVE_RE.search(block.attrs):
                continue
            if _should_skip_future_proofing(block):
                continue
            if block.kind == "struct":
                public_fields = len(_PUBLIC_FIELD_RE.findall(block.body))
                if public_fields >= 2:
                    entries.append(
                        _entry(
                            absolute,
                            line=block.line,
                            name=f"struct::{block.name}",
                            summary=(
                                f"Public struct `{block.name}` exposes {public_fields} public fields without `#[non_exhaustive]`; this hardens its API shape early"
                            ),
                            tier=3,
                            confidence="medium",
                        )
                    )
            elif block.kind == "enum":
                variant_count = len(_ENUM_VARIANT_RE.findall(block.body))
                if variant_count >= 5:
                    entries.append(
                        _entry(
                            absolute,
                            line=block.line,
                            name=f"enum::{block.name}",
                            summary=(
                                f"Public enum `{block.name}` has {variant_count} variants without `#[non_exhaustive]`; adding variants later may become a breaking change"
                            ),
                            tier=3,
                            confidence="low",
                        )
                    )
    return entries, len(files)


def detect_thread_safety_contracts(path: Path) -> tuple[list[dict], int]:
    """Flag manual Send/Sync contracts without visible assertion tests."""
    entries: list[dict] = []
    by_manifest = _group_files_by_manifest(path)
    for manifest_dir, files in by_manifest.items():
        corpus_parts: list[str] = []
        for filepath in files:
            absolute = Path(resolve_path(filepath))
            try:
                content = absolute.read_text(errors="replace")
            except OSError:
                continue
            if _is_test_content(absolute, content):
                corpus_parts.append(content)
        corpus = "\n".join(corpus_parts)
        for filepath in files:
            absolute = Path(resolve_path(filepath))
            try:
                content = absolute.read_text(errors="replace")
            except OSError:
                continue
            context = describe_rust_file(absolute)
            if not _is_library_api_file(context):
                continue
            for block in _iter_public_types(content):
                if block.kind != "struct":
                    continue
                if _looks_like_ffi_surface(block):
                    continue
                if not _has_manual_thread_contract(content, block.name):
                    continue
                if _has_thread_assertion(corpus, block.name):
                    continue
                entries.append(
                    _entry(
                        absolute,
                        line=block.line,
                        name=f"thread_contract::{block.name}",
                        summary=(
                            f"Public struct `{block.name}` has a manual Send/Sync contract but no visible assertion tests"
                        ),
                        tier=3,
                        confidence="low",
                    )
                )
    return entries, len(find_rust_files(path))


def iter_missing_features(path: Path) -> dict[str, list[str]]:
    """Return manifest-relative missing feature declarations grouped by manifest."""
    entries, _ = detect_feature_hygiene(path)
    grouped: dict[str, set[str]] = {}
    for entry in entries:
        manifest = entry["detail"]["manifest"]
        grouped.setdefault(manifest, set()).add(entry["detail"]["feature"])
    return {manifest: sorted(features) for manifest, features in grouped.items()}


def missing_readme_doctest_harnesses(path: Path) -> list[str]:
    """Return library crate roots that need a README doctest harness."""
    entries, _ = detect_doctest_hygiene(path)
    return [entry["file"] for entry in entries]


def _group_files_by_manifest(path: Path) -> dict[Path, list[str]]:
    grouped: dict[Path, list[str]] = {}
    for filepath in find_rust_files(path):
        absolute = Path(resolve_path(filepath))
        context = describe_rust_file(absolute)
        grouped.setdefault(context.manifest_dir, []).append(filepath)
    return grouped


def _declared_features(manifest_path: Path) -> set[str]:
    try:
        data = tomllib.loads(manifest_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    declared: set[str] = set()
    features = data.get("features")
    if isinstance(features, dict):
        declared.update(str(name) for name in features)
    for section_name in (
        "dependencies",
        "build-dependencies",
        "target",
        "workspace.dependencies",
    ):
        declared.update(_optional_dependency_features(data, section_name))
    target = data.get("target")
    if isinstance(target, dict):
        for section in target.values():
            if not isinstance(section, dict):
                continue
            for dependency_group in ("dependencies", "build-dependencies"):
                declared.update(_optional_dependency_features(section, dependency_group))
    return declared


def _is_internal_module(context) -> bool:
    try:
        relative = context.source_file.relative_to(context.manifest_dir)
    except ValueError:
        return False
    parts = relative.parts
    if not parts:
        return False
    if parts[0] != "src":
        return False
    if relative == Path("src/main.rs"):
        return False
    if parts[:2] == ("src", "bin"):
        return False
    return True


def _is_library_api_file(context) -> bool:
    return _is_internal_module(context) and (context.manifest_dir / "src" / "lib.rs").is_file()


def _iter_public_functions(content: str) -> list[PublicFnBlock]:
    blocks: list[PublicFnBlock] = []
    for match in _PUB_FN_RE.finditer(content):
        body_start = _find_block_start(content, match.end())
        if body_start is None:
            continue
        body_end = _find_matching_brace(content, body_start)
        if body_end is None:
            continue
        attrs = _preceding_metadata(content, match.start())
        signature = content[match.start() : body_start].strip()
        body = content[body_start : body_end + 1]
        blocks.append(
            PublicFnBlock(
                name=match.group(1),
                line=_line_number(content, match.start()),
                attrs=attrs,
                signature=signature,
                body=body,
                receiver=_receiver_from_signature(signature),
            )
        )
    return blocks


def _iter_public_types(content: str) -> list[PublicTypeBlock]:
    blocks: list[PublicTypeBlock] = []
    for match in _PUBLIC_TYPE_RE.finditer(content):
        body_start = _find_block_start(content, match.end())
        if body_start is None:
            continue
        body_end = _find_matching_brace(content, body_start)
        if body_end is None:
            continue
        preamble = _preceding_metadata(content, match.start())
        blocks.append(
            PublicTypeBlock(
                kind=match.group(1),
                name=match.group(2),
                line=_line_number(content, match.start()),
                attrs=_preceding_attributes(content, match.start()),
                preamble=preamble,
                body=content[body_start + 1 : body_end],
            )
        )
    return blocks


def _has_readme_doctest_harness(content: str) -> bool:
    return "include_str!(\"../README.md\")" in content or "cfg(doctest)" in content


def _has_inline_rust_doc_examples(content: str) -> bool:
    return "```rust" in content or "```no_run" in content or "```ignore" in content


def _is_test_content(filepath: Path, content: str) -> bool:
    normalized = rel(filepath)
    return normalized.startswith("tests/") or "#[cfg(test)]" in content or "#[test]" in content


def _receiver_from_signature(signature: str) -> str | None:
    open_index = signature.find("(")
    if open_index == -1:
        return None
    close_index = _find_matching_delimiter(signature, open_index, "(", ")")
    if close_index is None:
        return None
    params = signature[open_index + 1 : close_index]
    first = params.split(",", 1)[0].strip()
    return first or None


def _find_block_start(content: str, index: int) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    angle_depth = 0
    for cursor in range(index, len(content)):
        char = content[cursor]
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(0, angle_depth - 1)
        elif char == ";" and paren_depth == bracket_depth == angle_depth == 0:
            return None
        elif char == "{" and paren_depth == bracket_depth == angle_depth == 0:
            return cursor
    return None


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


def _find_matching_delimiter(text: str, start_index: int, opening: str, closing: str) -> int | None:
    depth = 0
    for index in range(start_index, len(text)):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _preceding_attributes(content: str, start: int) -> str:
    return "\n".join(
        line
        for line in _preceding_metadata(content, start).splitlines()
        if line.strip().startswith("#[")
    )


def _preceding_metadata(content: str, start: int) -> str:
    lines = content[:start].splitlines()
    attrs: list[str] = []
    index = len(lines) - 1
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped:
            if attrs:
                break
            index -= 1
            continue
        if stripped.startswith("#[") or stripped.startswith("///") or stripped.startswith("//!"):
            attrs.append(stripped)
            index -= 1
            continue
        break
    return "\n".join(reversed(attrs))


def _optional_dependency_features(data: dict, section_name: str) -> set[str]:
    section = data.get(section_name)
    if not isinstance(section, dict):
        return set()
    features: set[str] = set()
    for dep_name, dep_value in section.items():
        if isinstance(dep_value, dict) and dep_value.get("optional") is True:
            features.add(str(dep_name))
    return features


def _has_python_binding_attrs(attrs: str) -> bool:
    return any(token in attrs for token in ("#[getter", "#[setter", "#[pymethods", "#[pyfunction"))


def _argument_count(signature: str) -> int:
    open_index = signature.find("(")
    if open_index == -1:
        return 0
    close_index = _find_matching_delimiter(signature, open_index, "(", ")")
    if close_index is None:
        return 0
    params = [chunk.strip() for chunk in signature[open_index + 1 : close_index].split(",")]
    return len([param for param in params if param])


def _looks_like_plain_getter(block: PublicFnBlock) -> bool:
    return block.receiver in {"&self", "&mut self"} and _argument_count(block.signature) == 1


def _has_public_panic_path(body: str) -> bool:
    stripped = strip_rust_comments(body)
    if re.search(r"\b(?:panic|todo|unimplemented)!\s*\(", stripped):
        return True
    return bool(re.search(r"\.\s*(?:lock|read|write)\s*\(\)\s*\.\s*(?:unwrap|expect)\s*\(", stripped))


def _should_skip_future_proofing(block: PublicTypeBlock) -> bool:
    preamble = block.preamble.lower()
    attrs = block.attrs.lower()
    if any(token in attrs for token in ("repr(c)", "non_exhaustive", "pyclass", "doc(hidden)")):
        return True
    if any(token in preamble for token in ("unstable", "internal", "not part of the stable api")):
        return True
    return False


def _looks_like_ffi_surface(block: PublicTypeBlock) -> bool:
    attrs = block.attrs.lower()
    return "repr(c)" in attrs or "no_mangle" in attrs


def _has_manual_thread_contract(content: str, type_name: str) -> bool:
    return bool(
        re.search(
            rf"unsafe\s+impl(?:\s*<[^>]+>)?\s+(?:Send|Sync)\s+for\s+{re.escape(type_name)}\b",
            content,
        )
    )


def _has_thread_assertion(corpus: str, type_name: str) -> bool:
    if type_name not in corpus:
        return False
    if not _THREAD_ASSERT_RE.search(corpus):
        return False
    return bool(
        re.search(rf"\b{re.escape(type_name)}\b.*\b(?:Send|Sync)\b", corpus, re.DOTALL)
        or re.search(rf"\b(?:Send|Sync)\b.*\b{re.escape(type_name)}\b", corpus, re.DOTALL)
    )


def _entry(
    filepath: Path,
    *,
    line: int,
    name: str,
    summary: str,
    tier: int,
    confidence: str,
) -> dict:
    return {
        "file": rel(filepath),
        "line": line,
        "name": name,
        "summary": summary,
        "detail": {"line": line},
        "tier": tier,
        "confidence": confidence,
    }


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def replace_same_crate_imports(filepath: str) -> tuple[str, int]:
    """Rewrite `use my_crate::...` imports to `use crate::...` in one file."""
    absolute = Path(resolve_path(filepath))
    context = describe_rust_file(absolute)
    crate_name = context.package_name
    if not crate_name or not _is_internal_module(context):
        return absolute.read_text(errors="replace"), 0

    try:
        content = absolute.read_text(errors="replace")
    except OSError:
        return "", 0

    replacements = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal replacements
        statement = match.group(1)
        count = statement.count(f"{crate_name}::")
        if count == 0:
            return match.group(0)
        replacements += count
        return match.group(0).replace(f"{crate_name}::", "crate::")

    return _USE_STATEMENT_RE.sub(repl, content), replacements


def add_missing_features_to_manifest(manifest_path: str, missing_features: list[str]) -> str:
    """Insert missing feature declarations into a Cargo.toml manifest."""
    absolute = Path(resolve_path(manifest_path))
    raw = absolute.read_text(errors="replace")
    missing = [feature for feature in missing_features if feature]
    if not missing:
        return raw

    lines = raw.splitlines()
    feature_section_index = None
    for index, line in enumerate(lines):
        if line.strip() == "[features]":
            feature_section_index = index
            break

    additions = [f"{feature} = []" for feature in missing]
    if feature_section_index is None:
        suffix = "\n" if raw.endswith("\n") else "\n\n"
        block = "[features]\n" + "\n".join(additions) + "\n"
        return raw + suffix + block

    insert_at = len(lines)
    for index in range(feature_section_index + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            insert_at = index
            break
    updated = lines[:insert_at] + additions + lines[insert_at:]
    return "\n".join(updated) + ("\n" if raw.endswith("\n") or updated else "")


def ensure_readme_doctest_harness(lib_path: str) -> str:
    """Append the standard README doctest harness to `src/lib.rs` if needed."""
    absolute = Path(resolve_path(lib_path))
    content = absolute.read_text(errors="replace")
    if _has_readme_doctest_harness(content):
        return content
    snippet = (
        "\n\n#[cfg(doctest)]\n"
        "#[doc = include_str!(\"../README.md\")]\n"
        "mod readme_doctests {}\n"
    )
    return content.rstrip() + snippet


__all__ = [
    "add_missing_features_to_manifest",
    "detect_doctest_hygiene",
    "detect_error_boundaries",
    "detect_feature_hygiene",
    "detect_future_proofing",
    "detect_import_hygiene",
    "detect_public_api_conventions",
    "detect_thread_safety_contracts",
    "ensure_readme_doctest_harness",
    "iter_missing_features",
    "missing_readme_doctest_harnesses",
    "replace_same_crate_imports",
]
