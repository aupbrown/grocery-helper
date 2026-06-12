import sqlite3
from datetime import datetime, timezone


def _conn(path):
    return sqlite3.connect(str(path))


def init_db(path) -> None:
    conn = _conn(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "type TEXT NOT NULL, "
        "email TEXT, "
        "created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def log_event(path, event_type: str, email: str | None = None) -> None:
    conn = _conn(path)
    conn.execute(
        "INSERT INTO events (type, email, created_at) VALUES (?, ?, ?)",
        (event_type, email, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_stats(path) -> dict:
    conn = _conn(path)
    plans = conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'plan_generated'"
    ).fetchone()[0]
    emails = conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'email_captured'"
    ).fetchone()[0]
    conn.close()
    conversion = round(emails / plans, 3) if plans else 0.0
    return {"plans_generated": plans, "emails_captured": emails, "conversion": conversion}
