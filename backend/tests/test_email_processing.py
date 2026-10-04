import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routers.emails import _parse_import
from app.schemas import EmailCreate
from app.security import AuthenticatedUser
from app.services.ai import VECTOR_DIMENSIONS
from app.services.email_pipeline import (
    _confidence,
    extract_explicit_deadlines,
    process_email,
    split_email,
)
from fastapi import HTTPException


class EmailProcessingTests(unittest.TestCase):
    def test_split_email_preserves_text_across_overlapping_chunks(self) -> None:
        body = "A sentence about the project. " * 500
        chunks = split_email("Project update", body)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 2400 for chunk in chunks))
        self.assertTrue(all(chunk for chunk in chunks))
        self.assertIn("Project update", chunks[0])
        self.assertIn("project", " ".join(chunks).lower())

    def test_split_email_returns_no_chunks_for_empty_message(self) -> None:
        self.assertEqual(split_email("", " \n "), [])

    def test_confidence_is_clamped_and_invalid_values_are_missing(self) -> None:
        self.assertEqual(_confidence(1.7), 1.0)
        self.assertEqual(_confidence(-0.2), 0.0)
        self.assertIsNone(_confidence("not-a-number"))

    def test_extracts_explicit_meeting_date_and_time_from_email_text(self) -> None:
        deadlines = extract_explicit_deadlines(
            "Client Meeting Scheduled – October 9",
            "Confirm our client meeting is scheduled for Friday, October 9, 2026, at 11:00 AM.",
            "2026-10-04T14:20:55+00:00",
        )

        self.assertEqual(len(deadlines), 1)
        self.assertEqual(deadlines[0]["deadline_at"], "2026-10-09T11:00:00")
        self.assertIn("October 9, 2026", deadlines[0]["original_text"])

    def test_import_parses_single_eml_record(self) -> None:
        raw = (
            b"From: Jamie Example <jamie@example.com>\r\n"
            b"To: Alex Example <alex@example.com>\r\n"
            b"Subject: A careful note\r\n"
            b"Message-ID: <note-1@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Hello from the test."
        )
        result = _parse_import("eml", raw)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sender_email"], "jamie@example.com")
        self.assertEqual(result[0]["receiver_email"], "alex@example.com")
        self.assertEqual(result[0]["subject"], "A careful note")
        self.assertEqual(result[0]["body_text"], "Hello from the test.")

    def test_import_parses_json_array(self) -> None:
        result = _parse_import(
            "json",
            b'[{"subject":"Hello","body_text":"World"},{"subject":"Again","body_text":"Hi"}]',
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["subject"], "Hello")

    def test_import_parses_common_csv_headers_and_rfc_email_dates(self) -> None:
        result = _parse_import(
            "csv",
            (
                b"From,To,Subject,Body,Date,Starred\r\n"
                b"Jamie Example <jamie@example.com>,alex@example.com,Hello,World,"
                b'"Tue, 03 Oct 2023 10:00:00 +0000",false\r\n'
            ),
        )

        self.assertEqual(len(result), 1)
        email = EmailCreate.model_validate(result[0])
        self.assertEqual(email.sender_email, "jamie@example.com")
        self.assertEqual(email.sender_name, "Jamie Example")
        self.assertEqual(email.receiver_email, "alex@example.com")
        self.assertEqual(email.subject, "Hello")
        self.assertEqual(email.body_text, "World")
        self.assertFalse(email.is_starred)
        self.assertEqual(email.email_date.year, 2023)

    def test_json_import_supports_named_collection(self) -> None:
        records = _parse_import(
            "json",
            b'{"emails":[{"email_subject":"Hello","content":"World"}]}',
        )

        self.assertEqual(records, [{"subject": "Hello", "body_text": "World"}])
        self.assertEqual(EmailCreate.model_validate(records[0]).body_text, "World")

    def test_import_rejects_empty_datasets(self) -> None:
        for extension, content in (("json", b"[]"), ("csv", b"subject,body_text\r\n")):
            with self.subTest(extension=extension):
                with self.assertRaises(HTTPException) as raised:
                    _parse_import(extension, content)
                self.assertEqual(raised.exception.status_code, 422)


class EmailPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_embeddings_are_stored_before_ai_analysis_runs(self) -> None:
        events: list[str] = []
        inserted_embeddings: list[dict[str, object]] = []
        inserted_deadlines: list[dict[str, object]] = []
        update_calls: list[tuple[str, dict[str, object]]] = []

        class FakeDb:
            async def insert(self, table, row, **kwargs):
                if table == "email_processing_runs":
                    return {"id": "run-1"}
                if table == "email_chunks":
                    return {"id": f"chunk-{row['chunk_index']}", **row}
                if table == "embeddings":
                    events.append("embedding-stored")
                    inserted_embeddings.append(dict(row))
                    return {"id": f"embedding-{len(inserted_embeddings)}"}
                if table == "deadlines":
                    inserted_deadlines.append(dict(row))
                return {"id": f"{table}-1", **row}

            async def select(self, table, query, **kwargs):
                return []

            async def update(self, table, query, values):
                update_calls.append((table, dict(values)))
                return []

            async def delete(self, table, query):
                return None

        async def embed(*args, **kwargs):
            events.append("embedding-generated")
            return [[0.25] * VECTOR_DIMENSIONS]

        async def fail_analysis(*args, **kwargs):
            events.append("analysis-requested")
            raise RuntimeError("simulated chat-model outage")

        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="token")
        settings = SimpleNamespace(
            ai_provider="gemini",
            embedding_model="gemini-embedding-001",
            chat_model="gemini-flash-latest",
        )
        with (
            patch("app.services.email_pipeline.user_client", return_value=FakeDb()),
            patch("app.services.email_pipeline.get_settings", return_value=settings),
            patch("app.services.email_pipeline.embed_texts", side_effect=embed),
            patch("app.services.email_pipeline.analyze_email", side_effect=fail_analysis),
        ):
            with self.assertLogs("app.services.email_pipeline", level="ERROR"):
                await process_email(
                    user,
                    {
                        "id": "email-1",
                        "subject": "Client Meeting Scheduled – October 9",
                        "body_text": "Our client meeting is scheduled for Friday, October 9, 2026, at 11:00 AM.",
                        "email_date": "2026-10-04T14:20:55+00:00",
                    },
                )

        self.assertEqual(events, ["embedding-generated", "embedding-stored", "analysis-requested"])
        self.assertEqual(len(inserted_deadlines), 1)
        self.assertEqual(inserted_deadlines[0]["deadline_at"], "2026-10-09T11:00:00")
        self.assertEqual(len(inserted_embeddings), 1)
        self.assertEqual(inserted_embeddings[0]["model"], "gemini-embedding-001")
        self.assertEqual(inserted_embeddings[0]["chunk_id"], "chunk-0")
        self.assertTrue(
            any(
                table == "email_processing_runs"
                and values.get("status") == "FAILED"
                and "simulated chat-model outage" in str(values.get("error_message"))
                for table, values in update_calls
            )
        )

    async def test_retry_reuses_current_model_embeddings(self) -> None:
        content = split_email("A subject", "Email content")[0]
        chunk = {"id": "chunk-0", "chunk_index": 0, "content": content}

        class FakeDb:
            async def insert(self, table, row, **kwargs):
                if table == "email_processing_runs":
                    return {"id": "run-2"}
                raise AssertionError(f"unexpected insert into {table}")

            async def select(self, table, query, **kwargs):
                if table == "email_chunks":
                    return [chunk]
                if table == "embeddings":
                    return [{"chunk_id": "chunk-0", "model": "gemini-embedding-001"}]
                return []

            async def update(self, table, query, values):
                return []

            async def delete(self, table, query):
                raise AssertionError("matching chunks should be retained on retry")

        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="token")
        settings = SimpleNamespace(
            ai_provider="gemini",
            embedding_model="gemini-embedding-001",
            chat_model="gemini-flash-latest",
        )
        with (
            patch("app.services.email_pipeline.user_client", return_value=FakeDb()),
            patch("app.services.email_pipeline.get_settings", return_value=settings),
            patch("app.services.email_pipeline.embed_texts", new_callable=AsyncMock) as embeddings,
            patch(
                "app.services.email_pipeline.analyze_email",
                new_callable=AsyncMock,
                side_effect=RuntimeError("simulated chat-model outage"),
            ),
        ):
            with self.assertLogs("app.services.email_pipeline", level="ERROR"):
                await process_email(
                    user,
                    {"id": "email-1", "subject": "A subject", "body_text": "Email content"},
                )

        embeddings.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
