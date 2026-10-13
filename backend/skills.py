"""Agent Skills: reusable expertise packaged as folders with a SKILL.md (Anthropic Agent Skills format).

A skill is a folder holding ``SKILL.md`` - YAML frontmatter (``name``, ``description`` and
optional fields) followed by Markdown instructions - plus any reference files or scripts.

Progressive disclosure, as in Claude Code: the system prompt only carries each skill's
name and description; the agent loads the body with the ``skill`` tool when a task
matches, then reads the reference files it needs. The user can also run a skill
directly with ``/<skill-name> arguments``.

Locations (a name defined in several places resolves to the most specific one):
- builtin  ``backend/builtin/skills``  (read-only, shipped with the app)
- user     ``data/skills``             (all projects)
- project  ``<project>/.supercode/skills``, ``.claude/skills``, ``.agents/skills``

Supported frontmatter: name, description, when_to_use, argument-hint, allowed-tools,
disable-model-invocation, user-invocable, requires (an app feature, e.g. ``reverse``),
license, metadata.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, load_config
from .osutil import posix, read_text, write_text
from .tools import ToolContext, ToolDef, ToolError, ToolResult

BUILTIN_DIR = Path(__file__).resolve().parent / "builtin" / "skills"
USER_DIR = DATA_DIR / "skills"
PROJECT_SUBDIRS = (".supercode/skills", ".claude/skills", ".agents/skills")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_FILES = 200
SCOPES = ("project", "user", "builtin")


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    scope: str
    meta: dict = field(default_factory=dict)
    error: str = ""

    @property
    def model_invocable(self) -> bool:
        return not _truthy(self.meta.get("disable-model-invocation"))

    @property
    def user_invocable(self) -> bool:
        return _truthy(self.meta.get("user-invocable", True))

    @property
    def requires(self) -> str:
        return str(self.meta.get("requires") or "")

    def body(self) -> str:
        return parse(read_text(self.path / "SKILL.md"))[1]

    def files(self) -> list[str]:
        out = []
        for p in sorted(self.path.rglob("*")):
            if p.is_file() and p.name != "SKILL.md" and not any(part.startswith(".") for part in p.relative_to(self.path).parts):
                out.append(posix(p.relative_to(self.path)))
                if len(out) >= MAX_FILES:
                    break
        return out

    def summary(self, enabled: bool = True, available: bool = True) -> dict:
        return {"name": self.name, "description": self.description, "scope": self.scope, "path": str(self.path),
                "whenToUse": str(self.meta.get("when_to_use") or ""), "argumentHint": str(self.meta.get("argument-hint") or ""),
                "allowedTools": _list(self.meta.get("allowed-tools")), "modelInvocable": self.model_invocable,
                "userInvocable": self.user_invocable, "requires": self.requires, "license": str(self.meta.get("license") or ""),
                "enabled": enabled, "available": available, "error": self.error, "readOnly": self.scope == "builtin"}


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "1", "on")


def _list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [x for x in re.split(r"[,\s]+", str(v or "")) if x]


# ---------------------------------------------------------------------------
# SKILL.md parsing


def _simple_yaml(text: str) -> dict:
    """Fallback frontmatter parser: key: value lines, block scalars, one-level nesting."""
    out: dict = {}
    key = None
    block: list[str] = []
    for line in text.splitlines():
        if key and (line.startswith("  ") or not line.strip()):
            block.append(line.strip())
            continue
        if key:
            out[key] = " ".join(b for b in block if b) if block else out.get(key, "")
            key, block = None, []
        m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v in ("|", ">", "|-", ">-", ""):
            key, block = k, []
            out[k] = ""
        else:
            out[k] = v.strip("'\"")
    if key:
        out[key] = " ".join(b for b in block if b)
    return out


def parse(text: str) -> tuple[dict, str]:
    """SKILL.md -> (frontmatter, body)."""
    text = text.lstrip("﻿")
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", text, re.S)
    if not m:
        return {}, text
    raw = m.group(1)
    try:
        import yaml  # type: ignore

        meta = yaml.safe_load(raw) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:  # noqa: BLE001 - PyYAML missing or invalid YAML: best-effort parser
        meta = _simple_yaml(raw)
    return meta, text[m.end():]


def render(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v in (None, "", []):
            continue
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif isinstance(v, dict):
            lines.append(f"{k}:")
            lines += [f"  {kk}: {_scalar(vv)}" for kk, vv in v.items()]
        else:
            lines.append(f"{k}: {_scalar(v)}")
    return "\n".join(lines) + "\n---\n\n" + body.lstrip("\n")


def _scalar(v) -> str:
    s = str(v).replace("\n", " ")
    return f'"{s}"' if re.search(r"[:#\[\]{}&*!|>'\"%@`]", s) or s != s.strip() else s


def validate(meta: dict, body: str) -> list[str]:
    errors = []
    name = str(meta.get("name") or "")
    if not NAME_RE.match(name):
        errors.append("name: lowercase letters, digits and hyphens only, 1-64 characters")
    desc = str(meta.get("description") or "").strip()
    if not desc:
        errors.append("description: required - say what the skill does AND when to use it")
    elif len(desc) > 1024:
        errors.append("description: 1024 characters maximum")
    if not body.strip():
        errors.append("the body (instructions after the frontmatter) is empty")
    return errors


# ---------------------------------------------------------------------------
# discovery


def scope_dirs(directory: Optional[str]) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = []
    if directory:
        dirs += [("project", Path(directory) / sub) for sub in PROJECT_SUBDIRS]
    dirs += [("user", USER_DIR), ("builtin", BUILTIN_DIR)]
    return dirs


def _load(scope: str, folder: Path) -> Optional[Skill]:
    md = folder / "SKILL.md"
    if not md.is_file():
        return None
    try:
        meta, body = parse(read_text(md))
    except (OSError, UnicodeDecodeError) as e:
        return Skill(folder.name, "", folder, scope, {}, f"unreadable: {e}")
    name = str(meta.get("name") or folder.name)
    errors = validate({**meta, "name": name}, body)
    return Skill(name, str(meta.get("description") or "").strip(), folder, scope, meta, "; ".join(errors))


def discover(directory: Optional[str]) -> list[Skill]:
    """Every skill, most specific first; shadowed duplicates are dropped."""
    seen: dict[str, Skill] = {}
    for scope, root in scope_dirs(directory):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            if folder.is_dir() and not folder.name.startswith("."):
                sk = _load(scope, folder)
                if sk and sk.name not in seen:
                    seen[sk.name] = sk
    return sorted(seen.values(), key=lambda s: (SCOPES.index(s.scope), s.name))


def available(skill: Skill, cfg: dict) -> bool:
    if skill.requires == "reverse":
        rev = cfg.get("reverse") or {}
        return bool(rev.get("enabled") and rev.get("acknowledged"))
    return True


def active(directory: Optional[str], cfg: Optional[dict] = None) -> list[Skill]:
    cfg = cfg or load_config()
    disabled = set(cfg["skills"].get("disabled") or [])
    return [s for s in discover(directory) if s.name not in disabled and not s.error and available(s, cfg)]


def get(name: str, directory: Optional[str]) -> Optional[Skill]:
    return next((s for s in discover(directory) if s.name == name), None)


def listing(directory: Optional[str]) -> list[dict]:
    cfg = load_config()
    disabled = set(cfg["skills"].get("disabled") or [])
    return [s.summary(s.name not in disabled, available(s, cfg)) for s in discover(directory)]


# ---------------------------------------------------------------------------
# agent integration


def system_section(skills: list[Skill], max_desc: int = 240) -> str:
    usable = [s for s in skills if s.model_invocable]
    if not usable:
        return ""
    lines = ["- " + s.name + ": " + re.sub(r"\s+", " ", s.description)[:max_desc] for s in usable]
    return ("\n\n# Skills\nSkills are expert playbooks. When a task matches a skill's description, load it with the skill "
            "tool BEFORE starting and follow it; read its reference files only when needed.\n" + "\n".join(lines))


def load_for_agent(skill: Skill, ctx: Optional[ToolContext], arguments: str = "") -> str:
    body = skill.body().replace("$ARGUMENTS", arguments or "")
    files = skill.files()
    if ctx is not None and skill.path not in ctx.extra_read_roots:
        ctx.extra_read_roots.append(skill.path)
    out = [f'<skill name="{skill.name}" folder="{posix(skill.path)}">', body.strip()]
    if files:
        out += ["", "Files in this skill (read them with the read tool using the full path when needed):"]
        out += [f"- {posix(skill.path / f)}" for f in files[:60]]
    allowed = _list(skill.meta.get("allowed-tools"))
    if allowed:
        out += ["", f"This skill is designed for these tools: {', '.join(allowed)}."]
    if arguments and "$ARGUMENTS" not in skill.body():
        out += ["", f"ARGUMENTS: {arguments}"]
    out.append("</skill>")
    return "\n".join(out)


def tool_def(skills: list[Skill], on_load=None) -> Optional[ToolDef]:
    usable = {s.name: s for s in skills if s.model_invocable}
    if not usable:
        return None

    async def run(ctx: ToolContext, args: dict) -> ToolResult:
        name = str(args.get("name") or args.get("skill") or "").strip().lstrip("/")
        sk = usable.get(name)
        if not sk:
            raise ToolError(f"Unknown skill '{name}'. Available: {', '.join(usable)}")
        if on_load:
            on_load(sk.name)
        return ToolResult(load_for_agent(sk, ctx, str(args.get("arguments") or "")), sk.name, {"skill": sk.name})

    return ToolDef("skill", "Load a skill (expert instructions for a kind of task) by name. Use it as soon as a task matches a skill listed in the system prompt.",
                   {"type": "object", "properties": {"name": {"type": "string", "enum": sorted(usable)},
                                                     "arguments": {"type": "string", "description": "Optional input for the skill"}},
                    "required": ["name"]}, run, meta={"skill": True})


def expand_command(text: str, directory: Optional[str]) -> Optional[tuple[Skill, str]]:
    """``/skill-name args`` typed by the user -> (skill, prompt for the agent)."""
    m = re.match(r"^/([a-z0-9][a-z0-9-]*)(?:\s+(.*))?$", text.strip(), re.S)
    if not m:
        return None
    sk = next((s for s in active(directory) if s.name == m.group(1) and s.user_invocable), None)
    if not sk:
        return None
    args = (m.group(2) or "").strip()
    prompt = (f"Use the '{sk.name}' skill for this request.\n\n{load_for_agent(sk, None, args)}"
              + (f"\n\nRequest: {args}" if args else ""))
    return sk, prompt


# ---------------------------------------------------------------------------
# editing


def root_for(scope: str, directory: Optional[str]) -> Path:
    if scope == "user":
        return USER_DIR
    if scope == "project":
        if not directory:
            raise ValueError("A project skill needs a project folder")
        return Path(directory) / ".supercode" / "skills"
    raise ValueError("Builtin skills are read-only: duplicate the skill to edit it")


def _safe_rel(rel: str) -> str:
    rel = posix(Path(rel.strip().lstrip("/\\")))
    if not rel or rel.startswith("..") or "/../" in f"/{rel}/" or Path(rel).is_absolute():
        raise ValueError(f"Invalid file path: {rel}")
    return rel


def save(scope: str, name: str, content: str, directory: Optional[str], files: Optional[dict] = None,
         rename_from: Optional[str] = None) -> Skill:
    meta, body = parse(content)
    meta["name"] = meta.get("name") or name
    errors = validate(meta, body)
    if errors:
        raise ValueError("; ".join(errors))
    root = root_for(scope, directory)
    folder = root / str(meta["name"])
    if rename_from and rename_from != meta["name"]:
        old = root / rename_from
        if folder.exists():
            raise ValueError(f"A skill named {meta['name']} already exists here")
        if old.is_dir():
            old.rename(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_text(folder / "SKILL.md", content if content.lstrip().startswith("---") else render(meta, body))
    for rel, text in (files or {}).items():
        target = folder / _safe_rel(rel)
        if text is None:
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, str(text))
    sk = _load(scope, folder)
    assert sk
    return sk


def delete(scope: str, name: str, directory: Optional[str]) -> None:
    folder = root_for(scope, directory) / name
    if not (folder / "SKILL.md").is_file():
        raise FileNotFoundError(name)
    shutil.rmtree(folder)


def duplicate(name: str, directory: Optional[str], scope: str = "user", new_name: Optional[str] = None) -> Skill:
    src = get(name, directory)
    if not src:
        raise FileNotFoundError(name)
    target_name = new_name or src.name
    dest = root_for(scope, directory) / target_name
    if dest.exists():
        raise ValueError(f"{dest} already exists")
    shutil.copytree(src.path, dest)
    if target_name != src.name:
        meta, body = parse(read_text(dest / "SKILL.md"))
        meta["name"] = target_name
        write_text(dest / "SKILL.md", render(meta, body))
    sk = _load(scope, dest)
    assert sk
    return sk


def read_file(skill: Skill, rel: str) -> str:
    p = (skill.path / _safe_rel(rel)).resolve()
    if skill.path.resolve() not in p.parents:
        raise ValueError("outside the skill folder")
    return read_text(p)


def import_folder(source: str, scope: str, directory: Optional[str]) -> list[Skill]:
    """Copy a skill folder (or a folder of skills) into the user / project skills."""
    src = Path(source).expanduser()
    if not src.is_dir():
        raise FileNotFoundError(f"Folder not found: {src}")
    folders = [src] if (src / "SKILL.md").is_file() else [p for p in sorted(src.iterdir()) if (p / "SKILL.md").is_file()]
    if not folders:
        raise ValueError("No SKILL.md found in this folder or its sub-folders")
    out = []
    for f in folders:
        sk = _load(scope, f)
        if not sk or sk.error:
            raise ValueError(f"{f.name}: {sk.error if sk else 'invalid'}")
        dest = root_for(scope, directory) / sk.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(f, dest, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        loaded = _load(scope, dest)
        assert loaded
        out.append(loaded)
    return out


GENERATOR = """You write Agent Skills (SKILL.md files) for DOINg.AI SuperCode, a local coding agent.

