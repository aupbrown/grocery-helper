from fastapi.testclient import TestClient

import app.main as main
from app import jobs
from app.models import Goal, GeneratedPlan, Meal, MealIngredient

client = TestClient(main.app)


def _fill_wizard(c, **step1):
    data = {"weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
            "max_cook_minutes": "120", "activity_level": "light"}
    data.update({k: str(v) for k, v in step1.items()})
    c.post("/plan/new?step=1", data=data)
    c.post("/plan/new?step=2", data={"kitchen_preset": "apartment",
                                     "rendered_preset": "apartment"})


def _sync_jobs(monkeypatch, fake_plan):
    """Make generation deterministic and inline for flow tests."""
    monkeypatch.setattr(jobs, "generate", lambda *a, **k: fake_plan)
    monkeypatch.setattr(jobs, "_spawn", lambda job: jobs._run(job))
    monkeypatch.setattr(jobs.storage, "log_event", lambda *a, **k: None)


def test_generate_flow_reaches_done_status(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1500),
                 MealIngredient(ingredient_id="rice_white", grams=1500)])])
    _sync_jobs(monkeypatch, fake)
    c = TestClient(main.app)
    _fill_wizard(c)
    r = c.post("/plan/generate", data={
        "target_calories": "2700", "target_protein": "180",
        "target_carbs": "326", "target_fat": "75"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan/generating"
    s = c.get("/plan/status").json()
    assert s["state"] == "done"
    assert "over_budget" in s
    r2 = c.get("/plan/generating", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/plan"    # no-JS server-side redirect when done


def test_generating_page_renders_while_running(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)])])
    monkeypatch.setattr(jobs, "generate", lambda *a, **k: fake)
    monkeypatch.setattr(jobs, "_spawn", lambda job: None)     # stay queued
    c = TestClient(main.app)
    _fill_wizard(c)
    c.post("/plan/generate", data={"target_calories": "2700", "target_protein": "180",
                                   "target_carbs": "326", "target_fat": "75"})
    r = c.get("/plan/generating")
    assert r.status_code == 200
    assert "Building your week" in r.text
    assert 'role="status"' in r.text
    assert 'http-equiv="refresh"' in r.text     # no-JS fallback keeps polling
    assert c.get("/plan/status").json()["state"] == "queued"


def test_generation_failure_lands_on_failed_page(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(jobs, "generate", _boom)
    monkeypatch.setattr(jobs, "_spawn", lambda job: jobs._run(job))
    c = TestClient(main.app)
    _fill_wizard(c)
    c.post("/plan/generate", data={"target_calories": "2700", "target_protein": "180",
                                   "target_carbs": "326", "target_fat": "75"})
    r = c.get("/plan/generating", follow_redirects=False)
    assert r.headers["location"] == "/plan/failed"
    r2 = c.get("/plan/failed")
    assert "That one stumped us" in r2.text
    assert 'action="/plan/generate"' in r2.text  # Try again re-POSTs with saved targets
    assert 'value="180' in r2.text               # inputs preserved from the session


def test_home_is_coach_landing():
    r = client.get("/")
    assert r.status_code == 200
    assert "Eat well on a college budget" in r.text
    assert "/plan/new?step=1" in r.text
    assert "no account needed" in r.text


def test_wizard_step1_roundtrip():
    c = TestClient(main.app)
    r = c.post("/plan/new?step=1", data={
        "weekly_budget": "45", "goal": "cut", "bodyweight_lb": "160",
        "max_cook_minutes": "90", "activity_level": "active"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan/new?step=2"
    r2 = c.get("/plan/new?step=2")
    assert r2.status_code == 200
    assert "Your kitchen" in r2.text
    # Back-nav re-renders step 1 with the session's values, not defaults.
    r_back = c.get("/plan/new?step=1")
    assert 'value="45"' in r_back.text


def test_wizard_step2_stores_kitchen_and_owned():
    c = TestClient(main.app)
    c.post("/plan/new?step=1", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "170",
        "max_cook_minutes": "120", "activity_level": "light"})
    r = c.post("/plan/new?step=2", data={
        "kitchen_preset": "dorm", "rendered_preset": "apartment",
        "owned_ids": "rice_white", "owned_qty_rice_white": "4",
        "owned_unit_rice_white": "cup"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan/new?step=3"
    # Re-render shows the chip checked and the amount row prefilled.
    r2 = c.get("/plan/new?step=2")
    assert 'value="rice_white" checked' in r2.text
    assert 'value="4' in r2.text


def test_settings_and_pantry_redirect_to_wizard():
    for path in ("/settings", "/pantry"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/plan/new?step=2&return=account"


def test_wizard_step3_shows_computed_targets():
    c = TestClient(main.app)
    c.post("/plan/new?step=1", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "max_cook_minutes": "120", "activity_level": "light"})
    r = c.get("/plan/new?step=3")
    assert r.status_code == 200
    assert "2700" in r.text                      # computed calorie default
    assert 'name="target_calories"' in r.text    # editable stat inputs
    assert 'name="target_protein"' in r.text
    assert 'action="/plan/generate"' in r.text   # CTA posts to the async generator
    assert "180 lb" in r.text                    # "based on" summary line


def test_wizard_step3_without_basics_redirects_guest_to_step1():
    c = TestClient(main.app)
    r = c.get("/plan/new?step=3", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan/new?step=1"


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


