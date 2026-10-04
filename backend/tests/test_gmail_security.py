import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx

from app.config import Settings
from app.main import app
from app.routers.integrations import (
    _create_state,
    _decrypt_token,
    _encrypt_token,
    _google_api_error,
    _needs_processing,
    _verify_state,
)


class GmailSecurityTests(unittest.TestCase):
    def test_gmail_api_failures_report_actionable_google_causes(self) -> None:
        not_enabled = Mock(status_code=403)
        not_enabled.json.return_value = {"error": {"errors": [{"reason": "accessNotConfigured"}]}}
        missing_scope = Mock(status_code=403)
        missing_scope.json.return_value = {"error": {"errors": [{"reason": "insufficientPermissions"}]}}

        self.assertIn("Gmail API is not enabled", _google_api_error(not_enabled, "Gmail message listing").detail)
        self.assertIn("connect it again", _google_api_error(missing_scope, "Gmail message listing").detail)

    def test_gmail_api_failure_does_not_echo_google_error_message(self) -> None:
        response = Mock(status_code=403)
        response.json.return_value = {
            "error": {"message": "private account detail", "errors": [{"reason": "forbidden"}]}
        }

        error = _google_api_error(response, "Gmail message listing")

        self.assertIn("Google HTTP 403, forbidden", error.detail)
        self.assertNotIn("private account detail", error.detail)

    def test_oauth_state_is_signed_and_bound_to_its_user(self) -> None:
        settings = SimpleNamespace(app_secret_key="s" * 40)
        with patch("app.routers.integrations.get_settings", return_value=settings):
            state = _create_state("user-1")
            _verify_state(state, "user-1")
            with self.assertRaises(HTTPException):
                _verify_state(state, "user-2")
            tampered = state[:-1] + ("x" if state[-1] != "x" else "y")
            with self.assertRaises(HTTPException):
                _verify_state(tampered, "user-1")

    def test_gmail_refresh_tokens_are_encrypted_before_storage(self) -> None:
        settings = SimpleNamespace(gmail_token_fernet_key=Fernet.generate_key().decode("ascii"))
        with patch("app.routers.integrations.get_settings", return_value=settings):
            ciphertext = _encrypt_token("refresh-token-value")

            self.assertNotIn("refresh-token-value", ciphertext)
            self.assertEqual(_decrypt_token(ciphertext), "refresh-token-value")

    def test_gmail_and_existing_gemini_env_names_are_supported(self) -> None:
        settings = Settings(
            _env_file=None,
            GMAIL_CLIENT_ID="client-id",
            GMAIL_CLIENT_SECRET="client-secret",
            GMAIL_REDIRECT_URI="http://localhost:8000/integrations/gmail/callback",
            GEMINI_API_KEY="gemini-key",
        )

        self.assertEqual(settings.google_client_id, "client-id")
        self.assertEqual(settings.google_client_secret, "client-secret")
        self.assertEqual(
            settings.google_redirect_uri,
            "http://localhost:8000/integrations/gmail/callback",
        )
        self.assertEqual(settings.gemini_api_key, "gemini-key")


class GmailCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_callback_validates_state_then_redirects_to_frontend(self) -> None:
        settings = SimpleNamespace(
            app_secret_key="s" * 40,
            frontend_url="http://localhost:3000",
        )
        with patch("app.routers.integrations.get_settings", return_value=settings):
            state = _create_state("user-1")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/integrations/gmail/callback",
                    params={"code": "one-time-code", "state": state},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 302)
        location = urlsplit(response.headers["location"])
        self.assertEqual(
            f"{location.scheme}://{location.netloc}{location.path}",
            "http://localhost:3000/integrations/gmail/callback",
        )
        query = parse_qs(location.query)
        self.assertEqual(query["code"], ["one-time-code"])
        self.assertEqual(query["state"], [state])
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_backend_callback_rejects_invalid_state(self) -> None:
        settings = SimpleNamespace(app_secret_key="s" * 40, frontend_url="http://localhost:3000")
        with patch("app.routers.integrations.get_settings", return_value=settings):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/integrations/gmail/callback",
                    params={"code": "one-time-code", "state": "invalid"},
                )

        self.assertEqual(response.status_code, 400)


class GmailProcessingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_duplicate_is_queued_for_processing(self) -> None:
        class FakeDb:
            async def select(self, table, query, *, limit=None):
                if table == "email_processing_runs":
                    return [{"status": "FAILED"}]
                raise AssertionError(f"unexpected query for {table}")

        self.assertTrue(await _needs_processing(FakeDb(), "user-1", "email-1"))

    async def test_completed_duplicate_is_skipped_when_analysis_and_vectors_exist(self) -> None:
        class FakeDb:
            async def select(self, table, query, *, limit=None):
                if table == "email_processing_runs":
                    return [{"status": "COMPLETED"}]
                if table == "ai_analyses":
                    return [{"id": "analysis-1"}]
                if table == "email_chunks":
                    return [{"id": "chunk-1"}, {"id": "chunk-2"}]
                if table == "embeddings":
                    return [{"chunk_id": "chunk-1"}, {"chunk_id": "chunk-2"}]
                raise AssertionError(f"unexpected query for {table}")

        self.assertFalse(await _needs_processing(FakeDb(), "user-1", "email-1"))

    async def test_completed_duplicate_missing_embedding_is_reprocessed(self) -> None:
        class FakeDb:
            async def select(self, table, query, *, limit=None):
                if table == "email_processing_runs":
                    return [{"status": "COMPLETED"}]
                if table == "ai_analyses":
                    return [{"id": "analysis-1"}]
                if table == "email_chunks":
                    return [{"id": "chunk-1"}, {"id": "chunk-2"}]
                if table == "embeddings":
                    return [{"chunk_id": "chunk-1"}]
                raise AssertionError(f"unexpected query for {table}")

        self.assertTrue(await _needs_processing(FakeDb(), "user-1", "email-1"))


if __name__ == "__main__":
    unittest.main()
