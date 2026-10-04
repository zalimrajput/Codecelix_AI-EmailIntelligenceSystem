import asyncio
import json
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.supabase import Supabase


VECTOR_DIMENSIONS = 1536
EmbeddingTask = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


async def openai_request(
    path: str,
    payload: dict[str, Any],
    *,
    db: Supabase | None = None,
    user_id: str | None = None,
    email_id: str | None = None,
    operation: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="AI services are not configured. Set OPENAI_API_KEY on the backend.",
        )
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            f"https://api.openai.com/v1/{path}",
            json=payload,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )
    if response.is_error:
        raise HTTPException(
            status_code=502,
            detail=_provider_error_detail("OpenAI", response),
        )
    result = response.json()
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="AI provider returned an invalid response.")
    if db and user_id and operation and model:
        usage = result.get("usage")
        if not isinstance(usage, dict):
            raise HTTPException(status_code=502, detail="AI provider omitted token usage information.")
        try:
            input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
            output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
            total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="AI provider returned invalid token usage.") from exc
        if min(input_tokens, output_tokens, total_tokens) < 0:
            raise HTTPException(status_code=502, detail="AI provider returned invalid token usage.")
        await db.insert(
            "ai_usage_logs",
            {"user_id": user_id, "email_id": email_id, "operation": operation,
             "ai_model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
             "total_tokens": total_tokens,
             "processing_time_ms": int((time.perf_counter() - started) * 1000),
             "status": "COMPLETED"},
        )
    return result


async def embed_texts(
    texts: list[str],
    *,
    db: Supabase | None = None,
    user_id: str | None = None,
    email_id: str | None = None,
    operation: str = "email_embeddings",
    task_type: EmbeddingTask = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    if settings.ai_provider == "gemini":
        return await _gemini_embed_texts(
            texts,
            db=db,
            user_id=user_id,
            email_id=email_id,
            operation=operation,
            task_type=task_type,
        )
    if settings.ai_provider != "openai":
        raise HTTPException(
            status_code=503,
            detail="AI services are not configured. Set GEMINI_API_KEY or OPENAI_API_KEY on the backend.",
        )
    result = await openai_request(
        "embeddings",
        {"model": settings.embedding_model, "input": texts},
        db=db, user_id=user_id, email_id=email_id,
        operation=operation, model=settings.embedding_model,
    )
    data = result.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise HTTPException(status_code=502, detail="AI provider returned an incomplete embedding batch.")
    vectors = [item.get("embedding") for item in sorted(data, key=lambda item: item["index"])]
    if any(not isinstance(vector, list) or len(vector) != VECTOR_DIMENSIONS for vector in vectors):
        raise HTTPException(
            status_code=502,
            detail=f"AI embeddings must contain {VECTOR_DIMENSIONS} dimensions to match the database.",
        )
    return vectors


async def _gemini_embed_texts(
    texts: list[str],
    *,
    db: Supabase | None,
    user_id: str | None,
    email_id: str | None,
    operation: str,
    task_type: EmbeddingTask,
) -> list[list[float]]:
    settings = get_settings()
    model = settings.embedding_model
    requests = [
        {
            "model": f"models/{model}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
            "outputDimensionality": VECTOR_DIMENSIONS,
        }
        for text in texts
    ]
    started = time.perf_counter()
    result = await _gemini_request(
        f"models/{model}:batchEmbedContents",
        {"requests": requests},
    )
    embeddings = result.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise HTTPException(status_code=502, detail="Gemini returned an incomplete embedding batch.")
    vectors = [item.get("values") for item in embeddings]
    if any(not isinstance(vector, list) or len(vector) != VECTOR_DIMENSIONS for vector in vectors):
        raise HTTPException(
            status_code=502,
            detail=f"Gemini embeddings must contain {VECTOR_DIMENSIONS} dimensions to match the database.",
        )
    await _record_gemini_usage(
        db,
        user_id,
        email_id,
        operation,
        model,
        result.get("usageMetadata"),
        started,
    )
    return vectors


async def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    db: Supabase | None = None,
    user_id: str | None = None,
    email_id: str | None = None,
    operation: str = "ai_assistant",
) -> str:
    settings = get_settings()
    if settings.ai_provider == "gemini":
        return await _gemini_chat(
            messages,
            json_mode=json_mode,
            db=db,
            user_id=user_id,
            email_id=email_id,
            operation=operation,
        )
    if settings.ai_provider != "openai":
        raise HTTPException(
            status_code=503,
            detail="AI services are not configured. Set GEMINI_API_KEY or OPENAI_API_KEY on the backend.",
        )
    payload: dict[str, Any] = {"model": settings.chat_model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    result = await openai_request(
        "chat/completions", payload, db=db, user_id=user_id, email_id=email_id,
        operation=operation, model=settings.chat_model,
    )
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(status_code=502, detail="AI provider returned no response.")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=502, detail="AI provider returned an empty response.")
    return content


