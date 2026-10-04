# Database — AI Email Intelligence & Smart Reply System

Complete reference for the PostgreSQL database: all tables, Role-Based Access
Control (RBAC), and data isolation (Row Level Security) rules.

> Connection details live in `backend/.env` (`DATABASE_URL`, `ANON_KEY`).
> **Never print, commit, or expose these values.** `ANON_KEY` is the public
> Supabase API key for the frontend; the database URL stays backend-only.

---

## 1. Overview

| Item | Value |
|---|---|
| Engine | PostgreSQL 17 (Supabase) |
| Vector search | pgvector `0.8.2` (extension `vector`, schema `public`) |
| UUIDs | `uuid-ossp` → `uuid_generate_v4()` defaults |
| Auth | Supabase Auth (`auth.users`); signup trigger `handle_new_user()` creates the profile |
| Total tables | **28** application tables (29 incl. `schema_migrations` bookkeeping) |
| RLS policies | **40** — RLS enabled on **every** table |
| Roles (DB API) | `anon`, `authenticated`, `service_role`, `postgres` |
| App roles | `ADMIN`, `USER` (see RBAC below) |

### Conventions

- Primary keys: `uuid PRIMARY KEY DEFAULT uuid_generate_v4()`
- Timestamps: `timestamptz NOT NULL DEFAULT now()`; `updated_at` maintained by
  the `update_updated_at_column()` trigger
- Ownership: `user_id uuid NOT NULL REFERENCES profiles(id)` —
  `profiles.id` → `auth.users.id` (`ON DELETE CASCADE`)
- Enums live in schema `public` with UPPERCASE values (see §5)

### Scripts (`backend/database/`)

| Script | Purpose |
|---|---|
| `check_db.py` | Connectivity, extensions, table list |
| `run_migrations.py` | Applies pending `migrations/*.sql` in order — already-applied files are tracked in `schema_migrations` and skipped |
| `verify_migration.py` | Structural and functional RLS checks, including Gmail-token isolation and semantic-search scope |
| `migrations/000_base_schema.sql` | Creates the core application tables, enums, profile signup trigger, timestamp triggers, initial categories, table grants, and fail-closed RLS on a fresh Supabase project |
| `migrations/001_rag_rbac_isolation.sql` | pgvector, RAG tables, RBAC tables, `is_admin()`, initial RLS policies |
| `migrations/002_permissions_catalog.sql` | Permission catalog synced to the RBAC tree (24 permissions) |
| `migrations/003_user_roles_single_source.sql` | Dropped `profiles.role` + `avatar_url`, removed the sync/guard triggers, made `user_roles` the single source of truth for roles |
| `migrations/004_gmail_and_rag_api.sql` | Encrypted Gmail OAuth connection storage and caller-scoped pgvector search RPC |
| `migrations/005_gmail_reply_sending.sql` | Reply sending status and Gmail delivery metadata |
| `migrations/006_admin_workspace_isolation.sql` | Owner-only workspace/profile RLS and guarded, content-free admin summary/management RPCs |

Applied migrations are recorded in `public.schema_migrations` (RLS-protected
bookkeeping table) and never re-executed. Migration files may therefore be
edited afterwards without affecting an already-initialized database.

For a new Supabase project, create the project first (Supabase supplies
`auth.users` and the database API roles), configure `DATABASE_URL`, then run
`backend/database/run_migrations.py`. Migration `000` creates the core schema;
migrations `001`–`006` complete it. Do not import another deployment's user or
email data as part of schema setup.

```bash
backend/venv/Scripts/python.exe backend/database/run_migrations.py
backend/venv/Scripts/python.exe backend/database/verify_migration.py
```

---

## 2. Entity relationships (summary)

