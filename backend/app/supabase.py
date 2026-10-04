from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.config import get_settings


class Supabase:
    def __init__(self, access_token: str | None = None) -> None:
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY.",
            )
        self.base_url = settings.supabase_url.rstrip("/")
        self.headers = {
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {access_token or settings.supabase_anon_key}",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        request_headers = {**self.headers, **(headers or {})}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                f"{self.base_url}/{path.lstrip('/')}",
                params=params,
                json=json,
                headers=request_headers,
            )
        if response.is_error:
            try:
                error = response.json()
            except ValueError:
                error = response.text
            raise HTTPException(
                status_code=response.status_code,
                detail={"message": "Supabase request failed", "error": error},
            )
        if response.status_code == status.HTTP_204_NO_CONTENT or not response.content:
            return None
        return response.json()

    async def select(
        self, table: str, query: Mapping[str, str], *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        params = dict(query)
        if limit is not None:
            params["limit"] = str(limit)
        result = await self.request("GET", f"rest/v1/{table}", params=params)
        return result if isinstance(result, list) else []

    async def count(self, table: str, query: Mapping[str, str]) -> int:
        params = {"select": "id", **query}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.head(
                f"{self.base_url}/rest/v1/{table}",
                params=params,
                headers={**self.headers, "Prefer": "count=exact", "Range": "0-0"},
            )
        if response.is_error:
            try:
                error = response.json()
            except ValueError:
                error = response.text
            raise HTTPException(
                status_code=response.status_code,
                detail={"message": "Supabase request failed", "error": error},
            )
        content_range = response.headers.get("content-range", "")
        total = content_range.rpartition("/")[2]
        if not total.isdecimal():
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Supabase did not return an exact count for {table}.",
            )
        return int(total)

    async def insert(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        upsert: bool = False,
        on_conflict: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Prefer": "return=representation"}
        if upsert:
            headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        params = {"on_conflict": on_conflict} if on_conflict else None
        result = await self.request(
            "POST", f"rest/v1/{table}", params=params, json=dict(row), headers=headers
        )
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else {}

    async def update(
        self, table: str, query: Mapping[str, str], values: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        result = await self.request(
            "PATCH",
            f"rest/v1/{table}",
            params=query,
            json=dict(values),
            headers={"Prefer": "return=representation"},
        )
        return result if isinstance(result, list) else []

    async def delete(self, table: str, query: Mapping[str, str]) -> None:
        await self.request("DELETE", f"rest/v1/{table}", params=query)
