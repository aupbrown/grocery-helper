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


def _generate_plan(monkeypatch, fake_plan, step2=None):
    """Drive the wizard + sync generation, returning a client whose session has a done job."""
    _sync_jobs(monkeypatch, fake_plan)
    c = TestClient(main.app)
    _fill_wizard(c)
    if step2:
        c.post("/plan/new?step=2", data=step2)
    c.post("/plan/generate", data={"target_calories": "2700", "target_protein": "180",
                                   "target_carbs": "326", "target_fat": "75"})
    return c


def test_plan_page_renders_variants_and_sheets(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Season the chicken with salt.\n2. Cook the rice and serve.",
             ingredients=[
                 MealIngredient(ingredient_id="rice_white", grams=1400),
                 MealIngredient(ingredient_id="chicken_breast", grams=1050),
                 MealIngredient(ingredient_id="banana", grams=840)]),
    ])
    c = _generate_plan(monkeypatch, fake)
    r = c.get("/plan")
    assert r.status_code == 200
    assert "Your week, sorted" in r.text                    # summary band eyebrow
    assert "summary-band" in r.text
    assert 'role="tablist"' in r.text and r.text.count('role="tab"') == 2
    assert "Budget-first" in r.text and "Protein-first" in r.text
    assert "Chicken &amp; rice" in r.text or "Chicken & rice" in r.text
    assert "meal-card" in r.text
    assert "<dialog" in r.text                              # server-rendered sheets
    assert "Shopping list" in r.text and "Save week" in r.text
    assert "use" in r.text and "<ol" in r.text              # meal detail: ingredients + steps
    assert "Daily protein shake" in r.text                  # whey top-up still added
    assert "Swap a meal" in r.text                          # ghost swap affordance
    assert "min cooking" in r.text                          # stat chips


def test_plan_page_flags_stove_meal_for_dorm_kitchen(monkeypatch):
    stove_meal = GeneratedPlan(meals=[
        Meal(name="Stovetop stir fry", slot="dinner", cook_time_minutes=25, servings=7,
             instructions="1. Sear chicken in a skillet on the stove.",
             equipment_required=["stove"], ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1400),
                 MealIngredient(ingredient_id="rice_white", grams=1400)])])
    c = _generate_plan(monkeypatch, stove_meal,
                       step2={"kitchen_preset": "dorm", "rendered_preset": "dorm"})
    r = c.get("/plan")
    assert r.status_code == 200
    assert "is-warn" in r.text                    # meal card flagged
    assert "stove" in r.text                      # missing equipment named


def test_regenerate_returns_partial_swap_unit(monkeypatch):
    base = GeneratedPlan(meals=[
        Meal(name="Old dinner", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1500),
                 MealIngredient(ingredient_id="rice_white", grams=1500)])])
    fresh = Meal(name="Fresh stir fry", slot="dinner", cook_time_minutes=15, servings=7,
                 instructions="1. Stir fry.", ingredients=[
                     MealIngredient(ingredient_id="chicken_breast", grams=1600),
                     MealIngredient(ingredient_id="rice_white", grams=1600)])
    c = _generate_plan(monkeypatch, base)
    monkeypatch.setattr(jobs, "generate_one", lambda *a, **k: fresh)
    r = c.post("/regenerate", data={"plan_kind": "protein", "slot": "dinner", "idx": "0"},
               headers={"X-Partial": "1"})
    assert r.status_code == 200
    assert "Fresh stir fry" in r.text             # the regenerated card comes back
    assert "swap-unit" in r.text
    assert "<html" not in r.text                  # partial, not a full page
    # Without the header, the same POST falls back to a full-page redirect.
    r2 = c.post("/regenerate", data={"plan_kind": "budget", "slot": "dinner", "idx": "0"},
                follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/plan"


def test_plan_flow_survives_generation_failure(monkeypatch):
    # If the Gemini client itself can't be created, generate() falls back to templates —
    # the job still completes and the plan page renders.
    import app.generator as gen

    def _boom(*a, **k):
        raise RuntimeError("no API key")

    monkeypatch.setattr(gen.genai, "Client", _boom)
    monkeypatch.setattr(jobs, "_spawn", lambda job: jobs._run(job))
    monkeypatch.setattr(jobs.storage, "log_event", lambda *a, **k: None)
    c = TestClient(main.app)
    _fill_wizard(c)
    c.post("/plan/generate", data={"target_calories": "2400", "target_protein": "150",
                                   "target_carbs": "250", "target_fat": "70"})
    assert c.get("/plan/status").json()["state"] == "done"
    r = c.get("/plan")
    assert r.status_code == 200
    assert "Your week, sorted" in r.text


def test_plan_partial_ownership_shows_buy_the_rest_note(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken bowl", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook the chicken and rice.",
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=2000),
                          MealIngredient(ingredient_id="rice_white", grams=1400)])])
    # Skip macro scaling so the owned amount stays a clear partial (stable assertion).
    monkeypatch.setattr(jobs, "adjust_to_targets", lambda gen, *a, **k: gen)
    _sync_jobs(monkeypatch, fake)
    c = TestClient(main.app)
    _fill_wizard(c)
    c.post("/plan/new?step=2", data={
        "kitchen_preset": "apartment", "rendered_preset": "apartment",
        "owned_ids": "chicken_breast", "owned_qty_chicken_breast": "500",
        "owned_unit_chicken_breast": "g"})
    c.post("/plan/generate", data={"target_calories": "2700", "target_protein": "180",
                                   "target_carbs": "326", "target_fat": "75"})
    r = c.get("/plan")
    assert r.status_code == 200
    assert "buying only the rest" in r.text      # partial-ownership note rendered
    assert "you own some" in r.text              # shopping-list badge


