import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from email.utils import parsedate_to_datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.schemas import GmailExchange
from app.security import AuthenticatedUser, require_permission, user_client
from app.services.email_pipeline import process_email

router = APIRouter(prefix="/integrations", tags=["integrations"])
GmailUser = Annotated[AuthenticatedUser, Depends(require_permission("emails:import"))]
RepliesUser = Annotated[AuthenticatedUser, Depends(require_permission("smart_replies:manage"))]
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly "
    f"{GMAIL_SEND_SCOPE} https://www.googleapis.com/auth/userinfo.email"
)


@router.get("/gmail/callback")
async def gmail_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if not state:
        raise HTTPException(status_code=400, detail="Google callback is missing its OAuth state.")
    _read_state(state)
    if not code and not error:
        raise HTTPException(status_code=400, detail="Google callback is missing its authorization code.")
    query = {"state": state}
    if code:
        query["code"] = code
    if error:
        query["error"] = "access_denied"
    response = RedirectResponse(
        f"{get_settings().frontend_url.rstrip('/')}/integrations/gmail/callback?{urlencode(query)}",
        status_code=302,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/gmail/status")
async def gmail_status(user: GmailUser) -> dict[str, Any]:
    rows = await user_client(user).select(
        "gmail_connections",
        {"select": "google_email,last_synced_at,last_history_id,created_at",
         "user_id": f"eq.{user.id}"},
        limit=1,
    )
    return {"connected": bool(rows), **(rows[0] if rows else {})}


@router.post("/gmail/authorize")
async def gmail_authorize(user: GmailUser) -> dict[str, str]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on the backend.")
    state = _create_state(user.id)
    query = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return {"authorization_url": f"https://accounts.google.com/o/oauth2/v2/auth?{query}"}


@router.post("/gmail/exchange")
async def gmail_exchange(body: GmailExchange, user: GmailUser) -> dict[str, str]:
    _verify_state(body.state, user.id)
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on the backend.")
    if not settings.gmail_token_fernet_key:
        raise HTTPException(status_code=503, detail="Set GMAIL_TOKEN_FERNET_KEY to encrypt Gmail refresh tokens.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": body.code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.is_error:
            raise HTTPException(status_code=502, detail="Google rejected the OAuth authorization code.")
        token_response = response.json()
        access_token = token_response.get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="Google did not return an access token.")
        profile_response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if profile_response.is_error:
        raise HTTPException(status_code=502, detail="Could not verify the connected Google account.")
    profile = profile_response.json()
    db = user_client(user)
    existing = await db.select(
        "gmail_connections",
        {"select": "encrypted_refresh_token", "user_id": f"eq.{user.id}"},
        limit=1,
    )
    refresh_token = token_response.get("refresh_token")
    encrypted = _encrypt_token(refresh_token) if refresh_token else (
        existing[0].get("encrypted_refresh_token") if existing else None
    )
    if not encrypted:
        raise HTTPException(
            status_code=409,
            detail="Google did not issue a refresh token. Revoke this app in Google Account settings and reconnect.",
        )
    await db.insert(
        "gmail_connections",
        {"user_id": user.id, "google_email": profile.get("email"),
         "encrypted_refresh_token": encrypted, "granted_scopes": token_response.get("scope", GMAIL_SCOPES)},
        upsert=True,
        on_conflict="user_id",
    )
    return {"status": "connected", "google_email": str(profile.get("email") or "")}


@router.post("/gmail/sync", status_code=202)
async def sync_gmail(
    user: GmailUser,
    background_tasks: BackgroundTasks,
    max_messages: int = 25,
    page_token: str | None = None,
) -> dict[str, Any]:
    if not 1 <= max_messages <= 50:
        raise HTTPException(status_code=422, detail="max_messages must be between 1 and 50.")
    connection = await _connection(user)
    token = await _access_token(connection)
    async with httpx.AsyncClient(timeout=30) as client:
        listing = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={key: value for key, value in {
                "maxResults": max_messages, "pageToken": page_token
            }.items() if value},
            headers={"Authorization": f"Bearer {token}"},
        )
        if listing.is_error:
            raise _google_api_error(listing, "Gmail message listing")
        listing_data = listing.json()
        items = listing_data.get("messages", [])
        db = user_client(user)
        imported, skipped, reprocessed = 0, 0, 0
        for item in items:
            message_id = item.get("id")
            if not message_id:
                continue
            result = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                params={"format": "full"},
                headers={"Authorization": f"Bearer {token}"},
            )
            if result.is_error:
                raise _google_api_error(result, "Gmail message retrieval")
            message = result.json()
            row, is_new = await _gmail_email(db, user.id, message)
            if row is None:
                skipped += 1
                continue
            if not is_new:
                if await _needs_processing(db, user.id, str(row["id"])):
                    background_tasks.add_task(process_email, user, row)
                    reprocessed += 1
                else:
                    skipped += 1
                continue
            else:
                imported += 1
            background_tasks.add_task(process_email, user, row)
        await db.update(
            "gmail_connections", {"id": f"eq.{connection['id']}"},
            {"last_synced_at": datetime.now(UTC).isoformat(),
             "last_history_id": listing_data.get("historyId")},
        )
    return {"status": "queued", "imported": imported, "skipped": skipped,
            "reprocessed": reprocessed,
            "next_page_token": listing_data.get("nextPageToken")}


