"""Repo map (OpenClaude / aider style): the most referenced files and their signatures.

Gives planners and workers structural awareness in a fixed character budget instead
of many list/grep/read round-trips. Python is parsed with ``ast``; JS/TS, Go, Rust,
Java, C# and others with light regexes. Files are ranked by how often the symbols they
define are referenced from other files (IDF-weighted), then rendered until the budget.
"""
from __future__ import annotations

import ast
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .osutil import posix, read_text
from .tools import IGNORED_DIRS

SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".cs",
              ".rb", ".php", ".swift", ".vue", ".svelte", ".c", ".h", ".cpp", ".hpp"}
MAX_FILES = 1500
MAX_BYTES = 300_000

_cache: dict[str, tuple[float, int, list[str], set[str], Counter]] = {}

JS_DEFS = [
    re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(([^)]*)\)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", re.M),
    re.compile(r"^\s*export\s+(?:const|let)\s+(\w+)\s*[:=]", re.M),
    re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?type\s+(\w+)\s*(?:<[^=]*>)?\s*=", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:enum)\s+(\w+)", re.M),
    re.compile(r"^\s*(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.M),
]
GENERIC_DEFS = [
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)", re.M),            # go
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)", re.M),          # rust
    re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl)\s+(\w+)", re.M),            # rust
    re.compile(r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:final\s+)?(?:class|interface|record|enum)\s+(\w+)", re.M),
    re.compile(r"^\s*(?:public|protected)\s+(?:static\s+)?[\w<>\[\],\s]+\s+(\w+)\s*\(", re.M),
    re.compile(r"^\s*def\s+(\w+)", re.M),                                                # ruby
]
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _py_defs(text: str) -> tuple[list[str], set[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], set()
    lines, names = [], set()
    for node in tree.body:
        if getattr(node, "name", "").startswith("_"):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            lines.append(f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({args})")
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases) if node.bases else ""
            lines.append(f"class {node.name}{f'({bases})' if bases else ''}:")
            names.add(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    not sub.name.startswith("_") or sub.name == "__init__"
                ):
                    args = ", ".join(a.arg for a in sub.args.args[1:])
                    lines.append(f"    def {sub.name}({args})")
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.isupper():
                lines.append(f"{name} = ...")
                names.add(name)
    return lines, names


def _regex_defs(text: str, patterns) -> tuple[list[str], set[str]]:
    found: list[tuple[int, str]] = []
    names = set()
    for rx in patterns:
        for m in rx.finditer(text):
            name = m.group(1)
            sig = m.group(0).strip()
            sig = re.sub(r"\s+", " ", sig)[:140]
            found.append((m.start(), sig))
            names.add(name)
    found.sort()
    seen, lines = set(), []
    for _, sig in found:
        if sig not in seen:
            seen.add(sig)
            lines.append(sig)
    return lines, names


def _defs(path: Path, text: str) -> tuple[list[str], set[str]]:
    ext = path.suffix
    if ext == ".py":
        return _py_defs(text)
    if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"):
        return _regex_defs(text, JS_DEFS)
    return _regex_defs(text, GENERIC_DEFS)


def _files(root: Path) -> list[Path]:
    out = []
    real = root.resolve()
    from .config import DATA_DIR

    data = DATA_DIR.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
                             and (Path(dirpath) / d).resolve() != data)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():
                try:
                    target = p.resolve()
                except OSError:
                    continue
                if target != real and real not in target.parents:
                    continue
            if p.suffix in SOURCE_EXT:
                out.append(p)
                if len(out) >= MAX_FILES:
                    return out
    return out


def build(directory: str, budget_chars: int = 6000, focus: str = "") -> str:
    root = Path(directory)
    if not root.is_dir():
        return ""
    defs: dict[str, list[str]] = {}
    names_of: dict[str, set[str]] = {}
    words_of: dict[str, Counter] = {}
    for p in _files(root):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > MAX_BYTES:
            continue
        key = str(p)
        cached = _cache.get(key)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            lines, names, words = cached[2], cached[3], cached[4]
        else:
            try:
                text = read_text(p)
            except OSError:
                continue
            lines, names = _defs(p, text)
            words = Counter(WORD.findall(text))
            _cache[key] = (st.st_mtime, st.st_size, lines, names, words)
        rel = posix(p.relative_to(root))
        defs[rel] = lines
        names_of[rel] = names
        words_of[rel] = words

    # Symbol -> defining files; IDF over how many files define a name.
    definers: dict[str, list[str]] = defaultdict(list)
    for rel, names in names_of.items():
        for n in names:
            definers[n].append(rel)
    n_files = max(1, len(defs))
    score: dict[str, float] = {rel: 0.1 for rel in defs}
    for rel, words in words_of.items():
        for name, count in words.items():
            files = definers.get(name)
            if not files:
                continue
            idf = math.log(1 + n_files / len(files))
            for target in files:
                if target != rel:
                    score[target] += math.sqrt(count) * idf
    focus_l = focus.lower().strip()
    if focus_l:
        for rel in score:
            if focus_l in rel.lower() or any(focus_l == n.lower() for n in names_of.get(rel, ())):
                score[rel] += 1000
    ranked = sorted(defs, key=lambda r: (-score[r], r))

    out, used = [], 0
    for rel in ranked:
        sigs = defs[rel]
        block = rel + ("\n" + "\n".join(f"  {s}" for s in sigs[:25]) if sigs else "")
        if used + len(block) + 1 > budget_chars:
            line_only = rel
            if used + len(line_only) + 1 > budget_chars:
                break
            block = line_only
        out.append(block)
        used += len(block) + 1
    return "\n".join(out)
