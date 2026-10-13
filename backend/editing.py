"""Edit matching and the apply_patch format, after OpenCode's edit / patch tools.

Local models rarely reproduce text byte-for-byte, so ``find_matches`` tries, in order:
1. the exact text;
2. the text with typographic quotes, dashes and odd spaces normalized;
3. line by line, ignoring trailing whitespace;
4. line by line, ignoring all indentation differences (only when unique);
5. the text with ``read`` line-number prefixes ("    12<TAB>") stripped, then 1-4 again.
CRLF files are matched on their LF view and keep their line endings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                        "“": '"', "”": '"', "„": '"', "‟": '"',
                        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
                        "―": "-", "−": "-", " ": " ", " ": " ", " ": " ", "　": " "})
_SPACES = re.compile(r"[ - ]")
_LINE_NO = re.compile(r"^\s*\d+\t")


class EditError(Exception):
    pass


def norm(s: str) -> str:
    return _SPACES.sub(" ", s.translate(QUOTES))


@dataclass
class Match:
    start: int
    end: int


def _find_all(content: str, search: str) -> list[Match]:
    out, i = [], 0
    if not search:
        return out
    while True:
        i = content.find(search, i)
        if i < 0:
            return out
        out.append(Match(i, i + len(search)))
        i += len(search)


def _line_spans(content: str) -> list[tuple[int, int, str]]:
    """(start, end_without_newline, text) for each line."""
    spans, pos = [], 0
    for line in content.split("\n"):
        spans.append((pos, pos + len(line), line))
        pos += len(line) + 1
    return spans


def _find_linewise(content: str, search: str, key) -> list[Match]:
    want = search.split("\n")
    trailing_nl = search.endswith("\n")
    if trailing_nl:
        want.pop()
    if not want:
        return []
    want_k = [key(w) for w in want]
    if all(not w for w in want_k):
        return []
    spans = _line_spans(content)
    out: list[Match] = []
    for i in range(len(spans) - len(want) + 1):
        if all(key(spans[i + j][2]) == want_k[j] for j in range(len(want))):
            last = spans[i + len(want) - 1]
            end = last[1] + 1 if trailing_nl and last[1] < len(content) else last[1]
            m = Match(spans[i][0], end)
            if not out or m.start >= out[-1].end:
                out.append(m)
    return out


def _strip_line_numbers(text: str) -> Optional[str]:
    lines = text.split("\n")
    body = [l for l in lines if l.strip()]
    if body and all(_LINE_NO.match(l) for l in body):
        return "\n".join(_LINE_NO.sub("", l) for l in lines)
    return None


def find_matches(content: str, search: str) -> tuple[list[Match], str]:
    """Returns (matches, strategy)."""
    strategies = [
        ("exact", lambda c, s: _find_all(c, s)),
        ("normalized", lambda c, s: _find_all(norm(c), norm(s)) if len(norm(c)) == len(c) else []),
        ("trailing-whitespace", lambda c, s: _find_linewise(c, s, lambda l: norm(l).rstrip())),
        ("indentation", lambda c, s: _find_linewise(c, s, lambda l: norm(l).strip())),
    ]
    candidates = [search]
    stripped = _strip_line_numbers(search)
    if stripped is not None:
        candidates.append(stripped)
    for cand in candidates:
        for name, fn in strategies:
            found = fn(content, cand)
            if found:
                if name == "indentation" and len(found) > 1:
                    continue  # too loose to pick one
                return found, name if cand is search else f"{name}+line-numbers"
    return [], ""


def _reindent(replacement: str, region: str, search: str) -> str:
    """When matched ignoring indentation, shift the replacement by the indentation delta."""
    def indent(s: str) -> str:
        for l in s.split("\n"):
            if l.strip():
                return l[: len(l) - len(l.lstrip())]
        return ""
    have, want = indent(region), indent(search)
    if have == want:
        return replacement
    out = []
    for l in replacement.split("\n"):
        if l.startswith(want):
            l = have + l[len(want):]
        out.append(l)
    return "\n".join(out)


def apply_edit(content: str, old: str, new: str, replace_all: bool = False) -> tuple[str, int, str]:
    """Returns (new content, replacements, strategy). Raises EditError."""
    if old == new:
        raise EditError("old_string and new_string are identical")
    crlf = "\r\n" in content
    view = content.replace("\r\n", "\n") if crlf else content
    old_v, new_v = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
    if old_v == "":
        if view.strip():
            raise EditError("old_string is empty but the file is not: give the exact text to replace")
        out = new_v
        return (out.replace("\n", "\r\n") if crlf else out), 1, "create"
    matches, strategy = find_matches(view, old_v)
    if not matches:
        raise EditError(
            "old_string not found in the file. Read the file again and copy the exact lines "
            "(without the line-number prefix), including indentation."
        )
    if len(matches) > 1 and not replace_all:
        lines = [view.count("\n", 0, m.start) + 1 for m in matches[:10]]
        raise EditError(
            f"old_string matches {len(matches)} places (lines {', '.join(map(str, lines))}). "
            "Add surrounding lines to make it unique, or set replace_all=true."
        )
    pieces, last = [], 0
    search_for_indent = _strip_line_numbers(old_v) or old_v
    for m in matches:
        region = view[m.start:m.end]
        repl = _reindent(new_v, region, search_for_indent) if strategy.startswith("indentation") else new_v
        pieces.append(view[last:m.start])
        pieces.append(repl)
        last = m.end
    pieces.append(view[last:])
    out = "".join(pieces)
    if crlf:
        out = out.replace("\n", "\r\n")
    return out, len(matches), strategy


# ---------------------------------------------------------------------------
# apply_patch (OpenCode / Codex envelope)

PATCH_HELP = """*** Begin Patch
*** Add File: path/new.py
+line 1
+line 2
*** Update File: path/existing.py
*** Move to: path/renamed.py   (optional)
@@ optional context line, e.g. a def or class header
 unchanged context line
