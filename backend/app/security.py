from collections.abc import Callable
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException

from app.config import get_settings
from app.supabase import Supabase


class AuthenticatedUser(dict[str, Any]):
    @property
    def id(self) -> str:
        return str(self["id"])


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="A Supabase bearer token is required.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="A Supabase bearer token is required.")
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="Supabase authentication is not configured.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
            headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="The Supabase access token is invalid or expired.")
    user = response.json()
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="The Supabase token did not identify a user.")
    authenticated = AuthenticatedUser(**user, _access_token=token)
    profiles = await Supabase(token).select(
        "profiles", {"select": "is_active", "id": f"eq.{authenticated.id}"}, limit=1
    )
    if not profiles:
        raise HTTPException(status_code=403, detail="The user profile has not been initialized.")
    if not profiles[0].get("is_active", True):
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    return authenticated


def user_client(user: AuthenticatedUser) -> Supabase:
    return Supabase(user["_access_token"])


async def permission_granted(user: AuthenticatedUser, permission_code: str) -> bool:
    db = user_client(user)
    assignments = await db.select(
        "user_roles", {"select": "role_id", "user_id": f"eq.{user.id}"}
    )
    role_ids = [str(row["role_id"]) for row in assignments if row.get("role_id")]
    if not role_ids:
        return False
    roles = await db.select(
        "roles", {"select": "id,name", "id": f"in.({','.join(role_ids)})"}
    )
    if any(row.get("name") == "ADMIN" for row in roles):
        return True
    role_permissions = await db.select(
        "role_permissions",
        {"select": "permission_id", "role_id": f"in.({','.join(role_ids)})"},
    )
    permission_ids = [
        str(row["permission_id"]) for row in role_permissions if row.get("permission_id")
    ]
    if not permission_ids:
        return False
    permissions = await db.select(
        "permissions", {"select": "code", "id": f"in.({','.join(permission_ids)})"}
    )
    return any(row.get("code") == permission_code for row in permissions)


def require_permission(permission_code: str) -> Callable[..., Any]:
    async def check_permission(
        user: Annotated[AuthenticatedUser, Depends(current_user)],
    ) -> AuthenticatedUser:
        if await permission_granted(user, permission_code):
            return user
        assignments = await user_client(user).select(
            "user_roles", {"select": "role_id", "user_id": f"eq.{user.id}"}
        )
        if not assignments:
            raise HTTPException(status_code=403, detail="No application role is assigned.")
        raise HTTPException(status_code=403, detail=f"Permission required: {permission_code}")

    return check_permission