```
auth.users ──< profiles (id) ──< user_roles >── roles ──< role_permissions >── permissions
                 │
                 ├──< email_threads ──< emails ──< email_attachments
                 │                       │  ├──< ai_analyses
                 │                       │  ├──< extracted_information
                 │                       │  ├──< email_processing_runs
                 │                       ├──< action_items
                 │                       ├──< deadlines
                 │                       ├──< ai_replies
                 │                       ├──< ai_usage_logs
                 │                       ├──< notifications
                 │                       └──< email_chunks ──< embeddings   (vector 1536)
                 │                              └── rag_retrievals ──> ai_conversations
                 ├──< ai_conversations ──< ai_conversation_messages
                 ├──< email_senders / email_imports / notification_preferences / gmail_connections
                 └──< audit_logs
```

`email_categories` (shared reference data, admin-managed)
`system_settings` (admin-managed, non-secret rows readable by users)

---

## 3. All tables (27)

> Bookkeeping: `public.schema_migrations` additionally stores which migrations
> have been applied — it is not an application table.

### 3.1 Users & auth

#### `profiles`
User account data only — **roles are not stored here**; `user_roles` is the
single source of truth (the duplicated `profiles.role` column was removed in
migration 003, together with the sync/guard triggers). `id` references
`auth.users(id)`; a row is created automatically on signup and immediately
receives the `USER` role in `user_roles` (trigger `assign_default_user_role`).

| Column | Type | Notes |
|---|---|---|
| id | uuid PK | → `auth.users.id` |
| full_name | varchar | |
| phone | varchar | |
| is_active | boolean | default `true` |
| email_verified | boolean | default `false` |
| created_at / updated_at | timestamptz | |

### 3.2 Email core

#### `emails` (owner: `user_id`)
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid NOT NULL | → profiles (**owner**) |
| thread_id | uuid | → email_threads |
| sender_id | uuid | → email_senders |
| category_id | uuid | → email_categories |
| sender_email / sender_name | varchar | |
| receiver_email / receiver_name | varchar | |
| cc / bcc | text | |
| subject | varchar | |
| body_text / body_html | text | |
| preview | text | |
| message_id / in_reply_to | varchar | RFC message ids |
| email_date | timestamptz | |
| status | `email_status` | `UNREAD/READ/ARCHIVED/DELETED` |
| is_starred / is_archived / is_deleted | boolean | |
| reply_required / action_required | boolean | |
| source_type | varchar | default `'MANUAL'` |
| raw_email | text | |
| created_at / updated_at | timestamptz | |

Imported emails use `source_type = 'IMPORT'`; Gmail-synced emails use
`source_type = 'GMAIL'`; manually created ones use `'MANUAL'`.

#### `email_threads` (owner: `user_id`)
`id, user_id, subject, thread_reference, first_email_at, last_email_at,
email_count int, is_archived, created_at, updated_at`

#### `email_senders` (owner: `user_id`)
`id, user_id, email_address, display_name, company_name, phone_number,
customer_reference, created_at, updated_at`

#### `email_categories` (shared, admin-managed)
`id, name, description, is_system, is_active, created_at`

#### `email_imports` (owner: `user_id`)
`id, user_id, import_type (EML/CSV/JSON/MANUAL), file_name, total_records,
successful_records, failed_records, status (processing_status), error_message,
created_at, completed_at`

#### `email_attachments` (owner via email)
`id, email_id → emails, file_name, file_type, file_size bigint, storage_path,
created_at`

#### `email_processing_runs` (owner via email)
`id, email_id, status (processing_status), parser_completed,
cleaning_completed, classification_completed, summary_completed,
extraction_completed, sentiment_completed, priority_completed,
action_detection_completed, deadline_detection_completed, error_message,
started_at, completed_at, created_at`

### 3.3 AI analysis & intelligence

#### `ai_analyses` (owner via email)
`id, email_id, category_id, intent, priority (email_priority), priority_reason,
sentiment (email_sentiment), sentiment_confidence numeric, short_summary,
detailed_summary, reply_required, action_required, ai_model,
processing_time_ms, created_at, updated_at`

#### `extracted_information` (owner via email)
`id, email_id, customer_name, company_name, phone_number, email_address,
order_number, invoice_number, product, amount numeric, currency,
mentioned_date date, deadline_date date, meeting_date timestamptz, location,
requested_action, additional_data jsonb, created_at, updated_at`

