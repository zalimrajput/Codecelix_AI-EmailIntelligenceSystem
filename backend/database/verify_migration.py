"""Verify the RAG / RBAC / data-isolation migration.

Structure checks: pgvector, the 8 new tables (28 application tables), RLS enabled on every
table, policies on every table, role/permission seeds, vector index, grants.

Functional checks: creates two throwaway users in auth.users (profiles and
default roles are created by the existing signup triggers), then impersonates
them via request.jwt.claims to prove that:
  * a user only sees their own emails/chunks/profile/audit rows,
  * shared tables (roles, permissions, categories) are readable,
  * users cannot write other users' rows, self-promote, or grant themselves ADMIN,
  * an admin cannot read another user's workspace through RLS,
  * guarded admin RPCs return safe profile/account metadata only,
and finally removes all test data.

Usage:
    backend/venv/Scripts/python.exe backend/database/verify_migration.py

Never prints credentials.
"""
import json
import os
import sys
import uuid

from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in backend/.env")
    raise SystemExit(1)

EXPECTED_TABLES = {
    # pre-existing
    "action_items", "ai_analyses", "ai_conversation_messages", "ai_conversations",
    "ai_replies", "ai_usage_logs", "audit_logs", "deadlines", "email_attachments",
    "email_categories", "email_imports", "email_processing_runs", "email_senders",
    "email_threads", "emails", "extracted_information", "notification_preferences",
    "notifications", "profiles", "system_settings",
    # new
    "email_chunks", "embeddings", "rag_retrievals",
    "roles", "permissions", "role_permissions", "user_roles", "gmail_connections",
}

failures = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    line = f"  [{tag}] {name}"
    if detail:
        line += f" -> {detail}"
    print(line)
    if not ok:
        failures.append(name)


def expect_error(name, cur, sql, params=None):
    try:
        cur.execute(sql, params)
    except psycopg2.Error as exc:
        msg = str(exc).strip().splitlines()[0][:120]
        check(name, True, msg)
        return
    check(name, False, "statement unexpectedly succeeded")


def set_jwt(cur, user_id):
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, false)", (str(user_id),))
    claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
    cur.execute("SELECT set_config('request.jwt.claims', %s, false)", (claims,))


