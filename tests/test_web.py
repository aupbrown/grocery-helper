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
    assert "Total at the register" in r.text                # grand total in the list sheet


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


def test_login_page_offers_plan_first_path():
    r = client.get("/login")
    assert r.status_code == 200
    assert "Welcome back" in r.text
    assert "Plan a week first" in r.text            # guest path stays primary
    assert "create an account when you save" in r.text


def _stub_accounts(monkeypatch, saved):
    monkeypatch.setattr(main.storage, "create_user", lambda e, h: 7)
    monkeypatch.setattr(main.storage, "get_user",
                        lambda uid: {"id": 7, "email": "sam@school.edu"})
    monkeypatch.setattr(main.storage, "save_plan",
                        lambda uid, rec: saved.update(uid=uid, rec=rec) or 99)
    monkeypatch.setattr(main.storage, "save_kitchen_profile", lambda uid, prof: None)
    monkeypatch.setattr(main.storage, "list_pantry", lambda uid: [])
    monkeypatch.setattr(main.storage, "add_pantry_item", lambda uid, item: 1)
    monkeypatch.setattr(main.storage, "delete_pantry_item", lambda iid, uid: None)
    monkeypatch.setattr(main.storage, "list_plans", lambda uid: [])


def test_register_from_save_sheet_saves_job_plan(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1500),
                 MealIngredient(ingredient_id="rice_white", grams=1500)])])
    c = _generate_plan(monkeypatch, fake)
    saved = {}
    _stub_accounts(monkeypatch, saved)
    r = c.post("/register", data={"email": "sam@school.edu", "password": "secret123",
                                  "plan_kind": "budget"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plans/99"     # straight to the saved week
    assert saved["uid"] == 7
    assert saved["rec"]["weekly_budget"] == 40
    assert saved["rec"]["snapshot"]["meals"]        # full plan snapshot persisted


def test_register_error_reopens_save_sheet(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)])])
    c = _generate_plan(monkeypatch, fake)
    r = c.post("/register", data={"email": "sam@school.edu", "password": "short",
                                  "plan_kind": "budget"})
    assert r.status_code == 200
    assert "at least 8 characters" in r.text        # inline error
    assert "data-open-on-load" in r.text            # sheet reopens
    assert 'value="sam@school.edu"' in r.text       # input never cleared


def _login(c, monkeypatch):
    monkeypatch.setattr(main.storage, "get_user_by_email",
                        lambda e: {"id": 7, "email": "sam@school.edu", "password_hash": "h"})
    monkeypatch.setattr(main.auth, "verify_password", lambda p, h: True)
    monkeypatch.setattr(main.storage, "get_user",
                        lambda uid: {"id": 7, "email": "sam@school.edu"})
    c.post("/login", data={"email": "sam@school.edu", "password": "whatever1"})


def test_account_renders_saved_weeks_with_badges(monkeypatch):
    import datetime
    c = TestClient(main.app)
    _login(c, monkeypatch)
    now = datetime.datetime(2026, 7, 1)
    monkeypatch.setattr(main.storage, "list_plans", lambda uid: [
        {"id": 1, "created_at": now, "goal": "maintain", "weekly_budget": 40,
         "estimated_total_cost": 37.86, "protein_target": 136, "calorie_target": 2200,
         "status": "active", "validation_status": "valid", "week_start_date": None},
        {"id": 2, "created_at": now, "goal": "maintain", "weekly_budget": 40,
         "estimated_total_cost": 41.02, "protein_target": 145, "calorie_target": 2200,
         "status": "active", "validation_status": "invalid", "week_start_date": None},
    ])
    monkeypatch.setattr(main.storage, "list_pantry", lambda uid: [
        {"id": 1, "item_name": "White rice", "normalized_item_key": "rice_white",
         "quantity": None, "unit": None}])
    r = c.get("/account")
    assert r.status_code == 200
    assert "Welcome back, Sam" in r.text
    assert "✓ ready" in r.text and "needs fixes" in r.text
    assert "Plan a new week" in r.text
    assert "1 item" in r.text                      # pantry line


def test_account_empty_states(monkeypatch):
    c = TestClient(main.app)
    _login(c, monkeypatch)
    monkeypatch.setattr(main.storage, "list_plans", lambda uid: [])
    monkeypatch.setattr(main.storage, "list_pantry", lambda uid: [])
    r = c.get("/account")
    assert "No weeks saved yet" in r.text
    assert "Nothing on your shelf" in r.text
    assert "Plan my first week" in r.text


