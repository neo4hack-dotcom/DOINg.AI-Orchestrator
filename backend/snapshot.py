"""Workspace snapshots in a shadow git repository (OpenCode's snapshot/revert).

The shadow repo lives in ``data/snapshots/<hash>``, never in the user's ``.git``:
``track()`` records the whole working tree as a git tree object, ``diff`` /
``changed`` compare two snapshots (or a snapshot and the live tree), ``restore``
puts files back. Without git installed every call is a no-op.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

EXCLUDES = ["node_modules/", ".venv/", "venv/", "__pycache__/", "dist/", "build/", ".next/",
            ".pytest_cache/", ".mypy_cache/", "*.pyc", ".DS_Store", ".git/", "target/", ".gradle/"]
MAX_FILE_BYTES = 5_000_000

_locks: dict[str, asyncio.Lock] = {}


def available() -> bool:
    return shutil.which("git") is not None


class Snapshots:
    def __init__(self, directory: str):
        self.directory = str(Path(directory).resolve())
        key = hashlib.sha1(self.directory.encode()).hexdigest()[:16]
        self.git_dir = DATA_DIR / "snapshots" / key
        self.lock = _locks.setdefault(key, asyncio.Lock())

    def _run(self, *args: str, input_: Optional[bytes] = None, check: bool = True) -> str:
        env = {**os.environ, "GIT_DIR": str(self.git_dir), "GIT_WORK_TREE": self.directory,
               "GIT_AUTHOR_NAME": "orchestrator", "GIT_AUTHOR_EMAIL": "orchestrator@local",
               "GIT_COMMITTER_NAME": "orchestrator", "GIT_COMMITTER_EMAIL": "orchestrator@local"}
        res = subprocess.run(["git", *args], cwd=self.directory, env=env, input=input_,
                             capture_output=True, timeout=120)
        if check and res.returncode != 0:
            raise RuntimeError(res.stderr.decode("utf-8", "replace")[:500])
        return res.stdout.decode("utf-8", "replace")

    def _init(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        self.git_dir.mkdir(parents=True, exist_ok=True)
        self._run("init", "-q")
        self._run("config", "core.autocrlf", "false")
        self._run("config", "core.quotepath", "false")
        info = self.git_dir / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text("\n".join(EXCLUDES) + "\n", encoding="utf-8")

    def _track_sync(self) -> str:
        self._init()
        self._run("add", "-A", "--", ".")
        return self._run("write-tree").strip()

    async def track(self) -> Optional[str]:
        if not available() or not Path(self.directory).is_dir():
            return None
        async with self.lock:
            try:
                return await asyncio.to_thread(self._track_sync)
            except (RuntimeError, subprocess.TimeoutExpired, OSError):
                return None

    async def changed(self, base: str, to: Optional[str] = None) -> list[dict]:
        """Files changed between ``base`` and ``to`` (default: the live tree)."""
        if not base or not available():
            return []
        to = to or await self.track()
        if not to:
            return []
        async with self.lock:
            out = await asyncio.to_thread(self._run, "diff", "--numstat", "--no-renames", base, to, check=False)
            status = await asyncio.to_thread(self._run, "diff", "--name-status", "--no-renames", base, to, check=False)
        kinds = {}
        for line in status.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                kinds[parts[1]] = {"A": "added", "D": "deleted"}.get(parts[0][:1], "modified")
        files = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                add, rem, path = parts
                files.append({"path": path, "additions": int(add) if add.isdigit() else 0,
                              "deletions": int(rem) if rem.isdigit() else 0, "status": kinds.get(path, "modified")})
        return files

    async def diff(self, base: str, to: Optional[str] = None, path: Optional[str] = None) -> str:
        if not base or not available():
            return ""
        to = to or await self.track()
        if not to:
            return ""
        args = ["diff", "--no-renames", "--no-color", base, to]
        if path:
            args += ["--", path]
        async with self.lock:
            return await asyncio.to_thread(self._run, *args, check=False)

    def _restore_sync(self, tree: str, paths: Optional[list[str]]) -> list[str]:
        current = self._track_sync()
        listed = self._run("diff", "--name-status", "--no-renames", tree, current, check=False)
        touched = []
        for line in listed.splitlines():
            kind, _, path = line.partition("\t")
            if paths is not None and path not in paths:
                continue
            target = Path(self.directory) / path
            if kind.startswith("A"):
                # Created after the snapshot: remove it.
                if target.exists():
                    target.unlink()
            else:
                blob = self._run("rev-parse", f"{tree}:{path}").strip()
                data = subprocess.run(["git", "cat-file", "blob", blob], capture_output=True,
                                      env={**os.environ, "GIT_DIR": str(self.git_dir)}).stdout
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            touched.append(path)
        return touched

    async def restore(self, tree: str, paths: Optional[list[str]] = None) -> list[str]:
        """Bring the working tree (or only ``paths``) back to ``tree``. Returns touched paths."""
        if not tree or not available():
            return []
        async with self.lock:
            return await asyncio.to_thread(self._restore_sync, tree, paths)
