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
             instructions="1. Season the chicken with salt.\n2. Cook the rice and serve.",
             ingredients=[
                 MealIngredient(ingredient_id="rice_white", grams=200),
                 MealIngredient(ingredient_id="chicken_breast", grams=150),
                 MealIngredient(ingredient_id="banana", grams=240)]),
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
        "target_carbs": "326", "target_fat": "75",
    })
    assert r.status_code == 200
    assert "Chicken &amp; rice" in r.text or "Chicken & rice" in r.text
    assert "This week&#39;s groceries" in r.text or "This week's groceries" in r.text
    assert "Fits your budget" in r.text   # both plans rendered
    assert "Hits your protein" in r.text
    assert "Per serving:" in r.text       # per-serving meal total labeled
    assert "/serving" in r.text           # per-ingredient per-serving amounts
    assert "cup" in r.text                # quantities shown in household measures, not grams
    assert "Protein target:" in r.text    # met/not-met reporting
    assert "Calorie target:" in r.text
    assert "not met" in r.text            # the stub plan is far below 2700/180
    assert "Daily protein shake" in r.text  # protein topped up via a separate daily drink
    assert "Carb target:" in r.text       # all four macros now reported
    assert "Fat target:" in r.text
    assert "weekly meal plan" in r.text.lower()  # framed as a week of meals
    assert "one a day" in r.text          # slot batches stated as one serving/day
    assert "<ol" in r.text                # recipe rendered as numbered steps
    assert "banana" in r.text             # countable shown as units, not raw grams
    assert "Salt" in r.text               # seasoning mentioned in steps -> listed & priced
    assert "Budget estimated at" in r.text or "over your" in r.text  # Budget Guarantee summary
    assert "/ day" in r.text and "/ meal" in r.text                  # budget-guarantee metrics


def test_post_plan_flags_stove_meal_for_microwave_only_kitchen(monkeypatch):
    from app.models import KitchenProfile
    stove_meal = GeneratedPlan(meals=[
        Meal(name="Stovetop stir fry", slot="dinner", cook_time_minutes=25, servings=7,
             instructions="1. Sear chicken in a skillet on the stove.",
             equipment_required=["stove"], ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1400),
                 MealIngredient(ingredient_id="rice_white", grams=1400)])])
    monkeypatch.setattr(main, "generate", lambda *a, **k: stove_meal)
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    dorm = KitchenProfile(microwave=True, stove=False, oven=False).model_dump_json()
    r = client.post("/plan", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120", "dietary_pattern": "none",
        "target_calories": "2700", "target_protein": "180", "target_carbs": "326",
        "target_fat": "75", "kitchen_json": dorm,
    })
    assert r.status_code == 200
    assert "which your kitchen lacks" in r.text   # validator flagged the stove violation
    assert "stove" in r.text                       # equipment named on the meal chip
    assert "session" in r.text                     # batch-cook prep transparency rendered


def test_post_regenerate_swaps_one_slot_and_keeps_macros(monkeypatch):
    base = GeneratedPlan(meals=[
        Meal(name="Old dinner", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1500),
                 MealIngredient(ingredient_id="rice_white", grams=1500)])])
    fresh = Meal(name="Fresh stir fry", slot="dinner", cook_time_minutes=15, servings=7,
                 instructions="1. Stir fry.", ingredients=[
                     MealIngredient(ingredient_id="chicken_breast", grams=1600),
                     MealIngredient(ingredient_id="rice_white", grams=1600)])
    monkeypatch.setattr(main, "generate_one",
                        lambda *a, **k: fresh)
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    bj = base.model_dump_json()
    r = client.post("/regenerate", data={
        "plan_kind": "protein", "slot": "dinner",
        "budget_base_json": bj, "protein_base_json": bj,
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120", "dietary_pattern": "none",
        "target_calories": "2200", "target_protein": "110",
        "target_carbs": "250", "target_fat": "70",
    })
    assert r.status_code == 200
    assert "Fresh stir fry" in r.text                       # the slot was regenerated
    protein_block = r.text.split("planblock")[-1]           # Plan B (protein-first)
    assert "Fresh stir fry" in protein_block
    assert "Protein target: <span class=\"ok\">met" in protein_block   # macros still hold


def test_plan_route_survives_generation_failure(monkeypatch):
    # If the Gemini client itself can't be created, generate() must fall back to templates
    # rather than 500 the request.
    import app.generator as gen

    def _boom(*a, **k):
        raise RuntimeError("no API key")

    monkeypatch.setattr(gen.genai, "Client", _boom)
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    r = client.post("/plan", data={
        "weekly_budget": "50", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "180", "dietary_pattern": "none",
        "target_calories": "2400", "target_protein": "150", "target_carbs": "250",
        "target_fat": "70",
    })
    assert r.status_code == 200
    assert "weekly meal plan" in r.text.lower()   # a real (fallback) plan rendered


def test_post_signup_thanks(monkeypatch):
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    r = client.post("/signup", data={"email": "student@example.com"})
    assert r.status_code == 200
    assert "Thanks" in r.text
