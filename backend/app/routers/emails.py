import csv
import io
import json
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Annotated, Any

from bs4 import BeautifulSoup
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import EmailCreate, EmailImportResult, EmailStatus, EmailUpdate, ReplyRequest, ReplyUpdate
from app.security import AuthenticatedUser, permission_granted, require_permission, user_client
from app.services.ai import chat
from app.services.email_pipeline import process_email, save_email

router = APIRouter(tags=["emails"])
EmailReader = Annotated[AuthenticatedUser, Depends(require_permission("emails:read"))]
EmailImporter = Annotated[AuthenticatedUser, Depends(require_permission("emails:import"))]
AiReader = Annotated[AuthenticatedUser, Depends(require_permission("ai_analysis:read"))]
RepliesUser = Annotated[AuthenticatedUser, Depends(require_permission("smart_replies:manage"))]


@router.get("/emails")
async def list_emails(
    user: EmailReader,
    q: str | None = Query(default=None, max_length=200),
    status_filter: EmailStatus | None = Query(default=None, alias="status"),
    starred: bool | None = None,
    category_id: str | None = None,
    sender_email: str | None = None,
    reply_required: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    db = user_client(user)
    filters: dict[str, str] = {
        "select": "*", "user_id": f"eq.{user.id}",
        "order": "email_date.desc.nullslast,created_at.desc",
    }
    if status_filter:
        filters["status"] = f"eq.{status_filter.value}"
    if starred is not None:
        filters["is_starred"] = f"eq.{str(starred).lower()}"
    if category_id:
        filters["category_id"] = f"eq.{category_id}"
    if sender_email:
        filters["sender_email"] = f"ilike.*{_postgrest_literal(sender_email)}*"
    if q and not await permission_granted(user, "emails:search"):
        raise HTTPException(status_code=403, detail="Permission required: emails:search")
    if reply_required is not None:
        filters["reply_required"] = f"eq.{str(reply_required).lower()}"
    filters["is_deleted"] = "eq.false"
    offset = 0 if q else (page - 1) * page_size
    rows = await db.select("emails", filters, limit=min(1000, page_size if not q else 1000))
    if q:
        query = q.casefold()
        rows = [
            row for row in rows
            if query in str(row.get("subject") or "").casefold()
            or query in str(row.get("body_text") or "").casefold()
            or query in str(row.get("sender_email") or "").casefold()
            or query in str(row.get("sender_name") or "").casefold()
        ]
        offset = (page - 1) * page_size
    return {"items": rows[offset:offset + page_size], "page": page, "page_size": page_size,
            "total": len(rows) if q else None}


@router.post("/emails", status_code=status.HTTP_201_CREATED)
async def create_email(
    body: EmailCreate,
    background_tasks: BackgroundTasks,
    user: EmailImporter,
) -> dict[str, Any]:
    db = user_client(user)
    row = body.model_dump(mode="json")
    row["source_type"] = "MANUAL"
    row["preview"] = (body.body_text or BeautifulSoup(body.body_html or "", "html.parser").get_text(" "))[:300]
    saved = await save_email(db, user.id, row)
    background_tasks.add_task(process_email, user, saved)
    return saved


@router.post("/emails/import", response_model=EmailImportResult, status_code=201)
async def import_emails(
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks,
    user: EmailImporter,
) -> EmailImportResult:
    settings = get_settings()
    content = await file.read(settings.max_import_bytes + 1)
    if len(content) > settings.max_import_bytes:
        raise HTTPException(status_code=413, detail="Import file exceeds the configured size limit.")
    name = (file.filename or "").strip()
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    import_type = {"eml": "EML", "csv": "CSV", "json": "JSON"}.get(extension)
    if not import_type:
        raise HTTPException(status_code=415, detail="Supported import formats are .eml, .csv, and .json.")
    records = _parse_import(extension, content)
    db = user_client(user)
    import_row = await db.insert(
        "email_imports",
        {"user_id": user.id, "import_type": import_type, "file_name": name,
         "total_records": len(records), "successful_records": 0, "failed_records": 0,
         "status": "PROCESSING"},
    )
    imported: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        try:
            email_data = EmailCreate.model_validate(record)
            row = email_data.model_dump(mode="json")
            row["source_type"] = "IMPORT"
            row["preview"] = (email_data.body_text or "")[:300]
            saved = await save_email(db, user.id, row)
            imported.append(saved)
            background_tasks.add_task(process_email, user, saved)
        except ValidationError as exc:
            errors.append(f"Record {index}: {exc.errors()[0]['msg']}")
    await db.update(
        "email_imports", {"id": f"eq.{import_row['id']}", "user_id": f"eq.{user.id}"},
        {"successful_records": len(imported), "failed_records": len(errors),
         "status": "COMPLETED" if not errors else ("FAILED" if not imported else "COMPLETED"),
         "error_message": "\n".join(errors)[:2000] or None, "completed_at": datetime.now(UTC).isoformat()},
    )
    return EmailImportResult(imported=len(imported), failed=len(errors), errors=errors, emails=imported)


@router.get("/emails/{email_id}")
async def email_detail(email_id: str, user: EmailReader) -> dict[str, Any]:
    if not await permission_granted(user, "ai_analysis:read"):
        raise HTTPException(status_code=403, detail="Permission required: ai_analysis:read")
    db = user_client(user)
    rows = await db.select(
        "emails", {"select": "*", "id": f"eq.{email_id}", "user_id": f"eq.{user.id}"}, limit=1
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Email not found.")
    result: dict[str, Any] = {"email": rows[0]}
    for table in ("ai_analyses", "extracted_information", "action_items", "deadlines",
                  "ai_replies", "email_attachments", "email_processing_runs"):
        filters = {"select": "*", "email_id": f"eq.{email_id}",
                   "order": "created_at.desc"}
        if table in {"action_items", "deadlines", "ai_replies"}:
            filters["user_id"] = f"eq.{user.id}"
        result[table] = await db.select(table, filters)
    thread_id = rows[0].get("thread_id")
    result["thread_emails"] = (
        await db.select(
            "emails", {"select": "id,sender_name,sender_email,subject,body_text,email_date",
                       "user_id": f"eq.{user.id}",
                       "thread_id": f"eq.{thread_id}", "order": "email_date.asc"},
            limit=100,
        )
        if thread_id else [rows[0]]
    )
    return result


@router.patch("/emails/{email_id}")
async def update_email(email_id: str, body: EmailUpdate, user: EmailImporter) -> dict[str, Any]:
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(status_code=422, detail="At least one field must be supplied.")
    db = user_client(user)
    rows = await db.update(
        "emails", {"id": f"eq.{email_id}", "user_id": f"eq.{user.id}"}, values
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Email not found.")
    return rows[0]


@router.delete("/emails/{email_id}", status_code=204)
async def delete_email(email_id: str, user: EmailImporter) -> None:
    db = user_client(user)
    updated = await db.update(
        "emails", {"id": f"eq.{email_id}", "user_id": f"eq.{user.id}"},
        {"is_deleted": True, "status": "DELETED"},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Email not found.")


@router.get("/emails/{email_id}/analysis")
async def email_analysis(email_id: str, user: AiReader) -> dict[str, Any]:
    db = user_client(user)
    email = await db.select(
        "emails", {"select": "id", "id": f"eq.{email_id}", "user_id": f"eq.{user.id}"}, limit=1
    )
    if not email:
        raise HTTPException(status_code=404, detail="Email not found.")
    analyses = await db.select(
        "ai_analyses", {"select": "*", "email_id": f"eq.{email_id}"}
    )
    information = await db.select(
        "extracted_information", {"select": "*", "email_id": f"eq.{email_id}"}
    )
    return {"analysis": analyses, "extracted_information": information}


@router.post("/emails/{email_id}/reprocess", status_code=202)
async def reprocess_email(email_id: str, background_tasks: BackgroundTasks, user: AiReader) -> dict[str, str]:
    rows = await user_client(user).select(
        "emails", {"select": "*", "id": f"eq.{email_id}", "user_id": f"eq.{user.id}"}, limit=1
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Email not found.")
    background_tasks.add_task(process_email, user, rows[0])
    return {"status": "queued"}


@router.post("/emails/{email_id}/replies", status_code=201)
async def generate_reply(email_id: str, body: ReplyRequest, user: RepliesUser) -> dict[str, Any]:
    db = user_client(user)
    emails = await db.select(
        "emails",
        {"select": "id,subject,body_text,sender_name",
         "id": f"eq.{email_id}", "user_id": f"eq.{user.id}"},
        limit=1,
    )
    if not emails:
        raise HTTPException(status_code=404, detail="Email not found.")
    email = emails[0]
    reply = await chat(
        [
            {"role": "system", "content": (
                "Draft a helpful reply to the email. Do not send it. Match the requested tone, "
                "avoid inventing facts, and return only the reply body."
            )},
            {"role": "user", "content": (
                f"Tone: {body.tone}\nSubject: {email.get('subject') or ''}\n"
                f"Sender: {email.get('sender_name') or ''}\n\n"
                f"Email:\n{str(email.get('body_text') or '')[:18000]}"
            )},
        ],
        db=db,
        user_id=user.id,
        email_id=email_id,
        operation="smart_reply",
    )
    return await db.insert(
        "ai_replies",
        {"user_id": user.id, "email_id": email_id, "tone": body.tone.upper(),
         "generated_reply": reply, "status": "DRAFT", "generation_count": 1},
    )


@router.patch("/replies/{reply_id}")
async def edit_reply(reply_id: str, body: ReplyUpdate, user: RepliesUser) -> dict[str, Any]:
    db = user_client(user)
    rows = await db.update(
        "ai_replies",
        {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}",
         "status": "in.(DRAFT,EDITED)"},
        {"edited_reply": body.edited_reply, "status": "EDITED"},
    )
    if not rows:
        existing = await db.select(
            "ai_replies", {"select": "id,status", "id": f"eq.{reply_id}",
                           "user_id": f"eq.{user.id}"}, limit=1
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Reply not found.")
        raise HTTPException(status_code=409, detail="Only an unsent draft can be edited.")
    return rows[0]


@router.post("/replies/{reply_id}/approve")
async def approve_reply(reply_id: str, user: RepliesUser) -> dict[str, Any]:
    db = user_client(user)
    rows = await db.update(
        "ai_replies",
        {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}",
         "status": "in.(DRAFT,EDITED)"},
        {"status": "APPROVED", "approved_at": datetime.now(UTC).isoformat()},
    )
    if not rows:
        existing = await db.select(
            "ai_replies", {"select": "id,status", "id": f"eq.{reply_id}",
                           "user_id": f"eq.{user.id}"}, limit=1
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Reply not found.")
        if existing[0].get("status") == "APPROVED":
            return existing[0]
        raise HTTPException(status_code=409, detail="This reply can no longer be approved.")
    return rows[0]


@router.post("/replies/{reply_id}/reject")
async def reject_reply(reply_id: str, user: RepliesUser) -> dict[str, Any]:
    db = user_client(user)
    rows = await db.update(
        "ai_replies",
        {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}",
         "status": "in.(DRAFT,EDITED,APPROVED)"},
        {"status": "REJECTED", "rejected_at": datetime.now(UTC).isoformat()},
    )
    if not rows:
        existing = await db.select(
            "ai_replies", {"select": "id,status", "id": f"eq.{reply_id}",
                           "user_id": f"eq.{user.id}"}, limit=1
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Reply not found.")
        raise HTTPException(status_code=409, detail="This reply can no longer be rejected.")
    return rows[0]


@router.get("/threads")
async def list_threads(user: EmailReader, limit: int = Query(default=50, ge=1, le=100)) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "email_threads",
        {"select": "*", "user_id": f"eq.{user.id}", "order": "last_email_at.desc"},
        limit=limit,
    )


def _postgrest_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("*", "\\*").replace(",", "\\,").replace("(", "\\(").replace(")", "\\)")


def _parse_import(extension: str, content: bytes) -> list[dict[str, Any]]:
    if extension == "eml":
        message = BytesParser(policy=policy.default).parsebytes(content)
        text_parts: list[str] = []
        html_parts: list[str] = []
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() in {"text/plain", "text/html"}:
                    value = part.get_content()
                    if isinstance(value, str):
                        (text_parts if part.get_content_type() == "text/plain" else html_parts).append(value)
        else:
            value = message.get_content()
            if isinstance(value, str):
                (text_parts if message.get_content_type() == "text/plain" else html_parts).append(value)
        body_html = "\n".join(html_parts) or None
        body_text = "\n".join(text_parts) or (
            BeautifulSoup(body_html, "html.parser").get_text("\n") if body_html else ""
        )
        sender_name, sender_email = parseaddr(str(message.get("From", "")))
        receiver_name, receiver_email = parseaddr(str(message.get("To", "")))
        return [{
            "sender_name": sender_name or None, "sender_email": sender_email or None,
            "receiver_name": receiver_name or None, "receiver_email": receiver_email or None,
            "subject": str(message.get("Subject") or "(no subject)"),
            "body_text": body_text, "body_html": body_html,
            "message_id": str(message.get("Message-ID") or "").strip() or None,
            "in_reply_to": str(message.get("In-Reply-To") or "").strip() or None,
            "email_date": message.get("Date"),
            "raw_email": content.decode("utf-8", errors="replace"),
        }]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Import files must use UTF-8 text encoding.") from exc
    if extension == "json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="The JSON import file is malformed.") from exc
        if isinstance(value, dict):
            value = value.get("emails", [value])
        if not isinstance(value, list) or len(value) > 1000 or not all(isinstance(row, dict) for row in value):
            raise HTTPException(status_code=422, detail="JSON imports must be an object or an array of up to 1000 email objects.")
        if not value:
            raise HTTPException(status_code=422, detail="JSON import files must contain at least one email record.")
        return [_normalize_import_record(record) for record in value]
    try:
        reader = csv.DictReader(io.StringIO(text))
        records = list(reader)
    except csv.Error as exc:
        raise HTTPException(status_code=422, detail="The CSV import file is malformed.") from exc
    if len(records) > 1000:
        raise HTTPException(status_code=422, detail="CSV imports are limited to 1000 records.")
    if not records:
        raise HTTPException(status_code=422, detail="CSV import files must contain a header and at least one email record.")
    return [_normalize_import_record(record) for record in records]


_IMPORT_FIELD_ALIASES = {
    "from": "sender_email",
    "from_email": "sender_email",
    "sender": "sender_email",
    "sender_address": "sender_email",
    "to": "receiver_email",
    "to_email": "receiver_email",
    "recipient": "receiver_email",
    "recipient_email": "receiver_email",
    "email_subject": "subject",
    "title": "subject",
    "body": "body_text",
    "content": "body_text",
    "text": "body_text",
    "message": "body_text",
    "email_body": "body_text",
    "html": "body_html",
    "date": "email_date",
    "received_at": "email_date",
    "timestamp": "email_date",
    "starred": "is_starred",
}
_EMAIL_ADDRESS_FIELDS = {"sender_email", "receiver_email"}


def _normalize_import_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_name, value in record.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip().casefold().replace("-", "_").replace(" ", "_")
        if not name:
            continue
        field = _IMPORT_FIELD_ALIASES.get(name, name)
        if isinstance(value, str):
            value = value.strip() or None
        if field in _EMAIL_ADDRESS_FIELDS and isinstance(value, str):
            display_name, address = parseaddr(value)
            if address:
                value = address
                name_field = "sender_name" if field == "sender_email" else "receiver_name"
                if display_name and name_field not in normalized:
                    normalized[name_field] = display_name
        if field == "email_date" and isinstance(value, str):
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                parsed = None
            if parsed is not None:
                value = parsed.isoformat()
        normalized[field] = value
    return normalized
