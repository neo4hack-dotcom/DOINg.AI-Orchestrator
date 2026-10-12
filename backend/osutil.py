"""Cross-platform helpers (Windows, macOS, Linux).

- ``read_text`` / ``write_text`` keep line endings byte-exact: Python's text mode
  turns CRLF into LF on read and LF into CRLF on write on Windows, which would
  rewrite whole files on every edit;
- ``posix`` gives forward-slash relative paths on every OS (globs, diffs, the UI);
- ``detect_shell`` picks the shell the agents talk to: bash on macOS/Linux; on
  Windows Git Bash when installed (agents and plans speak bash), else PowerShell;
- ``run_command`` runs a command without asyncio subprocess support (works on any
  event loop, including the selector loop), streams output, honours timeouts and
  cancellation, and kills the whole process tree (``taskkill /T`` on Windows);
- ``atomic_write`` retries ``os.replace`` when Windows briefly locks a file
  (antivirus, indexer).
"""
from __future__ import annotations

import asyncio
import locale
import os
import shutil
import signal
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# files


def read_text(p: Path) -> str:
    with open(p, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def write_text(p: Path, text: str) -> None:
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    write_text(tmp, text)
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def posix(p: Path | str) -> str:
    return str(p).replace("\\", "/")


def lock_key(p: Path) -> str:
    return os.path.normcase(str(p))


# ---------------------------------------------------------------------------
# shell


def _git_bash_candidates() -> list[str]:
    out = []
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            out += [os.path.join(base, "Git", "bin", "bash.exe"), os.path.join(base, "Programs", "Git", "bin", "bash.exe")]
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent  # ...\Git\cmd\git.exe -> ...\Git
        out += [str(root / "bin" / "bash.exe"), str(root / "usr" / "bin" / "bash.exe")]
    found = shutil.which("bash")
    if found and "system32" not in found.lower():  # System32\bash.exe is WSL: different filesystem
        out.append(found)
    return out


@lru_cache(maxsize=8)
def detect_shell(preference: str = "auto") -> dict:
    """{"name": "bash" | "powershell" | "cmd" | "sh", "argv": [...] | None}"""
    pref = (preference or "auto").strip()
    if pref not in ("auto", "bash", "powershell", "cmd", "sh") and Path(pref).exists():
        name = "powershell" if "powershell" in pref.lower() or "pwsh" in pref.lower() else "bash"
        return {"name": name, "argv": [pref, "-c"] if name == "bash" else [pref, "-NoProfile", "-NonInteractive", "-Command"]}
    if not IS_WINDOWS:
        if pref in ("auto", "bash") and Path("/bin/bash").exists():
            return {"name": "bash", "argv": ["/bin/bash", "-c"]}
        return {"name": "sh", "argv": ["/bin/sh", "-c"]}
    if pref in ("auto", "bash"):
        for cand in _git_bash_candidates():
            if os.path.isfile(cand):
                return {"name": "bash", "argv": [cand, "-c"]}
    if pref in ("auto", "bash", "powershell"):
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if ps:
            return {"name": "powershell", "argv": [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]}
    return {"name": "cmd", "argv": None}


def shell_description(preference: str = "auto") -> str:
    sh = detect_shell(preference)
    if sh["name"] == "bash":
        return "bash (Git Bash)" if IS_WINDOWS else "bash"
    if sh["name"] == "powershell":
        return "PowerShell (use PowerShell syntax: Get-ChildItem, Test-Path, `;` between commands)"
    if sh["name"] == "cmd":
        return "cmd.exe (use Windows commands: dir, type, `&&` between commands)"
    return "sh"


# ---------------------------------------------------------------------------
# processes


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _spawn(command: str, cwd: str, env: dict, shell_pref: str, has_stdin: bool) -> subprocess.Popen:
    sh = detect_shell(shell_pref)
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    if sh["name"] == "powershell":
        command = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command
    args = sh["argv"] + [command] if sh["argv"] else command
    return subprocess.Popen(
        args, shell=sh["argv"] is None, cwd=cwd, env=env,
        stdin=subprocess.PIPE if has_stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
    )


async def run_command(command: str, cwd: str, timeout: float, *, stdin: Optional[bytes] = None,
                      env: Optional[dict] = None, shell: str = "auto") -> tuple[int, str, bool]:
    """Returns (exit code, combined output, timed_out). Cancellation kills the process tree."""
    full_env = {**os.environ, "CI": "1", "GIT_PAGER": "cat", "PAGER": "cat", "NO_COLOR": "1", "FORCE_COLOR": "0",
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    proc = _spawn(command, cwd, full_env, shell, stdin is not None)
    chunks: list[bytes] = []

    def pump() -> int:
        if stdin is not None and proc.stdin:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except OSError:
                pass
        assert proc.stdout is not None
        reader = getattr(proc.stdout, "read1", proc.stdout.read)
        while True:
            chunk = reader(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return proc.wait()

    worker = asyncio.ensure_future(asyncio.to_thread(pump))
    try:
        code = await asyncio.wait_for(asyncio.shield(worker), timeout)
        timed_out = False
    except asyncio.TimeoutError:
        kill_tree(proc)
        try:
            await asyncio.wait_for(worker, 15)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        code, timed_out = -1, True
    except asyncio.CancelledError:
        kill_tree(proc)
        raise
    return code, _decode(b"".join(chunks)), timed_out
