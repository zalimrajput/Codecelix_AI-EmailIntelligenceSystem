import unittest
from typing import Any

from app.services.email_pipeline import save_email


class MemoryDatabase:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {"emails": [], "email_threads": []}

    async def select(self, table: str, filters: dict[str, str], limit: int | None = None) -> list[dict[str, Any]]:
        rows = self.tables.get(table, [])
        for column, condition in filters.items():
            if condition.startswith("eq."):
                target = condition[3:]
                rows = [row for row in rows if str(row.get(column)) == target]
        return rows[:limit] if limit else rows

    async def insert(self, table: str, values: dict[str, Any]) -> dict[str, Any]:
        row = {**values, "id": f"{table}-{len(self.tables[table]) + 1}"}
        self.tables[table].append(row)
        return row

    async def update(self, table: str, filters: dict[str, str], values: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await self.select(table, filters)
        for row in rows:
            row.update(values)
        return rows


class EmailThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_reuses_parent_thread_and_updates_count(self) -> None:
        db = MemoryDatabase()
        first = await save_email(db, "user-1", {"subject": "Project update", "message_id": "<first>"})
        reply = await save_email(
            db, "user-1",
            {"subject": "Re: Project update", "message_id": "<reply>", "in_reply_to": "<first>"},
        )

        self.assertEqual(reply["thread_id"], first["thread_id"])
        self.assertEqual(len(db.tables["email_threads"]), 1)
        self.assertEqual(db.tables["email_threads"][0]["email_count"], 2)


if __name__ == "__main__":
    unittest.main()
