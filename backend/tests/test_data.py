"""Data analysis add-in: result parsing, local SQLite / CSV / XLSX sources, MCP database server, views,
profile, exports, write confirmation, agent tools."""
import asyncio
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

import fake_llm
from backend import data
from backend.config import update_config
from backend.main import app
from backend.mcp_client import manager
from backend.session import store

HERE = Path(__file__).resolve().parent
SQLITE_MCP = str(HERE / "mcp_sqlite_server.py")


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, country TEXT, amount REAL, day TEXT)")
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", [
        (1, "France", 120.5, "2026-01-02"), (2, "Spain", 80.0, "2026-01-03"), (3, "France", 42.0, "2026-01-03"),
        (4, "Italy", None, "2026-01-04"), (5, "France", 300.0, "2026-01-05"), (6, "Spain", 15.25, "2026-01-05")])
    conn.commit()
    conn.close()


def configure(fake_server, workspace, mode="ask", servers=None):
    def mutate(cfg):
        cfg["llm"].update(provider="openai_compatible", baseUrl=fake_server, model="fake-coder:7b", toolMode="auto")
        cfg["workspace"] = str(workspace)
        cfg["permissionMode"] = mode
        cfg["permissions"].update(edit="allow", bash="ask")
        cfg["mcp"]["servers"] = servers or {}
        cfg["mcp"]["deferThreshold"] = 50
        cfg["data"].update(enabled=True, maxRows=5000)
    update_config(mutate)


async def idle(sid, timeout=20.0):
    for _ in range(int(timeout / 0.05)):
        if not store.get(sid).busy:
            return store.get(sid)
        await asyncio.sleep(0.05)
    raise AssertionError("busy")


# ---------------------------------------------------------------------------
# parsing


@pytest.mark.parametrize("raw,cols,first", [
    ('[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]', ["a", "b"], [1, "x"]),
    ("[{'id': 1, 'name': 'Alice'}, {'id': 2, 'name': None}]", ["id", "name"], [1, "Alice"]),
    ('{"columns": ["n", "v"], "rows": [[1, 2], [3, 4]]}', ["n", "v"], [1, 2]),
    ('{"n": [1, 2], "v": ["a", "b"]}', ["n", "v"], [1, "a"]),
    ("Results:\n[{\"k\": \"v\"}]", ["k"], ["v"]),
    ("a,b\n1,2\n3,4\n", ["a", "b"], ["1", "2"]),
    ("| a | b |\n|---|---|\n| 1 | x |\n", ["a", "b"], ["1", "x"]),
    (" id | name\n----+------\n  1 | Bob\n(1 row)", ["id", "name"], ["1", "Bob"]),
])
def test_parse_table_shapes(raw, cols, first):
    c, rows = data.parse_table(raw)
    assert c == cols and rows[0] == first


def test_normalize_and_write_detection():
    names, types, rows = data.normalize(["a", "a", "d"], [["1", "2.5", "2026-01-01"], ["3", None, "2026-02-01"]])
    assert names == ["a", "a_2", "d"] and types == ["integer", "number", "date"] and rows[0][:2] == [1, 2.5]
    for sql in ("SELECT 1", "with x as (select 1) select * from x", "  -- c\nselect * from t", "PRAGMA table_info(t)", "EXPLAIN SELECT 1"):
        assert not data.is_write(sql), sql
    for sql in ("DELETE FROM t", "update t set a=1", "select 1; drop table t", "PRAGMA journal_mode=WAL",
                "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "CREATE TABLE x(a)", "SELECT * INTO backup FROM t",
                "SELECT * FROM users INTO OUTFILE '/tmp/u.csv'", "ATTACH DATABASE 'x.db' AS x"):
        assert data.is_write(sql), sql


# ---------------------------------------------------------------------------
# local sources through the API


