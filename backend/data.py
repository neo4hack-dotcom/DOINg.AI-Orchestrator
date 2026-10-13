"""Data analysis add-in: query databases through MCP servers (or local SQLite / CSV / Excel files),
browse the results as tables, filter / sort / profile them, export CSV or Excel - from the UI or by
asking the agent, who sees and drives the same datasets.

- **Sources**: every MCP server of the session that exposes a SQL tool (an argument named ``sql`` /
  ``query`` / ``statement``), classified with its list-tables / describe tools and a dialect guess;
  plus SQLite databases and CSV / TSV / JSON Lines / XLSX files of the workspace (files are loaded
  into an in-memory SQLite database, so they can be queried in SQL too).
- **Results** of any shape are turned into a table: JSON (records, ``{columns, rows}``, columnar),
  Python literals, CSV / TSV, Markdown and psql-style text tables.
- **Datasets** live per session (``data/datasets/<session>/<id>.json``) with a shared *view*
  (filters, sort, hidden columns, search): what the user sets in the grid, the agent sees, and the
  ``data_view`` tool changes what the user sees.
- **Writes** (INSERT, UPDATE, DROP...) are detected; they need a confirmation unless the session is
  in auto mode. Exports: CSV (UTF-8 with BOM, formula-injection safe), XLSX (no dependency), JSON.
"""
from __future__ import annotations

import ast
import asyncio
import csv
import io
import json
import math
import re
import sqlite3
import statistics
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

from .config import DATA_DIR, load_config
from .osutil import atomic_write, posix, read_text
from .session import new_id, now_ms

DATASETS_DIR = DATA_DIR / "datasets"
MAX_DATASETS = 60
SQL_ARGS = ("sql", "query", "statement", "sql_query", "sqlquery", "q", "command")
LIST_TOOLS = re.compile(r"^(list[_-]?tables|tables|show[_-]?tables|get[_-]?tables|list[_-]?objects|list[_-]?collections|list[_-]?relations)$", re.I)
DESCRIBE_TOOLS = re.compile(r"(describe|schema|columns|table[_-]?info)", re.I)
LOCAL_DB_EXT = {".db", ".sqlite", ".sqlite3"}
LOCAL_FILE_EXT = {".csv", ".tsv", ".jsonl", ".ndjson", ".json", ".xlsx"}


class DataError(Exception):
    pass


# ---------------------------------------------------------------------------
# SQL helpers


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


READ_KEYWORDS = ("select", "with", "show", "describe", "desc", "explain", "values", "table", "pragma")


def is_write(sql: str) -> bool:
    """True when any statement may change data or schema (conservative: unknown statements count as writes)."""
    text = strip_sql_comments(sql)
    for stmt in [s.strip() for s in re.split(r";\s*", text) if s.strip()]:
        word = re.match(r"^\(*\s*([a-zA-Z]+)", stmt)
        kw = word.group(1).lower() if word else ""
        if kw not in READ_KEYWORDS:
            return True
        if kw == "pragma" and "=" in stmt:
            return True
        if kw in ("with", "explain") and re.search(r"\b(insert|update|delete|merge|drop|alter|create|truncate|replace)\b", stmt, re.I):
            return True
        if kw == "select" and re.search(r"\binto\b\s+[\w\"`\[]", stmt, re.I) and not re.search(r"\binto\s+(outfile|dumpfile)\b", stmt, re.I):
            return True
    return False


def quote_ident(name: str, dialect: str) -> str:
    parts = [p for p in re.split(r"\.(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", name) if p]
    def q(p: str) -> str:
        p = p.strip('"`[]')
        if dialect == "mysql":
            return "`" + p.replace("`", "``") + "`"
        if dialect == "mssql":
            return "[" + p.replace("]", "]]") + "]"
        return '"' + p.replace('"', '""') + '"'
    return ".".join(q(p) for p in parts)


def preview_sql(table: str, dialect: str, limit: int = 200) -> str:
    if dialect == "mssql":
        return f"SELECT TOP {limit} * FROM {quote_ident(table, dialect)}"
    return f"SELECT * FROM {quote_ident(table, dialect)} LIMIT {limit}"


def tables_sql(dialect: str) -> str:
    if dialect == "sqlite":
        return "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    return ("SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN "
            "('information_schema','pg_catalog','sys','mysql','performance_schema') ORDER BY 1, 2")


def guess_dialect(text: str) -> str:
    low = text.lower()
    for key, dialect in (("sqlite", "sqlite"), ("postgres", "postgres"), ("pg_", "postgres"), ("mysql", "mysql"), ("mariadb", "mysql"),
                         ("duckdb", "duckdb"), ("motherduck", "duckdb"), ("mssql", "mssql"), ("sql server", "mssql"), ("sqlserver", "mssql"),
                         ("snowflake", "snowflake"), ("bigquery", "bigquery"), ("oracle", "oracle"), ("clickhouse", "clickhouse")):
        if key in low:
            return dialect
    return "generic"


# ---------------------------------------------------------------------------
# turning any result into a table


def _coerce(v: Any) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def _from_data(data: Any) -> Optional[tuple[list[str], list[list]]]:
    if isinstance(data, dict):
        for key in ("rows", "data", "results", "result", "records", "items", "values", "content", "table", "output"):
            if key in data and isinstance(data[key], (list, dict)):
                cols = data.get("columns") or data.get("fields") or data.get("headers")
                inner = data[key]
                if cols and isinstance(inner, list) and inner and isinstance(inner[0], (list, tuple)):
                    names = [c if isinstance(c, str) else str((c or {}).get("name") or (c or {}).get("column") or "") for c in cols]
                    return names, [[_coerce(v) for v in r] for r in inner]
                found = _from_data(inner)
                if found:
                    return found
        lists = {k: v for k, v in data.items() if isinstance(v, list)}
        if lists and len(lists) == len(data) and len({len(v) for v in lists.values()}) == 1:  # columnar
            cols = list(lists)
            n = len(next(iter(lists.values())))
            return cols, [[_coerce(lists[c][i]) for c in cols] for i in range(n)]
        if data and all(not isinstance(v, (list, dict)) for v in data.values()):
            return list(data), [[_coerce(v) for v in data.values()]]
        return None
    if isinstance(data, (list, tuple)):
        if not data:
            return [], []
        if all(isinstance(r, dict) for r in data):
            cols: list[str] = []
            for r in data:
                for k in r:
                    if k not in cols:
                        cols.append(str(k))
            return cols, [[_coerce(r.get(c)) for c in cols] for r in data]
        if all(isinstance(r, (list, tuple)) for r in data):
            width = max(len(r) for r in data)
            first = data[0]
            header = (len(data) > 1 and all(isinstance(v, str) for v in first)
                      and not all(all(isinstance(v, str) for v in r) for r in data[1:]))
            if header:
                return [str(v) for v in first], [[_coerce(v) for v in r] for r in data[1:]]
            return [f"col{i + 1}" for i in range(width)], [[_coerce(v) for v in r] + [None] * (width - len(r)) for r in data]
        return ["value"], [[_coerce(v)] for v in data]
    return None


