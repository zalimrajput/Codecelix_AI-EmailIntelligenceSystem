"""Apply SQL migrations from backend/database/migrations in filename order.

Already-applied files are recorded in public.schema_migrations and skipped,
so migrations are never re-executed (and can be edited safely afterwards).

Usage:
    backend/venv/Scripts/python.exe backend/database/run_migrations.py

Each .sql file runs in its own transaction; on error the transaction is
rolled back and the script exits non-zero. Never prints credentials.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in backend/.env")
    raise SystemExit(1)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def main() -> int:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.schema_migrations (
                    filename   text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            cur.execute("ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY")
            cur.execute("SELECT filename FROM public.schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        conn.commit()

        files = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in applied]
        if not files:
            print("No pending migrations (all applied).")
            return 0

        for path in files:
            print(f"Applying {path.name} ...")
            sql = path.read_text(encoding="utf-8")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO public.schema_migrations (filename) VALUES (%s)",
                        (path.name,),
                    )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"  FAILED: {exc}")
                return 1
            print("  OK")

        print(f"Applied {len(files)} migration(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
