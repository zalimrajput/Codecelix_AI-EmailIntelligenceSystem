"""Quick connectivity check: reads DATABASE_URL from .env, tests connection,
checks PostgreSQL version, pgvector extension, and existing tables.
Never prints credentials."""
import os

from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in backend/.env")
    print("Env keys present:", [k for k in os.environ if "DATABASE" in k.upper() or "POSTGRES" in k.upper()])
    raise SystemExit(1)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("SELECT version();")
print("Connected:", cur.fetchone()[0].split(",")[0])

cur.execute("SELECT current_database(), current_user;")
db, usr = cur.fetchone()
print("Database:", db, "| connected as:", usr)

# pgvector availability
cur.execute("SELECT name FROM pg_available_extensions WHERE name = 'vector';")
print("pgvector available:", bool(cur.fetchone()))

# extensions already installed
cur.execute("SELECT extname FROM pg_extension ORDER BY extname;")
print("Installed extensions:", [r[0] for r in cur.fetchall()])

# existing tables
cur.execute("""
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public' ORDER BY tablename;
""")
tables = [r[0] for r in cur.fetchall()]
print("Existing tables (%d):" % len(tables), tables)

cur.close()
conn.close()