@router.post("/gmail/replies/{reply_id}/send")
async def send_gmail_reply(reply_id: str, user: RepliesUser) -> dict[str, Any]:
    db = user_client(user)
    reply_rows = await db.select(
        "ai_replies",
        {"select": "*", "id": f"eq.{reply_id}", "user_id": f"eq.{user.id}"},
        limit=1,
    )
    if not reply_rows:
        raise HTTPException(status_code=404, detail="Reply draft not found.")
    reply = reply_rows[0]
    reply_status = str(reply.get("status") or "").upper()
    if reply_status == "SENT":
        return reply
    if reply_status != "APPROVED":
        raise HTTPException(status_code=409, detail="Approve this reply draft before sending it.")

    email_rows = await db.select(
        "emails",
        {"select": "sender_email,sender_name,subject,message_id,thread_id,source_type",
         "id": f"eq.{reply.get('email_id')}", "user_id": f"eq.{user.id}"},
        limit=1,
    )
    if not email_rows:
        raise HTTPException(status_code=404, detail="The original email was not found.")
    original = email_rows[0]
    if original.get("source_type") == "GMAIL" and original.get("thread_id"):
        threads = await db.select(
            "email_threads",
            {"select": "thread_reference", "id": f"eq.{original['thread_id']}",
             "user_id": f"eq.{user.id}"},
            limit=1,
        )
        if threads:
            original["gmail_thread_reference"] = threads[0].get("thread_reference")
    recipient = str(original.get("sender_email") or "").strip()
    if not recipient:
        raise HTTPException(status_code=422, detail="The original email has no sender address to reply to.")

    connection = await _connection(user)
    granted_scopes = str(connection.get("granted_scopes") or "").split()
    if GMAIL_SEND_SCOPE not in granted_scopes:
        raise HTTPException(
            status_code=409,
            detail="Gmail send permission is missing. Reconnect Gmail to grant permission to send replies.",
        )

    reply_body = str(reply.get("edited_reply") or reply.get("generated_reply") or "").strip()
    if not reply_body:
        raise HTTPException(status_code=422, detail="The approved reply draft is empty.")
    sending = await db.update(
        "ai_replies",
        {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}", "status": "eq.APPROVED"},
        {"status": "SENDING"},
    )
    if not sending:
        raise HTTPException(status_code=409, detail="This reply is no longer available to send.")
    try:
        access_token = await _access_token(connection)
    except HTTPException:
        await db.update(
            "ai_replies", {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}",
                           "status": "eq.SENDING"},
            {"status": "APPROVED"},
        )
        raise
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                json=_build_gmail_reply(reply, original, recipient, reply_body),
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="The send result could not be confirmed. Check Gmail Sent mail before trying again.",
        ) from exc
    if response.is_error:
        await db.update(
            "ai_replies", {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}",
                           "status": "eq.SENDING"},
            {"status": "APPROVED"},
        )
        raise _google_api_error(response, "Gmail reply sending")
    sent = response.json()
    gmail_message_id = str(sent.get("id") or "")
    if not gmail_message_id:
        raise HTTPException(
            status_code=502,
            detail="Gmail accepted the request but returned no message ID; verify Sent mail before retrying.",
        )
    updated = await db.update(
        "ai_replies",
        {"id": f"eq.{reply_id}", "user_id": f"eq.{user.id}", "status": "eq.SENDING"},
        {"status": "SENT", "sent_at": datetime.now(UTC).isoformat(),
         "gmail_message_id": gmail_message_id},
    )
    if not updated:
        raise HTTPException(
            status_code=502,
            detail="Gmail sent the reply, but the draft status could not be updated. Check Sent mail before retrying.",
        )
    return updated[0]


