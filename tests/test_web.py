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
    # Calm results layout: at-a-glance hero, tabbed plans, detail collapsed by default.
    assert "Your week, sorted" in r.text                       # confident hero headline
    assert 'role="tablist"' in r.text and r.text.count('role="tab"') >= 2  # Plan A / Plan B tabs
    assert "Plan A" in r.text and "Plan B" in r.text
    assert "meter-fill" in r.text                              # budget tally meter (signature)
    assert "Protein" in r.text and "Calories" in r.text        # macro bars
    assert "Carbs" in r.text and "Fat" in r.text               # all four macros still reported
    assert "Under budget" in r.text or "over budget" in r.text  # budget verdict
    assert "/day" in r.text and "/meal" in r.text              # per-day / per-meal cost
    assert 'details class="meal"' in r.text                    # meals collapsed by default
    assert "/serving" in r.text                                # ingredient amounts per serving
    assert "<ol" in r.text                                     # recipe steps preserved
    assert 'details class="shop"' in r.text and "Shopping list" in r.text  # one collapsed list
    assert "Daily protein shake" in r.text                     # whey top-up still added
    assert "banana" in r.text and "Salt" in r.text             # ingredients still listed in detail
    assert "fat per serving" not in r.text                     # per-ingredient macro breakdown cut


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
    protein_block = r.text.split('id="panel-protein"')[-1]  # Plan B (protein-first) panel
    assert "Fresh stir fry" in protein_block                # regenerated meal lands in Plan B
    assert "✓" in protein_block                             # protein target met -> check shown


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
    assert "Your week, sorted" in r.text          # a real (fallback) plan rendered


def test_targets_converts_owned_amounts_to_grams():
    r = client.post("/targets", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120", "dietary_pattern": "none",
        "owned_ingredient_ids": "chicken_breast",
        "owned_qty_chicken_breast": "2", "owned_unit_chicken_breast": "lb",
    })
    assert r.status_code == 200
    # 2 lb -> ~907.2 g, carried forward to /plan as the single owned_grams_json hidden field.
    assert "owned_grams_json" in r.text
    assert "chicken_breast" in r.text and "907.2" in r.text


def test_plan_partial_ownership_shows_buy_the_rest_note(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken bowl", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook the chicken and rice.",
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=2000),
                          MealIngredient(ingredient_id="rice_white", grams=1400)])])
    monkeypatch.setattr(main, "generate", lambda *a, **k: fake)
    # Skip macro scaling so the owned amount stays a clear partial (stable assertion).
    monkeypatch.setattr(main, "adjust_to_targets", lambda gen, *a, **k: gen)
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    r = client.post("/plan", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120", "dietary_pattern": "none",
        "target_calories": "2700", "target_protein": "180", "target_carbs": "326",
        "target_fat": "75", "owned_grams_json": '{"chicken_breast": 500}',
    })
    assert r.status_code == 200
    assert "buying only the rest" in r.text      # partial-ownership note rendered
    assert "owned_grams_json" in r.text          # threaded into the regenerate/register forms


def test_post_signup_thanks(monkeypatch):
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    r = client.post("/signup", data={"email": "student@example.com"})
    assert r.status_code == 200
    assert "Thanks" in r.text
