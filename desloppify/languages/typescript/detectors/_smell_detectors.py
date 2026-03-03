"""TypeScript function-level smell detectors — monster functions, stub functions, etc.

Every public detector has the uniform signature ``(ctx: _FileContext, smell_counts) → None``.
"""

import os
import re

from desloppify.languages.typescript.detectors._smell_helpers import (
    _FileContext,
    _code_text,
    _content_line_info,
    _extract_block_body,
    _strip_ts_comments,
    _track_brace_body,
    _ts_match_is_in_string,
)

__all__ = [
    "_detect_async_no_await",
    "_detect_catch_return_default",
    "_detect_dead_useeffects",
    "_detect_empty_if_chains",
    "_detect_error_no_throw",
    "_detect_high_cyclomatic_complexity",
    "_detect_monster_functions",
    "_detect_nested_closures",
    "_detect_stub_functions",
    "_detect_swallowed_errors",
    "_detect_switch_no_default",
    "_detect_window_globals",
    "_find_function_start",
]

# ── Constants ────────────────────────────────────────────────

_MONSTER_FUNCTION_LOC = 150
_HIGH_CYCLOMATIC_THRESHOLD = 15
_NESTED_CLOSURE_THRESHOLD = 3
_CATCH_DEFAULT_FIELD_THRESHOLD = 2
_SWITCH_CASE_MINIMUM = 2

_MAX_CATCH_BODY = 1000  # max characters to scan for catch block body
_MAX_SWITCH_BODY_SCAN = 5000

# Basenames that indicate logger/error-handler utility files (case-insensitive).
_ERROR_HANDLER_BASENAMES = ("logger", "errorpresentation", "errorhandler", "errorreporting")

# Patterns in preceding context that indicate legitimate error handling.
_PRECEDING_SKIP_PATTERNS = re.compile(
    r"componentDidCatch|import\.meta\.env\.DEV|process\.env\.NODE_ENV"
)

# Patterns in following lines that count as "handled" (not just throw/return).
_HANDLED_RE = re.compile(
    r"\b(?:throw|return)\b|toast\(|normalizeAndPresentError\(|presentError\(|rethrow"
)

_FUNC_RE = re.compile(r"\bfunction\s*[\w(]")
_ARROW_RE = re.compile(r"=>\s*\{")

_TS_BRANCH_PATTERNS = (
    re.compile(r"\bif\s*\("),
    re.compile(r"\belse\s+if\s*\("),
    re.compile(r"\bcase\s+"),
    re.compile(r"\bcatch\s*\("),
    re.compile(r"\bfor\s*\("),
    re.compile(r"\bwhile\s*\("),
)

_OPERATOR_BRANCH_RE = re.compile(r"&&|\|\||\?(?!=)")

# ── Empty-if-chain patterns ─────────────────────────────────

_IF_START = re.compile(r"(?:else\s+)?if\s*\(")
_SINGLE_EMPTY_IF = re.compile(r"(?:else\s+)?if\s*\([^)]*\)\s*\{\s*\}\s*$")
_SINGLE_EMPTY_ELSE_IF = re.compile(r"else\s+if\s*\([^)]*\)\s*\{\s*\}\s*$")
_SINGLE_EMPTY_ELSE = re.compile(r"(?:\}\s*)?else\s*\{\s*\}\s*$")
_MULTI_IF_OPEN = re.compile(r"(?:else\s+)?if\s*\([^)]*\)\s*\{\s*$")
_MULTI_ELSE_IF_OPEN = re.compile(r"\}\s*else\s+if\s*\([^)]*\)\s*\{\s*$")
_MULTI_ELSE_OPEN = re.compile(r"\}\s*else\s*\{\s*$")
_ELSE_CONT = re.compile(r"else\s")

# ── Internal helpers ─────────────────────────────────────────


def _emit(
    smell_counts: dict[str, list[dict]],
    key: str,
    ctx: _FileContext,
    line: int,
    content: str,
) -> None:
    """Append a smell finding."""
    smell_counts[key].append({"file": ctx.filepath, "line": line, "content": content})


