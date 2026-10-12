"""Context management for long runs: pruning (micro-compaction) and checkpoint summaries.

1. ``prune``: old tool results are replaced by a short marker once the history passes
   ``context.pruneAt`` of the window; the most recent ``keepRecentShare`` stays verbatim
   (OpenClaude microCompact / OpenCode prune).
2. ``summarize``: above ``context.compactAt`` everything but the pinned head (the
   user's request or the worker's brief) and the recent tail is replaced by a
   structured checkpoint (OpenCode's compaction template: objective, requirements,
   decisions, work state, next move, relevant files).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import llm

CHECKPOINT_RE = re.compile(r"\n*<conversation-checkpoint>\n(.*?)\n</conversation-checkpoint>\n[^\n]*", re.S)
CLEARED = "[Old tool result cleared to save context. Run the tool again if you need it.]"
MIN_PRUNE_CHARS = 400

SUMMARY_SYSTEM = "You write compact, structured progress checkpoints so another agent can resume coding work. Output Markdown only."

SUMMARY_TEMPLATE = """Summarize the conversation below into a checkpoint another agent will use to resume the work.
Use exactly these sections (omit none, write "(none)" when empty):

## Objective
- one or two sentences.
## Requirements
- constraints and preferences stated by the user or the brief.
## Decisions
- decisions already made and why.
## Work State
### Completed
### Active
### Blocked
## Next Move
1. ordered next actions.
## Relevant Files
- `path`: why it matters (max 15).
## Important Context
- facts the next agent cannot easily recover (exact error strings, commands, identifiers).

Rules: terse single-line bullets; keep exact paths, symbols, commands and errors; do not continue the task; no preamble.
{update}
CONVERSATION:
{transcript}"""


def _render(messages: list[dict], limit: int = 40_000) -> str:
    out = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append(f"TOOL {m.get('name', '')}: {(m.get('content') or '')[:600]}")
            continue
        text = m.get("content") or ""
        calls = m.get("tool_calls") or []
        if calls:
            text += " " + " ".join(
                f"[called {c['name']}({json.dumps(c.get('arguments', {}), ensure_ascii=False)[:160]})]" for c in calls
            )
        out.append(f"{role.upper()}: {text.strip()}")
    joined = "\n".join(out)
    return joined if len(joined) <= limit else "...\n" + joined[-limit:]


def prune(history: list[dict], window: int, cfg: dict) -> int:
    """Clear old tool outputs in place. Returns how many were cleared."""
    total = llm.messages_tokens(history)
    if total <= window * float(cfg.get("pruneAt", 0.5)):
        return 0
    keep = window * float(cfg.get("keepRecentShare", 0.3))
    acc = 0
    protected_from = len(history)
    for i in range(len(history) - 1, -1, -1):
        acc += llm.messages_tokens([history[i]])
        if acc > keep:
            protected_from = i + 1
            break
    else:
        return 0
    cleared = 0
    for m in history[:protected_from]:
        if m["role"] == "tool" and len(m.get("content") or "") > MIN_PRUNE_CHARS and m["content"] != CLEARED:
            m["content"] = CLEARED
            cleared += 1
    return cleared


def needs_summary(history: list[dict], window: int, cfg: dict) -> bool:
    return llm.messages_tokens(history) > window * float(cfg.get("compactAt", 0.75))


def _cut_index(history: list[dict], pinned: int, keep_tokens: float) -> Optional[int]:
    acc = 0
    best = None
    for i in range(len(history) - 1, pinned, -1):
        acc += llm.messages_tokens([history[i]])
        if history[i]["role"] == "assistant":
            if acc <= keep_tokens:
                best = i
            elif best is None:
                best = i  # even the last turn is big: keep at least it
                break
            else:
                break
    if best is None or best <= pinned:
        return None
    return best


async def summarize(llm_cfg: dict, model: str, history: list[dict], window: int, cfg: dict, pinned: int) -> Optional[dict]:
    """Replace the middle of ``history`` (in place) with a checkpoint. Returns stats or None."""
    keep = window * float(cfg.get("keepRecentShare", 0.3))
    cut = _cut_index(history, pinned, keep)
    if cut is None:
        return None
    before = llm.messages_tokens(history)
    head, middle, tail = history[:pinned], history[pinned:cut], history[cut:]
    if not middle:
        return None
    previous = ""
    for m in head + middle:
        found = CHECKPOINT_RE.search(m.get("content") or "")
        if found:
            previous = found.group(1)
    if head and head[-1]["role"] == "user":
        head = head[:-1] + [{**head[-1], "content": CHECKPOINT_RE.sub("", head[-1].get("content") or "")}]
    update = (f"EARLIER CHECKPOINT (merge it; newer history wins; move finished work to Completed):\n{previous}\n"
              if previous else "")
    prompt = SUMMARY_TEMPLATE.format(update=update, transcript=_render(middle))
    res = await llm.chat(llm_cfg, model=model, temperature=0,
                         messages=[{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": prompt}])
    summary = res.text.strip()
    if not summary:
        return None
    block = f"<conversation-checkpoint>\n{summary}\n</conversation-checkpoint>\nContinue the work from this checkpoint without repeating completed steps."
    if head and head[-1]["role"] == "user":
        head = head[:-1] + [{**head[-1], "content": (head[-1].get("content") or "") + "\n\n" + block}]
        new = head + tail
    else:
        new = head + [{"role": "user", "content": block}] + tail
    history[:] = new
    return {"before": before, "after": llm.messages_tokens(history), "summary": summary,
            "tokens": res.prompt_tokens + res.completion_tokens}
