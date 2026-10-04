# Letterwise AI processing flow

This document describes the implemented path from email ingestion to
AI-enriched inbox data, semantic retrieval, and the Ask/Find assistant. Email
content and derived data remain scoped to the owning user's workspace.

## At a glance

```text
Manual compose ─┐
EML/CSV/JSON ───┼─> persist original email + thread ─> background process_email
Gmail sync ─────┘                                         │
                                                         ├─> split/reuse email chunks
                                                         ├─> generate/store 1536-D vectors
                                                         ├─> AI analysis and structured fields
                                                         ├─> action items, deadlines, alerts
                                                         └─> record processing status/usage

Ask / Find ─> embed query ─> owner-scoped pgvector search ─> answer or email matches
```

## 1. Ingestion and ownership

An email enters through one of three paths:

1. **Manual compose** calls `POST /emails`.
2. **File import** calls `POST /emails/import` for `.eml`, `.csv`, or `.json`.
3. **Gmail sync** calls `POST /integrations/gmail/sync` after the user connects
   their own Google account.

Manual and imported messages go through `save_email`; Gmail messages are
normalized by the integration route. The original email is stored in
PostgreSQL with the authenticated user's `user_id` and associated with a
thread. The API schedules `process_email` as a FastAPI background task.

Each user's Gmail connection belongs to that user. Its refresh token is
encrypted server-side with the Fernet key before storage. User JWTs,
application permission checks, and PostgreSQL RLS guard access to email and
derived rows; an administrator's normal workspace queries remain private to
that administrator.

## 2. Email processing

`process_email` records a row in `email_processing_runs` and proceeds in order:

1. **Chunking.** The email subject and body are normalized into chunks of up
   to 2,400 characters with overlap. Matching existing chunks are reused;
   changed content causes chunks to be rebuilt.
2. **Embedding.** Chunks without a vector for the configured provider's active
   embedding model are embedded and stored in `embeddings`. Vectors have 1,536
   dimensions to match the database column. This stage happens before chat
   analysis, so a later analysis failure does not undo successfully stored
   chunks or vectors.
3. **AI analysis.** The configured chat model is asked for structured JSON:
   intent, priority, sentiment, summaries, reply/action flags, category,
   extracted details and dates, action items, and deadlines. Results are saved
   to `ai_analyses` and related tables.
4. **Follow-ups.** Action items, deadlines, and notification rows are saved
   under the same user's account. AI usage is recorded when the provider
   returns usage details.
5. **Completion/failure.** Processing stages and final status are recorded in
   `email_processing_runs`. Exceptions are logged and the run is marked
   `FAILED` where possible.

Gmail sync skips messages that are already complete with their analysis,
chunks, and active-model embeddings. It queues messages again when processing
failed or required data is missing. Existing chunks and embeddings are reused
when they still match.

## 3. Date and deadline behavior

The processor asks the AI model to extract deadline and meeting dates. It also
has a deterministic fallback for explicit English month-name dates (for
example, “October 9, 2026” or “Oct 9 at 11:00 AM”) in nearby meeting/deadline
context.

- If AI analysis succeeds, its deadlines and fallback-detected explicit dates
  are combined and stored without duplicate dates for that email.
- If AI analysis fails after embedding has completed, explicit fallback dates
  can still be saved; the processing run remains marked failed so it can be
  retried.
- If the embedding request fails before analysis starts, this later date
  fallback is not reached; processing is marked failed and must be retried.
- The deterministic fallback is intentionally conservative: it recognizes
  English month-name formats and context words such as meeting, scheduled,
  deadline, due, interview, call, and event. Other date formats depend on
  successful AI extraction.
- The dashboard's **Upcoming dates** metric counts incomplete deadlines within
  the next seven days. A date outside that window may still be present in the
  deadlines list but not in that metric.

## 4. Semantic search and RAG

Email chunks and embeddings are used by `public.search_email_chunks`, the
pgvector search function. It runs as a security-invoker function and remains
subject to the caller's RLS and `auth.uid()` scope.

For a query, the backend:

1. Embeds the query as a retrieval query using the active provider/model.
2. Calls `search_email_chunks` with the query vector.
3. Keeps only results meeting the current 0.70 similarity floor and ranks
   email matches by similarity.
4. Returns matching email snippets to Find, or provides retrieved context to
   the Assistant.

### Ask mode

`POST /assistant/ask` retrieves the user's closest qualifying email match,
stores the conversation and retrieval metadata in the same user's workspace,
and asks the model to answer using only that retrieved email context and the
recent conversation. The assistant is instructed to say when the retrieved
email does not support an answer. The response includes the email reference(s)
used.

### Find emails mode

`GET /assistant/search?q=...` embeds the search phrase and returns qualifying
email matches/snippets. It does not ask the chat model to compose an answer.

## 5. Smart replies

Reply generation uses the selected user's email as context and creates a
`DRAFT`; approval is a separate step. Sending is another explicit action that
requires the user's connected Gmail account and the Gmail send scope. Reply
state tracks review and delivery to avoid treating a draft or approval as a
sent email.

## 6. Provider configuration and errors

Set provider keys in `backend/.env`, never in frontend configuration:

- `GEMINI_API_KEY` selects Gemini when present.
- Otherwise, `OPENAI_API_KEY` selects OpenAI.
- Optional model settings are `GEMINI_CHAT_MODEL`,
  `GEMINI_EMBEDDING_MODEL`, `OPENAI_CHAT_MODEL`, and
  `OPENAI_EMBEDDING_MODEL`.

Provider rate limits, missing credentials, invalid model output, network
failures, and invalid embeddings can interrupt processing. The API records
processing failures where possible; review backend logs and
`email_processing_runs` when a message has not received its analysis. A Gemini
429 response indicates provider quota/rate limiting, not a missing email or a
workspace isolation issue. Retry after the provider allows requests or resolve
the project's quota.

## 7. Relevant implementation

| Concern | Implementation |
|---|---|
| Manual/import email creation and ownership | `backend/app/routers/emails.py` |
| Gmail OAuth, sync, and send | `backend/app/routers/integrations.py` |
| Chunking, enrichment, dates, and persistence | `backend/app/services/email_pipeline.py` |
| Provider calls, embeddings, and structured analysis | `backend/app/services/ai.py` |
| Ask, Find, and owner-scoped vector retrieval | `backend/app/routers/assistant.py` |
| Analytics and upcoming-date counts | `backend/app/routers/insights.py` |
| RLS, schema, and pgvector function | `backend/database/migrations/` |

