import logging
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import get_settings
from app.security import AuthenticatedUser, user_client
from app.services.ai import analyze_email, embed_texts

logger = logging.getLogger(__name__)
CHUNK_CHARACTERS = 2400
CHUNK_OVERLAP = 240
_MONTHS = {
    name.casefold(): number
    for number, names in enumerate(
        (
            ("January", "Jan"),
            ("February", "Feb"),
            ("March", "Mar"),
            ("April", "Apr"),
            ("May",),
            ("June", "Jun"),
            ("July", "Jul"),
            ("August", "Aug"),
            ("September", "Sep", "Sept"),
            ("October", "Oct"),
            ("November", "Nov"),
            ("December", "Dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_DATE = re.compile(
    r"\b(?P<month>" + "|".join(_MONTHS) + r")\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,\s*(?P<year>\d{4}))?"
    r"(?:\s*,?\s*(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
    r"\s*(?P<ampm>a\.?m\.?|p\.?m\.?))?",
    re.IGNORECASE,
)
_DATE_CONTEXT = re.compile(
    r"\b(meeting|scheduled|deadline|due|appointment|interview|call|conference|event)\b",
    re.IGNORECASE,
)
_GROUPED_AMOUNT = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


async def save_email(db: Any, user_id: str, values: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    email_date = values.get("email_date") or now
    thread = None
    in_reply_to = values.get("in_reply_to")
    if in_reply_to:
        parent_rows = await db.select(
            "emails",
            {"select": "thread_id", "user_id": f"eq.{user_id}", "message_id": f"eq.{in_reply_to}"},
            limit=1,
        )
        if parent_rows and parent_rows[0].get("thread_id"):
            rows = await db.select(
                "email_threads",
                {"select": "id,email_count,last_email_at", "id": f"eq.{parent_rows[0]['thread_id']}"},
                limit=1,
            )
            thread = rows[0] if rows else None
    if thread:
        thread_id = str(thread["id"])
        await db.update(
            "email_threads", {"id": f"eq.{thread_id}"},
            {"email_count": int(thread.get("email_count") or 0) + 1,
             "last_email_at": _later_timestamp(thread.get("last_email_at"), email_date)},
        )
    else:
        thread = await db.insert(
            "email_threads",
            {"user_id": user_id, "subject": values.get("subject") or "(no subject)",
             "thread_reference": in_reply_to or values.get("message_id") or f"manual:{uuid.uuid4()}",
             "first_email_at": email_date, "last_email_at": email_date, "email_count": 1},
        )
        thread_id = str(thread["id"])
    return await db.insert(
        "emails", {**values, "user_id": user_id, "thread_id": thread_id, "email_date": email_date}
    )


def extract_explicit_deadlines(
    subject: str, body: str, email_date: Any = None
) -> list[dict[str, str]]:
    reference = _parse_datetime(email_date) or datetime.now(UTC)
    text = f"{body}\n{subject}"
    found: dict[str, dict[str, str]] = {}
    for match in _MONTH_DATE.finditer(text):
        sentence_start = max(
            text.rfind(mark, 0, match.start()) for mark in (".", "!", "?", "\n")
        ) + 1
        sentence_end = len(text)
        for mark in (".", "!", "?", "\n"):
            boundary = text.find(mark, match.end())
            if boundary >= 0:
                sentence_end = min(sentence_end, boundary + 1)
        sentence = text[sentence_start:sentence_end].strip()
        if not _DATE_CONTEXT.search(sentence):
            continue
        year = int(match.group("year") or reference.year)
        try:
            event_date = date(year, _MONTHS[match.group("month").casefold()], int(match.group("day")))
        except ValueError:
            continue
        if not match.group("year") and event_date < reference.date() - timedelta(days=30):
            try:
                event_date = event_date.replace(year=year + 1)
            except ValueError:
                continue

        hour = int(match.group("hour") or 0)
        minute = int(match.group("minute") or 0)
        if match.group("ampm"):
            meridiem = match.group("ampm").casefold().replace(".", "")
            if hour < 1 or hour > 12 or minute > 59:
                continue
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif hour > 23 or minute > 59:
            continue
        event_at = datetime.combine(event_date, time(hour, minute))
        key = event_date.isoformat()
        candidate = {
            "title": subject.strip()[:255] or "Meeting",
            "description": "Date detected from the email text.",
            "original_text": sentence[:1000],
            "deadline_at": event_at.isoformat(),
        }
        current = found.get(key)
        if current is None or (
            current["deadline_at"].endswith("T00:00:00")
            and not candidate["deadline_at"].endswith("T00:00:00")
        ):
            found[key] = candidate
    return list(found.values())


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _deadline_date_key(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None


async def _persist_deadlines(
    db: Any,
    user_id: str,
    email_id: str,
    deadlines: list[dict[str, Any]],
    preferences: dict[str, Any] | None = None,
) -> None:
    existing = await db.select(
        "deadlines",
        {"select": "deadline_at", "user_id": f"eq.{user_id}", "email_id": f"eq.{email_id}"},
    )
    known_dates = {
        key for row in existing if (key := _deadline_date_key(row.get("deadline_at")))
    }
    for item in deadlines[:20]:
        title = item.get("title")
        deadline_at = item.get("deadline_at")
        date_key = _deadline_date_key(deadline_at)
        if not isinstance(title, str) or not title.strip() or not date_key or date_key in known_dates:
            continue
        deadline = await db.insert(
            "deadlines",
            {"user_id": user_id, "email_id": email_id, "title": title.strip()[:255],
             "description": item.get("description"), "original_text": item.get("original_text"),
             "deadline_at": deadline_at},
        )
        known_dates.add(date_key)
        if preferences and preferences.get("upcoming_deadline", True):
            await db.insert(
                "notifications",
                {"user_id": user_id, "email_id": email_id, "deadline_id": deadline.get("id"),
                 "type": "UPCOMING_DEADLINE", "title": "A date to keep in mind",
                 "message": title.strip()[:500]},
            )


def split_email(subject: str, body: str) -> list[str]:
    if not subject.strip() and not body.strip():
        return []
    source = f"Subject: {subject.strip()}\n\n{body.strip()}".strip()
    source = re.sub(r"\n{3,}", "\n\n", source)
    if not source:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(source):
        end = min(start + CHUNK_CHARACTERS, len(source))
        if end < len(source):
            boundary = source.rfind("\n", start + CHUNK_CHARACTERS // 2, end)
            if boundary < 0:
                boundary = source.rfind(" ", start + CHUNK_CHARACTERS // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(source[start:end].strip())
        if end >= len(source):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [chunk for chunk in chunks if chunk]


def _enum(value: Any, allowed: set[str], default: str) -> str:
    candidate = str(value or "").upper()
    return candidate if candidate in allowed else default


async def process_email(user: AuthenticatedUser, email: dict[str, Any]) -> None:
    db = user_client(user)
    email_id = str(email["id"])
    run: dict[str, Any] = {}
    try:
        run = await db.insert(
            "email_processing_runs",
            {"email_id": email_id, "status": "PROCESSING", "parser_completed": True},
        )
        parts = split_email(email.get("subject", ""), email.get("body_text", ""))
        existing = await db.select(
            "email_chunks",
            {"select": "id,chunk_index,content", "email_id": f"eq.{email_id}",
             "order": "chunk_index.asc"},
        )
        if [row.get("content") for row in existing] == parts:
            chunks = existing
        else:
            if existing:
                await db.delete("email_chunks", {"email_id": f"eq.{email_id}"})
            chunks = [
                await db.insert(
                    "email_chunks",
                    {"user_id": user.id, "email_id": email_id, "chunk_index": index,
                     "content": content, "token_count": max(1, len(content.split()))},
                )
                for index, content in enumerate(parts)
            ]
        await db.update(
            "email_processing_runs",
            {"id": f"eq.{run['id']}"},
            {"cleaning_completed": True},
        )
        model = get_settings().embedding_model
        if chunks:
            chunk_ids = [str(chunk["id"]) for chunk in chunks]
            existing_embeddings = await db.select(
                "embeddings",
                {"select": "chunk_id,model", "user_id": f"eq.{user.id}",
                 "chunk_id": f"in.({','.join(chunk_ids)})"},
            )
            embedded_ids = {
                str(row.get("chunk_id"))
                for row in existing_embeddings
                if row.get("model") == model
            }
            chunks_to_embed = [
                chunk for chunk in chunks if str(chunk["id"]) not in embedded_ids
            ]
            vectors = await embed_texts(
                [str(chunk["content"]) for chunk in chunks_to_embed],
                db=db, user_id=user.id, email_id=email_id,
            ) if chunks_to_embed else []
            for chunk, vector in zip(chunks_to_embed, vectors, strict=True):
                await db.insert(
                    "embeddings",
                    {"user_id": user.id, "chunk_id": chunk["id"],
                     "embedding": "[" + ",".join(str(float(value)) for value in vector) + "]",
                     "model": model},
                    upsert=True,
                )

        fallback_deadlines = extract_explicit_deadlines(
            email.get("subject", ""), email.get("body_text", ""), email.get("email_date")
        )
        try:
            analysis = await analyze_email(
                email.get("subject", ""), email.get("body_text", ""),
                db=db, user_id=user.id, email_id=email_id,
            )
        except Exception:
            await _persist_deadlines(db, user.id, email_id, fallback_deadlines)
            raise
        categories = await db.select(
            "email_categories", {"select": "id,name", "is_active": "eq.true"}
        )
        category_id = next(
            (
                str(item["id"])
                for item in categories
                if str(item.get("name", "")).casefold()
                == str(analysis.get("category", "")).casefold()
            ),
            None,
        )
        analysis_row = {
            "email_id": email_id,
            "category_id": category_id,
            "intent": str(analysis.get("intent") or "")[:255] or None,
            "priority": _enum(analysis.get("priority"), {"LOW", "MEDIUM", "HIGH", "CRITICAL"}, "MEDIUM"),
            "priority_reason": analysis.get("priority_reason"),
            "sentiment": _enum(
                analysis.get("sentiment"), {"POSITIVE", "NEUTRAL", "NEGATIVE", "ANGRY", "URGENT"}, "NEUTRAL"
            ),
            "sentiment_confidence": _confidence(analysis.get("sentiment_confidence")),
            "short_summary": str(analysis.get("short_summary") or "")[:1000],
            "detailed_summary": str(analysis.get("detailed_summary") or ""),
            "reply_required": bool(analysis.get("reply_required", False)),
            "action_required": bool(analysis.get("action_required", False)),
            "ai_model": str(analysis.get("_ai_model") or get_settings().chat_model),
        }
        await db.insert(
            "ai_analyses", analysis_row, upsert=True, on_conflict="email_id"
        )
        await db.update(
            "emails",
            {"id": f"eq.{email_id}"},
            {"category_id": category_id, "reply_required": analysis_row["reply_required"],
             "action_required": analysis_row["action_required"]},
        )
        await db.update(
            "email_processing_runs", {"id": f"eq.{run['id']}"},
            {"classification_completed": True, "summary_completed": True,
             "extraction_completed": True, "sentiment_completed": True,
             "priority_completed": True},
        )

        preference_rows = await db.select(
            "notification_preferences", {"select": "*", "user_id": f"eq.{user.id}"}, limit=1
        )
        preferences = preference_rows[0] if preference_rows else {}
        extracted = analysis.get("extracted")
        if isinstance(extracted, dict) and any(value is not None for value in extracted.values()):
            allowed = {
                "customer_name", "company_name", "phone_number", "email_address", "order_number",
                "invoice_number", "product", "amount", "currency", "mentioned_date", "deadline_date",
                "meeting_date", "location", "requested_action",
            }
            row = {key: value for key, value in extracted.items() if key in allowed and value is not None}
            if "amount" in row:
                amount = _normalize_amount(row["amount"])
                if amount is None:
                    logger.warning("Skipping invalid extracted amount for email id %s", email_id)
                    row.pop("amount")
                else:
                    row["amount"] = amount
            if row:
                await db.insert("extracted_information", {"email_id": email_id, **row})
        for item in analysis.get("action_items", [])[:20]:
            if isinstance(item, dict) and item.get("title"):
                action = await db.insert(
                    "action_items",
                    {"user_id": user.id, "email_id": email_id, "title": str(item["title"])[:255],
                     "description": item.get("description"), "due_date": item.get("due_date"),
                     "status": "PENDING"},
                )
                if preferences.get("action_item", True):
                    await db.insert(
                        "notifications",
                        {"user_id": user.id, "email_id": email_id, "action_item_id": action.get("id"),
                         "type": "ACTION_ITEM", "title": "A follow-up to keep in mind",
                         "message": str(item["title"])[:500]},
                    )
        ai_deadlines = analysis.get("deadlines")
        deadline_items = (
            [item for item in ai_deadlines[:20] if isinstance(item, dict)]
            if isinstance(ai_deadlines, list)
            else []
        )
        deadline_items.extend(fallback_deadlines)
        await _persist_deadlines(db, user.id, email_id, deadline_items, preferences)
        await db.update(
            "email_processing_runs",
            {"id": f"eq.{run['id']}"},
            {"action_detection_completed": True, "deadline_detection_completed": True},
        )
        await db.update(
            "email_processing_runs",
            {"id": f"eq.{run['id']}"},
            {"status": "COMPLETED", "completed_at": datetime.now(UTC).isoformat()},
        )
    except Exception as exc:
        logger.exception("Email processing failed for email id %s", email_id)
        if run.get("id"):
            try:
                await db.update(
                    "email_processing_runs",
                    {"id": f"eq.{run['id']}"},
                    {"status": "FAILED", "error_message": str(exc)[:2000]},
                )
            except Exception:
                logger.exception("Could not record processing failure for email id %s", email_id)
        return


def _confidence(value: Any) -> float | None:
    try:
        return min(1.0, max(0.0, float(value)))
    except (ValueError, TypeError):
        return None


def _normalize_amount(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "," in text:
        if not _GROUPED_AMOUNT.fullmatch(text):
            return None
        text = text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return format(amount, "f")


def _later_timestamp(current: Any, candidate: Any) -> Any:
    if not isinstance(current, str) or not isinstance(candidate, str):
        return candidate or current
    try:
        current_date = datetime.fromisoformat(current.replace("Z", "+00:00"))
        candidate_date = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return candidate
    return candidate if candidate_date > current_date else current