async def test_local_sqlite_query_view_profile_export(client, fake_server, workspace):
    configure(fake_server, workspace)
    make_db(workspace / "shop.db")
    (workspace / "people.csv").write_text("name,age,city\nAnn,31,Paris\nBen,,Lyon\nCleo,25,Paris\n=cmd|' /C calc'!A0,40,Nice\n")
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    src = (await client.get(f"/api/sessions/{sid}/data/sources")).json()["sources"]
    ids = {s["id"] for s in src}
    assert "sqlite:shop.db" in ids and "file:people.csv" in ids
    assert [t["name"] for t in (await client.get(f"/api/sessions/{sid}/data/tables", params={"source": "sqlite:shop.db"})).json()] == ["orders"]
    cols = (await client.get(f"/api/sessions/{sid}/data/columns", params={"source": "sqlite:shop.db", "table": "orders"})).json()
    assert [c["name"] for c in cols] == ["id", "country", "amount", "day"]
    r = await client.post(f"/api/sessions/{sid}/data/query", json={"source": "sqlite:shop.db", "sql": "SELECT * FROM orders", "title": "orders"})
    page = r.json()
    assert page["total"] == 6 and page["types"] == ["integer", "text", "number", "date"]
    ds = page["dataset"]["id"]
    view = {"filters": [{"column": "country", "op": "eq", "value": "france"}], "sort": [{"column": "amount", "desc": True}], "hidden": ["day"]}
    page = (await client.put(f"/api/sessions/{sid}/data/datasets/{ds}/view", json={"view": view})).json()
    assert page["filtered"] == 3 and [r[2] for r in page["rows"]] == [300.0, 120.5, 42.0]
    prof = (await client.get(f"/api/sessions/{sid}/data/datasets/{ds}/profile")).json()
    amount = next(c for c in prof["columns"] if c["column"] == "amount")
    assert prof["rows"] == 3 and amount["max"] == 300.0 and amount["mean"] == pytest.approx(154.1666, rel=1e-3)
    agg = (await client.get(f"/api/sessions/{sid}/data/datasets/{ds}/aggregate", params={"x": "country", "y": "amount", "agg": "sum"})).json()
    assert agg["points"] == [{"x": "France", "y": 462.5}]
    csv_bytes = (await client.get(f"/api/sessions/{sid}/data/datasets/{ds}/export", params={"format": "csv"})).content
    assert csv_bytes.startswith(b"\xef\xbb\xbf") and b"day" not in csv_bytes and b"France,300.0" in csv_bytes
    xlsx = (await client.get(f"/api/sessions/{sid}/data/datasets/{ds}/export", params={"format": "xlsx", "scope": "all"})).content
    z = zipfile.ZipFile(io.BytesIO(xlsx))
    assert "xl/worksheets/sheet1.xml" in z.namelist() and b"Italy" in z.read("xl/worksheets/sheet1.xml")
    cols, rows = data._xlsx_rows(_write(workspace / "back.xlsx", xlsx), 100)[0], data._xlsx_rows(workspace / "back.xlsx", 100)[1:]
    assert cols == ["id", "country", "amount", "day"] and len(rows) == 6 and rows[0][2] == 120.5
    # CSV files: SQL over the file, formula injection neutralized on export
    page = (await client.post(f"/api/sessions/{sid}/data/query", json={"source": "file:people.csv", "sql": "SELECT city, count(*) n FROM people GROUP BY city ORDER BY n DESC"})).json()
    assert page["rows"][0] == ["Paris", 2]
    page = (await client.post(f"/api/sessions/{sid}/data/open-file", json={"path": "people.csv"})).json()
    assert page["total"] == 4
    out = (await client.get(f"/api/sessions/{sid}/data/datasets/{page['dataset']['id']}/export", params={"format": "csv"})).content.decode("utf-8-sig")
    assert "'=cmd|' /C calc'!A0" in out
    # XLSX import
    page = (await client.post(f"/api/sessions/{sid}/data/open-file", json={"path": "back.xlsx"})).json()
    assert page["total"] == 6 and page["columns"][1] == "country"


def _write(p: Path, b: bytes) -> Path:
    p.write_bytes(b)
    return p


async def test_writes_need_confirmation_unless_auto(client, fake_server, workspace):
    configure(fake_server, workspace, mode="ask")
    make_db(workspace / "shop.db")
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    r = await client.post(f"/api/sessions/{sid}/data/query", json={"source": "sqlite:shop.db", "sql": "DELETE FROM orders WHERE id = 1"})
    assert r.status_code == 409 and r.json()["detail"]["needsConfirmation"]
    r = await client.post(f"/api/sessions/{sid}/data/query", json={"source": "sqlite:shop.db", "sql": "DELETE FROM orders WHERE id = 1", "confirmWrite": True})
    assert r.status_code == 200 and r.json()["dataset"]["affected"] == 1
    await client.patch(f"/api/sessions/{sid}", json={"permissionMode": "auto"})
    r = await client.post(f"/api/sessions/{sid}/data/query", json={"source": "sqlite:shop.db", "sql": "DELETE FROM orders WHERE id = 2"})
    assert r.status_code == 200
    r = await client.post(f"/api/sessions/{sid}/data/query", json={"source": "file:none.csv", "sql": "SELECT 1"})
    assert r.status_code == 400
    # Even confirmed / in Auto mode, SQL cannot read or create database files outside the project.
    outside = workspace.parent / "outside.db"
    (workspace / "t.csv").write_text("a\n1\n")
    for source in ("sqlite:shop.db", "file:t.csv"):
        for sql in (f"ATTACH DATABASE '{outside.as_posix()}' AS x", f"VACUUM INTO '{outside.as_posix()}'"):
            r = await client.post(f"/api/sessions/{sid}/data/query", json={"source": source, "sql": sql, "confirmWrite": True})
            assert r.status_code == 400, (source, sql, r.text)
    assert not outside.exists()


