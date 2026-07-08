import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class EmailTaken(Exception):
    """Raised when registering an email that already has an account."""


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
        cur.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id BIGSERIAL PRIMARY KEY, "
            "email TEXT UNIQUE NOT NULL, "
            "password_hash TEXT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS weekly_plans ("
            "id BIGSERIAL PRIMARY KEY, "
            "user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "week_start_date DATE, "
            "goal TEXT, "
            "calorie_target INTEGER, protein_target INTEGER, "
            "carb_target INTEGER, fat_target INTEGER, "
            "weekly_budget NUMERIC, estimated_total_cost NUMERIC, "
            "estimated_total_prep_minutes INTEGER, "
            "status TEXT NOT NULL DEFAULT 'active', "
            "validation_status TEXT, validation_notes JSONB, "
            "inputs JSONB NOT NULL, snapshot JSONB NOT NULL, "
            "progress JSONB NOT NULL DEFAULT '{}'::jsonb, "
            "generated_by TEXT NOT NULL DEFAULT 'initial_generation', "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS kitchen_profiles ("
            "user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, "
            "profile JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS pantry_items ("
            "id BIGSERIAL PRIMARY KEY, "
            "user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
            "item_name TEXT NOT NULL, normalized_item_key TEXT, "
            "quantity NUMERIC, unit TEXT, expiration_date DATE, "
            "priority TEXT NOT NULL DEFAULT 'normal', "
            "source TEXT NOT NULL DEFAULT 'manually_added', "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS feedback ("
            "id BIGSERIAL PRIMARY KEY, "
            "user_id BIGINT REFERENCES users(id) ON DELETE SET NULL, "
            "rating INTEGER, comment TEXT, "
            "newsletter_optin BOOLEAN NOT NULL DEFAULT FALSE, "
            "email TEXT, dismissed BOOLEAN NOT NULL DEFAULT FALSE, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # The one-time feedback prompt flag (A9); ADD COLUMN IF NOT EXISTS keeps
        # existing databases upgradeable without a migration tool.
        cur.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "feedback_done BOOLEAN NOT NULL DEFAULT FALSE"
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


# --- Users (guest-first accounts) ---

def create_user(email: str, password_hash: str) -> int:
    """Insert a new user and return its id; raise EmailTaken if the email already exists."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                        (email.strip().lower(), password_hash))
            return cur.fetchone()[0]
    except psycopg.errors.UniqueViolation:
        raise EmailTaken(email)


def get_user(user_id: int) -> dict | None:
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, email, created_at, feedback_done FROM users WHERE id = %s",
                    (user_id,))
        return cur.fetchone()


def set_feedback_done(user_id: int) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET feedback_done = TRUE WHERE id = %s", (user_id,))


def save_feedback(fb: dict) -> int:
    """Persist one A9 feedback submission (or dismissal)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback (user_id, rating, comment, newsletter_optin, email, "
            "dismissed) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (fb.get("user_id"), fb.get("rating"), fb.get("comment"),
             bool(fb.get("newsletter_optin")), fb.get("email"), bool(fb.get("dismissed"))))
        return cur.fetchone()[0]


def get_user_by_email(email: str) -> dict | None:
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, email, password_hash FROM users WHERE email = %s",
                    (email.strip().lower(),))
        return cur.fetchone()


def delete_user(user_id: int) -> None:
    """Delete a user and (via ON DELETE CASCADE) all their data. Used by tests for cleanup."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


# --- Saved weekly plans (per user) ---

def save_plan(user_id: int, r: dict) -> int:
    """Persist one saved week and return its id. JSON columns take plain dicts."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO weekly_plans (user_id, week_start_date, goal, calorie_target, "
            "protein_target, carb_target, fat_target, weekly_budget, estimated_total_cost, "
            "estimated_total_prep_minutes, status, validation_status, validation_notes, inputs, "
            "snapshot, progress, generated_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (user_id, r.get("week_start_date"), r.get("goal"), r.get("calorie_target"),
             r.get("protein_target"), r.get("carb_target"), r.get("fat_target"),
             r.get("weekly_budget"), r.get("estimated_total_cost"),
             r.get("estimated_total_prep_minutes"), r.get("status", "active"),
             r.get("validation_status"), Jsonb(r.get("validation_notes") or {}),
             Jsonb(r["inputs"]), Jsonb(r["snapshot"]), Jsonb(r.get("progress") or {}),
             r.get("generated_by", "initial_generation")))
        return cur.fetchone()[0]


def get_plan(plan_id: int, user_id: int) -> dict | None:
    """A saved plan, scoped to its owner — returns None for another user's plan."""
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM weekly_plans WHERE id = %s AND user_id = %s",
                    (plan_id, user_id))
        return cur.fetchone()


def list_plans(user_id: int) -> list[dict]:
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, week_start_date, goal, weekly_budget, estimated_total_cost, "
            "calorie_target, protein_target, status, validation_status, created_at "
            "FROM weekly_plans WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        return cur.fetchall()


def update_plan_snapshot(plan_id: int, user_id: int, *, snapshot: dict, inputs: dict,
                         validation_status: str, validation_notes: dict,
                         estimated_total_cost) -> None:
    """Replace a saved plan's snapshot after a repair, scoped to its owner."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE weekly_plans SET snapshot = %s, inputs = %s, validation_status = %s, "
            "validation_notes = %s, estimated_total_cost = %s, generated_by = 'repair', "
            "updated_at = now() WHERE id = %s AND user_id = %s",
            (Jsonb(snapshot), Jsonb(inputs), validation_status, Jsonb(validation_notes),
             estimated_total_cost, plan_id, user_id))


def update_plan_progress(plan_id: int, user_id: int, progress: dict) -> None:
    """Replace a plan's progress JSON (meal check-offs etc.), scoped to its owner."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE weekly_plans SET progress = %s, updated_at = now() "
                    "WHERE id = %s AND user_id = %s", (Jsonb(progress), plan_id, user_id))


# --- Kitchen profile (one per user, stored as JSON) ---

def get_kitchen_profile(user_id: int) -> dict | None:
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT profile FROM kitchen_profiles WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        return row["profile"] if row else None


def save_kitchen_profile(user_id: int, profile: dict) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kitchen_profiles (user_id, profile) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET profile = EXCLUDED.profile, updated_at = now()",
            (user_id, Jsonb(profile)))


# --- Pantry (persistent "items I already have") ---

def add_pantry_item(user_id: int, item: dict) -> int:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pantry_items (user_id, item_name, normalized_item_key, quantity, unit, "
            "expiration_date, priority, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (user_id, item["item_name"], item.get("normalized_item_key"), item.get("quantity"),
             item.get("unit"), item.get("expiration_date"), item.get("priority", "normal"),
             item.get("source", "manually_added")))
        return cur.fetchone()[0]


def list_pantry(user_id: int) -> list[dict]:
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, item_name, normalized_item_key, quantity, unit, expiration_date, "
            "priority, source FROM pantry_items WHERE user_id = %s "
            "ORDER BY (priority = 'use_first') DESC, item_name", (user_id,))
        return cur.fetchall()


def delete_pantry_item(item_id: int, user_id: int) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM pantry_items WHERE id = %s AND user_id = %s", (item_id, user_id))