def clear_jwt(cur):
    cur.execute("RESET request.jwt.claim.sub")
    cur.execute("RESET request.jwt.claims")


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def main():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("\n== Structure ==")
    check("pgvector extension installed",
          scalar(cur, "SELECT extversion FROM pg_extension WHERE extname='vector'") is not None)

    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    tables = {r[0] for r in cur.fetchall()}
    missing = sorted(EXPECTED_TABLES - tables)
    check("all 28 application tables present", not missing, f"missing: {missing}" if missing
          else f"{len(tables)} tables")

    not_enabled = scalar(cur, "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity")

    cur.execute("SELECT tablename, count(*) FROM pg_policies WHERE schemaname='public' GROUP BY tablename")
    pol = dict(cur.fetchall())
    no_policy = sorted(t for t in EXPECTED_TABLES if pol.get(t, 0) == 0)
    check("every table has at least one RLS policy", not no_policy,
          f"no policy: {no_policy}" if no_policy else f"{sum(pol.values())} policies")
    check("RLS enabled on every table", not_enabled == 0)

    roles = scalar(cur, "SELECT count(*) FROM public.roles")
    check("roles seeded (ADMIN, USER)", roles == 2, f"{roles} roles")
    perms = scalar(cur, "SELECT count(*) FROM public.permissions")
    check("permissions seeded (24)", perms == 24, f"{perms} permissions")
    admin_p = scalar(cur, """SELECT count(*) FROM public.role_permissions rp
                             JOIN public.roles r ON r.id=rp.role_id WHERE r.name='ADMIN'""")
    check("ADMIN has all permissions", admin_p == 24, f"{admin_p}")
    user_p = scalar(cur, """SELECT count(*) FROM public.role_permissions rp
                            JOIN public.roles r ON r.id=rp.role_id WHERE r.name='USER'""")
    check("USER has 13 permissions", user_p == 13, f"{user_p}")

    check("is_admin() exists",
          scalar(cur, "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid "
                      "WHERE n.nspname='public' AND p.proname='is_admin'") == 1)
    check("HNSW vector index on embeddings",
          scalar(cur, "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
                      "AND indexname='idx_embeddings_vector'") == 1)
    check("authenticated has table grants",
          scalar(cur, "SELECT count(*) FROM information_schema.table_privileges "
                      "WHERE table_name='embeddings' AND grantee='authenticated'") > 0)
    check("authenticated can call owner-scoped semantic search",
          scalar(cur, "SELECT has_function_privilege('authenticated', "
                      "'public.search_email_chunks(vector,integer)', 'EXECUTE')"))
    check("authenticated can call guarded admin RPCs",
          all(scalar(cur, "SELECT has_function_privilege('authenticated', %s, 'EXECUTE')",
                     (signature,))
              for signature in (
                  "public.admin_workspace_overview()",
                  "public.admin_user_list(integer)",
                  "public.admin_user_details(uuid)",
                  "public.admin_set_user_active(uuid,boolean)",
                  "public.admin_set_user_roles(uuid,text[])",
                  "public.admin_ai_usage_summary()",
                  "public.admin_recent_audit_logs(integer)",
              )))

    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='profiles'")
    profile_cols = {r[0] for r in cur.fetchall()}
    check("profiles has no role / avatar_url columns (user_roles is the role source)",
          "role" not in profile_cols and "avatar_url" not in profile_cols)
    check("user_role enum dropped",
          scalar(cur, "SELECT count(*) FROM pg_type t "
                      "JOIN pg_namespace n ON t.typnamespace = n.oid "
                      "WHERE n.nspname = 'public' AND t.typname = 'user_role'") == 0)

    # ------------------------------------------------------------------
    print("\n== Functional data-isolation tests ==")
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    email_a = email_b = None
    try:
        for uid, tag in ((a, "a"), (b, "b")):
            cur.execute(
                "INSERT INTO auth.users (id, aud, role, email, encrypted_password, "
                "email_confirmed_at, raw_app_meta_data, raw_user_meta_data, created_at, updated_at) "
                "VALUES (%s, 'authenticated', 'authenticated', %s, 'verify-hash', now(), "
                "'{}', '{}', now(), now())",
                (uid, f"verify-{tag}@example.com"),
            )
        check("test users created (signup triggers ran)",
              scalar(cur, "SELECT count(*) FROM public.profiles WHERE id IN (%s, %s)", (a, b)) == 2)
        check("default USER role auto-assigned",
              scalar(cur, "SELECT count(*) FROM public.user_roles WHERE user_id IN (%s, %s)", (a, b)) == 2)

        cur.execute("INSERT INTO public.emails (user_id, subject, body_text) "
                    "VALUES (%s, 'A subject', 'A body') RETURNING id", (a,))
        email_a = str(cur.fetchone()[0])
        cur.execute("INSERT INTO public.emails (user_id, subject, body_text) "
                    "VALUES (%s, 'B subject', 'B body') RETURNING id", (b,))
        email_b = str(cur.fetchone()[0])

        cur.execute("INSERT INTO public.email_chunks (user_id, email_id, chunk_index, content) "
                    "VALUES (%s, %s, 0, 'A chunk') RETURNING id", (a, email_a))
        chunk_a = str(cur.fetchone()[0])
        vec = "[" + ",".join(["0.001"] * 1536) + "]"
        cur.execute("INSERT INTO public.embeddings (user_id, chunk_id, embedding) "
                    "VALUES (%s, %s, %s::vector)", (a, chunk_a, vec))
        cur.execute("INSERT INTO public.rag_retrievals (user_id, query_text, similarity) "
                    "VALUES (%s, 'verify query', 0.9)", (a,))
        cur.execute("INSERT INTO public.gmail_connections "
                    "(user_id, google_email, encrypted_refresh_token) "
                    "VALUES (%s, 'verify-a@example.com', 'test-ciphertext'), "
                    "(%s, 'verify-b@example.com', 'test-ciphertext')", (a, b))
        cur.execute("INSERT INTO public.audit_logs (user_id, action) VALUES (%s, 'verify_test')", (b,))
        cur.execute("INSERT INTO public.system_settings (setting_key, setting_value, is_secret) "
                    "VALUES ('verify_test_public', 'v', false), ('verify_test_secret', 'v', true)")

        # ---- impersonate user A -------------------------------------------------
        cur.execute("SET ROLE authenticated")
        set_jwt(cur, a)

        check("A sees only own emails",
              scalar(cur, "SELECT count(*) FROM public.emails") == 1)
        check("A sees only own profile",
              scalar(cur, "SELECT count(*) FROM public.profiles") == 1)
        check("A sees own email chunks / embeddings / retrievals",
              scalar(cur, "SELECT count(*) FROM public.email_chunks") == 1
              and scalar(cur, "SELECT count(*) FROM public.embeddings") == 1
              and scalar(cur, "SELECT count(*) FROM public.rag_retrievals") == 1)
        check("A sees only own Gmail connection",
              scalar(cur, "SELECT count(*) FROM public.gmail_connections") == 1)
        check("semantic search is scoped to A's email",
              scalar(cur, "SELECT count(*) FROM public.search_email_chunks(%s::vector, 10)", (vec,)) == 1)
        check("A does not see B's audit log",
              scalar(cur, "SELECT count(*) FROM public.audit_logs") == 0)
        check("A reads shared roles/permissions",
              scalar(cur, "SELECT count(*) FROM public.roles") == 2
              and scalar(cur, "SELECT count(*) FROM public.permissions") == 24
              and scalar(cur, "SELECT count(*) FROM public.role_permissions") == 37)
        check("A reads own role assignment only",
              scalar(cur, "SELECT count(*) FROM public.user_roles") == 1)
        check("A sees non-secret settings but not secret ones",
              scalar(cur, "SELECT count(*) FROM public.system_settings WHERE is_secret") == 0
              and scalar(cur, "SELECT count(*) FROM public.system_settings WHERE NOT is_secret") == 1)
        check("A is not admin", scalar(cur, "SELECT public.is_admin()") is False)

        cur.execute("UPDATE public.emails SET subject='hacked' WHERE id = %s", (email_b,))
        check("A cannot update B's email", cur.rowcount == 0, f"rowcount={cur.rowcount}")

        expect_error("A cannot insert email owned by B", cur,
                     "INSERT INTO public.emails (user_id, subject) VALUES (%s, 'x')", (b,))
        expect_error("A cannot grant themselves ADMIN in user_roles", cur,
                     "INSERT INTO public.user_roles (user_id, role_id) "
                     "SELECT %s, id FROM public.roles WHERE name='ADMIN'", (a,))
        cur.execute("UPDATE public.profiles SET full_name='A full name' WHERE id=%s", (a,))
        check("A can update own profile", cur.rowcount == 1, f"rowcount={cur.rowcount}")

        # ---- promote A via trusted path, then re-test as admin -------------------
        cur.execute("RESET ROLE")
        clear_jwt(cur)
        cur.execute("INSERT INTO public.user_roles (user_id, role_id) "
                    "SELECT %s, id FROM public.roles WHERE name='ADMIN'", (a,))
        check("trusted path grants ADMIN in user_roles (single source of truth)",
              scalar(cur, "SELECT count(*) FROM public.user_roles ur "
                          "JOIN public.roles r ON r.id=ur.role_id "
                          "WHERE ur.user_id=%s AND r.name='ADMIN'", (a,)) == 1)

        cur.execute("SET ROLE authenticated")
        set_jwt(cur, a)
        check("admin A is_admin() = true", scalar(cur, "SELECT public.is_admin()") is True)
        check("admin A still sees only own email/profile/Gmail rows",
              scalar(cur, "SELECT count(*) FROM public.emails") == 1
              and scalar(cur, "SELECT count(*) FROM public.profiles") == 1
              and scalar(cur, "SELECT count(*) FROM public.gmail_connections") == 1)
        check("admin A cannot see or change B's audit log or email",
              scalar(cur, "SELECT count(*) FROM public.audit_logs") == 0
              and scalar(cur, "SELECT count(*) FROM public.emails WHERE id=%s", (email_b,)) == 0)
        cur.execute("UPDATE public.emails SET subject='admin edit' WHERE id=%s", (email_b,))
        check("admin A cannot update B's email", cur.rowcount == 0, f"rowcount={cur.rowcount}")
        check("admin user-list RPC exposes approved metadata",
              scalar(cur, "SELECT count(*) FROM public.admin_user_list(100) "
                          "WHERE id IN (%s, %s)", (a, b)) == 2)
        details = scalar(cur, "SELECT public.admin_user_details(%s)", (b,))
        check("admin details RPC omits email content",
              isinstance(details, dict)
              and "email_activity" in details
              and not any(key in details for key in ("body_text", "short_summary", "generated_reply")))

        cur.execute("RESET ROLE")
        clear_jwt(cur)
    except Exception as exc:
        check("functional tests completed", False, str(exc).strip().splitlines()[0][:160])
    finally:
        # ---- cleanup test data (back as postgres) ----
        try:
            cur.execute("RESET ROLE")
        except psycopg2.Error:
            pass
        for uid in (a, b):
            for table in ("embeddings", "email_chunks", "rag_retrievals", "gmail_connections", "emails",
                          "audit_logs", "user_roles"):
                try:
                    cur.execute(f"DELETE FROM public.{table} WHERE user_id=%s", (uid,))
                except psycopg2.Error:
                    conn.rollback()
        try:
            cur.execute("DELETE FROM public.audit_logs WHERE action='verify_test'")
            cur.execute("DELETE FROM public.system_settings "
                        "WHERE setting_key IN ('verify_test_public', 'verify_test_secret')")
            cur.execute("DELETE FROM auth.users WHERE id IN (%s, %s)", (a, b))
        except psycopg2.Error as exc:
            print(f"  [WARN] cleanup: {str(exc).strip().splitlines()[0][:120]}")

    leftover = scalar(
        cur, "SELECT count(*) FROM public.profiles WHERE id IN (%s, %s)", (a, b)
    )
    check("test data cleaned up", leftover == 0, f"{leftover} profiles remain")

    cur.close()
    conn.close()

    print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