-removed line
+added line
*** Delete File: path/old.py
*** End Patch"""


@dataclass
class PatchOp:
    kind: str  # add | update | delete
    path: str
    move_to: Optional[str] = None
    lines: Optional[list[str]] = None  # add: content lines; update: hunk lines


def parse_patch(text: str) -> list[PatchOp]:
    lines = text.replace("\r\n", "\n").strip("\n").split("\n")
    # Tolerate a ``` fence around the patch.
    lines = [l for l in lines if not l.strip().startswith("```")]
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "*** Begin Patch")
    except StopIteration:
        raise EditError("Patch must start with '*** Begin Patch'")
    ops: list[PatchOp] = []
    cur: Optional[PatchOp] = None
    for raw in lines[start + 1:]:
        l = raw.rstrip("\r")
        if l.strip() == "*** End Patch":
            break
        if l.startswith("*** Add File:"):
            cur = PatchOp("add", l.split(":", 1)[1].strip(), lines=[])
            ops.append(cur)
        elif l.startswith("*** Update File:"):
            cur = PatchOp("update", l.split(":", 1)[1].strip(), lines=[])
            ops.append(cur)
        elif l.startswith("*** Delete File:"):
            cur = PatchOp("delete", l.split(":", 1)[1].strip())
            ops.append(cur)
        elif l.startswith("*** Move to:"):
            if not cur or cur.kind != "update":
                raise EditError("'*** Move to:' must follow '*** Update File:'")
            cur.move_to = l.split(":", 1)[1].strip()
        elif cur is None:
            continue
        elif cur.kind == "add":
            if not l.startswith("+") and l != "":
                raise EditError(f"In '*** Add File: {cur.path}' every line must start with '+': {l[:60]!r}")
            cur.lines.append(l[1:] if l.startswith("+") else "")
        elif cur.kind == "update":
            cur.lines.append(l)
    if not ops:
        raise EditError("Patch contains no file operation")
    return ops


def _hunks(lines: list[str]) -> list[tuple[Optional[str], list[str], list[str]]]:
    """Split update lines into hunks: (context header, old lines, new lines)."""
    hunks = []
    header: Optional[str] = None
    old: list[str] = []
    new: list[str] = []

    def flush():
        nonlocal old, new
        if old or new:
            hunks.append((header, old, new))
        old, new = [], []

    for l in lines:
        if l.startswith("@@"):
            flush()
            header = l[2:].strip() or None
            continue
        if l == "*** End of File":
            continue
        tag, body = (l[0], l[1:]) if l else (" ", "")
        if tag == "+":
            new.append(body)
        elif tag == "-":
            old.append(body)
        else:
            if tag != " ":
                body = l  # model forgot the leading space
            old.append(body)
            new.append(body)
    flush()
    return hunks


def apply_update(content: str, lines: list[str]) -> str:
    crlf = "\r\n" in content
    view = content.replace("\r\n", "\n") if crlf else content
    for header, old, new in _hunks(lines):
        base = 0
        if header:
            hm, _ = find_matches(view, header)
            if hm:
                base = hm[0].start
        old_text, new_text = "\n".join(old), "\n".join(new)
        if not old:
            # Pure insertion after the header line (or at the end).
            if header and base:
                eol = view.find("\n", base)
                eol = len(view) if eol < 0 else eol + 1
                view = view[:eol] + new_text + "\n" + view[eol:]
            else:
                view = view + ("" if view.endswith("\n") or not view else "\n") + new_text + "\n"
            continue
        tail = view[base:]
        matches, strategy = find_matches(tail, old_text)
        if not matches and base:
            tail, base = view, 0
            matches, strategy = find_matches(tail, old_text)
        if not matches:
            preview = "\n".join(old[:4])
            raise EditError(f"Patch hunk does not match the file. Expected lines:\n{preview}")
        m = matches[0]
        region = tail[m.start:m.end]
        repl = _reindent(new_text, region, old_text) if strategy.startswith("indentation") else new_text
        view = view[: base + m.start] + repl + view[base + m.end:]
    return view.replace("\n", "\r\n") if crlf else view