# ---------------------------------------------------------------------------
# a database MCP server + the agent


async def test_mcp_database_and_agent_tools(client, fake_server, workspace, monkeypatch):
    await manager.shutdown()
    db = workspace / "mcp.db"
    make_db(db)
    configure(fake_server, workspace, mode="ask", servers={"warehouse": {"command": sys.executable, "args": [SQLITE_MCP, str(db)]}})
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    src = next(s for s in (await client.get(f"/api/sessions/{sid}/data/sources")).json()["sources"] if s["id"] == "mcp:warehouse")
    assert src["queryTool"] == "read_query" and src["writeTool"] == "write_query" and src["listTool"] == "list_tables" and src["dialect"] == "sqlite"
    assert [t["name"] for t in (await client.get(f"/api/sessions/{sid}/data/tables", params={"source": "mcp:warehouse"})).json()] == ["orders"]
    cols = (await client.get(f"/api/sessions/{sid}/data/columns", params={"source": "mcp:warehouse", "table": "orders"})).json()
    assert [c["name"] for c in cols] == ["id", "country", "amount", "day"]
    page = (await client.post(f"/api/sessions/{sid}/data/open-table", json={"source": "mcp:warehouse", "table": "orders"})).json()
    assert page["total"] == 6 and page["types"][2] == "number"

    seen = {}

    def brain(messages):
        last_user = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        turn = sum(1 for m in messages[last_user:] if m.get("role") == "assistant")
        seen.setdefault("system", messages[0]["content"])
        if turn == 0:
            return "", [{"name": "data_query", "arguments": {"source": "mcp:warehouse", "title": "Sales by country",
                                                             "sql": "SELECT country, sum(amount) AS total FROM orders GROUP BY country"}}]
        if turn == 1:
            seen["query"] = messages[-1]["content"]
            ds = seen["query"].split("Dataset ")[1].split()[0]
            seen["ds"] = ds
            return "", [{"name": "data_view", "arguments": {"dataset": ds, "sort": [{"column": "total", "desc": True}]}},
                        {"name": "data_profile", "arguments": {"dataset": ds}},
                        {"name": "data_export", "arguments": {"dataset": ds, "format": "xlsx", "path": "exports/sales"}}]
        if turn == 2:
            seen["view"], seen["profile"] = messages[-3]["content"], messages[-2]["content"]
            return "", [{"name": "data_query", "arguments": {"source": "mcp:warehouse", "sql": "DELETE FROM orders WHERE country = 'Italy'"}}]
        return "Done.", []

    monkeypatch.setattr(fake_llm, "brain", brain)
    await client.post(f"/api/sessions/{sid}/message", json={"text": "total sales by country, biggest first", "agent": "build"})
    for _ in range(300):
        pending = store.get(sid).pending_permissions()
        if pending:
            break
        await asyncio.sleep(0.05)
    assert pending and pending[0]["category"] == "data"  # the DELETE asks in Ask mode; the SELECTs did not
    await client.post(f"/api/sessions/{sid}/permissions/{pending[0]['id']}", json={"response": "reject"})
    s = await idle(sid)
    assert "# Data analysis" in seen["system"]
    assert "Sales by country" in seen["query"] and "| country | total |" in seen["query"]
    assert seen["view"].index("France") < seen["view"].index("Spain")
    assert "duplicate" in seen["profile"]
    assert (workspace / "exports" / "sales.xlsx").is_file()
    ds = data.load_dataset(s, seen["ds"])
    assert ds["view"]["sort"] == [{"column": "total", "desc": True}] and ds["createdBy"] == "agent"
    rows = sqlite3.connect(db).execute("SELECT count(*) FROM orders WHERE country = 'Italy'").fetchone()[0]
    assert rows == 1  # rejected
    await manager.shutdown()