def test_logged_in_one_tap_save(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)])])
    c = _generate_plan(monkeypatch, fake)
    _login(c, monkeypatch)
    saved = {}
    monkeypatch.setattr(main.storage, "save_plan",
                        lambda uid, rec: saved.update(uid=uid) or 55)
    r = c.post("/plans/save", data={"plan_kind": "protein"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plans/55"
    assert saved["uid"] == 7


def _saved_rec(validation_status="valid", owned=(), kitchen=None):
    from app.models import KitchenProfile, PlanInputs
    import datetime
    inputs = PlanInputs(
        weekly_budget=40, goal=Goal.maintain, bodyweight_lb=170, activity_level="light",
        max_cook_minutes=120, target_calories=2200, target_protein=145,
        target_carbs=250, target_fat=70, kitchen=kitchen or KitchenProfile(),
        owned_ingredient_ids=list(owned))
    base = GeneratedPlan(meals=[
        Meal(name="Overnight oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak the oats.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)]),
        Meal(name="Seared chicken", slot="dinner", cook_time_minutes=25, servings=7,
             instructions="1. Sear the chicken.", equipment_required=["stove"],
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1400),
                          MealIngredient(ingredient_id="rice_white", grams=1400)])])
    from app.catalog import catalog_by_id as _cbi
    computed, validation = jobs.finalize(base, inputs, _cbi(main.CATALOG), 40)
    return {
        "id": 42, "created_at": datetime.datetime(2026, 6, 24), "goal": "maintain",
        "weekly_budget": 40, "estimated_total_cost": computed.total_cost,
        "validation_status": validation_status, "validation_notes": {"errors": [], "warnings": []},
        "inputs": inputs.model_dump(mode="json"),
        "snapshot": computed.model_dump(mode="json"),
    }


def test_saved_plan_ready_view(monkeypatch):
    c = TestClient(main.app)
    _login(c, monkeypatch)
    monkeypatch.setattr(main.storage, "get_plan", lambda pid, uid: _saved_rec())
    monkeypatch.setattr(main.storage, "list_pantry", lambda uid: [])
    r = c.get("/plans/42")
    assert r.status_code == 200
    assert "Week of Jun 24" in r.text
    assert "✓ ready" in r.text
    assert "Fix it for me" not in r.text
    assert "Overnight oats" in r.text and "meal-card" in r.text


def test_saved_plan_repair_callout_when_pantry_changed(monkeypatch):
    c = TestClient(main.app)
    _login(c, monkeypatch)
    # The plan counted on owned oats, but the pantry is now empty.
    monkeypatch.setattr(main.storage, "get_plan",
                        lambda pid, uid: _saved_rec(owned=["oats"]))
    monkeypatch.setattr(main.storage, "list_pantry", lambda uid: [])
    r = c.get("/plans/42")
    assert "This week needs" in r.text             # callout present (count varies with snacks)
    assert "Fix it for me" in r.text
    assert "is-warn" in r.text                     # problem meal highlighted
    assert "is-dim" in r.text                      # valid meal dimmed
    assert 'action="/plans/42/repair"' in r.text


def test_repair_endpoint_updates_snapshot(monkeypatch):
    c = TestClient(main.app)
    _login(c, monkeypatch)
    monkeypatch.setattr(main.storage, "get_plan",
                        lambda pid, uid: _saved_rec(owned=["oats"]))
    monkeypatch.setattr(main.storage, "list_pantry", lambda uid: [])
    fresh = Meal(name="Yogurt granola cup", slot="breakfast", cook_time_minutes=0,
                 servings=7, instructions="1. Layer.", ingredients=[
                     MealIngredient(ingredient_id="greek_yogurt", grams=1400)])
    monkeypatch.setattr(jobs, "generate_one", lambda *a, **k: fresh)
    updated = {}
    monkeypatch.setattr(main.storage, "update_plan_snapshot",
                        lambda pid, uid, **kw: updated.update(pid=pid, **kw))
    r = c.post("/plans/42/repair", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/plans/42"
    names = {m["name"] for m in updated["snapshot"]["meals"]}
    assert "Yogurt granola cup" in names           # broken meal replaced
    assert "Seared chicken" in names               # valid meal kept
    assert updated["inputs"]["owned_ingredient_ids"] == []   # ownership synced to pantry


def test_feedback_dialog_shows_once_after_first_save(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)])])
    c = _generate_plan(monkeypatch, fake)
    saved = {}
    _stub_accounts(monkeypatch, saved)
    monkeypatch.setattr(main.storage, "get_plan", lambda pid, uid: _saved_rec())
    r = c.post("/register", data={"email": "sam@school.edu", "password": "secret123",
                                  "plan_kind": "budget"}, follow_redirects=True)
    # First page after the first save: dialog present with labelled ratings.
    assert "data-feedback" in r.text
    assert r.text.count("data-rating") == 4
    assert 'aria-label="Loved it"' in r.text
    assert "newsletter_optin" in r.text
    # Second load: never again.
    r2 = c.get("/plans/99")
    assert "data-feedback" not in r2.text


