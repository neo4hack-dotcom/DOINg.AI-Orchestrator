"""Windows / cross-platform behaviour: line endings, paths, processes, shells."""
import asyncio
import sys
import time

import pytest

from backend import osutil
from backend.tools import ToolContext, t_edit, t_glob, t_read, t_write


def ctx(ws):
    return ToolContext(directory=ws, session=None, agent="test")


async def test_crlf_files_stay_crlf(workspace):
    (workspace / "win.txt").write_bytes(b"line one\r\nline two\r\n")
    c = ctx(workspace)
    out = await t_read(c, {"path": "win.txt"})
    assert "line two" in out.output
    await t_edit(c, {"path": "win.txt", "old_string": "line two", "new_string": "line 2"})
    assert (workspace / "win.txt").read_bytes() == b"line one\r\nline 2\r\n"
    await t_write(c, {"path": "win.txt", "content": "a\nb\n"})  # LF content into a CRLF file keeps CRLF
    assert (workspace / "win.txt").read_bytes() == b"a\r\nb\r\n"
    await t_write(c, {"path": "new.txt", "content": "x\ny\n"})  # new files are written byte-exact (LF)
    assert (workspace / "new.txt").read_bytes() == b"x\ny\n"


async def test_paths_are_forward_slashes(workspace):
    c = ctx(workspace)
    await t_write(c, {"path": "src/deep/mod.py", "content": "x = 1\n"})
    assert list(c.changes) == ["src/deep/mod.py"]
    assert "src/deep/mod.py" in (await t_glob(c, {"pattern": "src/*/*.py"})).output


def test_shell_detection():
    sh = osutil.detect_shell("auto")
    assert sh["name"] in ("bash", "sh", "powershell", "cmd")
    assert osutil.shell_description("auto")


async def test_run_command_output_exit_and_stdin(tmp_path):
    code, out, timed_out = await osutil.run_command("echo hello && exit 3", str(tmp_path), 30)
    assert code == 3 and "hello" in out and not timed_out
    py = sys.executable.replace("\\", "/")
    code, out, _ = await osutil.run_command(f'"{py}" -c "import sys; print(sys.stdin.read().upper())"', str(tmp_path), 30, stdin=b"abc")
    assert code == 0 and "ABC" in out


async def test_run_command_timeout_kills_tree(tmp_path):
    py = sys.executable.replace("\\", "/")
    start = time.monotonic()
    code, _, timed_out = await osutil.run_command(f'"{py}" -c "import time; time.sleep(30)"', str(tmp_path), 1)
    assert timed_out and code == -1 and time.monotonic() - start < 20


async def test_run_command_cancel_kills(tmp_path):
    py = sys.executable.replace("\\", "/")
    task = asyncio.ensure_future(osutil.run_command(f'"{py}" -c "import time; time.sleep(30)"', str(tmp_path), 60))
    await asyncio.sleep(0.5)
    task.cancel()
    start = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - start < 10


def test_atomic_write(tmp_path):
    p = tmp_path / "x.json"
    osutil.atomic_write(p, "one")
    osutil.atomic_write(p, "two\n")
    assert p.read_bytes() == b"two\n" and not (tmp_path / "x.json.tmp").exists()
