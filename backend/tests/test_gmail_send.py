import base64
import unittest
from unittest.mock import AsyncMock, patch
from email import policy
from email.parser import BytesParser

import httpx
from fastapi import HTTPException

from app.routers.integrations import GMAIL_SEND_SCOPE, send_gmail_reply
from app.routers.emails import approve_reply
from app.security import AuthenticatedUser


class FakeDb:
    def __init__(self, reply_status: str = "APPROVED", granted_scopes: str = GMAIL_SEND_SCOPE) -> None:
        self.reply = {
            "id": "reply-1", "user_id": "user-1", "email_id": "email-1",
            "status": reply_status, "generated_reply": "Thanks, I will attend.",
        }
        self.granted_scopes = granted_scopes
        self.updates: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def select(self, table, query, *, limit=None):
        if table == "ai_replies":
            return [self.reply]
        if table == "emails":
            return [{
                "sender_email": "sender@example.com", "sender_name": "Sender",
                "subject": "Meeting", "message_id": "<original@example.com>",
                "thread_id": "internal-thread-id", "source_type": "GMAIL",
            }]
        if table == "email_threads":
            return [{"thread_reference": "gmail-thread-id"}]
        if table == "gmail_connections":
            return [{
                "encrypted_refresh_token": "encrypted", "granted_scopes": self.granted_scopes,
                "google_email": "owner@example.com",
            }]
        raise AssertionError(f"Unexpected table {table}")

    async def update(self, table, query, values):
        status_filter = query.get("status", "")
        if status_filter.startswith("in.("):
            allowed = status_filter[4:-1].split(",")
            if self.reply.get("status") not in allowed:
                return []
        elif status_filter.startswith("eq.") and self.reply.get("status") != status_filter[3:]:
            return []
        self.updates.append((table, dict(query), dict(values)))
        self.reply.update(values)
        return [dict(self.reply)]


class GmailSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_approved_drafts_can_be_sent(self) -> None:
        user = AuthenticatedUser(id="user-1", email="owner@example.com", _access_token="token")
        db = FakeDb(reply_status="DRAFT")
        with patch("app.routers.integrations.user_client", return_value=db):
            with self.assertRaises(HTTPException) as raised:
                await send_gmail_reply("reply-1", user)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(db.updates, [])

    async def test_already_sent_reply_is_not_sent_again(self) -> None:
        user = AuthenticatedUser(id="user-1", email="owner@example.com", _access_token="token")
        db = FakeDb(reply_status="SENT")
        with patch("app.routers.integrations.user_client", return_value=db):
            result = await send_gmail_reply("reply-1", user)

        self.assertEqual(result["status"], "SENT")
        self.assertEqual(db.updates, [])

    async def test_sent_reply_cannot_be_approved_again(self) -> None:
        user = AuthenticatedUser(id="user-1", email="owner@example.com", _access_token="token")
        db = FakeDb(reply_status="SENT")
        with patch("app.routers.emails.user_client", return_value=db):
            with self.assertRaises(HTTPException) as raised:
                await approve_reply("reply-1", user)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(db.reply["status"], "SENT")

    async def test_send_requires_gmail_send_scope(self) -> None:
        user = AuthenticatedUser(id="user-1", email="owner@example.com", _access_token="token")
        db = FakeDb(granted_scopes="https://www.googleapis.com/auth/gmail.readonly")
        with patch("app.routers.integrations.user_client", return_value=db):
            with self.assertRaises(HTTPException) as raised:
                await send_gmail_reply("reply-1", user)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Reconnect Gmail", raised.exception.detail)
        self.assertEqual(db.updates, [])

    async def test_approved_reply_is_sent_threaded_and_marked_sent(self) -> None:
        user = AuthenticatedUser(id="user-1", email="owner@example.com", _access_token="token")
        db = FakeDb()
        request = AsyncMock(return_value=httpx.Response(
            200,
            json={"id": "gmail-message-id", "threadId": "gmail-thread-id"},
            request=httpx.Request("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"),
        ))
        client = type("FakeHttpClient", (), {"post": request})()
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None
        with (
            patch("app.routers.integrations.user_client", return_value=db),
            patch("app.routers.integrations._access_token", new_callable=AsyncMock, return_value="access-token"),
            patch("app.routers.integrations.httpx.AsyncClient", return_value=client_context),
        ):
            result = await send_gmail_reply("reply-1", user)

        self.assertEqual(result["status"], "SENT")
        self.assertEqual(result["gmail_message_id"], "gmail-message-id")
        self.assertEqual([update[2]["status"] for update in db.updates], ["SENDING", "SENT"])
        self.assertEqual(db.updates[0][1]["status"], "eq.APPROVED")
        self.assertEqual(db.updates[1][1]["status"], "eq.SENDING")
        payload = request.await_args.kwargs["json"]
        self.assertEqual(request.await_args.kwargs["headers"]["Authorization"], "Bearer access-token")
        self.assertEqual(payload["threadId"], "gmail-thread-id")
        raw = base64.urlsafe_b64decode(payload["raw"] + "=" * (-len(payload["raw"]) % 4))
        message = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(message["To"], "Sender <sender@example.com>")
        self.assertEqual(message["In-Reply-To"], "<original@example.com>")
        self.assertEqual(message["Subject"], "Re: Meeting")
        self.assertEqual(message.get_content().strip(), "Thanks, I will attend.")


if __name__ == "__main__":
    unittest.main()
