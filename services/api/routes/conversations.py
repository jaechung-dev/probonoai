from typing import Any

from psycopg2.extras import Json
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, field_validator

from services.core.db import get_db
from services.api.deps import require_auth

router = APIRouter(prefix="/conversations")

VALID_ROLES = {"user", "assistant"}


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    case_id: str | None = None


class ConversationPatch(BaseModel):
    title: str


class ConversationMessageItem(BaseModel):
    role: str
    content: str
    sources: list[dict[str, Any]] | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")
        return v


@router.get("")
async def list_conversations(
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, updated_at, case_id FROM conversations "
            "WHERE user_id = %s ORDER BY updated_at DESC LIMIT 50",
            (user_id,),
        )
        rows = cur.fetchall()
    return [
        {"id": str(r[0]), "title": r[1], "updated_at": r[2].isoformat(), "case_id": r[3]}
        for r in rows
    ]


@router.post("", status_code=201)
async def create_conversation(
    req: ConversationCreate,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (user_id, title, case_id) VALUES (%s, %s, %s) "
            "RETURNING id, title, updated_at, case_id",
            (user_id, req.title[:200], req.case_id),
        )
        row = cur.fetchone()
    return {"id": str(row[0]), "title": row[1], "updated_at": row[2].isoformat(), "case_id": row[3]}


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, updated_at, case_id, user_id FROM conversations WHERE id = %s",
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[4] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute(
            "SELECT id, role, content, sources, created_at FROM conversation_messages "
            "WHERE conversation_id = %s ORDER BY created_at ASC",
            (conv_id,),
        )
        msgs = cur.fetchall()
    return {
        "id": str(row[0]),
        "title": row[1],
        "updated_at": row[2].isoformat(),
        "case_id": row[3],
        "messages": [
            {
                "id": str(m[0]),
                "role": m[1],
                "content": m[2],
                "sources": m[3],
                "created_at": m[4].isoformat(),
            }
            for m in msgs
        ],
    }


@router.delete("/{conv_id}", status_code=204)
async def delete_conversation(
    conv_id: str,
    authorization: str | None = Header(default=None),
) -> None:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM conversations WHERE id = %s", (conv_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute("DELETE FROM conversations WHERE id = %s", (conv_id,))


@router.patch("/{conv_id}")
async def patch_conversation(
    conv_id: str,
    req: ConversationPatch,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM conversations WHERE id = %s", (conv_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        cur.execute(
            "UPDATE conversations SET title = %s, updated_at = NOW() WHERE id = %s "
            "RETURNING id, title, updated_at, case_id",
            (req.title[:200], conv_id),
        )
        updated = cur.fetchone()
    return {
        "id": str(updated[0]),
        "title": updated[1],
        "updated_at": updated[2].isoformat(),
        "case_id": updated[3],
    }


@router.post("/{conv_id}/messages", status_code=201)
async def append_messages(
    conv_id: str,
    messages: list[ConversationMessageItem],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = require_auth(authorization)
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM conversations WHERE id = %s", (conv_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        for msg in messages:
            cur.execute(
                "INSERT INTO conversation_messages (conversation_id, role, content, sources) "
                "VALUES (%s, %s, %s, %s)",
                (conv_id, msg.role, msg.content, Json(msg.sources) if msg.sources else None),
            )
        cur.execute("UPDATE conversations SET updated_at = NOW() WHERE id = %s", (conv_id,))
    return {"ok": True, "count": len(messages)}
