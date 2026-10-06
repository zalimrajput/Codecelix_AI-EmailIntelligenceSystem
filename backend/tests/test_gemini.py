import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.services.ai import (
    VECTOR_DIMENSIONS,
    _gemini_request,
    _provider_error_detail,
    chat,
    embed_texts,
    openrouter_request,
)


class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def test_openrouter_request_uses_openrouter_endpoint_and_key(self) -> None:
        settings = SimpleNamespace(
            openrouter_api_key="router-test-key",
            frontend_url="http://localhost:3000",
        )
        request = AsyncMock(return_value=httpx.Response(
            200,
            json={"result": "ok"},
            request=httpx.Request("POST", "https://openrouter.ai"),
        ))
        client = SimpleNamespace(post=request)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai.httpx.AsyncClient", return_value=client_context),
        ):
            result = await openrouter_request("chat/completions", {"model": "test/model"})

        self.assertEqual(result, {"result": "ok"})
        self.assertEqual(
            request.await_args.args[0],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(
            request.await_args.kwargs["headers"]["Authorization"],
            "Bearer router-test-key",
        )

    async def test_gemini_quota_failure_falls_back_to_openrouter(self) -> None:
        settings = SimpleNamespace(
            ai_provider="gemini",
            openrouter_api_key="router-key",
            openrouter_chat_model="openai/gpt-4o-mini",
            gemini_chat_model="gemini-flash-latest",
        )
        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch(
                "app.services.ai._gemini_chat",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=429, detail="quota exhausted"),
            ),
            patch("app.services.ai.openrouter_request", new_callable=AsyncMock) as request,
        ):
            request.return_value = {
                "choices": [{"message": {"content": '{"intent":"review"}'}}]
            }
            result = await chat(
                [{"role": "user", "content": "Analyze this message."}],
                json_mode=True,
            )

        self.assertEqual(result, '{"intent":"review"}')
        request.assert_awaited_once_with(
            "chat/completions",
            {
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "Analyze this message."}],
                "response_format": {"type": "json_object"},
            },
            db=None,
            user_id=None,
            email_id=None,
            operation="ai_assistant",
            model="openai/gpt-4o-mini",
        )

    async def test_gemini_permission_error_does_not_fall_back(self) -> None:
        settings = SimpleNamespace(
            ai_provider="gemini",
            openrouter_api_key="router-key",
            gemini_chat_model="gemini-flash-latest",
        )
        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch(
                "app.services.ai._gemini_chat",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=403, detail="Gemini denied API access"),
            ),
            patch("app.services.ai.openrouter_request", new_callable=AsyncMock) as request,
        ):
            with self.assertRaises(HTTPException):
                await chat([{"role": "user", "content": "Analyze this message."}])

        request.assert_not_awaited()

    async def test_gemini_request_retries_transient_503_then_succeeds(self) -> None:
        settings = SimpleNamespace(gemini_api_key="test-key")
        request = AsyncMock()
        request.side_effect = [
            httpx.Response(
                503, json={"error": {"status": "UNAVAILABLE"}},
                request=httpx.Request("POST", "https://example.test"),
            ),
            httpx.Response(
                200, json={"result": "ok"},
                request=httpx.Request("POST", "https://example.test"),
            ),
        ]
        client = SimpleNamespace(post=request)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai.httpx.AsyncClient", return_value=client_context),
            patch("app.services.ai.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await _gemini_request("models/test:generateContent", {"contents": []})

        self.assertEqual(result, {"result": "ok"})
        self.assertEqual(request.await_count, 2)
        sleep.assert_awaited_once_with(0.5)

    async def test_persistent_gemini_503_returns_retryable_unavailable_error(self) -> None:
        settings = SimpleNamespace(gemini_api_key="test-key")
        request = AsyncMock(return_value=httpx.Response(
            503,
            json={"error": {"status": "UNAVAILABLE"}},
            request=httpx.Request("POST", "https://example.test"),
        ))
        client = SimpleNamespace(post=request)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai.httpx.AsyncClient", return_value=client_context),
            patch("app.services.ai.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            with self.assertRaises(HTTPException) as raised:
                await _gemini_request("models/test:generateContent", {"contents": []})

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("temporarily unavailable", raised.exception.detail)
        self.assertIn("after automatic retries", raised.exception.detail)
        self.assertEqual(request.await_count, 4)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [0.5, 1.0, 2.0],
        )

    async def test_gemini_request_retries_rate_limit_then_succeeds(self) -> None:
        settings = SimpleNamespace(gemini_api_key="test-key")
        request = AsyncMock(side_effect=[
            httpx.Response(
                429,
                json={"error": {"status": "RESOURCE_EXHAUSTED"}},
                headers={"Retry-After": "1"},
                request=httpx.Request("POST", "https://example.test"),
            ),
            httpx.Response(
                200,
                json={"result": "ok"},
                request=httpx.Request("POST", "https://example.test"),
            ),
        ])
        client = SimpleNamespace(post=request)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai.httpx.AsyncClient", return_value=client_context),
            patch("app.services.ai.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await _gemini_request("models/test:generateContent", {"contents": []})

        self.assertEqual(result, {"result": "ok"})
        self.assertEqual(request.await_count, 2)
        sleep.assert_awaited_once_with(1.0)

    async def test_gemini_request_does_not_wait_for_long_quota_retry(self) -> None:
        settings = SimpleNamespace(gemini_api_key="test-key")
        request = AsyncMock(return_value=httpx.Response(
            429,
            json={"error": {"status": "RESOURCE_EXHAUSTED"}},
            headers={"Retry-After": "60"},
            request=httpx.Request("POST", "https://example.test"),
        ))
        client = SimpleNamespace(post=request)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai.httpx.AsyncClient", return_value=client_context),
            patch("app.services.ai.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            with self.assertRaises(HTTPException) as raised:
                await _gemini_request("models/test:generateContent", {"contents": []})

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("quota exceeded", raised.exception.detail)
        self.assertIn("trying again in about 1 minute", raised.exception.detail)
        self.assertEqual(raised.exception.headers, {"Retry-After": "60"})
        request.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_gemini_permission_error_preserves_status_for_fallback_decision(self) -> None:
        settings = SimpleNamespace(gemini_api_key="test-key")
        request = AsyncMock(return_value=httpx.Response(
            403,
            json={"error": {"status": "PERMISSION_DENIED"}},
            request=httpx.Request("POST", "https://example.test"),
        ))
        client = SimpleNamespace(post=request)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai.httpx.AsyncClient", return_value=client_context),
        ):
            with self.assertRaises(HTTPException) as raised:
                await _gemini_request("models/test:generateContent", {"contents": []})

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("denied API access", raised.exception.detail)

    async def test_gemini_quota_error_formats_long_retry_as_hours_and_minutes(self) -> None:
        response = httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "45151s",
                    }],
                }
            },
        )

        detail = _provider_error_detail("Gemini", response)

        self.assertIn("about 12 hours and 32 minutes", detail)
        self.assertNotIn("45151 seconds", detail)

    async def test_default_gemini_chat_model_is_current_api_model(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.gemini_chat_model, "gemini-flash-latest")

    async def test_provider_settings_keep_gemini_primary_with_openrouter_fallback(self) -> None:
        settings = Settings(
            _env_file=None,
            gemini_api_key="gemini-key",
            openrouter_api_key="openrouter-key",
        )

        self.assertEqual(settings.ai_provider, "gemini")
        self.assertEqual(settings.chat_model, "gemini-flash-latest")
        self.assertEqual(settings.embedding_provider, "gemini")
        self.assertEqual(settings.embedding_model, "gemini-embedding-001")

        openrouter_only = Settings(_env_file=None, openrouter_api_key="openrouter-key")
        self.assertEqual(openrouter_only.ai_provider, "openrouter")
        self.assertEqual(openrouter_only.embedding_provider, "openrouter")

    async def test_gemini_quota_failure_has_actionable_detail_without_provider_payload(self) -> None:
        response = httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "provider payload that should not be shown",
                }
            },
        )

        detail = _provider_error_detail("Gemini", response)

        self.assertIn("quota exceeded", detail)
        self.assertIn("RESOURCE_EXHAUSTED", detail)
        self.assertNotIn("provider payload", detail)

    async def test_ai_provider_key_error_identifies_backend_configuration(self) -> None:
        response = httpx.Response(401, json={"error": {"message": "secret provider detail"}})

        detail = _provider_error_detail("Gemini", response)

        self.assertIn("Check the backend API key", detail)
        self.assertNotIn("secret provider detail", detail)

    async def test_chat_maps_system_instruction_and_json_mode(self) -> None:
        settings = SimpleNamespace(
            ai_provider="gemini",
            embedding_provider="gemini",
            chat_model="gemini-flash-latest",
            gemini_chat_model="gemini-flash-latest",
            embedding_model="gemini-embedding-001",
        )
        provider_response = {
            "candidates": [{"content": {"parts": [{"text": '{"intent":"review"}'}]}}],
            "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 4, "totalTokenCount": 12},
        }
        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai._gemini_request", new_callable=AsyncMock) as request,
        ):
            request.return_value = provider_response
            result = await chat(
                [
                    {"role": "system", "content": "Return valid JSON."},
                    {"role": "user", "content": "Analyze this message."},
                ],
                json_mode=True,
            )

        self.assertEqual(result, '{"intent":"review"}')
        request.assert_awaited_once_with(
            "models/gemini-flash-latest:generateContent",
            {
                "contents": [{"role": "user", "parts": [{"text": "Analyze this message."}]}],
                "systemInstruction": {"parts": [{"text": "Return valid JSON."}]},
                "generationConfig": {"responseMimeType": "application/json"},
            },
        )

    async def test_embeddings_use_database_dimension_and_retrieval_task(self) -> None:
        settings = SimpleNamespace(
            ai_provider="gemini",
            embedding_provider="gemini",
            chat_model="gemini-flash-latest",
            embedding_model="gemini-embedding-001",
        )
        vector = [0.0] * VECTOR_DIMENSIONS
        response = {
            "embeddings": [{"values": vector}],
            "usageMetadata": {"promptTokenCount": 5, "totalTokenCount": 5},
        }
        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai._gemini_request", new_callable=AsyncMock) as request,
        ):
            request.return_value = response
            result = await embed_texts(["find the invoice"], task_type="RETRIEVAL_QUERY")

        self.assertEqual(result, [vector])
        request.assert_awaited_once_with(
            "models/gemini-embedding-001:batchEmbedContents",
            {
                "requests": [
                    {
                        "model": "models/gemini-embedding-001",
                        "content": {"parts": [{"text": "find the invoice"}]},
                        "taskType": "RETRIEVAL_QUERY",
                        "outputDimensionality": VECTOR_DIMENSIONS,
                    }
                ]
            },
        )

    async def test_batch_embedding_without_usage_metadata_still_returns_vectors(self) -> None:
        settings = SimpleNamespace(
            ai_provider="gemini",
            embedding_provider="gemini",
            chat_model="gemini-flash-latest",
            embedding_model="gemini-embedding-001",
        )
        db = SimpleNamespace(insert=AsyncMock())
        vector = [0.0] * VECTOR_DIMENSIONS
        with (
            patch("app.services.ai.get_settings", return_value=settings),
            patch("app.services.ai._gemini_request", new_callable=AsyncMock) as request,
        ):
            request.return_value = {"embeddings": [{"values": vector}]}
            result = await embed_texts(
                ["stored email chunk"],
                db=db,
                user_id="user-1",
                email_id="email-1",
            )

        self.assertEqual(result, [vector])
        db.insert.assert_not_awaited()
        request.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
