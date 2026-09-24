import os
from typing import Any
from uuid import uuid4

from psycopg2.extras import Json
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services.core.db import get_db
from services.core.settings import settings
from services.api.deps import get_user_from_header

router = APIRouter()

# Boto3 is provided by the Lambda runtime; None in local dev without AWS SDK installed.
try:
    import boto3 as _boto3
    _s3 = _boto3.client("s3", region_name=settings.AWS_REGION_NAME)
except ImportError:
    _s3 = None


class UploadUrlRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    case_id: str | None = None


class IntakeRequest(BaseModel):
    personal: dict[str, Any]
    matter: dict[str, Any]
    files: list[dict[str, Any]] = []


class TextSnippetRequest(BaseModel):
    text: str
    filename: str = "pasted-text.txt"
    case_id: str | None = None


@router.post("/intake/upload-url")
async def intake_upload_url(
    req: UploadUrlRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _s3 or not settings.UPLOADS_BUCKET:
        raise HTTPException(status_code=501, detail="File uploads not configured")
    user_id = get_user_from_header(authorization)
    safe_name = os.path.basename(req.filename)
    key = f"intakes/{user_id}/{uuid4()}/{safe_name}"
    params: dict[str, Any] = {
        "Bucket": settings.UPLOADS_BUCKET,
        "Key": key,
        "ContentType": req.content_type,
    }
    if req.case_id:
        params["Metadata"] = {"case-id": req.case_id}
    url = _s3.generate_presigned_url("put_object", Params=params, ExpiresIn=300, HttpMethod="PUT")
    return {"upload_url": url, "key": key, "max_bytes": 25 * 1024 * 1024}


@router.post("/intake/paste-text")
async def intake_paste_text(
    req: TextSnippetRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if not _s3 or not settings.UPLOADS_BUCKET:
        raise HTTPException(status_code=501, detail="File uploads not configured")
    user_id = get_user_from_header(authorization)
    body = req.text.encode("utf-8")
    if len(body) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Text exceeds 25 MB limit")
    safe_name = os.path.basename(req.filename)
    if not safe_name.endswith(".txt"):
        safe_name += ".txt"
    key = f"intakes/{user_id}/{uuid4()}/{safe_name}"
    _s3.put_object(
        Bucket=settings.UPLOADS_BUCKET,
        Key=key,
        Body=body,
        ContentType="text/plain",
        Metadata={"case-id": req.case_id} if req.case_id else {},
    )
    return {"key": key, "name": safe_name, "size": len(body)}


@router.post("/intake")
async def submit_intake(
    req: IntakeRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user_id = get_user_from_header(authorization)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO case_intakes (user_id, personal, matter, files) "
            "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (user_id, Json(req.personal), Json(req.matter), Json(req.files)),
        )
        row = cur.fetchone()
    return {"ok": True, "id": str(row[0]), "created_at": row[1].isoformat()}
