from typing import Any

from psycopg2.extras import Json
from fastapi import APIRouter, Header, HTTPException

from pydantic import BaseModel

from services.core.db import get_db
from services.api.deps import get_user_from_header, require_auth

router = APIRouter()


class FilesUpdate(BaseModel):
    files: list[dict[str, Any]]


@router.get("/user/case")
async def get_user_case(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, matter, created_at FROM case_intakes "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"case": None}
    return {"case": {"id": str(row[0]), "matter": row[1], "created_at": row[2].isoformat()}}


@router.get("/user/cases")
async def list_user_cases(
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, matter, files, created_at FROM case_intakes "
            "WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r[0]),
            "matter": r[1],
            "file_count": len(r[2]) if r[2] else 0,
            "created_at": r[3].isoformat(),
        }
        for r in rows
    ]


@router.get("/case/{case_id}")
async def get_case_detail(
    case_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, personal, matter, files, created_at, user_id FROM case_intakes WHERE id = %s",
            (case_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found")
    if row[5] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {
        "id": str(row[0]),
        "personal": row[1],
        "matter": row[2],
        "files": row[3] or [],
        "created_at": row[4].isoformat(),
    }


@router.delete("/case/{case_id}", status_code=204)
async def delete_case(
    case_id: str,
    authorization: str | None = Header(default=None),
) -> None:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM case_intakes WHERE id = %s", (case_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute("DELETE FROM case_chunks WHERE case_id = %s", (case_id,))
        cur.execute("DELETE FROM case_intakes WHERE id = %s", (case_id,))


@router.patch("/case/{case_id}/files")
async def update_case_files(
    case_id: str,
    req: FilesUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, files FROM case_intakes WHERE id = %s", (case_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        old_keys = {f["key"] for f in (row[1] or []) if f.get("key")}
        new_keys = {f["key"] for f in req.files if f.get("key")}
        removed_keys = old_keys - new_keys
        if removed_keys:
            cur.execute(
                "DELETE FROM case_chunks WHERE case_id = %s AND metadata->>'source_key' = ANY(%s)",
                (case_id, list(removed_keys)),
            )

        cur.execute(
            "UPDATE case_intakes SET files = %s WHERE id = %s",
            (Json(req.files), case_id),
        )
    return {"ok": True}


@router.get("/case/{case_id}/timeline")
def get_timeline(
    case_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM case_intakes WHERE id::text = %s", (case_id,))
        owner = cur.fetchone()
        if not owner:
            raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
        if owner[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute(
            "SELECT date, category, event_type, subject, summary, content, attachments "
            "FROM case_events WHERE case_id = %s ORDER BY date ASC",
            (case_id,),
        )
        rows = cur.fetchall()
    return {
        "case_id": case_id,
        "total": len(rows),
        "events": [
            {
                "date": str(r[0]),
                "category": r[1],
                "event_type": r[2],
                "subject": r[3],
                "summary": r[4],
                "content": r[5],
                "attachments": r[6],
            }
            for r in rows
        ],
    }