#### `action_items` (owner: `user_id`, also linked to email)
`id, user_id, email_id, title, description, status (action_status), due_date,
completed_at, assigned_to → profiles, created_at, updated_at`

#### `deadlines` (owner: `user_id`)
`id, user_id, email_id, title, description, original_text, deadline_at,
is_completed, completed_at, created_at, updated_at`

#### `ai_replies` — smart replies (owner: `user_id`)
`id, user_id, email_id, thread_id, tone (reply_tone), generated_reply,
edited_reply, status (reply_status: DRAFT/EDITED/APPROVED/REJECTED/SENDING/SENT),
ai_model, generation_count, approved_at, rejected_at, sent_at,
gmail_message_id, created_at, updated_at`

#### `ai_conversations` (owner: `user_id`)
`id, user_id, title, created_at, updated_at`

#### `ai_conversation_messages` (owner via conversation)
`id, conversation_id → ai_conversations, role varchar, content text,
referenced_email_ids uuid[], ai_model, created_at`

#### `ai_usage_logs` (owner: `user_id`; admin reads all)
`id, user_id, email_id, operation, ai_model, input_tokens, output_tokens,
total_tokens, processing_time_ms, estimated_cost numeric, status
(processing_status), error_message, created_at`

### 3.4 Notifications, preferences, audit

#### `notifications` (owner: `user_id`)
`id, user_id, email_id, action_item_id, deadline_id, type
(notification_type), title, message, is_read, created_at`

#### `notification_preferences` (owner: `user_id`)
`id, user_id, urgent_email, customer_complaint, reply_required,
upcoming_deadline, action_item, ai_processing_completed, email_notifications,
in_app_notifications, created_at, updated_at`

#### `audit_logs` (append-only for users, admin reads all)
`id, user_id, action, entity_type, entity_id, description, ip_address inet,
user_agent, metadata jsonb, created_at`

#### `system_settings` (admin-managed; non-secret readable by users)
`id, setting_key, setting_value, description, is_secret, updated_by →
profiles, created_at, updated_at`

### 3.5 RAG / Embedding layer (migration 001)

> pgvector `vector(1536)` — OpenAI `text-embedding-3-small`.
> HNSW index `idx_embeddings_vector` on `embedding vector_cosine_ops` (cosine).

#### `email_chunks` (owner: `user_id`)
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid NOT NULL | → profiles (**owner**) |
| email_id | uuid NOT NULL | → emails, `ON DELETE CASCADE` |
| chunk_index | integer | `UNIQUE(email_id, chunk_index)` |
| content | text | chunk text embedded for RAG |
| token_count | integer | |
| created_at / updated_at | timestamptz | |

#### `embeddings` (owner: `user_id`)
| Column | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid NOT NULL | → profiles (**owner**, denormalized for RLS) |
| chunk_id | uuid NOT NULL | → email_chunks, `ON DELETE CASCADE` |
| embedding | **vector(1536)** | cosine HNSW indexed |
| model | varchar | default `text-embedding-3-small` |
| created_at | timestamptz | `UNIQUE(chunk_id, model)` |

#### `rag_retrievals` (owner: `user_id`)
`id, user_id, conversation_id → ai_conversations (nullable), chunk_id →
email_chunks (nullable), query_text, similarity double precision, model,
created_at` — logs each retrieval for audit/analytics.

### 3.6 RBAC tables (migrations 001–003)

#### `roles`
`id, name UNIQUE ('ADMIN' | 'USER'), description, is_system bool, created_at,
updated_at`

#### `permissions`
`id, code UNIQUE (e.g. 'emails:read'), name, description, resource, action,
created_at, updated_at` — **24 rows** (see §4).

#### `role_permissions` — role → permission mapping
`(role_id, permission_id) PK, granted_by → profiles, created_at`

#### `user_roles` — user → role assignment (**single source of truth** for
roles; writes are admin-only via RLS)
`(user_id, role_id) PK, assigned_by → profiles, assigned_at`