Format, exactly:
---
name: lowercase-hyphenated-name (max 64 chars)
description: One or two sentences: WHAT the skill does AND WHEN to use it (trigger words a user would say). Max 1024 chars.
argument-hint: optional, e.g. "<file or feature>"
---

# Title

Then concise, imperative instructions an expert would follow:
- a short "When to use" section and a numbered workflow;
- concrete commands, checklists, file conventions and a definition of done;
- pitfalls to avoid; examples only when they clarify;
- refer to optional reference files as `references/<topic>.md` only if you also provide them.
Keep it under ~400 lines; the agent loads it only when the task matches. Write in English.
Answer with ONLY the SKILL.md content, no code fence, no commentary."""


async def generate(request: str, existing: str = "") -> str:
    from . import llm

    cfg = load_config()
    creator = get("skill-creator", None)
    guide = creator.body()[:6000] if creator else ""
    user = f"Write a skill for this need:\n{request.strip()}"
    if existing.strip():
        user = f"Improve this skill according to the request below. Keep what works.\n\nCurrent SKILL.md:\n{existing}\n\nRequest:\n{request.strip()}"
    res = await llm.chat(cfg["llm"], model=cfg["llm"]["model"], temperature=0.3, messages=[
        {"role": "system", "content": GENERATOR + ("\n\nSkill-writing guide:\n" + guide if guide else "")},
        {"role": "user", "content": user},
    ])
    text = res.text.strip()
    text = re.sub(r"^```(?:markdown|md|yaml)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    if not text.startswith("---"):
        m = re.search(r"^---\s*$", text, re.M)
        text = text[m.start():] if m else text
    return text.strip() + "\n"