def _build_gmail_reply(
    reply: dict[str, Any],
    original: dict[str, Any],
    recipient: str,
    reply_body: str,
) -> dict[str, Any]:
    message = EmailMessage()
    sender_name = str(original.get("sender_name") or "").strip()
    message["To"] = formataddr((sender_name, recipient)) if sender_name else recipient
    original_subject = str(original.get("subject") or "").strip()
    message["Subject"] = (
        original_subject if original_subject.casefold().startswith("re:")
        else f"Re: {original_subject}" if original_subject else "Re:"
    )
    original_message_id = str(original.get("message_id") or "").strip()
    if original_message_id:
        message["In-Reply-To"] = original_message_id
        message["References"] = original_message_id
    message["Message-ID"] = f"<letterwise-reply-{reply['id']}@letterwise.local>"
    message.set_content(reply_body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    payload: dict[str, Any] = {"raw": raw}
    if original.get("source_type") == "GMAIL" and original.get("gmail_thread_reference"):
        payload["threadId"] = str(original["gmail_thread_reference"])
    return payload


def _google_api_error(response: httpx.Response, operation: str) -> HTTPException:
    reason = ""
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            reasons = error.get("errors", [])
            if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict):
                reason = str(reasons[0].get("reason") or "")
            reason = reason or str(error.get("status") or "")
    except (ValueError, AttributeError):
        pass

    if reason == "accessNotConfigured":
        detail = "Gmail API is not enabled for the Google Cloud project. Enable the Gmail API, then retry."
    elif reason == "insufficientPermissions":
        detail = "Google authorization is missing the required Gmail permission. Disconnect Gmail and connect it again."
    elif reason in {"authError", "invalidCredentials"} or response.status_code == 401:
        detail = "Google rejected the Gmail authorization. Disconnect Gmail and connect it again."
    elif reason in {"rateLimitExceeded", "userRateLimitExceeded"}:
        detail = "Google rate-limited Gmail access. Wait briefly, then retry."
    else:
        safe_reason = reason if reason.isascii() and reason.replace("_", "").isalnum() else ""
        suffix = f", {safe_reason}" if safe_reason else ""
        detail = f"{operation} failed (Google HTTP {response.status_code}{suffix})."
    return HTTPException(status_code=502, detail=detail)


@router.delete("/gmail", status_code=204)
async def disconnect_gmail(user: GmailUser) -> None:
    await user_client(user).delete("gmail_connections", {"user_id": f"eq.{user.id}"})


