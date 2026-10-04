from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.schemas import DeadlineUpdate, NotificationPreferences, ActionItemUpdate
from app.security import AuthenticatedUser, current_user, require_permission, user_client

router = APIRouter(tags=["account"])
AccountUser = Annotated[AuthenticatedUser, Depends(current_user)]
ProfileEditor = Annotated[AuthenticatedUser, Depends(require_permission("profile:update"))]
ActionUser = Annotated[AuthenticatedUser, Depends(require_permission("action_items:manage"))]
DeadlineUser = Annotated[AuthenticatedUser, Depends(require_permission("deadlines:manage"))]
NotificationUser = Annotated[AuthenticatedUser, Depends(require_permission("notifications:manage"))]


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


def _notification_preference_values(row: dict[str, Any]) -> dict[str, bool]:
    fields = NotificationPreferences.model_fields
    return NotificationPreferences.model_validate(
        {name: row[name] for name in fields if name in row}
    ).model_dump()


@router.get("/profile")
async def get_profile(user: AccountUser) -> dict[str, Any]:
    rows = await user_client(user).select("profiles", {"select": "*", "id": f"eq.{user.id}"}, limit=1)
    return rows[0] if rows else {"id": user.id, "email": user.get("email")}


@router.patch("/profile")
async def update_profile(body: ProfileUpdate, user: ProfileEditor) -> dict[str, Any]:
    values = body.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(status_code=422, detail="At least one profile field is required.")
    rows = await user_client(user).update("profiles", {"id": f"eq.{user.id}"}, values)
    if not rows:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return rows[0]


@router.get("/action-items")
async def action_items(user: ActionUser) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "action_items", {"select": "*", "user_id": f"eq.{user.id}", "order": "due_date.asc.nullslast"},
        limit=200,
    )


@router.patch("/action-items/{item_id}")
async def update_action_item(item_id: str, body: ActionItemUpdate, user: ActionUser) -> dict[str, Any]:
    values = body.model_dump(exclude_none=True)
    if values.get("status") == "COMPLETED":
        values["completed_at"] = datetime.now(UTC).isoformat()
    rows = await user_client(user).update(
        "action_items", {"id": f"eq.{item_id}", "user_id": f"eq.{user.id}"}, values
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Action item not found.")
    return rows[0]


@router.get("/deadlines")
async def deadlines(user: DeadlineUser) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "deadlines", {"select": "*", "user_id": f"eq.{user.id}", "order": "deadline_at.asc"},
        limit=200,
    )


@router.patch("/deadlines/{deadline_id}")
async def update_deadline(deadline_id: str, body: DeadlineUpdate, user: DeadlineUser) -> dict[str, Any]:
    values: dict[str, Any] = {"is_completed": body.is_completed}
    values["completed_at"] = datetime.now(UTC).isoformat() if body.is_completed else None
    rows = await user_client(user).update(
        "deadlines", {"id": f"eq.{deadline_id}", "user_id": f"eq.{user.id}"}, values
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Deadline not found.")
    return rows[0]


@router.get("/notifications")
async def notifications(user: NotificationUser) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "notifications", {"select": "*", "user_id": f"eq.{user.id}", "order": "created_at.desc"},
        limit=100,
    )


@router.patch("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: NotificationUser) -> dict[str, Any]:
    rows = await user_client(user).update(
        "notifications",
        {"id": f"eq.{notification_id}", "user_id": f"eq.{user.id}"},
        {"is_read": True},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return rows[0]


@router.get("/notification-preferences")
async def notification_preferences(user: NotificationUser) -> dict[str, Any]:
    rows = await user_client(user).select(
        "notification_preferences", {"select": "*", "user_id": f"eq.{user.id}"}, limit=1
    )
    if rows:
        return _notification_preference_values(rows[0])
    return NotificationPreferences().model_dump()


@router.put("/notification-preferences")
async def save_notification_preferences(
    body: NotificationPreferences, user: NotificationUser
) -> dict[str, Any]:
    db = user_client(user)
    existing = await db.select(
        "notification_preferences", {"select": "id", "user_id": f"eq.{user.id}"}, limit=1
    )
    values = body.model_dump()
    if existing:
        rows = await db.update(
            "notification_preferences", {"id": f"eq.{existing[0]['id']}"}, values
        )
        if not rows:
            raise HTTPException(
                status_code=409,
                detail="Notification preferences changed before they could be saved. Reload and try again.",
            )
        return values
    await db.insert("notification_preferences", {"user_id": user.id, **values})
    return values