### 3.7 Gmail OAuth connection (migration 004)

#### `gmail_connections` (owner: `user_id`)
`id, user_id UNIQUE → profiles, google_email, encrypted_refresh_token,
granted_scopes, last_synced_at, last_history_id, created_at, updated_at`

The API encrypts Google refresh tokens with the backend-only
`GMAIL_TOKEN_FERNET_KEY` before storing them. RLS limits rows to the owning
authenticated user only. Google access tokens are refreshed
when needed and are not persisted. Gmail OAuth requests read and send scopes;
existing connections must reconnect to grant `gmail.send` before sending
approved reply drafts.

Migration 004 also adds `search_email_chunks(query_embedding, match_count)`,
a `SECURITY INVOKER` RPC that returns cosine-ranked email chunks only for
`auth.uid()`. It is granted to `authenticated` and retains RLS enforcement.

---

## 4. Role-Based Access Control (RBAC)

Two application roles: **ADMIN** and **USER**.

- **`user_roles` is the single source of truth for roles** — `profiles`
  stores profile data only (the duplicated `profiles.role` column was removed
  in migration 003, together with the sync/guard triggers).
- `public.is_admin()` returns `true` when the caller (`auth.uid()`) holds the
  `ADMIN` row in `user_roles`. It is `SECURITY DEFINER` and used inside RLS
  policies.
- Trigger `assign_default_user_role` — one-shot assignment of `USER` when a
  profile is created, so new signups are normal users automatically. There is
  **no ongoing synchronization**: role changes are written directly to
  `user_roles`.
- Privilege escalation is prevented by RLS itself: `user_roles` writes are
  admin-only (policy `user_roles_manage`), so a user can never grant
  themselves a role — no extra guard trigger needed.

### Permission catalog (24)

**ADMIN receives all 24. USER receives the 13 marked ✅.**

| Code | Permission | Resource/Action | USER |
|---|---|---|:--:|
| `users:manage` | User management | users/manage | |
| `emails:read_all` | View all users' emails | emails/read_all | |
| `emails:manage_all` | Manage users' emails | emails/manage_all | |
| `system_settings:manage` | System settings | system_settings/manage | |
| `system_analytics:read` | System analytics | system_analytics/read | |
| `audit_logs:read` | Audit logs | audit_logs/read | |
| `ai_usage:read` | AI usage / AI costs | ai_usage/read | |
| `email_categories:manage` | Email categories | email_categories/manage | |
| `ai_config:manage` | AI processing configuration | ai_config/manage | |
| `notification_config:manage` | Notification configuration | notification_config/manage | |
| `monitoring:read` | System-wide monitoring | monitoring/read | |
| `emails:read` | Own emails | emails/read | ✅ |
| `emails:import` | Import own emails | emails/import | ✅ |
| `emails:search` | Search/filter own emails | emails/search | ✅ |
| `ai_analysis:read` | Own AI analysis | ai_analysis/read | ✅ |
| `ai_summaries:read` | Own summaries | ai_summaries/read | ✅ |
| `extracted_info:read` | Own extracted information | extracted_info/read | ✅ |
| `action_items:manage` | Own action items | action_items/manage | ✅ |
| `deadlines:manage` | Own deadlines | deadlines/manage | ✅ |
| `smart_replies:manage` | Generate/edit/approve own AI replies | smart_replies/manage | ✅ |
| `notifications:manage` | Own notifications | notifications/manage | ✅ |
| `analytics:read` | Own analytics | analytics/read | ✅ |
| `ai_assistant:use` | AI Assistant | ai_assistant/use | ✅ |
| `rag_search:use` | RAG search over own emails | rag_search/use | ✅ |

Permission codes are for **application-layer checks** (middleware / service
guards). Data access itself is enforced by RLS regardless of these codes.

### Bootstrap an admin

Run from a trusted server-side connection (postgres / `service_role`, no JWT):

```sql
INSERT INTO user_roles (user_id, role_id)
SELECT '<user-uuid>', id FROM roles WHERE name = 'ADMIN';
```