async def _needs_processing(db: Any, user_id: str, email_id: str) -> bool:
    runs = await db.select(
        "email_processing_runs",
        {"select": "status", "email_id": f"eq.{email_id}", "order": "created_at.desc"},
        limit=1,
    )
    if runs and str(runs[0].get("status", "")).upper() == "PROCESSING":
        return False
    if runs and str(runs[0].get("status", "")).upper() == "FAILED":
        return True

    analyses = await db.select(
        "ai_analyses", {"select": "id", "email_id": f"eq.{email_id}"}, limit=1
    )
    if not analyses:
        return True
    chunks = await db.select(
        "email_chunks",
        {"select": "id", "user_id": f"eq.{user_id}", "email_id": f"eq.{email_id}"},
    )
    if not chunks:
        return True
    chunk_ids = [str(chunk["id"]) for chunk in chunks if chunk.get("id")]
    if len(chunk_ids) != len(chunks):
        return True
    embeddings = await db.select(
        "embeddings",
        {"select": "chunk_id", "user_id": f"eq.{user_id}",
         "chunk_id": f"in.({','.join(chunk_ids)})",
         "model": f"eq.{get_settings().embedding_model}"},
    )
    return {str(row.get("chunk_id")) for row in embeddings} != set(chunk_ids)


async def _connection(user: AuthenticatedUser) -> dict[str, Any]:
    rows = await user_client(user).select(
        "gmail_connections", {"select": "*", "user_id": f"eq.{user.id}"}, limit=1
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No Gmail account is connected.")
    return rows[0]


async def _access_token(connection: dict[str, Any]) -> str:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on the backend.")
    refresh_token = _decrypt_token(connection["encrypted_refresh_token"])
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.is_error:
        raise HTTPException(status_code=502, detail="Google token refresh failed; reconnect your Gmail account.")
    token = response.json().get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="Google returned no refreshed access token.")
    return token


async def _gmail_email(
    db: Any, user_id: str, message: dict[str, Any]
) -> tuple[dict[str, Any] | None, bool]:
    payload = message.get("payload") or {}
    headers = {
        str(header.get("name", "")).casefold(): str(header.get("value", ""))
        for header in payload.get("headers", [])
    }
    gmail_id = str(message.get("id") or "")
    rfc_message_id = headers.get("message-id", "").strip() or f"<gmail-{gmail_id}>"
    duplicate = await db.select(
        "emails",
        {"select": "id", "user_id": f"eq.{user_id}", "message_id": f"eq.{rfc_message_id}"},
        limit=1,
    )
    if duplicate:
        existing = await db.select(
            "emails", {"select": "*", "id": f"eq.{duplicate[0]['id']}"}, limit=1
        )
        return (existing[0], False) if existing else (None, False)
    text, html = _message_bodies(payload)
    sender_name, sender_email = _split_address(headers.get("from", ""))
    receiver_name, receiver_email = _split_address(headers.get("to", ""))
    internal_date = message.get("internalDate")
    date = datetime.fromtimestamp(int(internal_date) / 1000, UTC) if internal_date else None
    if not date and headers.get("date"):
        try:
            date = parsedate_to_datetime(headers["date"])
        except (TypeError, ValueError, OverflowError):
            date = None
    subject = headers.get("subject") or "(no subject)"
    thread_ref = str(message.get("threadId") or gmail_id)
    threads = await db.select(
        "email_threads",
        {"select": "id,email_count,first_email_at,last_email_at", "user_id": f"eq.{user_id}",
         "thread_reference": f"eq.{thread_ref}"},
        limit=1,
    )
    if threads:
        thread = threads[0]
        thread_id = thread["id"]
        await db.update(
            "email_threads", {"id": f"eq.{thread_id}"},
            {"email_count": int(thread.get("email_count") or 0) + 1,
             "first_email_at": _earlier_timestamp(
                 thread.get("first_email_at"), date.isoformat() if date else None
             ),
             "last_email_at": _later_timestamp(
                 thread.get("last_email_at"), date.isoformat() if date else None
             )},
        )
    else:
        thread = await db.insert(
            "email_threads",
            {"user_id": user_id, "subject": subject, "thread_reference": thread_ref,
             "first_email_at": date.isoformat() if date else None,
             "last_email_at": date.isoformat() if date else None, "email_count": 1},
        )
        thread_id = thread["id"]
    row = await db.insert(
        "emails",
        {"user_id": user_id, "thread_id": thread_id, "sender_email": sender_email or None,
         "sender_name": sender_name or None, "receiver_email": receiver_email or None,
         "receiver_name": receiver_name or None, "cc": headers.get("cc"),
         "subject": subject[:998], "body_text": text, "body_html": html,
         "preview": text[:300], "message_id": rfc_message_id,
         "in_reply_to": headers.get("in-reply-to"), "email_date": date.isoformat() if date else None,
         "status": "UNREAD", "is_starred": "starred" in message.get("labelIds", []),
         "source_type": "GMAIL", "raw_email": json.dumps(payload)[:2_000_000]},
    )
    return row, True