def _expensive_plan():
    # ~4.2kg of salmon fillet blows well past a $40 weekly budget.
    return GeneratedPlan(meals=[
        Meal(name="Salmon feast", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Sear the salmon.", ingredients=[
                 MealIngredient(ingredient_id="salmon_fillet", grams=4200),
                 MealIngredient(ingredient_id="rice_white", grams=1400)])])


def test_over_budget_plan_shows_recovery_cards(monkeypatch):
    # Pin adjust_to_targets so the budget pass can't shrink the plan under the cap.
    monkeypatch.setattr(jobs, "adjust_to_targets", lambda gen, *a, **k: gen)
    c = _generate_plan(monkeypatch, _expensive_plan())
    assert c.get("/plan/status").json()["over_budget"] is True
    r = c.get("/plan")
    assert "is-warn" in r.text                       # warn summary band
    assert "over budget" in r.text.lower()
    assert r.text.count("action-card") >= 3          # three recovery options
    assert "Swap the priciest meal" in r.text
    assert "Relax protein to 170g" in r.text         # 180 - 10
    assert "Bump budget to $45" in r.text            # 40 + 5
    assert "keep this plan anyway" in r.text


def test_recover_raise_budget_requeues_with_more_money(monkeypatch):
    monkeypatch.setattr(jobs, "adjust_to_targets", lambda gen, *a, **k: gen)
    c = _generate_plan(monkeypatch, _expensive_plan())
    r = c.post("/plan/recover", data={"action": "raise_budget"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plan/generating"
    r2 = c.get("/plan")
    assert "of $45" in r2.text                       # band reflects the new budget


def test_recover_keep_anyway_hides_recovery(monkeypatch):
    monkeypatch.setattr(jobs, "adjust_to_targets", lambda gen, *a, **k: gen)
    c = _generate_plan(monkeypatch, _expensive_plan())
    r = c.post("/plan/recover", data={"action": "keep"}, follow_redirects=False)
    assert r.headers["location"] == "/plan"
    r2 = c.get("/plan")
    assert "Swap the priciest meal" not in r2.text   # cards gone
    assert "is-warn" in r2.text                      # band still honest about the overage


def test_recover_swap_priciest_pins_other_meals(monkeypatch):
    monkeypatch.setattr(jobs, "adjust_to_targets", lambda gen, *a, **k: gen)
    two_meals = GeneratedPlan(meals=[
        Meal(name="Cheap oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)]),
        Meal(name="Salmon feast", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Sear.", ingredients=[
                 MealIngredient(ingredient_id="salmon_fillet", grams=4200)])])
    fresh = Meal(name="Lentil bowls", slot="dinner", cook_time_minutes=25, servings=7,
                 instructions="1. Simmer.", ingredients=[
                     MealIngredient(ingredient_id="lentils", grams=1400),
                     MealIngredient(ingredient_id="rice_white", grams=1400)])
    c = _generate_plan(monkeypatch, two_meals)
    monkeypatch.setattr(jobs, "generate_one", lambda *a, **k: fresh)
    c.post("/plan/recover", data={"action": "swap_priciest"})
    r = c.get("/plan")
    budget_panel = r.text.split('id="panel-protein"')[0]
    assert "Lentil bowls" in budget_panel            # priciest slot regenerated
    assert "Cheap oats" in budget_panel              # cheap meal kept pinned
    assert "Salmon feast" not in budget_panel
