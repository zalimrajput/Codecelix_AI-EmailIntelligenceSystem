from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal

from app.security import AuthenticatedUser, require_permission, user_client

router = APIRouter(prefix="/admin", tags=["administration"])
AdminUser = Annotated[AuthenticatedUser, Depends(require_permission("system_analytics:read"))]
CategoriesAdmin = Annotated[AuthenticatedUser, Depends(require_permission("email_categories:manage"))]
UsersAdmin = Annotated[AuthenticatedUser, Depends(require_permission("users:manage"))]
AuditAdmin = Annotated[AuthenticatedUser, Depends(require_permission("audit_logs:read"))]
UsageAdmin = Annotated[AuthenticatedUser, Depends(require_permission("ai_usage:read"))]


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class UserRolesUpdate(BaseModel):
    roles: list[Literal["ADMIN", "USER"]] = Field(min_length=1, max_length=2)


@router.get("/overview")
async def admin_overview(user: AdminUser) -> dict[str, int]:
    db = user_client(user)
    result = await db.request(
        "POST", "rest/v1/rpc/admin_workspace_overview", json={}
    )
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Admin overview returned an invalid response.")
    return result


@router.get("/users")
async def admin_users(
    user: UsersAdmin, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    rows = await user_client(user).request(
        "POST", "rest/v1/rpc/admin_user_list", json={"result_limit": limit}
    )
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="Admin user list returned an invalid response.")
    return rows


async def _require_admin_role(db: Any, user: AuthenticatedUser) -> None:
    assignments = await db.select(
        "user_roles", {"select": "role_id", "user_id": f"eq.{user.id}"}
    )
    role_ids = [str(row["role_id"]) for row in assignments if row.get("role_id")]
    if not role_ids:
        raise HTTPException(status_code=403, detail="Administrator role required.")
    roles = await db.select(
        "roles", {"select": "name", "id": f"in.({','.join(role_ids)})"}
    )
    if not any(row.get("name") == "ADMIN" for row in roles):
        raise HTTPException(status_code=403, detail="Administrator role required.")


@router.get("/users/{user_id}")
async def admin_user_details(user_id: str, user: UsersAdmin) -> dict[str, Any]:
    db = user_client(user)
    await _require_admin_role(db, user)
    details = await db.request(
        "POST",
        "rest/v1/rpc/admin_user_details",
        json={"target_user_id": user_id},
    )
    if not isinstance(details, dict):
        raise HTTPException(status_code=404, detail="User not found.")
    return details


@router.patch("/users/{user_id}/roles")
async def update_user_roles(
    user_id: str, body: UserRolesUpdate, user: UsersAdmin
) -> dict[str, Any]:
    requested = set(body.roles)
    if user_id == user.id and "ADMIN" not in requested:
        raise HTTPException(status_code=400, detail="You cannot remove your own administrator role.")
    db = user_client(user)
    result = await db.request(
        "POST",
        "rest/v1/rpc/admin_set_user_roles",
        json={"target_user_id": user_id, "requested_roles": sorted(requested)},
    )
    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="User not found.")
    return result


@router.get("/audit-logs")
async def audit_logs(
    user: AuditAdmin, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    rows = await user_client(user).request(
        "POST", "rest/v1/rpc/admin_recent_audit_logs", json={"result_limit": limit}
    )
    return rows if isinstance(rows, list) else []


@router.get("/ai-usage")
async def ai_usage(user: UsageAdmin) -> list[dict[str, Any]]:
    rows = await user_client(user).request(
        "POST", "rest/v1/rpc/admin_ai_usage_summary", json={}
    )
    return rows if isinstance(rows, list) else []


@router.get("/categories")
async def admin_categories(user: CategoriesAdmin) -> list[dict[str, Any]]:
    return await user_client(user).select(
        "email_categories", {"select": "*", "order": "name.asc"}, limit=200
    )


@router.post("/categories", status_code=201)
async def create_category(body: CategoryCreate, user: CategoriesAdmin) -> dict[str, Any]:
    return await user_client(user).insert(
        "email_categories", {"name": body.name, "description": body.description, "is_system": False}
    )


@router.patch("/users/{user_id}/active")
async def set_user_active(user_id: str, active: bool, user: UsersAdmin) -> dict[str, Any]:
    result = await user_client(user).request(
        "POST",
        "rest/v1/rpc/admin_set_user_active",
        json={"target_user_id": user_id, "account_active": active},
    )
    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="User not found.")
    return result