async def _gemini_chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
    db: Supabase | None,
    user_id: str | None,
    email_id: str | None,
    operation: str,
) -> str:
    settings = get_settings()
    system_parts = [item["content"] for item in messages if item.get("role") == "system"]
    contents = [
        {
            "role": "model" if item.get("role") == "assistant" else "user",
            "parts": [{"text": item.get("content", "")}],
        }
        for item in messages
        if item.get("role") in {"user", "assistant"} and item.get("content")
    ]
    if not contents:
        raise HTTPException(status_code=400, detail="Gemini requires at least one user or assistant message.")
    payload: dict[str, Any] = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}
    model = settings.chat_model
    started = time.perf_counter()
    result = await _gemini_request(f"models/{model}:generateContent", payload)
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise HTTPException(status_code=502, detail="Gemini returned no response.")
    parts = candidates[0].get("content", {}).get("parts", [])
    content = "".join(
        part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
    if not content.strip():
        raise HTTPException(status_code=502, detail="Gemini returned an empty response.")
    await _record_gemini_usage(
        db,
        user_id,
        email_id,
        operation,
        model,
        result.get("usageMetadata"),
        started,
    )
    return content


async def _gemini_request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = get_settings().gemini_api_key
    if not api_key:
        raise HTTPException(status_code=503, detail="Gemini is not configured on the backend.")
    async with httpx.AsyncClient(timeout=90) as client:
        for attempt in range(4):
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/{path}",
                json=payload,
                headers={"x-goog-api-key": api_key},
            )
            if response.status_code not in {429, 503} or attempt == 3:
                break
            retry_delay = _gemini_retry_delay(response)
            if retry_delay is not None and retry_delay > 2:
                break
            await asyncio.sleep(retry_delay if retry_delay is not None else 0.5 * (2 ** attempt))
    if response.is_error:
        retry_delay = _gemini_retry_delay(response) if response.status_code in {429, 503} else None
        raise HTTPException(
            status_code=response.status_code if response.status_code in {429, 503} else 502,
            detail=_provider_error_detail("Gemini", response),
            headers=(
                {"Retry-After": str(max(1, int(retry_delay)))}
                if retry_delay is not None
                else None
            ),
        )
    result = response.json()
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Gemini returned an invalid response.")
    return result


