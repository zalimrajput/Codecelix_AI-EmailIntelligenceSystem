from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.security import AuthenticatedUser, require_permission, user_client

router = APIRouter(tags=["analytics"])
AnalyticsUser = Annotated[AuthenticatedUser, Depends(require_permission("analytics:read"))]
EmailUser = Annotated[AuthenticatedUser, Depends(require_permission("emails:read"))]
AnalysisUser = Annotated[AuthenticatedUser, Depends(require_permission("ai_analysis:read"))]


@router.get("/analytics/overview")
async def analytics_overview(user: AnalyticsUser) -> dict[str, Any]:
    db = user_client(user)
    emails = await db.select(
        "emails", {"select": "id,status,is_starred,reply_required,action_required,email_date,source_type",
                   "user_id": f"eq.{user.id}"},
        limit=1000,
    )
    analyses = await db.select(
        "ai_analyses", {"select": "priority,sentiment,category_id"}, limit=1000
    )
    actions = await db.select(
        "action_items", {"select": "id,status,due_date", "user_id": f"eq.{user.id}"}, limit=1000
    )
    deadlines = await db.select(
        "deadlines", {"select": "id,is_completed,deadline_at",
                      "user_id": f"eq.{user.id}"}, limit=1000
    )
    usage = await db.select(
        "ai_usage_logs", {"select": "total_tokens", "user_id": f"eq.{user.id}"}, limit=1000
    )
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    recent = [
        item for item in emails
        if _datetime(item.get("email_date")) and _datetime(item["email_date"]) >= month_start
    ]
    return {
        "total_emails": len(emails),
        "unread_emails": sum(row.get("status") == "UNREAD" for row in emails),
        "starred_emails": sum(bool(row.get("is_starred")) for row in emails),
        "reply_required": sum(bool(row.get("reply_required")) for row in emails),
        "action_required": sum(bool(row.get("action_required")) for row in emails),
        "emails_this_month": len(recent),
        "priorities": dict(Counter(row.get("priority", "UNKNOWN") for row in analyses)),
        "sentiments": dict(Counter(row.get("sentiment", "UNKNOWN") for row in analyses)),
        "open_action_items": sum(row.get("status") in {"PENDING", "IN_PROGRESS"} for row in actions),
        "upcoming_deadlines": sum(
            not row.get("is_completed") and _datetime(row.get("deadline_at")) is not None
            and now <= _datetime(row["deadline_at"]) <= now + timedelta(days=7)
            for row in deadlines
        ),
        "ai_tokens_used": sum(int(row.get("total_tokens") or 0) for row in usage),
        "sources": dict(Counter(row.get("source_type", "UNKNOWN") for row in emails)),
    }


@router.get("/categories")
async def categories(user: EmailUser) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "email_categories", {"select": "id,name,description,is_system", "is_active": "eq.true"},
        limit=100,
    )


@router.get("/insights")
async def insights(user: AnalysisUser, limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    db = user_client(user)
    rows = await db.select(
        "ai_analyses",
        {"select": "*", "order": "created_at.desc"},
        limit=limit,
    )
    return rows


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