def _parse_pipe_table(lines: list[str]) -> Optional[tuple[list[str], list[list]]]:
    rows = [l for l in lines if "|" in l or (re.fullmatch(r"[\s|:+-]+", l) and "-" in l)]
    if len(rows) < 2:
        return None
    sep = next((i for i, l in enumerate(rows) if re.fullmatch(r"[\s|:+-]+", l) and "-" in l), None)
    if sep is None or sep == 0 or "|" not in rows[sep - 1]:
        return None
    def cells(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [c.strip() for c in line.split("|")]
    header = cells(rows[sep - 1])
    body = [cells(l) for l in rows[sep + 1:] if not re.fullmatch(r"\(\d+ rows?\)", l.strip())]
    return header, [[(None if v in ("", "NULL", "null") else v) for v in r[:len(header)]] + [None] * (len(header) - len(r)) for r in body]


def parse_table(raw: Any) -> tuple[list[str], list[list]]:
    """Any tool / file output -> (columns, rows)."""
    if not isinstance(raw, str):
        found = _from_data(raw)
        if found:
            return found
        raw = json.dumps(raw, default=str)
    text = raw.strip()
    if not text:
        return [], []
    candidates = [text]
    m = re.search(r"[\[{]", text)
    if m and m.start() > 0:
        candidates.append(text[m.start():])
    for cand in candidates:
        for loader in (json.loads, ast.literal_eval):
            try:
                found = _from_data(loader(cand))
            except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
                continue
            if found:
                return found
    lines = [l for l in text.splitlines() if l.strip()]
    piped = _parse_pipe_table(lines)
    if piped and piped[0]:
        return piped
    sample = "\n".join(lines[:50])
    if len(lines) >= 2:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            rows = list(csv.reader(io.StringIO("\n".join(lines)), dialect))
            if rows and len(rows[0]) > 1 and sum(len(r) == len(rows[0]) for r in rows) >= 0.8 * len(rows):
                return rows[0], [[(v if v != "" else None) for v in r] for r in rows[1:]]
        except csv.Error:
            pass
    return ["result"], [[l] for l in lines]


def infer_type(values: list) -> str:
    seen = [v for v in values if v is not None and v != ""][:500]
    if not seen:
        return "text"
    if all(isinstance(v, bool) for v in seen):
        return "boolean"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return "integer" if all(isinstance(v, int) or float(v).is_integer() for v in seen) else "number"
    strs = [str(v).strip() for v in seen]
    if all(re.fullmatch(r"[-+]?\d+", s) for s in strs):
        return "integer"
    if all(re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", s) for s in strs):
        return "number"
    if all(s.lower() in ("true", "false") for s in strs):
        return "boolean"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?", s) for s in strs):
        return "date" if all(len(s) == 10 for s in strs) else "datetime"
    return "text"


def normalize(columns: list[str], rows: list[list]) -> tuple[list[str], list[str], list[list]]:
    """Unique column names, inferred types, values converted to numbers / booleans where the column is typed."""
    names, seen = [], {}
    for i, c in enumerate(columns):
        base = str(c or f"col{i + 1}").strip() or f"col{i + 1}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        names.append(base if n == 0 else f"{base}_{n + 1}")
    types = [infer_type([r[i] if i < len(r) else None for r in rows]) for i in range(len(names))]
    out = []
    for r in rows:
        row = list(r[:len(names)]) + [None] * (len(names) - len(r))
        for i, t in enumerate(types):
            v = row[i]
            if v is None or v == "" or not isinstance(v, str):
                continue
            try:
                if t == "integer":
                    row[i] = int(v)
                elif t == "number":
                    row[i] = float(v)
                elif t == "boolean":
                    row[i] = v.strip().lower() == "true"
            except ValueError:
                pass
        out.append(row)
    return names, types, out


# ---------------------------------------------------------------------------
# local files


def _xlsx_rows(path: Path, max_rows: int) -> list[list]:
    """First worksheet of an .xlsx file (values only; no dependency)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheet = "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in names else sorted(n for n in names if n.startswith("xl/worksheets/sheet"))[0]
        root = ET.fromstring(z.read(sheet))
    rows: list[list] = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        values: dict[int, Any] = {}
        for c in row.findall("m:c", ns):
            ref = c.get("r", "")
            letters = re.match(r"[A-Z]+", ref)
            col = 0
            for ch in (letters.group(0) if letters else ""):
                col = col * 26 + (ord(ch) - 64)
            col = (col or len(values) + 1) - 1
            kind = c.get("t")
            v = c.find("m:v", ns)
            if kind == "s" and v is not None:
                values[col] = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
            elif kind == "inlineStr":
                values[col] = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            elif kind == "b" and v is not None:
                values[col] = v.text == "1"
            elif v is not None and v.text is not None:
                try:
                    num = float(v.text)
                    values[col] = int(num) if num.is_integer() and kind != "str" else (num if kind != "str" else v.text)
                except ValueError:
                    values[col] = v.text
        if values:
            width = max(values) + 1
            rows.append([values.get(i) for i in range(width)])
        if len(rows) > max_rows:
            break
    return rows


def read_local_file(path: Path, max_rows: int) -> tuple[list[str], list[list]]:
    ext = path.suffix.lower()
    if ext == ".xlsx":
        rows = _xlsx_rows(path, max_rows + 1)
        if not rows:
            return [], []
        header = [str(v) if v is not None else f"col{i + 1}" for i, v in enumerate(rows[0])]
        return header, rows[1:max_rows + 1]
    text = read_text(path)
    if ext in (".jsonl", ".ndjson"):
        records = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if len(records) > max_rows:
                break
        return parse_table(records)
    if ext == ".json":
        return parse_table(json.loads(text))
    delimiter = "\t" if ext == ".tsv" else None
    sample = text[:20000]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append(row)
        if len(rows) > max_rows + 1:
            break
    if not rows:
        return [], []
    return rows[0], [[(v if v != "" else None) for v in r] for r in rows[1:max_rows + 1]]


def _table_name(path: Path) -> str:
    name = re.sub(r"\W+", "_", path.stem).strip("_").lower() or "data"
    return name if not name[0].isdigit() else f"t_{name}"


def _sqlite_run(db: str, sql: str, max_rows: int, write: bool, timeout: float = 30.0, uri: bool = True) -> tuple[list[str], list[list], bool, int]:
    deadline = time.monotonic() + timeout
    conn = sqlite3.connect(db, uri=uri, timeout=10, check_same_thread=False)
    try:
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        cur = conn.cursor()
        statements = [s for s in re.split(r";\s*(?=(?:[^']*'[^']*')*[^']*$)", sql) if s.strip()]
        columns: list[str] = []
        rows: list = []
        affected = 0
        for stmt in statements:
            cur.execute(stmt)
            if cur.description:
                columns = [d[0] for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
            elif cur.rowcount and cur.rowcount > 0:
                affected += cur.rowcount
        if write:
            conn.commit()
        truncated = len(rows) > max_rows
        return columns, [list(r) for r in rows[:max_rows]], truncated, affected
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise DataError(f"query stopped after {int(timeout)}s") from e
        raise DataError(str(e)) from e
    except sqlite3.Error as e:
        raise DataError(str(e)) from e
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# sources


def _workspace_files(directory: str, limit: int = 300) -> list[Path]:
    from .tools import IGNORED_DIRS

    root = Path(directory)
    out: list[Path] = []
    data = DATA_DIR.resolve()
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".") and depth < 5
                       and (Path(dirpath) / d).resolve() != data]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in LOCAL_DB_EXT | LOCAL_FILE_EXT and not p.is_symlink():
                out.append(p)
                if len(out) >= limit:
                    return out
    return out


def _classify_tools(server: str, tools: list) -> Optional[dict]:
    """The SQL tools of an MCP server: read (SELECT), write (INSERT / DDL), list-tables and describe tools."""
    reads, writes, generic = [], [], []
    list_tool = describe = None
    for t in tools:
        props = (t.input_schema or {}).get("properties") or {}
        arg = next((a for a in props if a.lower() in SQL_ARGS and (props[a] or {}).get("type", "string") == "string"), None)
        name = t.name.lower()
        if arg:
            entry = {"tool": t.name, "arg": arg, "readOnly": t.read_only}
            if re.search(r"(write|insert|update|modify|mutat|ddl|create)", name) and not t.read_only:
                writes.append(entry)
            elif re.search(r"(read|select)", name) or t.read_only:
                reads.append(entry)
            else:
                generic.append(entry)
        elif LIST_TOOLS.match(t.name):
            list_tool = list_tool or t.name
        elif DESCRIBE_TOOLS.search(name):
            table_arg = next((a for a in props if a.lower() in ("table", "table_name", "tablename", "name", "object")), None)
            if table_arg:
                describe = describe or {"tool": t.name, "arg": table_arg}
    read = (reads or generic or [None])[0]
    write = (writes or generic or [None])[0]
    if not read and not write:
        return None
    text = server + " " + " ".join(f"{t.name} {t.description}" for t in tools)
    return {"read": read or write, "write": write, "list": list_tool, "describe": describe, "dialect": guess_dialect(text)}


async def list_sources(session) -> list[dict]:
    from . import mcp_tools
    from .mcp_client import manager

    cfg = load_config()
    out: list[dict] = []
    servers = mcp_tools.session_servers(cfg, session)
    if servers:
        tools, errors = await manager.tools(servers, session.directory)
        by_server: dict[str, list] = {}
        for t in tools:
            by_server.setdefault(t.server, []).append(t)
        for name in servers:
            info = _classify_tools(name, by_server.get(name, []))
            if info:
                out.append({"id": f"mcp:{name}", "kind": "mcp", "name": name, "dialect": info["dialect"],
                            "queryTool": info["read"]["tool"] if info["read"] else None,
                            "writeTool": info["write"]["tool"] if info["write"] else None,
                            "listTool": info["list"], "describeTool": (info["describe"] or {}).get("tool")})
            elif name in errors:
                out.append({"id": f"mcp:{name}", "kind": "mcp", "name": name, "error": errors[name].splitlines()[0][:200]})
    for p in await asyncio.to_thread(_workspace_files, session.directory):
        rel = posix(p.relative_to(Path(session.directory)))
        if p.suffix.lower() in LOCAL_DB_EXT:
            out.append({"id": f"sqlite:{rel}", "kind": "sqlite", "name": rel, "dialect": "sqlite"})
        else:
            out.append({"id": f"file:{rel}", "kind": "file", "name": rel, "dialect": "sqlite", "table": _table_name(p)})
    return out


def _local_path(session, rel: str) -> Path:
    root = Path(session.directory).resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise DataError("path outside the workspace")
    if not p.is_file():
        raise DataError(f"file not found: {rel}")
    return p


async def _mcp_call(session, server: str, tool: str, args: dict) -> Any:
    from . import mcp_tools
    from .mcp_client import McpError, manager

    cfg = load_config()
    servers = mcp_tools.session_servers(cfg, session)
    if server not in servers:
        raise DataError(f"MCP server '{server}' is not available in this session")
    try:
        conn = await manager.connection(server, servers[server], session.directory)
        res = await conn.call_tool(tool, args)
    except McpError as e:
        raise DataError(f"{server}/{tool}: {e}") from e
    texts = [str(c.get("text", "")) for c in res.get("content") or [] if c.get("type") == "text"]
    if res.get("isError"):
        raise DataError("\n".join(texts)[:2000] or "the tool returned an error")
    if res.get("structuredContent") is not None:
        return res["structuredContent"]
    return "\n".join(texts)


async def _server_info(session, server: str) -> dict:
    from . import mcp_tools
    from .mcp_client import manager

    cfg = load_config()
    servers = mcp_tools.session_servers(cfg, session)
    if server not in servers:
        raise DataError(f"MCP server '{server}' is not available in this session")
    conn = await manager.connection(server, servers[server], session.directory)
    info = _classify_tools(server, conn.tools)
    if not info:
        raise DataError(f"MCP server '{server}' has no SQL tool")
    return info


async def run_sql(session, source: str, sql: str, max_rows: int, write_ok: bool) -> dict:
    """-> {columns, rows, truncated, affected, elapsedMs, write}"""
    write = is_write(sql)
    if write and not write_ok:
        raise DataError("WRITE_NEEDS_CONFIRMATION")
    started = time.monotonic()
    kind, _, ref = source.partition(":")
    affected = 0
    if kind == "sqlite":
        path = _local_path(session, ref)
        uri = f"file:{path.as_posix()}?mode={'rw' if write else 'ro'}"
        columns, rows, truncated, affected = await asyncio.to_thread(_sqlite_run, uri, sql, max_rows, write)
    elif kind == "file":
        if write:
            raise DataError("files are read-only sources: export the result instead")
        path = _local_path(session, ref)
        columns, rows, truncated, _ = await asyncio.to_thread(_query_file, path, sql, max_rows)
    elif kind == "mcp":
        info = await _server_info(session, ref)
        entry = info["write"] if write else info["read"]
        if not entry:
            raise DataError(f"'{ref}' has no tool for this kind of statement")
        raw = await _mcp_call(session, ref, entry["tool"], {entry["arg"]: sql})
        columns, rows = parse_table(raw)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
    else:
        raise DataError(f"unknown source {source}")
    return {"columns": columns, "rows": rows, "truncated": truncated, "affected": affected,
            "elapsedMs": int((time.monotonic() - started) * 1000), "write": write}


def _load_file_db(path: Path, max_rows: int) -> tuple[sqlite3.Connection, str]:
    columns, rows = read_local_file(path, max_rows)
    names, types, rows = normalize(columns, rows)
    table = _table_name(path)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    sqltypes = {"integer": "INTEGER", "number": "REAL", "boolean": "INTEGER"}
    cols = ", ".join(f"{quote_ident(n, 'sqlite')} {sqltypes.get(t, 'TEXT')}" for n, t in zip(names, types))
    conn.execute(f"CREATE TABLE {quote_ident(table, 'sqlite')} ({cols})")
    if rows:
        conn.executemany(f"INSERT INTO {quote_ident(table, 'sqlite')} VALUES ({', '.join('?' * len(names))})", rows)
    return conn, table


def _query_file(path: Path, sql: str, max_rows: int) -> tuple[list[str], list[list], bool, int]:
    limit = int(load_config()["data"].get("maxRows") or 5000) * 20
    conn, _table = _load_file_db(path, limit)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
        return columns, [list(r) for r in rows[:max_rows]], len(rows) > max_rows, 0
    except sqlite3.Error as e:
        raise DataError(str(e)) from e
    finally:
        conn.close()


async def list_tables(session, source: str) -> list[dict]:
    kind, _, ref = source.partition(":")
    if kind == "sqlite":
        path = _local_path(session, ref)
        cols, rows, _, _ = await asyncio.to_thread(_sqlite_run, f"file:{path.as_posix()}?mode=ro", tables_sql("sqlite"), 1000, False)
        return [{"name": r[0]} for r in rows]
    if kind == "file":
        return [{"name": _table_name(_local_path(session, ref)), "file": ref}]
    if kind == "mcp":
        info = await _server_info(session, ref)
        if info["list"]:
            raw = await _mcp_call(session, ref, info["list"], {})
            columns, rows = parse_table(raw)
        else:
            res = await run_sql(session, source, tables_sql(info["dialect"] if info["dialect"] == "sqlite" else "generic"), 2000, False)
            columns, rows = res["columns"], res["rows"]
        if not columns:
            return []
        low = [c.lower() for c in columns]
        if "table_schema" in low and "table_name" in low:
            si, ti = low.index("table_schema"), low.index("table_name")
            return [{"name": f"{r[si]}.{r[ti]}" if r[si] not in (None, "", "public", "main", "dbo") else str(r[ti])} for r in rows]
        idx = next((low.index(k) for k in ("table_name", "name", "table", "tablename") if k in low), 0)
        return [{"name": str(r[idx])} for r in rows if r and r[idx] is not None]
    raise DataError(f"unknown source {source}")


async def table_columns(session, source: str, table: str) -> list[dict]:
    kind, _, ref = source.partition(":")
    if kind in ("sqlite", "file"):
        if kind == "file":
            res = await run_sql(session, source, f"SELECT * FROM {quote_ident(table, 'sqlite')} LIMIT 200", 200, False)
            names, types, _ = normalize(res["columns"], res["rows"])
            return [{"name": n, "type": t} for n, t in zip(names, types)]
        path = _local_path(session, ref)
        _, rows, _, _ = await asyncio.to_thread(_sqlite_run, f"file:{path.as_posix()}?mode=ro",
                                                f"PRAGMA table_info({quote_ident(table, 'sqlite')})", 2000, False)
        return [{"name": r[1], "type": r[2] or ""} for r in rows]
    info = await _server_info(session, ref)
    if info["describe"]:
        raw = await _mcp_call(session, ref, info["describe"]["tool"], {info["describe"]["arg"]: table})
        columns, rows = parse_table(raw)
        low = [c.lower() for c in columns]
        ni = next((low.index(k) for k in ("column_name", "name", "column", "field") if k in low), 0)
        ti = next((low.index(k) for k in ("data_type", "type", "column_type") if k in low), None)
        return [{"name": str(r[ni]), "type": str(r[ti]) if ti is not None else ""} for r in rows if r]
    res = await run_sql(session, source, preview_sql(table, info["dialect"], 50), 50, False)
    names, types, _ = normalize(res["columns"], res["rows"])
    return [{"name": n, "type": t} for n, t in zip(names, types)]


# ---------------------------------------------------------------------------
# datasets


def _dir(session) -> Path:
    return DATASETS_DIR / session.id


def _meta(ds: dict) -> dict:
    return {k: ds.get(k) for k in ("id", "title", "source", "sql", "columns", "types", "rowCount", "truncated", "createdAt",
                                    "elapsedMs", "createdBy", "view", "affected")}


_cache: dict[tuple[str, str], dict] = {}


def save_dataset(session, ds: dict) -> None:
    d = _dir(session)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write(d / f"{ds['id']}.json", json.dumps(ds, ensure_ascii=False, default=str))
    _cache[(session.id, ds["id"])] = ds
    index = [m for m in session.data.get("datasets", []) if m["id"] != ds["id"]]
    index.append(_meta(ds))
    for old in index[:-MAX_DATASETS]:
        try:
            (d / f"{old['id']}.json").unlink()
        except OSError:
            pass
        _cache.pop((session.id, old["id"]), None)
    session.data["datasets"] = index[-MAX_DATASETS:]
    session.touch()


def load_dataset(session, ds_id: str) -> dict:
    key = (session.id, ds_id)
    if key in _cache:
        return _cache[key]
    p = _dir(session) / f"{re.sub(r'[^A-Za-z0-9_]', '', ds_id)}.json"
    if not p.is_file():
        raise DataError(f"unknown dataset {ds_id}")
    ds = json.loads(read_text(p))
    _cache[key] = ds
    return ds


def delete_dataset(session, ds_id: str) -> None:
    _cache.pop((session.id, ds_id), None)
    try:
        (_dir(session) / f"{re.sub(r'[^A-Za-z0-9_]', '', ds_id)}.json").unlink()
    except OSError:
        pass
    session.data["datasets"] = [m for m in session.data.get("datasets", []) if m["id"] != ds_id]
    session.touch()


def make_dataset(session, columns: list[str], rows: list[list], *, title: str, source: str, sql: str, created_by: str,
                 truncated: bool = False, elapsed_ms: int = 0, affected: int = 0) -> dict:
    names, types, rows = normalize(columns, rows)
    ds = {"id": new_id("ds"), "title": (title or sql or source)[:120], "source": source, "sql": sql, "columns": names, "types": types,
          "rows": rows, "rowCount": len(rows), "truncated": truncated, "createdAt": now_ms(), "elapsedMs": elapsed_ms,
          "createdBy": created_by, "view": empty_view(), "affected": affected}
    save_dataset(session, ds)
    session.emit("data.dataset", {"dataset": _meta(ds)})
    return ds


def empty_view() -> dict:
    return {"filters": [], "sort": [], "hidden": [], "search": ""}


def history_add(session, source: str, sql: str, ok: bool, rows: int, by: str) -> None:
    hist = session.data.setdefault("dataHistory", [])
    hist.append({"ts": now_ms(), "source": source, "sql": sql[:4000], "ok": ok, "rows": rows, "by": by})
    del hist[:-80]


# ---------------------------------------------------------------------------
# view: filters, sort, search, hidden columns


OPS = ("eq", "neq", "contains", "not_contains", "starts", "ends", "gt", "gte", "lt", "lte", "empty", "not_empty", "in", "regex")


def clean_view(view: dict, columns: list[str]) -> dict:
    v = empty_view()
    for f in (view or {}).get("filters") or []:
        if isinstance(f, dict) and f.get("column") in columns and f.get("op") in OPS:
            v["filters"].append({"column": f["column"], "op": f["op"], "value": f.get("value")})
    for s in (view or {}).get("sort") or []:
        if isinstance(s, dict) and s.get("column") in columns:
            v["sort"].append({"column": s["column"], "desc": bool(s.get("desc"))})
    v["hidden"] = [c for c in (view or {}).get("hidden") or [] if c in columns]
    v["search"] = str((view or {}).get("search") or "")[:200]
    return v


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _match(value: Any, op: str, target: Any, numeric: bool) -> bool:
    if op == "empty":
        return value is None or value == ""
    if op == "not_empty":
        return not (value is None or value == "")
    if op == "in":
        options = target if isinstance(target, list) else [t.strip() for t in str(target or "").split(",")]
        return str(value) in {str(o) for o in options}
    if value is None:
        return op == "neq"
    if op in ("gt", "gte", "lt", "lte") or (numeric and op in ("eq", "neq")):
        a, b = _num(value), _num(target)
        if a is None or b is None:
            a, b = str(value), str(target)  # type: ignore[assignment]
        if op == "eq":
            return a == b
        if op == "neq":
            return a != b
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]  # type: ignore[operator]
    s, t = str(value).lower(), str(target if target is not None else "").lower()
    if op == "eq":
        return s == t
    if op == "neq":
        return s != t
    if op == "contains":
        return t in s
    if op == "not_contains":
        return t not in s
    if op == "starts":
        return s.startswith(t)
    if op == "ends":
        return s.endswith(t)
    if op == "regex":
        try:
            return re.search(str(target), str(value), re.I) is not None
        except re.error:
            return False
    return True


def apply_view(ds: dict, view: Optional[dict] = None) -> list[int]:
    """Indices of the rows the view keeps, in the view's order."""
    view = view if view is not None else ds.get("view") or empty_view()
    cols = ds["columns"]
    idx = {c: i for i, c in enumerate(cols)}
    numeric = {c for c, t in zip(cols, ds["types"]) if t in ("integer", "number")}
    rows = ds["rows"]
    keep = []
    search = (view.get("search") or "").lower()
    visible = [idx[c] for c in cols if c not in (view.get("hidden") or [])]
    for i, r in enumerate(rows):
        ok = all(_match(r[idx[f["column"]]], f["op"], f.get("value"), f["column"] in numeric) for f in view.get("filters") or [])
        if ok and search:
            ok = any(search in str(r[j]).lower() for j in visible if r[j] is not None)
        if ok:
            keep.append(i)
    for s in reversed(view.get("sort") or []):
        j = idx[s["column"]]
        is_num = s["column"] in numeric

        def key(i: int, j: int = j, is_num: bool = is_num):
            v = rows[i][j]
            if v is None or v == "":
                return (1, 0)
            return (0, _num(v) if is_num and _num(v) is not None else str(v).lower())
        nones = [i for i in keep if rows[i][j] is None or rows[i][j] == ""]
        vals = sorted((i for i in keep if not (rows[i][j] is None or rows[i][j] == "")), key=key, reverse=s["desc"])
        keep = vals + nones
    return keep


def page(ds: dict, offset: int = 0, limit: int = 200) -> dict:
    view = ds.get("view") or empty_view()
    order = apply_view(ds, view)
    rows = [ds["rows"][i] for i in order[offset:offset + limit]]
    return {"dataset": _meta(ds), "columns": ds["columns"], "types": ds["types"], "rows": rows, "total": ds["rowCount"],
            "filtered": len(order), "offset": offset, "limit": limit}


def set_view(session, ds: dict, view: dict, by: str) -> dict:
    ds["view"] = clean_view(view, ds["columns"])
    save_dataset(session, ds)
    session.emit("data.view", {"datasetId": ds["id"], "view": ds["view"], "by": by})
    return ds["view"]


# ---------------------------------------------------------------------------
# profile and aggregates


def profile(ds: dict, columns: Optional[list[str]] = None) -> dict:
    order = apply_view(ds)
    rows = [ds["rows"][i] for i in order]
    out = []
    for j, (c, t) in enumerate(zip(ds["columns"], ds["types"])):
        if columns and c not in columns:
            continue
        values = [r[j] for r in rows]
        present = [v for v in values if v is not None and v != ""]
        counts: dict[str, int] = {}
        for v in present:
            counts[str(v)] = counts.get(str(v), 0) + 1
        col = {"column": c, "type": t, "count": len(present), "nulls": len(values) - len(present), "distinct": len(counts),
               "top": sorted(counts.items(), key=lambda kv: -kv[1])[:5]}
        nums = [x for x in (_num(v) for v in present) if x is not None and not math.isnan(x)] if t in ("integer", "number", "boolean") else []
        if nums:
            col.update(min=min(nums), max=max(nums), mean=round(statistics.fmean(nums), 6), median=statistics.median(nums),
                       std=round(statistics.pstdev(nums), 6) if len(nums) > 1 else 0.0, sum=sum(nums))
        elif present:
            ordered = sorted(str(v) for v in present)
            col.update(min=ordered[0], max=ordered[-1], minLength=min(len(s) for s in ordered), maxLength=max(len(s) for s in ordered))
        out.append(col)
    dupes = len(rows) - len({json.dumps(r, default=str) for r in rows})
    return {"rows": len(rows), "duplicates": dupes, "columns": out}


def aggregate(ds: dict, x: str, y: Optional[str], agg: str, limit: int = 50) -> dict:
    if x not in ds["columns"] or (y and y not in ds["columns"]):
        raise DataError("unknown column")
    xi = ds["columns"].index(x)
    yi = ds["columns"].index(y) if y else None
    groups: dict[str, list] = {}
    for i in apply_view(ds):
        r = ds["rows"][i]
        groups.setdefault("" if r[xi] is None else str(r[xi]), []).append(r[yi] if yi is not None else 1)
    def reduce(vals: list) -> float:
        nums = [n for n in (_num(v) for v in vals) if n is not None]
        if agg == "count" or yi is None:
            return len(vals)
        if not nums:
            return 0
        return {"sum": sum(nums), "avg": statistics.fmean(nums), "min": min(nums), "max": max(nums)}.get(agg, len(vals))
    points = [{"x": k, "y": round(reduce(v), 6)} for k, v in groups.items()]
    if ds["types"][xi] in ("integer", "number", "date", "datetime"):
        points.sort(key=lambda p: (_num(p["x"]) if _num(p["x"]) is not None else 0, p["x"]))
    else:
        points.sort(key=lambda p: -p["y"])
    return {"x": x, "y": y, "agg": agg, "points": points[:limit], "groups": len(points)}


# ---------------------------------------------------------------------------
# exports


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _visible(ds: dict, scope: str) -> tuple[list[str], list[list]]:
    view = ds.get("view") or empty_view()
    order = apply_view(ds) if scope == "view" else list(range(len(ds["rows"])))
    hidden = set(view.get("hidden") or []) if scope == "view" else set()
    keep = [j for j, c in enumerate(ds["columns"]) if c not in hidden]
    return [ds["columns"][j] for j in keep], [[ds["rows"][i][j] for j in keep] for i in order]


def to_csv(ds: dict, scope: str = "view", excel_safe: bool = True) -> bytes:
    cols, rows = _visible(ds, scope)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        out = []
        for v in r:
            if v is None:
                out.append("")
            elif isinstance(v, str) and excel_safe and v.startswith(FORMULA_START):
                out.append("'" + v)  # CSV formula injection: Excel would evaluate =...
            else:
                out.append(v)
        w.writerow(out)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def to_json(ds: dict, scope: str = "view") -> bytes:
    cols, rows = _visible(ds, scope)
    return json.dumps([dict(zip(cols, r)) for r in rows], ensure_ascii=False, indent=1, default=str).encode("utf-8")


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def to_xlsx(ds: dict, scope: str = "view", sheet: str = "Data") -> bytes:
    """A minimal, valid Office Open XML workbook: bold frozen header, typed cells (no formulas, ever)."""
    cols, rows = _visible(ds, scope)
    sheet = re.sub(r"[\[\]:*?/\\]", "_", sheet)[:31] or "Data"

    def cell(ref: str, v: Any, style: int = 0) -> str:
        s = f' s="{style}"' if style else ""
        if v is None or v == "":
            return ""
        if isinstance(v, bool):
            return f'<c r="{ref}" t="b"{s}><v>{1 if v else 0}</v></c>'
        if isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return f'<c r="{ref}"{s}><v>{v}</v></c>'
        text = xml_escape(_XML_BAD.sub("", str(v)))[:32767]
        return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{text}</t></is></c>'

    widths = [min(60, max([len(str(c))] + [len(str(r[j])) for r in rows[:200] if r[j] is not None]) + 2) for j, c in enumerate(cols)]
    body = [f'<row r="1">{"".join(cell(_col_letter(j) + "1", c, 1) for j, c in enumerate(cols))}</row>']
    for i, r in enumerate(rows, start=2):
        body.append(f'<row r="{i}">{"".join(cell(_col_letter(j) + str(i), v) for j, v in enumerate(r))}</row>')
    last = f"{_col_letter(max(0, len(cols) - 1))}{len(rows) + 1}"
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 f'<dimension ref="A1:{last}"/>'
                 '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 '<cols>' + "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>' for j, w in enumerate(widths)) + '</cols>'
                 f'<sheetData>{"".join(body)}</sheetData>'
                 + (f'<autoFilter ref="A1:{last}"/>' if cols else "") + '</worksheet>')
    files = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                                '</Types>'),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                        '</Relationships>'),
        "xl/workbook.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                            f'<sheets><sheet name="{xml_escape(sheet)}" sheetId="1" r:id="rId1"/></sheets>'
                            + (f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">\'{xml_escape(sheet)}\'!$A$1:${_col_letter(max(0, len(cols) - 1))}${len(rows) + 1}</definedName></definedNames>' if cols else "")
                            + '</workbook>'),
        "xl/_rels/workbook.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                                       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                                       '</Relationships>'),
        "xl/styles.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                          '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                          '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                          '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                          '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EAF6"/><bgColor indexed="64"/></patternFill></fill></fills>'
                          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                          '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                          '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
                          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                          '</styleSheet>'),
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def export_bytes(ds: dict, fmt: str, scope: str = "view") -> tuple[bytes, str, str]:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", ds.get("title") or ds["id"]).strip("-")[:60] or ds["id"]
    if fmt == "xlsx":
        return to_xlsx(ds, scope), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{safe}.xlsx"
    if fmt == "json":
        return to_json(ds, scope), "application/json", f"{safe}.json"
    return to_csv(ds, scope), "text/csv; charset=utf-8", f"{safe}.csv"