After that, admins manage roles through the API (`user_roles` / `roles` /
`permissions` policies are admin-writable).

---

## 5. Enums

| Enum | Values |
|---|---|
| `email_status` | UNREAD, READ, ARCHIVED, DELETED |
| `email_priority` | LOW, MEDIUM, HIGH, CRITICAL |
| `email_sentiment` | POSITIVE, NEUTRAL, NEGATIVE, ANGRY, URGENT |
| `action_status` | PENDING, IN_PROGRESS, COMPLETED, CANCELLED |
| `reply_tone` | PROFESSIONAL, FRIENDLY, SHORT, DETAILED, APOLOGETIC, FORMAL |
| `reply_status` | DRAFT, APPROVED, REJECTED, EDITED, SENDING, SENT |
| `processing_status` | PENDING, PROCESSING, COMPLETED, FAILED |
| `import_type` | EML, CSV, JSON, MANUAL |
| `notification_type` | URGENT_EMAIL, CUSTOMER_COMPLAINT, REPLY_REQUIRED, UPCOMING_DEADLINE, ACTION_ITEM, AI_PROCESSING_COMPLETED, SYSTEM |

---

## 6. Data isolation (Row Level Security)

**Every application table has RLS enabled.** Migration 006 supersedes the
initial administrator-wide workspace bypass: an admin's normal database/API
access is limited to their own workspace. Cross-user admin screens use guarded
RPCs that return only the fields and aggregate counts explicitly listed in the
admin interface.

How it works:

1. The frontend/API calls Supabase with a user JWT; PostgREST runs as the
   `authenticated` role and `auth.uid()` resolves to the user's UUID.
2. Workspace and profile policies evaluate ownership against `auth.uid()` —
   including for administrators. An admin's inbox, Gmail connection, Assistant
   conversations, and profile remain private to that admin.
3. Cross-user admin data is returned only by `SECURITY DEFINER` RPCs that
   verify `public.is_admin()` and expose limited metadata/counts (not email
   bodies, summaries, extracted content, or generated replies). Admin user
   management also uses guarded RPCs because profiles are owner-only.
4. `service_role` bypasses RLS; direct `postgres` connections (backend with
   `DATABASE_URL`) are the table owner and are **not** subject to RLS — keep
   the database URL backend-only.
5. `anon` (no JWT) matches no policy → **zero rows** on every table.

### Policy map

| Table(s) | Policy | Rule |
|---|---|---|
| `emails`, `email_threads`, `email_senders`, `email_imports`, `notification_preferences`, `notifications`, `ai_conversations`, `ai_replies`, `ai_usage_logs`, `action_items`, `deadlines`, `email_chunks`, `embeddings`, `rag_retrievals` (14 tables) | `user_isolation` (ALL) | `user_id = auth.uid()` — each user, including admins, can access only their own workspace |
| `gmail_connections` | `gmail_connections_owner` (ALL) | `user_id = auth.uid()` — encrypted account tokens belong to one user |
| `audit_logs` | `audit_logs_select` | own rows or admin |
| | `audit_logs_insert` | only own rows (`user_id = auth.uid()`) — append-only |
| | `audit_logs_update` / `audit_logs_delete` | **admin only** |
| `ai_analyses`, `extracted_information`, `email_attachments`, `email_processing_runs` | `owner_via_email` (ALL) | owner of the parent email (`emails.user_id = auth.uid()`) |
| `ai_conversation_messages` | `owner_via_conversation` (ALL) | owner of the parent conversation |
| `profiles` | `profiles_select` / `profiles_insert` / `profiles_update` | own row only; cross-user profile details are available only through limited admin RPCs |
| | `profiles_delete` | denied to authenticated users |
| `email_categories` | `shared_read` | readable by all authenticated users |
| | `admin_manage` | writes admin only |
| `system_settings` | `settings_read` | admin sees all; users see rows with `is_secret = false` |
| | `settings_manage` | writes admin only |
| `roles`, `permissions`, `role_permissions` | `shared_read` | readable by all authenticated users |
| | `admin_manage` | writes admin only |
| `user_roles` | `user_roles_select` | own assignments or admin |
| | `user_roles_manage` | writes admin only — **a user can never grant themselves a role** |

