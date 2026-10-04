import httpx
from fastapi import APIRouter, HTTPException
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.config import get_settings
from app.security import AuthenticatedUser, current_user, user_client
from fastapi import Depends
from typing import Annotated

router = APIRouter(prefix="/auth", tags=["authentication"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    full_name: str = Field(min_length=1, max_length=255)


async def auth_request(path: str, payload: dict[str, Any]) -> dict:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(status_code=503, detail="Supabase Auth is not configured.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/{path}",
            json=payload,
            headers={"apikey": settings.supabase_anon_key, "Content-Type": "application/json"},
        )
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


@router.post("/register", status_code=201)
async def register(body: Registration) -> dict:
    return await auth_request(
        "signup",
        {
            "email": str(body.email),
            "password": body.password,
            "data": {"full_name": body.full_name},
        },
    )


@router.post("/login")
async def login(body: Credentials) -> dict:
    return await auth_request(
        "token?grant_type=password",
        {"email": str(body.email), "password": body.password},
    )


@router.post("/logout", status_code=204)
async def logout(user: Annotated[AuthenticatedUser, Depends(current_user)]) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/logout?scope=local",
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {user['_access_token']}",
            },
        )
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail="Could not revoke the session.")


@router.get("/me")
async def me(user: Annotated[AuthenticatedUser, Depends(current_user)]) -> dict[str, Any]:
    db = user_client(user)
    assignments = await db.select(
        "user_roles", {"select": "role_id", "user_id": f"eq.{user.id}"}
    )
    role_ids = [str(row["role_id"]) for row in assignments if row.get("role_id")]
    roles = await db.select(
        "roles", {"select": "name", "id": f"in.({','.join(role_ids)})"}
    ) if role_ids else []
    return {
        **{key: value for key, value in user.items() if key != "_access_token"},
        "roles": sorted(str(row["name"]) for row in roles if row.get("name")),
    }
