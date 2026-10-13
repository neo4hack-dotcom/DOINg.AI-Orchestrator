"""Unit tests for the harness building blocks."""
import shutil

import pytest

from backend import compaction, editing, permissions, planner, repomap
from backend.snapshot import Snapshots
from backend.tools import ToolContext, ToolError, bound_output, t_apply_patch, t_edit, t_multiedit, t_read, t_write


def ctx(ws):
    return ToolContext(directory=ws, session=None, agent="test")


# ---------------------------------------------------------------- editing
def test_edit_unicode_quotes_and_line_numbers():
    src = 'msg = "it\u2019s ok"\nx = 1\n'
    out, n, strategy = editing.apply_edit(src, 'msg = "it\'s ok"', 'msg = "fine"')
    assert out == 'msg = "fine"\nx = 1\n' and strategy == "normalized"
    out, _, strategy = editing.apply_edit("a = 1\nb = 2\n", "     1\ta = 1\n     2\tb = 2", "a = 3\nb = 4")
    assert out == "a = 3\nb = 4\n" and "line-numbers" in strategy


def test_edit_indentation_tolerance_reindents():
    src = "class A:\n    def f(self):\n        return 1\n"
    out, _, strategy = editing.apply_edit(src, "def f(self):\n    return 1", "def f(self):\n    return 2")
    assert strategy == "indentation"
    assert out == "class A:\n    def f(self):\n        return 2\n"


def test_edit_keeps_crlf():
    src = "a\r\nb\r\n"
    out, _, _ = editing.apply_edit(src, "a\nb", "a\nc")
    assert out == "a\r\nc\r\n"


def test_edit_ambiguous_reports_lines():
    with pytest.raises(editing.EditError, match="lines 1, 2"):
        editing.apply_edit("x\nx\n", "x", "y")


async def test_read_before_edit_and_stale_detection(workspace):
    (workspace / "f.py").write_text("a = 1\n")
    c1, c2 = ctx(workspace), ctx(workspace)
    with pytest.raises(ToolError, match="Read f.py before"):
        await t_edit(c1, {"path": "f.py", "old_string": "a = 1", "new_string": "a = 2"})
    await t_read(c1, {"path": "f.py"})
    await t_read(c2, {"path": "f.py"})
    await t_edit(c2, {"path": "f.py", "old_string": "a = 1", "new_string": "a = 2"})  # another worker edits
    with pytest.raises(ToolError, match="modified"):
        await t_edit(c1, {"path": "f.py", "old_string": "a = 2", "new_string": "a = 3"})
    await t_read(c1, {"path": "f.py"})
    await t_edit(c1, {"path": "f.py", "old_string": "a = 2", "new_string": "a = 3"})
    assert (workspace / "f.py").read_text() == "a = 3\n"


async def test_multiedit_is_atomic(workspace):
    (workspace / "m.py").write_text("one\ntwo\n")
    c = ctx(workspace)
    await t_read(c, {"path": "m.py"})
    with pytest.raises(ToolError, match="edit 2 of 2"):
        await t_multiedit(c, {"path": "m.py", "edits": [{"old_string": "one", "new_string": "1"},
                                                         {"old_string": "missing", "new_string": "x"}]})
    assert (workspace / "m.py").read_text() == "one\ntwo\n"
    res = await t_multiedit(c, {"path": "m.py", "edits": [{"old_string": "one", "new_string": "1"},
                                                           {"old_string": "two", "new_string": "2"}]})
    assert (workspace / "m.py").read_text() == "1\n2\n" and res.metadata["additions"] == 2


async def test_apply_patch_add_update_move_delete(workspace):
    (workspace / "app.py").write_text("def greet():\n    print('Hi')\n    return 1\n")
    (workspace / "old.txt").write_text("bye\n")
    c = ctx(workspace)
    await t_read(c, {"path": "app.py"})
    await t_read(c, {"path": "old.txt"})
    patch = """*** Begin Patch
*** Add File: new/hello.txt
+Hello
+world
*** Update File: app.py
*** Move to: main.py
@@ def greet():
-    print('Hi')
+    print('Hello')
*** Delete File: old.txt
*** End Patch"""
    res = await t_apply_patch(c, {"patch": patch})
    assert (workspace / "new/hello.txt").read_text() == "Hello\nworld\n"
    assert not (workspace / "app.py").exists() and "print('Hello')" in (workspace / "main.py").read_text()
    assert not (workspace / "old.txt").exists()
    assert "A new/hello.txt" in res.output and set(c.changes) >= {"new/hello.txt", "app.py", "main.py", "old.txt"}


