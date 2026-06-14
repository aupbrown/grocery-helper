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
    monkeypatch.setattr(
        main, "generate",
        lambda inputs, catalog, client=None, priority="budget", **kw: fake,
    )
    r = client.post("/plan", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120",
        "dietary_pattern": "none", "target_calories": "2700", "target_protein": "180",
    })
    assert r.status_code == 200
    assert "Chicken &amp; rice" in r.text or "Chicken & rice" in r.text
    assert "Grocery list" in r.text
    assert "Fits your budget" in r.text   # both plans rendered
    assert "Hits your protein" in r.text


def test_post_signup_thanks():
    r = client.post("/signup", data={"email": "student@example.com"})
    assert r.status_code == 200
    assert "Thanks" in r.text
