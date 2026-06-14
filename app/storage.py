import os

import psycopg


def _connect():
    """Open a connection to the Postgres database named by DATABASE_URL.

    Used as a context manager so the transaction commits (or rolls back on error)
    and the connection closes on exit. One connection per call is fine at this
    traffic level; switch to psycopg_pool if request volume grows.
    """
    return psycopg.connect(os.environ["DATABASE_URL"])


def init_db() -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id BIGSERIAL PRIMARY KEY, "
            "type TEXT NOT NULL, "
            "email TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )


def log_event(event_type: str, email: str | None = None) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO events (type, email) VALUES (%s, %s)",
            (event_type, email),
        )


def get_stats() -> dict:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM events WHERE type = 'plan_generated'")
        plans = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM events WHERE type = 'email_captured'")
        emails = cur.fetchone()[0]
    conversion = round(emails / plans, 3) if plans else 0.0
    return {"plans_generated": plans, "emails_captured": emails, "conversion": conversion}