def test_feedback_post_saves_and_sets_flag(monkeypatch):
    c = TestClient(main.app)
    _login(c, monkeypatch)
    calls = {}
    monkeypatch.setattr(main.storage, "save_feedback",
                        lambda fb: calls.update(fb=fb) or 1)
    monkeypatch.setattr(main.storage, "set_feedback_done",
                        lambda uid: calls.update(done=uid))
    monkeypatch.setattr(main.storage, "log_event",
                        lambda *a, **k: calls.update(event=(a, k)))
    r = c.post("/feedback", data={"rating": "4", "comment": "great",
                                  "newsletter_optin": "on"})
    assert r.status_code == 204
    assert calls["fb"]["rating"] == 4
    assert calls["fb"]["newsletter_optin"] is True
    assert calls["fb"]["email"] == "sam@school.edu"   # from the logged-in account
    assert calls["done"] == 7
    assert calls["event"][0][0] == "email_captured"   # opt-in feeds the funnel metric


def test_feedback_dismiss_records_dismissal(monkeypatch):
    c = TestClient(main.app)
    calls = {}
    monkeypatch.setattr(main.storage, "save_feedback",
                        lambda fb: calls.update(fb=fb) or 1)
    r = c.post("/feedback", data={"dismissed": "1"})
    assert r.status_code == 204
    assert calls["fb"]["dismissed"] is True
    assert calls["fb"]["rating"] is None


def test_shopping_list_shows_grand_total(monkeypatch):
    import re
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Season the chicken with salt.\n2. Cook the rice and serve.",
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=1400),
                          MealIngredient(ingredient_id="chicken_breast", grams=1050)])])
    c = _generate_plan(monkeypatch, fake)
    r = c.get("/plan")
    assert r.status_code == 200
    assert "Total at the register" in r.text
    assert "not counted against your weekly budget" in r.text   # buy-once framing intact
    block = r.text.split('aria-label="Cost summary"')[1].split("</dialog>")[0]
    week, pantry, total = (float(x) for x in re.findall(r"\$(\d+\.\d\d)", block)[:3])
    assert pantry > 0                                # the seasoning pass stocked staples
    assert abs(week + pantry - total) < 0.011        # grand total = groceries + staples
    # Budget math unchanged: the summary band still reports the weekly figure only.
    band = r.text.split('class="summary-band', 2)[1].split("</section>")[0]
    assert f"${week:.2f}" in band
    assert f"${total:.2f}" not in band


def test_meal_detail_quantities_are_human_readable(monkeypatch):
    # 720 g of banana = 6 bananas across 7 servings — must never read "0.9 bananas" —
    # and the salt/oil the seasoning pass adds must collapse into one staples row.
    fake = GeneratedPlan(meals=[
        Meal(name="Banana oats", slot="breakfast", cook_time_minutes=5, servings=7,
             instructions="1. Season with salt and cook the oats.\n2. Slice the banana.",
             ingredients=[MealIngredient(ingredient_id="oats", grams=700),
                          MealIngredient(ingredient_id="banana", grams=720)])])
    # Pin macro scaling so the 6-banana batch (0.857/serving) reaches the page unchanged.
    monkeypatch.setattr(jobs, "adjust_to_targets", lambda gen, *a, **k: gen)
    c = _generate_plan(monkeypatch, fake)
    r = c.get("/plan")
    assert r.status_code == 200
    assert "about 1 banana" in r.text            # 6/7 per serving, in plain words
    assert "0.9 banana" not in r.text
    assert "0.1 clove" not in r.text and "0.1 onion" not in r.text
    assert "pantry staples" in r.text            # seasonings grouped, not micro-dosed
