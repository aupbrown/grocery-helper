"""Postgres integration tests for accounts + saved plans. Run only when DATABASE_URL is set
(point it at a Neon dev database); each test cleans up the rows it creates."""
import os
from uuid import uuid4

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skipping Postgres integration test")

from app import auth, storage   # noqa: E402


def _make_user() -> tuple[int, str]:
    storage.init_db()
    email = f"test-{uuid4()}@example.com"
    return storage.create_user(email, auth.hash_password("password123")), email


def _record() -> dict:
    return {"goal": "maintain", "calorie_target": 2000, "protein_target": 120,
            "carb_target": 200, "fat_target": 60, "weekly_budget": 40,
            "estimated_total_cost": 30.0, "estimated_total_prep_minutes": 60,
            "validation_status": "valid", "inputs": {"goal": "maintain"},
            "snapshot": {"meals": [], "total_cost": 30.0}, "progress": {}}


def test_user_crud_and_email_taken():
    uid, email = _make_user()
    try:
        assert storage.get_user(uid)["email"] == email.lower()
        assert storage.get_user_by_email(email)["id"] == uid
        with pytest.raises(storage.EmailTaken):
            storage.create_user(email, auth.hash_password("password123"))
    finally:
        storage.delete_user(uid)


def test_saved_plans_are_scoped_to_their_owner():
    uid_a, _ = _make_user()
    uid_b, _ = _make_user()
    try:
        pid = storage.save_plan(uid_a, _record())
        assert storage.get_plan(pid, uid_a) is not None        # owner can read
        assert storage.get_plan(pid, uid_b) is None            # another user cannot
        assert len(storage.list_plans(uid_a)) == 1
        assert storage.list_plans(uid_b) == []
    finally:
        storage.delete_user(uid_a)
        storage.delete_user(uid_b)


def test_kitchen_profile_upsert_and_get():
    uid, _ = _make_user()
    try:
        assert storage.get_kitchen_profile(uid) is None
        storage.save_kitchen_profile(uid, {"microwave": True, "stove": False})
        assert storage.get_kitchen_profile(uid)["stove"] is False
        storage.save_kitchen_profile(uid, {"microwave": True, "stove": True})   # upsert
        assert storage.get_kitchen_profile(uid)["stove"] is True
    finally:
        storage.delete_user(uid)


def test_pantry_add_list_delete_are_scoped():
    uid_a, _ = _make_user()
    uid_b, _ = _make_user()
    try:
        pid = storage.add_pantry_item(uid_a, {"item_name": "Rice",
            "normalized_item_key": "rice_white", "priority": "use_first"})
        items = storage.list_pantry(uid_a)
        assert len(items) == 1 and items[0]["normalized_item_key"] == "rice_white"
        assert storage.list_pantry(uid_b) == []                # scoping
        storage.delete_pantry_item(pid, uid_b)                 # wrong owner: no-op
        assert len(storage.list_pantry(uid_a)) == 1
        storage.delete_pantry_item(pid, uid_a)
        assert storage.list_pantry(uid_a) == []
    finally:
        storage.delete_user(uid_a)
        storage.delete_user(uid_b)


def test_logged_in_form_prefills_kitchen_and_pantry():
    from fastapi.testclient import TestClient
    import app.main as main

    storage.init_db()
    c = TestClient(main.app)
    email = f"prefill-{uuid4()}@example.com"
    c.post("/register", data={"email": email, "password": "password123"})
    c.post("/settings", data={"microwave": "true"})           # stove/oven left off
    c.post("/pantry", data={"add_ids": "rice_white"})
    r = c.get("/")
    user = storage.get_user_by_email(email)
    try:
        assert 'value="rice_white" checked' in r.text          # pantry item pre-checked
        assert 'name="stove" value="true" checked' not in r.text  # saved kitchen has stove off
    finally:
        if user:
            storage.delete_user(user["id"])


def test_register_via_web_saves_the_plan(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.models import GeneratedPlan, Meal, MealIngredient

    storage.init_db()
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", cook_time_minutes=20, servings=7, instructions="1. Cook.",
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=1400),
                          MealIngredient(ingredient_id="chicken_breast", grams=1200)])])
    monkeypatch.setattr(main, "generate", lambda *a, **k: fake)
    c = TestClient(main.app)
    email = f"web-{uuid4()}@example.com"
    r = c.post("/register", data={
        "email": email, "password": "password123", "plan_kind": "budget",
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120", "dietary_pattern": "none",
        "target_calories": "2700", "target_protein": "180", "target_carbs": "326",
        "target_fat": "75", "budget_base_json": fake.model_dump_json(),
        "protein_base_json": fake.model_dump_json(), "kitchen_json": ""}, follow_redirects=True)
    user = storage.get_user_by_email(email)
    try:
        assert r.status_code == 200 and "Saved weeks" in r.text   # landed on /account
        assert user is not None and len(storage.list_plans(user["id"])) == 1
    finally:
        if user:
            storage.delete_user(user["id"])
