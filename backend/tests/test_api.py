import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException

from app.main import app
from app.routers.admin import (
    UserRolesUpdate,
    admin_user_details,
    admin_users,
    set_user_active,
    update_user_roles,
)
from app.routers.emails import email_analysis, list_emails
from app.routers.insights import analytics_overview, insights
from app.routers.users import notification_preferences, save_notification_preferences
from app.security import AuthenticatedUser, current_user
from app.schemas import (
    ActionItemUpdate,
    AssistantRequest,
    DeadlineUpdate,
    EmailCreate,
    EmailUpdate,
    GmailExchange,
    NotificationPreferences,
    ReplyRequest,
    ReplyUpdate,
)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, path: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(path)

    async def test_health_check_is_public(self) -> None:
        response = await self.request("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_email_routes_require_a_supabase_token(self) -> None:
        response = await self.request("/emails")
        self.assertEqual(response.status_code, 401)

    async def test_current_user_response_includes_only_the_callers_roles(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        app.dependency_overrides[current_user] = lambda: user
        try:
            with patch("app.routers.auth.user_client") as make_client:
                db = make_client.return_value
                db.select = AsyncMock(
                    side_effect=[
                        [{"role_id": "role-1"}],
                        [{"name": "USER"}],
                    ]
                )
                response = await self.request("/auth/me")
        finally:
            app.dependency_overrides.pop(current_user, None)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["roles"], ["USER"])
        self.assertNotIn("_access_token", response.json())

    async def test_admin_user_details_are_aggregated_and_content_free(self) -> None:
        user = AuthenticatedUser(id="admin-1", email="admin@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.select = AsyncMock(
            side_effect=[
                [{"role_id": "admin-role"}],
                [{"name": "ADMIN"}],
            ]
        )
        mock_db.request = AsyncMock(return_value={
            "id": "target-1",
            "full_name": "Rajput",
            "roles": ["USER"],
            "account_status": "ACTIVE",
            "email_verified": True,
            "created_at": "2026-01-01T00:00:00Z",
            "email_activity": {
                "emails_received": 5,
                "gmail_emails_received": 3,
                "emails_processed": 2,
                "unread_emails": 2,
                "ai_analyses": 5,
                "pending_action_items": 1,
            },
            "ai_usage": {"requests": 2, "tokens": 42},
            "smart_replies_generated": 3,
            "last_gmail_sync_at": "2026-10-03T10:00:00Z",
        })
        with patch("app.routers.admin.user_client", return_value=mock_db):
            details = await admin_user_details("target-1", user)

        self.assertEqual(details["full_name"], "Rajput")
        self.assertEqual(details["roles"], ["USER"])
        self.assertEqual(details["email_activity"], {
            "emails_received": 5,
            "gmail_emails_received": 3,
            "emails_processed": 2,
            "unread_emails": 2,
            "ai_analyses": 5,
            "pending_action_items": 1,
        })
        self.assertEqual(details["ai_usage"], {"requests": 2, "tokens": 42})
        self.assertEqual(details["smart_replies_generated"], 3)
        self.assertEqual(details["last_gmail_sync_at"], "2026-10-03T10:00:00Z")
        self.assertNotIn("body_text", details)
        self.assertNotIn("short_summary", details)
        self.assertNotIn("generated_reply", details)
        mock_db.request.assert_awaited_once_with(
            "POST",
            "rest/v1/rpc/admin_user_details",
            json={"target_user_id": "target-1"},
        )

    async def test_admin_user_details_reject_non_admin_callers(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.count = AsyncMock()
        mock_db.select = AsyncMock(
            side_effect=[[{"role_id": "user-role"}], [{"name": "USER"}]]
        )
        with patch("app.routers.admin.user_client", return_value=mock_db):
            with self.assertRaises(HTTPException) as raised:
                await admin_user_details("target-1", user)

        self.assertEqual(raised.exception.status_code, 403)
        mock_db.count.assert_not_awaited()

    async def test_admin_inbox_query_is_scoped_to_the_admins_own_workspace(self) -> None:
        user = AuthenticatedUser(id="admin-1", email="admin@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.select = AsyncMock(return_value=[])
        with patch("app.routers.emails.user_client", return_value=mock_db):
            await list_emails(user, q=None, status_filter=None, page=1, page_size=30)

        filters = mock_db.select.await_args.args[1]
        self.assertEqual(filters["user_id"], "eq.admin-1")

    async def test_analytics_reads_ai_analyses_through_parent_email_rls(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.select = AsyncMock(return_value=[])
        with patch("app.routers.insights.user_client", return_value=mock_db):
            await analytics_overview(user)

        analysis_call = next(
            call for call in mock_db.select.await_args_list
            if call.args[0] == "ai_analyses"
        )
        self.assertNotIn("user_id", analysis_call.args[1])

    async def test_email_analysis_does_not_filter_child_tables_by_missing_user_id(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.select = AsyncMock(side_effect=[
            [{"id": "email-1"}],
            [],
            [],
        ])
        with patch("app.routers.emails.user_client", return_value=mock_db):
            await email_analysis("email-1", user)

        analysis_call, extraction_call = mock_db.select.await_args_list[1:]
        self.assertEqual(analysis_call.args[0], "ai_analyses")
        self.assertEqual(extraction_call.args[0], "extracted_information")
        self.assertNotIn("user_id", analysis_call.args[1])
        self.assertNotIn("user_id", extraction_call.args[1])

    async def test_insights_reads_ai_analyses_through_parent_email_rls(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.select = AsyncMock(return_value=[])
        with patch("app.routers.insights.user_client", return_value=mock_db):
            await insights(user, limit=10)

        filters = mock_db.select.await_args.args[1]
        self.assertNotIn("user_id", filters)

    async def test_admin_user_list_uses_limited_summary_rpc(self) -> None:
        user = AuthenticatedUser(id="admin-1", email="admin@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.request = AsyncMock(return_value=[{
            "id": "target-1",
            "full_name": "Rajput",
            "is_active": True,
            "email_verified": True,
            "created_at": "2026-01-01T00:00:00Z",
            "roles": ["USER"],
        }])
        with patch("app.routers.admin.user_client", return_value=mock_db):
            users = await admin_users(user, limit=25)

        self.assertEqual(users[0]["roles"], ["USER"])
        self.assertNotIn("phone", users[0])
        mock_db.request.assert_awaited_once_with(
            "POST", "rest/v1/rpc/admin_user_list", json={"result_limit": 25}
        )

    async def test_admin_user_changes_use_restricted_database_rpcs(self) -> None:
        user = AuthenticatedUser(id="admin-1", email="admin@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.request = AsyncMock(side_effect=[
            {"user_id": "target-1", "roles": ["ADMIN", "USER"]},
            {"user_id": "target-1", "is_active": False},
        ])
        with patch("app.routers.admin.user_client", return_value=mock_db):
            roles = await update_user_roles(
                "target-1",
                UserRolesUpdate(roles=["USER", "ADMIN"]),
                user,
            )
            active = await set_user_active("target-1", False, user)

        self.assertEqual(roles["roles"], ["ADMIN", "USER"])
        self.assertEqual(active["is_active"], False)
        self.assertEqual(mock_db.request.await_count, 2)

    async def test_notification_preferences_hide_database_metadata(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        row = {
            "id": "preference-1",
            "user_id": "user-1",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "urgent_email": False,
        }
        mock_db = Mock()
        mock_db.select = AsyncMock(return_value=[row])
        mock_db.update = AsyncMock(return_value=[row])
        with patch("app.routers.users.user_client", return_value=mock_db):
            fetched = await notification_preferences(user)
            saved = await save_notification_preferences(
                NotificationPreferences.model_validate(fetched), user
            )

        self.assertEqual(fetched["urgent_email"], False)
        self.assertEqual(saved["urgent_email"], False)
        self.assertEqual(set(fetched), set(NotificationPreferences.model_fields))
        self.assertEqual(set(saved), set(NotificationPreferences.model_fields))
        self.assertNotIn("id", fetched)
        self.assertNotIn("user_id", saved)

    async def test_notification_preferences_update_conflict_is_not_reported_as_success(self) -> None:
        user = AuthenticatedUser(id="user-1", email="user@example.com", _access_token="secret")
        mock_db = Mock()
        mock_db.select = AsyncMock(return_value=[{"id": "preference-1"}])
        mock_db.update = AsyncMock(return_value=[])
        with patch("app.routers.users.user_client", return_value=mock_db):
            with self.assertRaises(HTTPException) as raised:
                await save_notification_preferences(NotificationPreferences(), user)

        self.assertEqual(raised.exception.status_code, 409)

    async def test_frontend_request_shapes_validate_against_backend_models(self) -> None:
        requests = (
            (EmailCreate, {
                "subject": "Project update",
                "sender_email": "sender@example.com",
                "receiver_email": "recipient@example.com",
                "body_text": "Hello",
            }),
            (EmailUpdate, {"status": "READ", "is_starred": True}),
            (ReplyRequest, {"tone": "PROFESSIONAL"}),
            (ReplyUpdate, {"edited_reply": "Thanks for the update."}),
            (AssistantRequest, {"message": "Find the due date", "conversation_id": None}),
            (ActionItemUpdate, {"status": "COMPLETED"}),
            (DeadlineUpdate, {"is_completed": True}),
            (NotificationPreferences, {
                "urgent_email": True,
                "customer_complaint": True,
                "reply_required": True,
                "upcoming_deadline": True,
                "action_item": True,
                "ai_processing_completed": False,
                "email_notifications": False,
                "in_app_notifications": True,
            }),
            (GmailExchange, {"code": "oauth-code", "state": "signed-state"}),
        )
        for model, payload in requests:
            with self.subTest(model=model.__name__):
                model.model_validate(payload)

    async def test_versioned_api_prefix_is_not_registered(self) -> None:
        response = await self.request("/api/v1/emails")
        self.assertEqual(response.status_code, 404)

    async def test_openapi_includes_major_product_surfaces(self) -> None:
        paths = app.openapi()["paths"]

        for path in (
            "/auth/login",
            "/emails",
            "/integrations/gmail/authorize",
            "/integrations/gmail/callback",
            "/integrations/gmail/sync",
            "/assistant/ask",
            "/assistant/search",
            "/analytics/overview",
            "/admin/overview",
            "/admin/users",
            "/admin/users/{user_id}",
        ):
            with self.subTest(path=path):
                self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()