### Isolation guarantees (verified by `verify_migration.py`)

- User A cannot read or update user B's emails, chunks, embeddings,
  retrievals, profiles, or audit logs — verified with two real JWT identities.
- User A cannot insert rows owned by B (RLS `WITH CHECK` violation).
- User A cannot grant themselves `ADMIN` in `user_roles` (RLS violation) — with
  `profiles.role` removed, `user_roles` is the only role store, so this single
  check fully prevents self-promotion.
- An admin cannot read or modify another user's email/workspace rows through
  direct PostgREST access. Admin dashboards call verified RPCs for their
  intended aggregate counts and restricted user metadata; these RPCs never
  return email content.

---

## 7. Indexes

| Index | Table | Purpose |
|---|---|---|
| `idx_embeddings_vector` (HNSW, cosine) | embeddings | vector similarity search for RAG |
| `idx_email_chunks_user_id` / `idx_email_chunks_email_id` | email_chunks | ownership + chunk lookup |
| `idx_embeddings_user_id` / `idx_embeddings_chunk_id` | embeddings | ownership + chunk join |
| `idx_rag_retrievals_user/conversation/created_at` | rag_retrievals | retrieval history |
| `idx_user_roles_role_id` | user_roles | reverse role lookup |
| `idx_role_permissions_permission_id` | role_permissions | reverse permission lookup |
| PKs + FK columns | (all tables) | uuid PK indexes; FK columns indexed where queried |

---

## 8. Notes for API development

- **Authentication**: users authenticate through Supabase Auth; send the user
  JWT on every request so policies apply to `authenticated`. The `ANON_KEY`
  alone (no JWT) yields no data.
- **AI API keys** (OpenAI, Gemini etc.) belong in `backend/.env` on the server
  only — never expose them to the frontend and never store them in
  `system_settings.is_secret = false`.
- **Permission checks**: gate endpoints with
  `role_permissions`/`permissions` (e.g. `has_permission('emails:import')`);
  RLS is the safety net, not the primary authorizer.
- **Smart replies** require explicit approval and a separate Send action.
  Sending uses the user's connected Gmail account, and the app only records
  `SENT` after Gmail returns a message ID. Gmail OAuth must include the
  `gmail.send` scope; existing connections need to reconnect to grant it.
- **RAG flow**: chunk (`email_chunks`) → embed (`embeddings`, vector(1536)) →
  cosine similarity search filtered implicitly by RLS → log into
  `rag_retrievals` → feed retrieved context to the LLM.
- **Gmail OAuth**: Google refresh tokens are encrypted by the backend before
  storage in `gmail_connections`; configure `APP_SECRET_KEY` and
  `GMAIL_TOKEN_FERNET_KEY` server-side.

---

## 9. Implementation status

| Area | Status |
|---|---|
| PostgreSQL schema + migrations (28 tables, RLS, 24 permissions) | Implemented; apply pending migrations in order |
| Supabase Auth + RBAC + owner-only workspace RLS | Implemented; migration 006 adds admin workspace isolation |
| Gmail OAuth 2.0 + encrypted refresh tokens + sync | Implemented |
| Manual email creation + `.eml`/CSV/JSON import | Implemented |
| AI analysis, summaries, classification, extraction, action items, deadlines | Implemented |
| Smart replies (draft/approve/reject) | Implemented |
| Notifications + preferences | Implemented |
| Analytics + insights | Implemented |
| AI Assistant + RAG semantic search | Implemented |
| Admin dashboard | Implemented |
| Frontend (login, dashboard, email, search, AI insights, smart replies, actions, deadlines, assistant, notifications, analytics, admin) | Implemented; `npm run build` passes |
| Backend unit tests | 15 passed / 1 failing (stale env-var mapping test) |
| Deployed end-to-end (real Supabase + Gmail + AI provider) | Not yet deployed — requires credentials |