async def test_apply_patch_is_all_or_nothing(workspace):
    (workspace / "a.py").write_text("x = 1\n")
    c = ctx(workspace)
    await t_read(c, {"path": "a.py"})
    bad = "*** Begin Patch\n*** Add File: b.py\n+ok\n*** Update File: a.py\n-nope\n+y\n*** End Patch"
    with pytest.raises(ToolError, match="does not match"):
        await t_apply_patch(c, {"patch": bad})
    assert not (workspace / "b.py").exists()


def test_bound_output_spills():
    text = "line\n" * 5000
    out, spill = bound_output(text, 1000)
    assert spill and len(out) < 1500 and "Full output saved" in out
    assert open(spill).read() == text


# ---------------------------------------------------------------- permissions
RULES = [{"pattern": "git status*", "action": "allow"}, {"pattern": "ls*", "action": "allow"},
         {"pattern": "git push*", "action": "deny"}, {"pattern": "sudo *", "action": "deny"}]


def test_bash_rules():
    assert permissions.bash_decision("git status", RULES, "ask", []) == "allow"
    assert permissions.bash_decision("ls -la && git status --short", RULES, "ask", []) == "allow"
    assert permissions.bash_decision("ls && rm -rf build", RULES, "ask", []) == "ask"
    assert permissions.bash_decision("ls; git push origin main", RULES, "allow", []) == "deny"
    assert permissions.bash_decision("npm test", RULES, "ask", ["npm test*"]) == "allow"
    assert permissions.grant_pattern("npm run build -- --prod") == "npm run*"
    assert permissions.grant_pattern("pytest -q tests/") == "pytest*"


# ---------------------------------------------------------------- planner
def T(title, **kw):
    base = {"title": title, "brief": title, "size": "M", "complexity": 3}
    base.update(kw)
    return planner.clean_task(base, 0)


def test_clean_task_tier_floor():
    t = planner.clean_task({"title": "hard", "tier": "light", "complexity": 5}, 0)
    assert t["tier"] == "careful"  # never more than one level below heavy
    assert planner.clean_task({"title": "x", "complexity": 1}, 0)["tier"] == "light"


def test_assemble_modules_and_dependencies():
    mods = planner.clean_modules([
        {"id": "ui", "dependsOn": ["api"]},
        {"id": "api", "dependsOn": ["db"]},
        {"id": "db"},
    ])
    assert [m["id"] for m in mods] == ["db", "api", "ui"]
    tasks = planner.assemble([
        (mods[0], [T("schema"), T("seed", after=[1])]),
        (mods[1], [T("routes"), T("auth")]),
        (mods[2], [T("pages", after=["missing", 5])]),
    ])
    by = {t["title"]: t for t in tasks}
    assert by["seed"]["after"] == [1]
    # api entry tasks wait for db's sink (seed), not its first task
    assert by["routes"]["after"] == [2] and by["auth"]["after"] == [2]
    # invalid / forward references are dropped; ui waits for both api sinks
    assert by["pages"]["after"] == [3, 4]


def test_merge_small_chain():
    tasks = planner.assemble([(None, [T("a", size="S", complexity=1), T("b", size="S", complexity=2, after=[1]),
                                      T("c", after=[2])])])
    merged = planner.merge_small(tasks)
    assert len(merged) == 2 and merged[0]["title"] == "a + b" and merged[1]["after"] == [1]