def _gemini_retry_delay(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass

    try:
        details = response.json().get("error", {}).get("details", [])
    except (ValueError, AttributeError):
        return None
    if not isinstance(details, list):
        return None
    for detail in details:
        if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith("google.rpc.RetryInfo"):
            continue
        retry_duration = detail.get("retryDelay")
        if isinstance(retry_duration, str):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", retry_duration)
            if match:
                return float(match.group(1))
    return None


def _provider_error_detail(provider: str, response: httpx.Response) -> str:
    code = ""
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            candidate = error.get("status") or error.get("code")
            if isinstance(candidate, (str, int)):
                code = str(candidate)
    except (ValueError, AttributeError):
        pass

    suffix = f", {code}" if code.isascii() and code.replace("_", "").isalnum() else ""
    status = response.status_code
    if status == 401:
        return f"{provider} rejected the API key (HTTP 401). Check the backend API key."
    if status == 403:
        return f"{provider} denied API access (HTTP 403{suffix}). Check API permissions and billing."
    if status == 404:
        return f"{provider} could not find the configured model (HTTP 404). Check the backend model setting."
    if status == 503:
        retry_delay = _gemini_retry_delay(response) if provider == "Gemini" else None
        if retry_delay is not None and retry_delay > 2:
            return (
                f"{provider} is temporarily unavailable (HTTP 503{suffix}). "
                f"Try again in about {_format_retry_duration(retry_delay)}."
            )
        return (
            f"{provider} is temporarily unavailable (HTTP 503{suffix}) after automatic retries. "
            "Wait briefly and try drafting the reply again."
        )
    if status == 429:
        retry_delay = _gemini_retry_delay(response) if provider == "Gemini" else None
        if retry_delay is not None and retry_delay > 2:
            return (
                f"{provider} rate limit or quota exceeded (HTTP 429{suffix}). "
                f"Google suggests trying again in about {_format_retry_duration(retry_delay)}. "
                "Check the AI Studio project and model quota; a new key in the same project shares that quota."
            )
        return (
            f"{provider} rate limit or quota exceeded (HTTP 429{suffix}). "
            "Check the project/model quota and billing; exhausted daily quota requires a quota reset "
            "or an available provider."
        )
    return f"{provider} request failed (HTTP {status}{suffix})."


def _format_retry_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} hour{'s' if hours != 1 else ''} and {minutes} minute{'s' if minutes != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if minutes:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    rounded_seconds = max(1, round(seconds))
    return f"{rounded_seconds} second{'s' if rounded_seconds != 1 else ''}"


async def _record_gemini_usage(
    db: Supabase | None,
    user_id: str | None,
    email_id: str | None,
    operation: str,
    model: str,
    usage: Any,
    started: float,
) -> None:
    if not db or not user_id:
        return
    if not isinstance(usage, dict):
        return
    try:
        input_tokens = int(usage.get("promptTokenCount", 0) or 0)
        output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
        total_tokens = int(usage.get("totalTokenCount") or input_tokens + output_tokens)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Gemini returned invalid token usage.") from exc
    if min(input_tokens, output_tokens, total_tokens) < 0:
        raise HTTPException(status_code=502, detail="Gemini returned invalid token usage.")
    await db.insert(
        "ai_usage_logs",
        {"user_id": user_id, "email_id": email_id, "operation": operation,
         "ai_model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
         "total_tokens": total_tokens,
         "processing_time_ms": int((time.perf_counter() - started) * 1000),
         "status": "COMPLETED"},
    )


async def analyze_email(
    subject: str,
    body: str,
    *,
    db: Supabase | None = None,
    user_id: str | None = None,
    email_id: str | None = None,
) -> dict[str, Any]:
    raw = await chat(
        [
            {
                "role": "system",
                "content": (
                    "Analyze an email. Return a JSON object with keys intent, priority "
                    "(LOW, MEDIUM, HIGH, CRITICAL), priority_reason, sentiment "
                    "(POSITIVE, NEUTRAL, NEGATIVE, ANGRY, URGENT), sentiment_confidence "
                    "(0 to 1), short_summary, detailed_summary, reply_required, "
                    "action_required, category, extracted (object with customer_name, "
                    "company_name, phone_number, email_address, order_number, invoice_number, "
                    "product, amount, currency, mentioned_date, deadline_date, meeting_date, "
                    "location, requested_action), action_items (array of title, description, "
                    "due_date), and deadlines (array of title, description, original_text, "
                    "deadline_at). Use null for unknown values; never invent facts. "
                    "Use ISO 8601 dates or null. Categories should be concise."
                ),
            },
            {"role": "user", "content": f"Subject: {subject}\n\nEmail:\n{body[:30000]}"},
        ],
        json_mode=True,
        db=db,
        user_id=user_id,
        email_id=email_id,
        operation="email_analysis",
    )
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="AI analysis was not valid JSON.") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail="AI analysis had an invalid data shape.")
    return value
