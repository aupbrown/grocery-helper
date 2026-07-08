import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

# Storage now talks to Postgres, so this is an integration test. It runs only when
# DATABASE_URL is set (point it at your Neon dev database), and cleans up after
# itself so it leaves the database as it found it.
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set — skipping Postgres integration test",
)


def test_log_event_and_get_stats_roundtrip():
    import psycopg

    from app import storage

    storage.init_db()

    # Snapshot the high-water id so we can delete exactly the rows this test adds.
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM events")
        before_max_id = cur.fetchone()[0]

    before = storage.get_stats()
    storage.log_event("plan_generated")
    storage.log_event("email_captured", email="integration-test@example.com")
    after = storage.get_stats()

    try:
        assert after["plans_generated"] == before["plans_generated"] + 1
        assert after["emails_captured"] == before["emails_captured"] + 1
        assert after["conversion"] == round(
            after["emails_captured"] / after["plans_generated"], 3
        )
    finally:
        # Leave the dev database as we found it.
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE id > %s", (before_max_id,))


def test_feedback_roundtrip():
    import psycopg

    from app import storage

    storage.init_db()
    fb_id = storage.save_feedback({
        "user_id": None, "rating": 4, "comment": "integration test",
        "newsletter_optin": False, "email": None, "dismissed": False})
    try:
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT rating, comment FROM feedback WHERE id = %s", (fb_id,))
            assert cur.fetchone() == (4, "integration test")
    finally:
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM feedback WHERE id = %s", (fb_id,))
