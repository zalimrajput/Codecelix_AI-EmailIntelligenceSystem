# Letterwise — AI Email Intelligence

Letterwise is a private email workspace for organizing, understanding, and
searching email. The application combines a FastAPI backend, a Next.js
frontend, Supabase Auth, PostgreSQL Row Level Security (RLS), and pgvector
semantic search.

## What the application does

- Creates user accounts and signs users in through Supabase Auth.
- Keeps each account's email, Gmail connection, assistant conversations, and
  other workspace data private to that user. Administrators have their own
  private workspace as well.
- Provides admin-only account management and approved aggregate views. The
  admin dashboard does not expose other users' email bodies, summaries, or
  generated replies.
- Adds email by manual composition, `.eml`/CSV/JSON import, or Gmail OAuth 2.0
  synchronization.
- Groups related messages into email threads and processes new messages in a
  shared background pipeline.
- Uses Gemini or OpenAI, when configured, for email analysis, summaries,
  classification, extraction, action items, dates, smart replies, and
  embeddings.
- Supports semantic email search and a grounded AI Assistant with **Ask** and
  **Find emails** modes.
- Offers inbox filters, action items, upcoming dates, notifications,
  analytics, approved reply workflows, and Gmail reply sending.

For the detailed AI lifecycle, failure behavior, and retrieval flow, see
[AI_PROCESSING_FLOW.md](./AI_PROCESSING_FLOW.md). The database schema, RLS, and
migration notes are in [DATABASE.md](./DATABASE.md).

## Project layout

```text
backend/
  app/                 FastAPI routes, auth, integrations, and AI services
  database/
    migrations/        Ordered PostgreSQL/Supabase SQL migrations
    run_migrations.py
    verify_migration.py
  tests/               Python unittest suite
frontend/
  app/                 Next.js App Router pages and dashboard
  lib/                 API and Supabase browser clients
```

## Requirements

- Python 3.11 or later
- Node.js 20 or later and npm
- A Supabase project with PostgreSQL, Auth, and pgvector support
- At least one AI provider key (Gemini or OpenAI) for AI processing and search
- Google OAuth web-client credentials if Gmail integration is needed

## Local setup (Windows PowerShell)

### 1. Configure the backend

```powershell
cd "E:\AI Email Intelligence System\backend"
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `backend\.env` and set the values for your own Supabase project and
provider configuration. Do not copy secrets into the frontend or commit either
`.env` file.

The backend settings include:

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Supabase project URL used by the backend |
| `SUPABASE_ANON_KEY` | Supabase public/anon key sent with the user's JWT |
| `DATABASE_URL` | PostgreSQL connection used by migration/verification scripts |
| `GEMINI_API_KEY` | Gemini chat and embedding provider (takes precedence if set) |
| `GEMINI_CHAT_MODEL` / `GEMINI_EMBEDDING_MODEL` | Optional Gemini model overrides |
| `OPENAI_API_KEY` | OpenAI provider, used when Gemini is not configured |
| `OPENAI_CHAT_MODEL` / `OPENAI_EMBEDDING_MODEL` | Optional OpenAI model overrides |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Google OAuth web-client credentials |
| `GMAIL_REDIRECT_URI` | OAuth callback; local default is `http://localhost:8000/integrations/gmail/callback` |
| `APP_SECRET_KEY` | Secret used to sign and validate OAuth state |
| `GMAIL_TOKEN_FERNET_KEY` | Fernet key used to encrypt Gmail refresh tokens at rest |
| `FRONTEND_URL` / `CORS_ORIGINS` | Frontend callback URL and allowed browser origins |

The AI provider is selected automatically: a configured `GEMINI_API_KEY` is
preferred; otherwise the backend uses `OPENAI_API_KEY`. Keep AI, database, and
Google OAuth secrets in the backend environment only.

### 2. Apply database migrations

Create a Supabase project first. Supabase provides the `auth` schema and
authentication tables that the application references. Then configure
`DATABASE_URL` for that project's PostgreSQL database and run from the backend
directory:

```powershell
python database\run_migrations.py
python database\verify_migration.py
```