def _message_bodies(payload: dict[str, Any]) -> tuple[str, str | None]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    stack = [payload]
    while stack:
        part = stack.pop()
        stack.extend(reversed(part.get("parts") or []))
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        try:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
                "utf-8", errors="replace"
            )
        except (ValueError, UnicodeError):
            continue
        mime = part.get("mimeType")
        if mime == "text/plain":
            text_parts.append(decoded)
        elif mime == "text/html":
            html_parts.append(decoded)
    html = "\n".join(html_parts) or None
    text = "\n".join(text_parts)
    if not text and html:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(html, "html.parser").get_text("\n")
    return text, html


def _split_address(value: str) -> tuple[str, str]:
    from email.utils import parseaddr
    return parseaddr(value)


def _earlier_timestamp(current: Any, candidate: Any) -> Any:
    if not isinstance(current, str) or not isinstance(candidate, str):
        return current or candidate
    try:
        current_date = datetime.fromisoformat(current.replace("Z", "+00:00"))
        candidate_date = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return candidate
    return candidate if candidate_date < current_date else current


def _later_timestamp(current: Any, candidate: Any) -> Any:
    if not isinstance(current, str) or not isinstance(candidate, str):
        return candidate or current
    try:
        current_date = datetime.fromisoformat(current.replace("Z", "+00:00"))
        candidate_date = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return candidate
    return candidate if candidate_date > current_date else current


def _fernet() -> Fernet:
    key = get_settings().gmail_token_fernet_key
    if not key:
        raise HTTPException(status_code=503, detail="Gmail token encryption is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="GMAIL_TOKEN_FERNET_KEY is invalid.") from exc


def _encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def _decrypt_token(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise HTTPException(status_code=503, detail="The stored Gmail token could not be decrypted.") from exc


def _create_state(user_id: str) -> str:
    secret = get_settings().app_secret_key
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Set APP_SECRET_KEY to a random value of at least 32 characters.")
    payload = {"sub": user_id, "exp": int(time.time()) + 600, "nonce": secrets.token_urlsafe(18)}
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def _verify_state(state: str, user_id: str) -> None:
    payload = _read_state(state)
    if payload["sub"] != user_id:
        raise HTTPException(status_code=400, detail="The OAuth state is invalid or expired.")


def _read_state(state: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.app_secret_key or "." not in state:
        raise HTTPException(status_code=400, detail="The OAuth state is invalid or expired.")
    encoded, signature = state.split(".", 1)
    expected = _b64(hmac.new(settings.app_secret_key.encode(), encoded.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="The OAuth state signature is invalid.")
    try:
        payload = json.loads(_unb64(encoded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="The OAuth state payload is invalid.") from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("sub"), str)
        or not isinstance(payload.get("exp"), int)
        or payload["exp"] < time.time()
    ):
        raise HTTPException(status_code=400, detail="The OAuth state is invalid or expired.")
    return payload


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