# ---------------------------------------------------------------------------
# agent tools


def markdown_preview(columns: list[str], rows: list[list], limit: int) -> str:
    if not columns:
        return "(no columns)"
    def fmt(v: Any) -> str:
        s = "" if v is None else str(v)
        return s.replace("|", "\\|").replace("\n", " ")[:60]
    lines = ["| " + " | ".join(fmt(c) for c in columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(fmt(v) for v in r) + " |" for r in rows[:limit]]
    return "\n".join(lines)


def describe_dataset(ds: dict, limit: int) -> str:
    order = apply_view(ds)
    view = ds.get("view") or empty_view()
    cols = [c for c in ds["columns"] if c not in (view.get("hidden") or [])]
    idx = [ds["columns"].index(c) for c in cols]
    rows = [[ds["rows"][i][j] for j in idx] for i in order[:limit]]
    head = (f"Dataset {ds['id']} \"{ds['title']}\" - {ds['rowCount']} rows x {len(ds['columns'])} columns"
            + (" (truncated)" if ds.get("truncated") else "")
            + (f"; the user's view keeps {len(order)} rows" if len(order) != ds["rowCount"] else "") + f" - source {ds['source']}")
    types = ", ".join(f"{c}: {t}" for c, t in zip(ds["columns"], ds["types"]))
    view_txt = ""
    if view.get("filters") or view.get("sort") or view.get("search") or view.get("hidden"):
        view_txt = "\nCurrent view: " + json.dumps(view, ensure_ascii=False)
    return f"{head}\nColumns: {types}{view_txt}\n\n{markdown_preview(cols, rows, limit)}"


def tool_defs(session, read_only: bool) -> dict:
    from .tools import ToolContext, ToolDef, ToolError, ToolResult

    cfg = load_config()["data"]
    preview_rows = int(cfg.get("previewRows") or 15)
    max_rows = int(cfg.get("maxRows") or 5000)

    def get(ds_id: str) -> dict:
        try:
            return load_dataset(session, str(ds_id or "").strip())
        except DataError as e:
            known = ", ".join(f"{m['id']} ({m['title'][:40]})" for m in session.data.get("datasets", [])[-10:])
            raise ToolError(f"{e}. Datasets: {known or 'none'}") from e

    async def t_sources(_ctx: ToolContext, args: dict) -> ToolResult:
        try:
            sources = await list_sources(session)
        except DataError as e:
            raise ToolError(str(e)) from e
        lines = []
        for s in sources:
            if s.get("error"):
                lines.append(f"- {s['id']}: unavailable ({s['error']})")
                continue
            line = f"- {s['id']} ({s['kind']}, {s.get('dialect', '')} SQL)"
            if args.get("tables", True):
                try:
                    tables = await list_tables(session, s["id"])
                    line += ": tables " + ", ".join(t["name"] for t in tables[:40]) + (" ..." if len(tables) > 40 else "")
                except DataError as e:
                    line += f": tables unavailable ({str(e)[:120]})"
            lines.append(line)
        return ToolResult("\n".join(lines) or "No data source: configure a database MCP server (Settings > MCP) or add a SQLite / CSV / XLSX file to the project.",
                          f"{len(sources)} source(s)")

    async def t_query(_ctx: ToolContext, args: dict) -> ToolResult:
        source, sql = str(args.get("source") or ""), str(args.get("sql") or "").strip()
        if not sql:
            raise ToolError("sql is required")
        write = is_write(sql)
        if write and read_only:
            raise ToolError("Read-only agent: data-changing statements are not allowed.")
        try:
            res = await run_sql(session, source, sql, int(args.get("max_rows") or max_rows), True)
        except DataError as e:
            history_add(session, source, sql, False, 0, "agent")
            raise ToolError(f"Query failed: {e}") from e
        history_add(session, source, sql, True, len(res["rows"]), "agent")
        if write and not res["columns"]:
            return ToolResult(f"Statement executed on {source} ({res['affected']} row(s) affected, {res['elapsedMs']} ms).",
                              f"{source} write", {"data": {"write": True, "affected": res["affected"]}})
        ds = make_dataset(session, res["columns"], res["rows"], title=str(args.get("title") or sql.splitlines()[0])[:120], source=source,
                          sql=sql, created_by="agent", truncated=res["truncated"], elapsed_ms=res["elapsedMs"], affected=res["affected"])
        text = describe_dataset(ds, preview_rows)
        text += ("\n\nThe user sees this dataset in the Data view. data_view filters / sorts what they see, data_profile gives column "
                 "statistics, data_export saves CSV / XLSX.")
        return ToolResult(text, f"{ds['rowCount']} rows · {source}",
                          {"data": {"dataset": _meta(ds), "preview": {"columns": ds["columns"], "rows": ds["rows"][:8]}}})

    async def t_view(_ctx: ToolContext, args: dict) -> ToolResult:
        ds = get(args.get("dataset"))
        view = empty_view() if args.get("reset") else dict(ds.get("view") or empty_view())
        for key in ("filters", "sort", "search"):
            if key in args and args[key] is not None:
                view[key] = args[key]
        if args.get("columns"):
            wanted = [c for c in args["columns"] if c in ds["columns"]]
            view["hidden"] = [c for c in ds["columns"] if c not in wanted]
        view = set_view(session, ds, view, "agent")
        return ToolResult(describe_dataset(ds, preview_rows), f"view of {ds['id']}", {"data": {"dataset": _meta(ds), "view": view}})

    async def t_profile(_ctx: ToolContext, args: dict) -> ToolResult:
        ds = get(args.get("dataset"))
        prof = profile(ds, args.get("columns"))
        lines = [f"{prof['rows']} rows in the current view, {prof['duplicates']} duplicate row(s)."]
        for c in prof["columns"]:
            stats = f"{c['column']} ({c['type']}): {c['count']} values, {c['nulls']} empty, {c['distinct']} distinct"
            if "mean" in c:
                stats += f"; min {c['min']}, max {c['max']}, mean {c['mean']}, median {c['median']}, std {c['std']}"
            elif "min" in c:
                stats += f"; first {str(c['min'])[:30]!r}, last {str(c['max'])[:30]!r}"
            if c["top"]:
                stats += "; top " + ", ".join(f"{k[:30]!r} x{v}" for k, v in c["top"][:3])
            lines.append("- " + stats)
        return ToolResult("\n".join(lines), f"profile {ds['id']}", {"data": {"profile": prof, "dataset": _meta(ds)}})

    async def t_export(ctx: ToolContext, args: dict) -> ToolResult:
        from .tools import resolve, rel

        ds = get(args.get("dataset"))
        fmt = str(args.get("format") or "csv").lower()
        if fmt not in ("csv", "xlsx", "json"):
            raise ToolError("format: csv, xlsx or json")
        data, _mime, name = export_bytes(ds, fmt, "all" if args.get("all_rows") else "view")
        target = resolve(ctx, str(args.get("path") or f"exports/{name}"))
        if target.suffix.lower() != f".{fmt}":
            target = target.with_suffix(f".{fmt}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return ToolResult(f"Exported {ds['id']} to {rel(ctx, target)} ({len(data)} bytes).", rel(ctx, target),
                          {"files": [rel(ctx, target)], "data": {"export": rel(ctx, target)}})

    def query_permission(args: dict) -> Optional[str]:
        return "data" if is_write(str(args.get("sql") or "")) else None

    S = {"type": "string"}
    filt = {"type": "array", "items": {"type": "object", "properties": {"column": S, "op": {"type": "string", "enum": list(OPS)},
                                                                         "value": {}}, "required": ["column", "op"]}}
    tools = {
        "data_sources": ToolDef("data_sources", "List the data sources (database MCP servers, SQLite / CSV / XLSX files of the project) and their tables.",
                                {"type": "object", "properties": {"tables": {"type": "boolean"}}}, t_sources),
        "data_query": ToolDef("data_query", "Run SQL on a data source (id from data_sources). The result becomes a dataset the user sees as a table in "
                              "the Data view; you get its first rows. Write statements need the user's confirmation unless in auto mode.",
                              {"type": "object", "properties": {"source": S, "sql": S, "title": {**S, "description": "Short name for the result"},
                                                                "max_rows": {"type": "integer"}}, "required": ["source", "sql"]},
                              t_query, meta={"data": True}, dynamic_permission=query_permission),
        "data_view": ToolDef("data_view", "Filter / sort / pick the columns of a dataset AS THE USER SEES IT in the Data view (the grid updates "
                             "live), e.g. when the user asks 'show only France sorted by revenue'. Ops: " + ", ".join(OPS) + ".",
                             {"type": "object", "properties": {"dataset": S, "filters": filt,
                                                               "sort": {"type": "array", "items": {"type": "object", "properties": {"column": S, "desc": {"type": "boolean"}}, "required": ["column"]}},
                                                               "columns": {"type": "array", "items": S, "description": "Columns to show, in order"},
                                                               "search": S, "reset": {"type": "boolean"}}, "required": ["dataset"]},
                             t_view, meta={"data": True}),
        "data_profile": ToolDef("data_profile", "Column statistics of a dataset's current view: types, empties, distinct, min / max / mean / median, "
                                "top values, duplicate rows. Use it to check data quality.",
                                {"type": "object", "properties": {"dataset": S, "columns": {"type": "array", "items": S}}, "required": ["dataset"]},
                                t_profile, meta={"data": True}),
    }
    if not read_only:
        tools["data_export"] = ToolDef("data_export", "Save a dataset (its current view, or all_rows) as CSV, XLSX or JSON in the project.",
                                       {"type": "object", "properties": {"dataset": S, "format": {"type": "string", "enum": ["csv", "xlsx", "json"]},
                                                                         "path": S, "all_rows": {"type": "boolean"}}, "required": ["dataset", "format"]},
                                       t_export, "edit", meta={"data": True})
    return tools


def system_section() -> str:
    return ("\n\n# Data analysis\nYou can query databases and data files: data_sources lists them, data_query runs SQL and shows the "
            "result to the user as a table, data_view changes what the user sees (filters, sort, columns), data_profile checks "
            "data quality, data_export saves CSV / XLSX. Prefer small, explicit SELECTs with a LIMIT; never change data unless asked.")
