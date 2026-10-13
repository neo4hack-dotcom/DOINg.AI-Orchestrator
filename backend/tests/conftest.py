import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["ORCH_DATA_DIR"] = tempfile.mkdtemp(prefix="orch-data-")

import uvicorn  # noqa: E402

import fake_llm  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fake_server():
    port = _free_port()
    fake_llm.STATE["delay"] = 0
    server = uvicorn.Server(uvicorn.Config(fake_llm.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("# demo\n")
    return ws


# ---------------------------------------------------------------- fake Bitbucket (Server + Cloud APIs)
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

BB = {"calls": [], "existing": False}
bb_app = FastAPI()


@bb_app.get("/rest/api/1.0/projects/{key}/repos/{slug}")
async def bb_server_repo(key: str, slug: str, request: Request):
    BB["calls"].append(("GET repo", key, slug, request.headers.get("authorization")))
    return {"slug": slug, "name": slug.upper(), "project": {"key": key}}


@bb_app.get("/rest/api/1.0/projects/{key}/repos/{slug}/default-branch")
async def bb_server_default(key: str, slug: str):
    return {"id": "refs/heads/develop", "displayId": "develop"}


@bb_app.post("/rest/api/1.0/projects/{key}/repos/{slug}/pull-requests")
async def bb_server_pr(key: str, slug: str, request: Request):
    body = await request.json()
    BB["calls"].append(("POST pr", key, slug, request.headers.get("authorization"), body))
    if BB["existing"]:
        return JSONResponse({"errors": [{"message": "Only one pull request may be open for a given source and target branch"}]}, 409)
    return {"id": 7, "title": body["title"], "links": {"self": [{"href": f"https://bb.local/projects/{key}/repos/{slug}/pull-requests/7"}]}}


@bb_app.get("/rest/api/1.0/projects/{key}/repos/{slug}/pull-requests")
async def bb_server_list(key: str, slug: str):
    return {"values": [{"id": 3, "links": {"self": [{"href": "https://bb.local/pr/3"}]}}]}


@bb_app.get("/2.0/repositories/{ws}/{slug}")
async def bb_cloud_repo(ws: str, slug: str):
    return {"full_name": f"{ws}/{slug}", "mainbranch": {"name": "main"}}


@bb_app.post("/2.0/repositories/{ws}/{slug}/pullrequests")
async def bb_cloud_pr(ws: str, slug: str, request: Request):
    body = await request.json()
    BB["calls"].append(("POST cloud pr", ws, slug, request.headers.get("authorization"), body))
    return {"id": 12, "links": {"html": {"href": f"https://bitbucket.org/{ws}/{slug}/pull-requests/12"}}}


@pytest.fixture(scope="session")
def bitbucket_server():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(bb_app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
