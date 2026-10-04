import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.supabase import Supabase


class SupabaseCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_count_uses_exact_count_header(self) -> None:
        db = Supabase.__new__(Supabase)
        db.base_url = "https://example.supabase.co"
        db.headers = {"apikey": "public-key", "Authorization": "Bearer user-token"}
        client = Mock()
        client.head = AsyncMock(
            return_value=Mock(is_error=False, headers={"content-range": "*/17"})
        )
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch("app.supabase.httpx.AsyncClient", return_value=context):
            count = await db.count("emails", {"user_id": "eq.user-1"})

        self.assertEqual(count, 17)
        client.head.assert_awaited_once()
        self.assertEqual(client.head.await_args.kwargs["headers"]["Prefer"], "count=exact")
        self.assertEqual(client.head.await_args.kwargs["headers"]["Range"], "0-0")

    async def test_count_fails_if_supabase_omits_exact_count(self) -> None:
        db = Supabase.__new__(Supabase)
        db.base_url = "https://example.supabase.co"
        db.headers = {"apikey": "public-key", "Authorization": "Bearer user-token"}
        client = Mock()
        client.head = AsyncMock(
            return_value=Mock(is_error=False, headers={"content-range": "*/"})
        )
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch("app.supabase.httpx.AsyncClient", return_value=context):
            with self.assertRaises(HTTPException) as raised:
                await db.count("emails", {"user_id": "eq.user-1"})

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