def test_conflicts_serialize_and_schedule():
    tasks = planner.assemble([(None, [T("a", files=["src/api/"]), T("b", files=["src/api/users.py"]),
                                      T("c", files=["docs/x.md"]), T("d", after=[2, 3])])])
    tasks, stats = planner.finalize(tasks, merge=False)
    by = {t["title"]: t for t in tasks}
    assert by["b"]["after"] == [1]       # shares src/api with a
    assert by["c"]["after"] == []        # independent: runs in parallel
    assert by["a"]["wave"] == 1 and by["b"]["wave"] == 2 and by["c"]["wave"] == 1
    assert by["a"]["priority"] > by["c"]["priority"]  # a is on the longer chain
    assert stats["conflictEdges"] == 1 and stats["waves"] == 3 and stats["maxWidth"] == 2


# ---------------------------------------------------------------- snapshots
@pytest.mark.skipif(not shutil.which("git"), reason="git missing")
async def test_snapshot_track_changed_restore(workspace):
    snaps = Snapshots(str(workspace))
    base = await snaps.track()
    (workspace / "README.md").write_text("# changed\n")
    (workspace / "new.txt").write_text("new\n")
    files = {f["path"]: f["status"] for f in await snaps.changed(base)}
    assert files == {"README.md": "modified", "new.txt": "added"}
    assert "+# changed" in await snaps.diff(base, path="README.md")
    await snaps.restore(base, ["README.md"])
    assert (workspace / "README.md").read_text() == "# demo\n" and (workspace / "new.txt").exists()
    await snaps.restore(base)
    assert not (workspace / "new.txt").exists()


# ---------------------------------------------------------------- repomap
def test_repomap_ranks_referenced_files(tmp_path):
    (tmp_path / "core.py").write_text("def helper(x):\n    return x\n\nclass Store:\n    def save(self, item):\n        pass\n")
    (tmp_path / "a.py").write_text("from core import helper, Store\nhelper(1)\nStore()\n")
    (tmp_path / "b.ts").write_text("export function render(props: Props) {}\nexport interface Props { a: string }\n")
    out = repomap.build(str(tmp_path), 2000)
    assert out.startswith("core.py") and "def helper(x)" in out and "class Store:" in out
    assert "export function render(props: Props)" in out
    assert repomap.build(str(tmp_path), 2000, focus="b.ts").startswith("b.ts")


# ---------------------------------------------------------------- compaction
def test_prune_keeps_recent():
    history = [{"role": "user", "content": "brief"}]
    for i in range(10):
        history.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "name": "read", "arguments": {}}]})
        history.append({"role": "tool", "tool_call_id": str(i), "name": "read", "content": "x" * 4000})
    cleared = compaction.prune(history, 8000, {"pruneAt": 0.5, "keepRecentShare": 0.3})
    assert cleared > 0
    assert history[-1]["content"] == "x" * 4000 and history[2]["content"] == compaction.CLEARED


async def test_summarize_pins_brief(fake_server):
    history = [{"role": "user", "content": "THE BRIEF"}]
    for i in range(12):
        history.append({"role": "assistant", "content": f"step {i}", "tool_calls": [{"id": str(i), "name": "read", "arguments": {}}]})
        history.append({"role": "tool", "tool_call_id": str(i), "name": "read", "content": "y" * 3000})
    cfg = {"provider": "ollama", "baseUrl": fake_server, "timeout": 30}
    stats = await compaction.summarize(cfg, "m", history, 10000, {"keepRecentShare": 0.3}, pinned=1)
    assert stats and stats["after"] < stats["before"]
    assert history[0]["role"] == "user" and history[0]["content"].startswith("THE BRIEF")
    assert "<conversation-checkpoint>" in history[0]["content"] and history[1]["role"] == "assistant"
    # a second compaction replaces the checkpoint instead of stacking it
    for i in range(12):
        history.append({"role": "assistant", "content": "more", "tool_calls": [{"id": f"z{i}", "name": "read", "arguments": {}}]})
        history.append({"role": "tool", "tool_call_id": f"z{i}", "name": "read", "content": "z" * 3000})
    await compaction.summarize(cfg, "m", history, 10000, {"keepRecentShare": 0.3}, pinned=1)
    assert history[0]["content"].count("<conversation-checkpoint>") == 1
