"""Multi-line smell detection utilities (brace-tracked).

Shared utilities: string-aware scanning, brace tracking, comment stripping,
and line-state classification. All detector functions live in _smell_detectors.py.
"""

from __future__ import annotations

from typing import NamedTuple

from desloppify.core.text_api import strip_c_style_comments
from desloppify.languages.typescript.syntax.scanner import scan_code

__all__ = [
    "_FileContext",
    "_build_ts_line_state",
    "_code_text",
    "_content_line_info",
    "_extract_block_body",
    "_find_block_end",
    "_scan_template_content",
    "_strip_ts_comments",
    "_track_brace_body",
    "_ts_match_is_in_string",
    "scan_code",
]


class _FileContext(NamedTuple):
    """Per-file data bundle passed to all smell detectors."""

    filepath: str
    content: str
    lines: list[str]
    line_state: dict[int, str]


def _strip_ts_comments(text: str) -> str:
    """Strip // and /* */ comments while preserving strings.

    Delegates to the shared implementation in utils.py.
    """
    return strip_c_style_comments(text)


def _ts_match_is_in_string(line: str, match_start: int) -> bool:
    """Check if a match position falls inside a string literal or comment on a single line.

    Mirrors Python's _match_is_in_string but for TS syntax (', ", `, //).
    """
    i = 0
    in_str = None

    while i < len(line):
        if i == match_start:
            return in_str is not None

        ch = line[i]

        # Escape sequences inside strings
        if in_str and ch == "\\" and i + 1 < len(line):
            i += 2
            continue

        if in_str:
            if ch == in_str:
                in_str = None
            i += 1
            continue

        # Line comment — everything after is non-code
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return match_start > i

        if ch in ("'", '"', "`"):
            in_str = ch
            i += 1
            continue

        i += 1

    return False


def _track_brace_body(
    lines: list[str], start_line: int, *, max_scan: int = 2000
) -> int | None:
    """Find the closing brace that matches the first opening brace from start_line.

    Tracks brace depth with string-literal awareness (', ", `).
    Returns the line index of the closing brace, or None if not found.
    """
    depth = 0
    found_open = False
    for line_idx in range(start_line, min(start_line + max_scan, len(lines))):
        for _, ch, in_string in scan_code(lines[line_idx]):
            if in_string:
                continue
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return line_idx
    return None


def _find_block_end(content: str, brace_start: int, max_scan: int = 5000) -> int | None:
    """Find the closing brace position in a content string starting from an opening brace.

    Uses scan_code for string-literal awareness. Returns the index of the
    matching ``}`` or None.
    """
    depth = 0
    for ci, ch, in_s in scan_code(
        content, brace_start, min(brace_start + max_scan, len(content))
    ):
        if in_s:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return ci
    return None


def _extract_block_body(
    content: str, brace_start: int, max_scan: int = 5000
) -> str | None:
    """Return the text between ``{`` at *brace_start* and its matching ``}``.

    Delegates to :func:`_find_block_end` for brace tracking.
    Returns ``None`` when the closing brace is not found.
    """
    end = _find_block_end(content, brace_start, max_scan)
    if end is None:
        return None
    return content[brace_start + 1 : end]


def _content_line_info(content: str, pos: int) -> tuple[int, str]:
    """Return ``(1-based line number, stripped snippet[:100])`` for a position in *content*."""
    line_no = content[:pos].count("\n") + 1
    line_start = content.rfind("\n", 0, pos) + 1
    line_end = content.find("\n", pos)
    if line_end == -1:
        line_end = len(content)
    return line_no, content[line_start:line_end].strip()[:100]


def _code_text(text: str) -> str:
    """Blank string literals and ``//`` comments to spaces, preserving positions.

    Built on :func:`scan_code` with added line-comment detection.
    """
    out = list(text)
    in_line_comment = False
    prev_code_idx = -2
    prev_code_ch = ""
    for i, ch, in_s in scan_code(text):
        if ch == "\n":
            in_line_comment = False
            prev_code_ch = ""
            continue
        if in_line_comment:
            out[i] = " "
            continue
        if in_s:
            out[i] = " "
            continue
        if ch == "/" and prev_code_ch == "/" and prev_code_idx == i - 1:
            out[prev_code_idx] = " "
            out[i] = " "
            in_line_comment = True
            prev_code_ch = ""
            continue
        prev_code_idx = i
        prev_code_ch = ch
    return "".join(out)


def _scan_template_content(
    line: str, start: int, brace_depth: int = 0
) -> tuple[int, bool, int]:
    """Scan template literal content from *start* in *line*.

    Returns ``(end_pos, found_close, brace_depth)`` where *found_close* is True
    if a closing backtick was found and *end_pos* is the position after it.
    """
    j = start
    while j < len(line):
        ch = line[j]
        if ch == "\\" and j + 1 < len(line):
            j += 2
            continue
        if ch == "$" and j + 1 < len(line) and line[j + 1] == "{":
            brace_depth += 1
            j += 2
            continue
        if ch == "}" and brace_depth > 0:
            brace_depth -= 1
            j += 1
            continue
        if ch == "`" and brace_depth == 0:
            return (j + 1, True, brace_depth)
        j += 1
    return (j, False, brace_depth)


def _scan_code_line(line: str) -> tuple[bool, bool, int]:
    """Scan a normal code line for block comment or template literal start.

    Returns ``(entered_block_comment, entered_template, template_brace_depth)``.
    """
    j = 0
    in_str = None
    while j < len(line):
        ch = line[j]

        # Skip escape sequences
        if in_str and ch == "\\" and j + 1 < len(line):
            j += 2
            continue

        # String tracking
        if in_str:
            if ch == in_str:
                in_str = None
            j += 1
            continue

        # Line comment — rest is not code
        if ch == "/" and j + 1 < len(line) and line[j + 1] == "/":
            break

        # Block comment start
        if ch == "/" and j + 1 < len(line) and line[j + 1] == "*":
            # Check if it closes on same line
            close = line.find("*/", j + 2)
            if close != -1:
                j = close + 2
                continue
            return (True, False, 0)

        # Template literal start
        if ch == "`":
            end_pos, found_close, depth = _scan_template_content(line, j + 1)
            if found_close:
                j = end_pos
                continue
            return (False, True, depth)

        if ch in ("'", '"'):
            in_str = ch
            j += 1
            continue

        j += 1

    return (False, False, 0)


def _build_ts_line_state(lines: list[str]) -> dict[int, str]:
    """Build a map of line numbers that are inside block comments or template literals.

    Returns {0-indexed line: reason} where reason is "block_comment" or "template_literal".
    Lines not in the map are normal code lines suitable for regex checks.

    Tracks:
    - Block comment state (opened by /*, closed by */)
    - Template literal state (opened by backtick, closed by backtick,
      with ${} nesting awareness)
    """
    state: dict[int, str] = {}
    in_block_comment = False
    in_template = False
    template_brace_depth = 0

    for i, line in enumerate(lines):
        if in_block_comment:
            state[i] = "block_comment"
            if "*/" in line:
                in_block_comment = False
            continue

        if in_template:
            state[i] = "template_literal"
            _, found_close, template_brace_depth = _scan_template_content(
                line, 0, template_brace_depth
            )
            if found_close:
                in_template = False
            continue

        in_block_comment, in_template, depth = _scan_code_line(line)
        if in_template:
            template_brace_depth = depth

    return state
