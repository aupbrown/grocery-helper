from fastapi.testclient import TestClient

import app.main as main
from app.models import Goal, GeneratedPlan, Meal, MealIngredient

client = TestClient(main.app)


def test_get_form():
    r = client.get("/")
    assert r.status_code == 200
    assert "Weekly budget" in r.text


def test_post_targets_computes_defaults():
    r = client.post("/targets", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120",
        "dietary_pattern": "none",
    })
    assert r.status_code == 200
    assert "2700" in r.text  # computed calorie default
    assert "180" in r.text   # computed protein default


def test_post_plan_renders_results(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", cook_time_minutes=20, servings=2,
             instructions="Cook.", ingredients=[
                 MealIngredient(ingredient_id="rice_white", grams=200),
                 MealIngredient(ingredient_id="chicken_breast", grams=150)]),
    ])
    # Stub the LLM (called twice — budget + protein) and Postgres logging so the
    # web test stays offline.
    monkeypatch.setattr(
        main, "generate",
        lambda inputs, catalog, client=None, priority="budget", **kw: fake,
    )
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    r = client.post("/plan", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120",
        "dietary_pattern": "none", "target_calories": "2700", "target_protein": "180",
    })
    assert r.status_code == 200
    assert "Chicken &amp; rice" in r.text or "Chicken & rice" in r.text
    assert "This week&#39;s groceries" in r.text or "This week's groceries" in r.text
    assert "Fits your budget" in r.text   # both plans rendered
    assert "Hits your protein" in r.text
    assert "Per serving:" in r.text       # per-serving meal total labeled
    assert "g/serving" in r.text          # per-ingredient per-serving amounts
    assert "Protein target:" in r.text    # met/not-met reporting
    assert "Calorie target:" in r.text
    assert "not met" in r.text            # the stub plan is far below 2700/180
    assert "Daily protein shake" in r.text  # protein topped up via a separate daily drink


def test_post_signup_thanks(monkeypatch):
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    r = client.post("/signup", data={"email": "student@example.com"})
    assert r.status_code == 200
    assert "Thanks" in r.text