Migrations `000`–`006` create the application's full database schema from an
empty Supabase project: core tables, enums, signup/profile setup, email
categories, RAG/pgvector tables, RBAC permissions, Gmail connection storage,
reply delivery fields, RLS policies, and guarded admin functions. They are
applied in filename order and recorded in `public.schema_migrations`.
Migration 006 enforces owner-only workspaces, including for administrators.
The verification script runs structural and functional checks against the
configured database; use it only where its temporary test users can safely be
created and removed.

### 3. Run the backend

From the `backend` directory, with the virtual environment activated and
`backend\.env` configured, start the FastAPI development server:

```powershell
cd "E:\AI Email Intelligence System\backend"
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Keep this terminal open while using the application. The API is available at
`http://localhost:8000`, the interactive API documentation at
`http://localhost:8000/docs`, and the health endpoint at
`http://localhost:8000/health`.

### 4. Configure and run the frontend

In a second PowerShell terminal:

```powershell
cd "E:\AI Email Intelligence System\frontend"
Copy-Item .env.example .env.local
npm install
npm run dev
```

Set these values in `frontend\.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-public-anon-key
```

Use the same Supabase project URL in both applications. Only the public anon
key belongs in the frontend; never put a service-role key there.

Open `http://localhost:3000`. Keep both the backend and frontend terminals
running while using the application.

### Gmail OAuth

If enabling Gmail, configure the Google OAuth client's authorized redirect URI
to exactly match the backend `GMAIL_REDIRECT_URI`, normally:

```text
http://localhost:8000/integrations/gmail/callback
```

The authenticated user connects their own Google account from the application.
The backend stores only the encrypted refresh token; short-lived access tokens
are refreshed when needed and are not persisted.

## Main API areas

All API paths are root-level; the backend does not add an `/api/v1` prefix.

| Area | Paths |
|---|---|
| Auth | `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me` |
| Account | `/profile`, `/action-items`, `/deadlines`, `/notifications`, `/notification-preferences` |
| Email | `/emails`, `/emails/import`, `/emails/{id}`, `/emails/{id}/analysis`, `/emails/{id}/reprocess`, `/emails/{id}/replies`, `/replies/{id}`, `/replies/{id}/approve`, `/replies/{id}/reject`, `/threads` |
| Gmail | `/integrations/gmail/authorize`, `/integrations/gmail/callback`, `/integrations/gmail/status`, `/integrations/gmail/exchange`, `/integrations/gmail/sync`, `/integrations/gmail`, `/integrations/gmail/replies/{id}/send` |
| Assistant | `/assistant/ask`, `/assistant/conversations`, `/assistant/conversations/{id}/messages`, `/assistant/search` |
| Insights | `/analytics/overview`, `/categories`, `/insights` |
| Admin | `/admin/overview`, `/admin/users`, `/admin/users/{id}`, `/admin/audit-logs`, `/admin/ai-usage`, `/admin/categories` |

See `http://localhost:8000/docs` for request models, methods, and response
schemas. Endpoint access is protected by application permissions and database
RLS. Cross-user admin screens use limited, role-checked database RPCs rather
than broad access to user workspaces.

## Tests and checks

Backend unit tests:

```powershell
cd "E:\AI Email Intelligence System\backend"
.\venv\Scripts\Activate.ps1
python -m unittest discover -s tests -v
```

Frontend lint and production build:

```powershell
cd "E:\AI Email Intelligence System\frontend"
npm run lint
npm run build
```

Database verification (requires the configured PostgreSQL connection):

```powershell
cd "E:\AI Email Intelligence System\backend"
python database\verify_migration.py
```

## Security notes

- Never commit `backend/.env` or `frontend/.env.local`; they may contain
  credentials or deployment-specific configuration.
- Never expose the PostgreSQL URL, provider keys, Google OAuth client secret,
  Fernet key, or Supabase service-role key in browser code or logs.
- Gmail tokens are encrypted server-side using `GMAIL_TOKEN_FERNET_KEY`.
- RLS scopes workspace records to `auth.uid()`, including for admins.
- Admin reports expose only specifically approved profile metadata and
  aggregates; they do not reveal other users' email content.
