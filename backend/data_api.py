"""HTTP API of the data analysis add-in (see backend/data.py)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import data, journal
from .config import load_config, permission_mode
from .session import Session, store

router = APIRouter()


def _get(sid: str) -> Session:
    s = store.get(sid)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _ds(s: Session, ds_id: str) -> dict:
    try:
        return data.load_dataset(s, ds_id)
    except data.DataError as e:
        raise HTTPException(404, str(e)) from e


class QueryIn(BaseModel):
    source: str
    sql: str
    title: str = ""
    maxRows: Optional[int] = None
    confirmWrite: bool = False


class TableIn(BaseModel):
    source: str
    table: str


class FileIn(BaseModel):
    path: str


class ViewIn(BaseModel):
    view: dict
    offset: int = 0
    limit: int = 200


class TitleIn(BaseModel):
    title: str


@router.get("/api/sessions/{sid}/data/sources")
async def sources(sid: str) -> dict:
    s = _get(sid)
    cfg = load_config()
    return {"enabled": cfg["data"].get("enabled", True), "sources": await data.list_sources(s),
            "datasets": s.data.get("datasets", []), "history": (s.data.get("dataHistory") or [])[-50:],
            "mode": permission_mode(cfg, s.data)}


@router.get("/api/sessions/{sid}/data/tables")
async def tables(sid: str, source: str) -> list:
    try:
        return await data.list_tables(_get(sid), source)
    except data.DataError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/sessions/{sid}/data/columns")
async def columns(sid: str, source: str, table: str) -> list:
    try:
        return await data.table_columns(_get(sid), source, table)
    except data.DataError as e:
        raise HTTPException(400, str(e)) from e


async def _run(s: Session, source: str, sql: str, title: str, max_rows: Optional[int], confirm: bool) -> dict:
    cfg = load_config()
    limit = max(1, min(int(max_rows or cfg["data"].get("maxRows") or 5000), 200_000))
    write_ok = confirm or permission_mode(cfg, s.data) == "auto"
    try:
        res = await data.run_sql(s, source, sql, limit, write_ok)
    except data.DataError as e:
        if str(e) == "WRITE_NEEDS_CONFIRMATION":
            raise HTTPException(409, {"needsConfirmation": True, "message": "This statement changes data: confirm to run it."}) from e
        data.history_add(s, source, sql, False, 0, "user")
        raise HTTPException(400, str(e)) from e
    data.history_add(s, source, sql, True, len(res["rows"]), "user")
    journal.log(s, "data", source=source, sql=sql[:1000], rows=len(res["rows"]), write=res["write"], by="user")
    ds = data.make_dataset(s, res["columns"], res["rows"], title=title or sql.strip().splitlines()[0], source=source, sql=sql,
                           created_by="user", truncated=res["truncated"], elapsed_ms=res["elapsedMs"], affected=res["affected"])
    return data.page(ds)


@router.post("/api/sessions/{sid}/data/query")
async def query(sid: str, body: QueryIn) -> dict:
    """SQL typed by the user in the Data view. Writes need confirmWrite unless the session is in auto mode."""
    if not body.sql.strip():
        raise HTTPException(400, "Empty query")
    return await _run(_get(sid), body.source, body.sql, body.title, body.maxRows, body.confirmWrite)


@router.post("/api/sessions/{sid}/data/open-table")
async def open_table(sid: str, body: TableIn) -> dict:
    s = _get(sid)
    src = next((x for x in await data.list_sources(s) if x["id"] == body.source), None)
    dialect = (src or {}).get("dialect") or "generic"
    limit = int(load_config()["data"].get("maxRows") or 5000)
    return await _run(s, body.source, data.preview_sql(body.table, dialect, min(limit, 1000)), body.table, limit, False)


@router.post("/api/sessions/{sid}/data/open-file")
async def open_file(sid: str, body: FileIn) -> dict:
    s = _get(sid)
    from pathlib import Path

    ext = Path(body.path).suffix.lower()
    if ext in data.LOCAL_DB_EXT:
        raise HTTPException(400, "SQLite database: pick a table in the sources list")
    if ext not in data.LOCAL_FILE_EXT:
        raise HTTPException(400, "Supported files: CSV, TSV, JSON, JSON Lines, XLSX")
    try:
        path = data._local_path(s, body.path)
        limit = int(load_config()["data"].get("maxRows") or 5000)
        cols, rows = data.read_local_file(path, limit + 1)
    except (data.DataError, OSError, ValueError, KeyError, IndexError) as e:
        raise HTTPException(400, f"Cannot read {body.path}: {e}") from e
    ds = data.make_dataset(s, cols, rows[:limit], title=body.path, source=f"file:{body.path}", sql="", created_by="user",
                           truncated=len(rows) > limit)
    return data.page(ds)


@router.get("/api/sessions/{sid}/data/datasets")
async def datasets(sid: str) -> list:
    return _get(sid).data.get("datasets", [])


@router.get("/api/sessions/{sid}/data/datasets/{ds_id}")
async def dataset_page(sid: str, ds_id: str, offset: int = 0, limit: int = 200) -> dict:
    s = _get(sid)
    return data.page(_ds(s, ds_id), max(0, offset), max(1, min(limit, 2000)))


@router.put("/api/sessions/{sid}/data/datasets/{ds_id}/view")
async def dataset_view(sid: str, ds_id: str, body: ViewIn) -> dict:
    s = _get(sid)
    ds = _ds(s, ds_id)
    data.set_view(s, ds, body.view, "user")
    return data.page(ds, max(0, body.offset), max(1, min(body.limit, 2000)))


@router.patch("/api/sessions/{sid}/data/datasets/{ds_id}")
async def dataset_rename(sid: str, ds_id: str, body: TitleIn) -> dict:
    s = _get(sid)
    ds = _ds(s, ds_id)
    ds["title"] = body.title.strip()[:120] or ds["title"]
    data.save_dataset(s, ds)
    return data._meta(ds)


@router.delete("/api/sessions/{sid}/data/datasets/{ds_id}")
async def dataset_delete(sid: str, ds_id: str) -> dict:
    data.delete_dataset(_get(sid), ds_id)
    return {"ok": True}


@router.get("/api/sessions/{sid}/data/datasets/{ds_id}/profile")
async def dataset_profile(sid: str, ds_id: str) -> dict:
    s = _get(sid)
    return data.profile(_ds(s, ds_id))


@router.get("/api/sessions/{sid}/data/datasets/{ds_id}/aggregate")
async def dataset_aggregate(sid: str, ds_id: str, x: str, y: str = "", agg: str = "count", limit: int = 50) -> dict:
    s = _get(sid)
    try:
        return data.aggregate(_ds(s, ds_id), x, y or None, agg, max(1, min(limit, 500)))
    except data.DataError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/sessions/{sid}/data/datasets/{ds_id}/export")
async def dataset_export(sid: str, ds_id: str, format: str = "csv", scope: str = "view") -> Response:
    s = _get(sid)
    ds = _ds(s, ds_id)
    content, mime, name = data.export_bytes(ds, format if format in ("csv", "xlsx", "json") else "csv", "all" if scope == "all" else "view")
    journal.log(s, "data", export=format, dataset=ds_id, bytes=len(content))
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})
