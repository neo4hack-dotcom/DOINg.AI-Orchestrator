from pathlib import Path

import pytest

from backend import llm
from backend.tools import ToolContext, ToolError, t_edit, t_glob, t_grep, t_read, t_write


def ctx(ws: Path) -> ToolContext:
    return ToolContext(directory=ws, session=None, agent="test")


async def test_write_read_edit_roundtrip(workspace):
    c = ctx(workspace)
    res = await t_write(c, {"path": "src/a.py", "content": "x = 1\ny = 2\n"})
    assert res.metadata["additions"] == 2
    assert c.changes == {"src/a.py": None}
    res = await t_edit(c, {"path": "src/a.py", "old_string": "y = 2", "new_string": "y = 3"})
    assert "+y = 3" in res.metadata["diff"]
    out = await t_read(c, {"path": "src/a.py"})
    assert "y = 3" in out.output and out.output.splitlines()[0].strip().startswith("1")
    assert c.changes == {"src/a.py": None}  # first "before" is kept


async def test_edit_requires_unique_match(workspace):
    c = ctx(workspace)
    await t_write(c, {"path": "b.txt", "content": "a\na\n"})
    with pytest.raises(ToolError):
        await t_edit(c, {"path": "b.txt", "old_string": "a", "new_string": "b"})
    await t_edit(c, {"path": "b.txt", "old_string": "a", "new_string": "b", "replace_all": True})
    assert (workspace / "b.txt").read_text() == "b\nb\n"


async def test_edit_tolerates_trailing_whitespace(workspace):
    c = ctx(workspace)
    (workspace / "c.py").write_text("def f():   \n    return 1\n")
    await t_read(c, {"path": "c.py"})
    await t_edit(c, {"path": "c.py", "old_string": "def f():\n    return 1", "new_string": "def f():\n    return 2"})
    assert "return 2" in (workspace / "c.py").read_text()


async def test_sandbox(workspace):
    with pytest.raises(ToolError):
        await t_read(ctx(workspace), {"path": "../outside.txt"})
    with pytest.raises(ToolError):
        await t_write(ctx(workspace), {"path": "/etc/passwd_copy", "content": "x"})


async def test_glob_and_grep(workspace):
    c = ctx(workspace)
    await t_write(c, {"path": "pkg/mod.py", "content": "def hello():\n    pass\n"})
    assert "pkg/mod.py" in (await t_glob(c, {"pattern": "**/*.py"})).output
    assert "pkg/mod.py:1" in (await t_grep(c, {"pattern": r"def \w+", "include": "*.py"})).output


def test_parse_text_tool_calls():
    text = 'Let me read.\n<tool_call>\n{"name": "read", "arguments": {"path": "a.py"},}\n</tool_call>'
    clean, calls = llm.parse_text_tool_calls(text, {"read"})
    assert clean == "Let me read."
    assert calls[0].name == "read" and calls[0].arguments == {"path": "a.py"}
    # unknown tools are ignored
    assert llm.parse_text_tool_calls('<tool_call>{"name": "rm", "arguments": {}}</tool_call>', {"read"})[1] == []
    # fenced json fallback
    _, calls = llm.parse_text_tool_calls('```json\n{"name": "list", "arguments": {}}\n```', {"list"})
    assert calls and calls[0].name == "list"


def test_think_splitter_handles_split_tags():
    s = llm.ThinkSplitter()
    out = []
    for chunk in ["<thi", "nk>reason", "ing</th", "ink>answer"]:
        out += s.feed(chunk)
    out += s.flush()
    text = "".join(p for k, p in out if k == "text")
    reasoning = "".join(p for k, p in out if k == "reasoning")
    assert text == "answer" and reasoning == "reasoning"


def test_flatten_for_text_protocol():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "list", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "1", "name": "list", "content": "a.py"},
    ]
    flat = llm.flatten_for_text_protocol(msgs)
    assert flat[1]["content"].startswith("<tool_call>")
    assert flat[2]["role"] == "user" and "<tool_result" in flat[2]["content"]