def _find_function_start(line: str, next_lines: list[str]) -> str | None:
    """Return function name for declarations/assignments, else None."""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith(("interface ", "type ", "enum ", "class ")):
        return None

    declaration_match = re.match(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(",
        stripped,
    )
    if declaration_match:
        return declaration_match.group(1)

    assignment_match = re.match(
        r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b",
        stripped,
    )
    if not assignment_match:
        return None

    combined = "\n".join(
        [stripped] + [next_line.strip() for next_line in next_lines[:2]]
    )
    eq_pos = combined.find("=", assignment_match.end())
    if eq_pos == -1:
        return None
    after_eq = combined[eq_pos + 1 :].lstrip()
    if re.match(r"(?:async\s+)?\([^)]*\)\s*=>", after_eq):
        return assignment_match.group(1)
    if re.match(r"function\b", after_eq):
        return assignment_match.group(1)
    return None


def _find_opening_brace_line(lines: list[str], start: int, *, window: int = 5) -> int | None:
    for idx in range(start, min(start + window, len(lines))):
        if "{" in lines[idx]:
            return idx
    return None


def _extract_function_body(
    lines: list[str], start_line: int, *, max_scan: int = 2000,
) -> str | None:
    """Extract the inner body text of a function starting at start_line.

    Returns the text between the opening and closing braces, or None.
    """
    brace_line = _find_opening_brace_line(lines, start_line, window=5)
    if brace_line is None:
        return None
    end_line = _track_brace_body(lines, brace_line, max_scan=max_scan)
    if end_line is None:
        return None
    body_text = "\n".join(lines[brace_line : end_line + 1])
    first_brace = body_text.find("{")
    last_brace = body_text.rfind("}")
    if first_brace == -1 or last_brace == -1 or first_brace >= last_brace:
        return None
    return body_text[first_brace + 1 : last_brace]


def _count_pattern_in_body(body: str, pattern: re.Pattern[str]) -> int:
    """Count regex matches in body that aren't inside string literals."""
    return len(pattern.findall(_code_text(body)))


def _compute_ts_cyclomatic_complexity(body: str) -> int:
    """Compute cyclomatic complexity for a TypeScript function body string."""
    stripped = _strip_ts_comments(body)
    complexity = 1
    for pattern in _TS_BRANCH_PATTERNS:
        complexity += len(pattern.findall(stripped))
    complexity += len(_OPERATOR_BRANCH_RE.findall(stripped))
    return complexity


# ── Detectors (alphabetical — uniform signature: ctx, smell_counts) ──


def _detect_async_no_await(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Find async functions that don't use await."""
    async_re = re.compile(r"(?:async\s+function\s+(\w+)|(\w+)\s*=\s*async)")
    for i, line in enumerate(ctx.lines):
        m = async_re.search(line)
        if not m:
            continue
        name = m.group(1) or m.group(2)
        body = _extract_function_body(ctx.lines, i)
        if body is not None and not re.search(r"\bawait\b", _code_text(body)):
            _emit(
                smell_counts, "async_no_await", ctx, i + 1,
                f"async {name or '(anonymous)'} has no await",
            )


def _detect_catch_return_default(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Find catch blocks that return object literals with default/no-op values.

    Catches the pattern:
      catch (...) { ... return { key: false, key: null, key: () => {} }; }

    This is a silent failure — the caller gets valid-looking data but the
    operation actually failed.
    """
    catch_re = re.compile(r"catch\s*\([^)]*\)\s*\{")
    for m in catch_re.finditer(ctx.content):
        body = _extract_block_body(ctx.content, m.end() - 1, _MAX_CATCH_BODY)
        if body is None:
            continue

        # Check if body contains "return {" — a return with object literal
        return_obj = re.search(r"\breturn\s*\{", body)
        if not return_obj:
            continue

        # Extract the returned object content
        obj_start = body.find("{", return_obj.start())
        obj_content = _extract_block_body(body, obj_start)
        if obj_content is None:
            continue

        # Count default/no-op fields
        noop_count = len(re.findall(r"\(\)\s*=>\s*\{\s*\}", obj_content))  # () => {}
        false_count = len(
            re.findall(r":\s*(?:false|null|undefined|0|''|\"\")\b", obj_content)
        )
        default_fields = noop_count + false_count

        if default_fields >= _CATCH_DEFAULT_FIELD_THRESHOLD:
            line_no, snippet = _content_line_info(ctx.content, m.start())
            _emit(smell_counts, "catch_return_default", ctx, line_no, snippet)


def _detect_dead_useeffects(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
) -> None:
    """Find useEffect calls with empty/whitespace/comment-only bodies."""
    for line_no, line in enumerate(ctx.lines):
        stripped = line.strip()
        if not re.match(r"(?:React\.)?useEffect\s*\(\s*\(\s*\)\s*=>\s*\{", stripped):
            continue

        text = "\n".join(ctx.lines[line_no : line_no + 30])
        arrow_pos = text.find("=>")
        if arrow_pos == -1:
            continue
        brace_pos = text.find("{", arrow_pos)
        if brace_pos == -1:
            continue

        body = _extract_block_body(text, brace_pos)
        if body is None:
            continue

        if _strip_ts_comments(body).strip() == "":
            _emit(smell_counts, "dead_useeffect", ctx, line_no + 1, stripped[:100])


def _scan_single_line_chain(
    ctx: _FileContext, index: int, smell_counts: dict[str, list[dict]]
) -> int:
    """Consume a single-line empty if/else-if chain starting at *index*. Returns next index."""
    cursor = index + 1
    while cursor < len(ctx.lines):
        next_stripped = ctx.lines[cursor].strip()
        if _SINGLE_EMPTY_ELSE_IF.match(next_stripped):
            cursor += 1
            continue
        if _SINGLE_EMPTY_ELSE.match(next_stripped):
            cursor += 1
            continue
        break
    _emit(smell_counts, "empty_if_chain", ctx, index + 1, ctx.lines[index].strip()[:100])
    return cursor


def _scan_multi_line_chain(
    ctx: _FileContext, index: int, smell_counts: dict[str, list[dict]]
) -> int:
    """Consume a multi-line empty if/else chain starting at *index*. Returns next index."""
    chain_all_empty = True
    cursor = index
    while cursor < len(ctx.lines):
        current = ctx.lines[cursor].strip()
        if cursor == index:
            if not _MULTI_IF_OPEN.match(current):
                chain_all_empty = False
                break
        elif _MULTI_ELSE_IF_OPEN.match(current):
            pass
        elif _MULTI_ELSE_OPEN.match(current):
            pass
        elif current == "}":
            tail = cursor + 1
            while tail < len(ctx.lines) and ctx.lines[tail].strip() == "":
                tail += 1
            if tail < len(ctx.lines) and _ELSE_CONT.match(ctx.lines[tail].strip()):
                cursor = tail
                continue
            cursor += 1
            break
        elif current == "":
            cursor += 1
            continue
        else:
            chain_all_empty = False
            break
        cursor += 1
    if chain_all_empty and cursor > index + 1:
        _emit(smell_counts, "empty_if_chain", ctx, index + 1, ctx.lines[index].strip()[:100])
    return max(index + 1, cursor)


def _detect_empty_if_chains(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
) -> None:
    """Find if/else chains where all branches are empty."""
    index = 0
    while index < len(ctx.lines):
        stripped = ctx.lines[index].strip()
        if not _IF_START.match(stripped):
            index += 1
            continue
        if _SINGLE_EMPTY_IF.match(stripped):
            index = _scan_single_line_chain(ctx, index, smell_counts)
            continue
        if _MULTI_IF_OPEN.match(stripped):
            index = _scan_multi_line_chain(ctx, index, smell_counts)
            continue
        index += 1


def _detect_error_no_throw(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
) -> None:
    """Find console.error calls not followed by throw/return or other handling."""
    # Skip files that are themselves logger/error-handler utilities.
    basename = os.path.basename(ctx.filepath).lower()
    basename_no_ext = os.path.splitext(basename)[0]
    if any(tag in basename_no_ext for tag in _ERROR_HANDLER_BASENAMES):
        return

    for index, line in enumerate(ctx.lines):
        if "console.error" not in line:
            continue

        # Skip if preceding 10 lines contain error-boundary or dev-only context.
        preceding = "\n".join(ctx.lines[max(0, index - 10) : index])
        if _PRECEDING_SKIP_PATTERNS.search(preceding):
            continue

        following = "\n".join(ctx.lines[index + 1 : index + 4])
        if not _HANDLED_RE.search(following):
            _emit(smell_counts, "console_error_no_throw", ctx, index + 1, line.strip()[:100])


def _detect_high_cyclomatic_complexity(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Flag functions with cyclomatic complexity > 15."""
    for i, line in enumerate(ctx.lines):
        name = _find_function_start(line, ctx.lines[i + 1 : i + 3])
        if not name:
            continue

        body = _extract_function_body(ctx.lines, i)
        if body is None:
            continue

        complexity = _compute_ts_cyclomatic_complexity(body)
        if complexity > _HIGH_CYCLOMATIC_THRESHOLD:
            _emit(
                smell_counts, "high_cyclomatic_complexity", ctx, i + 1,
                f"{name}() — cyclomatic complexity {complexity}",
            )


def _detect_monster_functions(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Find functions/components exceeding 150 LOC via brace-tracking.

    Matches: function declarations, named arrow functions, and React components.
    Skips: interfaces, types, enums, and objects/arrays.
    """
    for i, line in enumerate(ctx.lines):
        name = _find_function_start(line, ctx.lines[i + 1 : i + 3])
        if not name:
            continue

        brace_line = _find_opening_brace_line(ctx.lines, i, window=5)
        if brace_line is None:
            continue

        end_line = _track_brace_body(ctx.lines, brace_line, max_scan=2000)
        if end_line is not None:
            loc = end_line - i + 1
            if loc > _MONSTER_FUNCTION_LOC:
                _emit(
                    smell_counts, "monster_function", ctx, i + 1,
                    f"{name}() — {loc} LOC",
                )


def _detect_nested_closures(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Find functions with >= 3 nested closure definitions."""
    for i, line in enumerate(ctx.lines):
        name = _find_function_start(line, ctx.lines[i + 1 : i + 3])
        if not name:
            continue

        body = _extract_function_body(ctx.lines, i)
        if body is None:
            continue

        closure_count = (
            _count_pattern_in_body(body, _FUNC_RE)
            + _count_pattern_in_body(body, _ARROW_RE)
        )
        if closure_count >= _NESTED_CLOSURE_THRESHOLD:
            _emit(
                smell_counts, "nested_closure", ctx, i + 1,
                f"{name}() — {closure_count} nested closures",
            )


def _detect_stub_functions(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Find functions with empty body or only return/return null (stub functions).

    Matches function declarations and named arrow functions.
    Skips decorated functions (TS decorators on line above).
    """
    for i, line in enumerate(ctx.lines):
        if i > 0 and ctx.lines[i - 1].strip().startswith("@"):
            continue

        name = _find_function_start(line, ctx.lines[i + 1 : i + 3])
        if not name:
            continue

        body = _extract_function_body(ctx.lines, i, max_scan=30)
        if body is None:
            continue

        body_clean = _strip_ts_comments(body).strip().rstrip(";")
        if body_clean in ("", "return", "return null", "return undefined"):
            label = body_clean or "empty"
            _emit(
                smell_counts, "stub_function", ctx, i + 1,
                f"{name}() — body is {label}",
            )


def _detect_swallowed_errors(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
) -> None:
    """Find catch blocks whose only content is console.error/warn/log."""
    catch_re = re.compile(r"catch\s*\([^)]*\)\s*\{")
    for match in catch_re.finditer(ctx.content):
        body = _extract_block_body(ctx.content, match.end() - 1, 500)
        if body is None:
            continue

        body_clean = _strip_ts_comments(body).strip()
        if not body_clean:
            continue

        statements = [
            stmt.strip().rstrip(";")
            for stmt in re.split(r"[;\n]", body_clean)
            if stmt.strip()
        ]
        if not statements:
            continue

        all_console = all(
            re.match(r"console\.(error|warn|log)\s*\(", stmt) for stmt in statements
        )
        if all_console:
            line_no, snippet = _content_line_info(ctx.content, match.start())
            _emit(smell_counts, "swallowed_error", ctx, line_no, snippet)


def _detect_switch_no_default(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Flag switch statements that have no default case."""
    switch_re = re.compile(r"\bswitch\s*\([^)]*\)\s*\{")
    for m in switch_re.finditer(ctx.content):
        body = _extract_block_body(ctx.content, m.end() - 1, _MAX_SWITCH_BODY_SCAN)
        if body is None:
            continue

        # Count case labels — only flag if there are actual cases
        case_count = len(re.findall(r"\bcase\s+", body))
        if case_count < _SWITCH_CASE_MINIMUM:
            continue

        if re.search(r"\bdefault\s*:", body):
            continue

        line_no, snippet = _content_line_info(ctx.content, m.start())
        _emit(smell_counts, "switch_no_default", ctx, line_no, snippet)


def _detect_window_globals(
    ctx: _FileContext, smell_counts: dict[str, list[dict]]
):
    """Find window.__* assignments — global state escape hatches.

    Matches:
    - window.__foo = ...
    - (window as any).__foo = ...
    - window['__foo'] = ...
    """
    window_re = re.compile(
        r"""(?:"""
        r"""\(?\s*window\s+as\s+any\s*\)?\s*\.\s*(?:__\w+)"""  # (window as any).__name
        r"""|window\s*\.\s*(?:__\w+)"""  # window.__name
        r"""|window\s*\[\s*['"](?:__\w+)['"]\s*\]"""  # window['__name']
        r""")\s*=""",
    )
    for i, line in enumerate(ctx.lines):
        if i in ctx.line_state:
            continue
        m = window_re.search(line)
        if not m:
            continue
        if _ts_match_is_in_string(line, m.start()):
            continue
        _emit(smell_counts, "window_global", ctx, i + 1, line.strip()[:100])
