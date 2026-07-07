from app import jobs
from app.catalog import catalog_by_id, load_catalog
from app.models import (
    GeneratedPlan, Goal, KitchenProfile, Meal, MealIngredient, PlanInputs,
)

CATALOG = load_catalog("data/ingredients.json")
BY_ID = catalog_by_id(CATALOG)


def _inputs(**over) -> PlanInputs:
    base = dict(
        weekly_budget=40, goal=Goal.maintain, bodyweight_lb=170, activity_level="light",
        max_cook_minutes=120, target_calories=2200, target_protein=145,
        target_carbs=250, target_fat=70, kitchen=KitchenProfile())
    base.update(over)
    return PlanInputs(**base)


def _fake_plan() -> GeneratedPlan:
    return GeneratedPlan(meals=[
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook the rice.\n2. Sear the chicken.",
             equipment_required=["stove"],
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1500),
                          MealIngredient(ingredient_id="rice_white", grams=1500)])])


def test_run_completes_and_flags_budget(monkeypatch):
    monkeypatch.setattr(jobs, "generate", lambda *a, **k: _fake_plan())
    monkeypatch.setattr(jobs.storage, "log_event", lambda *a, **k: None)
    job = jobs.Job(id="t1", inputs=_inputs(), catalog=CATALOG)
    jobs._run(job)
    assert job.state == "done"
    assert job.budget_base is not None and job.protein_base is not None
    assert isinstance(job.over_budget, bool)


def test_run_failure_sets_failed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(jobs, "generate", _boom)
    job = jobs.Job(id="t2", inputs=_inputs(), catalog=CATALOG)
    jobs._run(job)
    assert job.state == "failed"


def test_swap_slot_keeps_other_meals_pinned(monkeypatch):
    pinned = GeneratedPlan(meals=[
        Meal(name="Oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)]),
        Meal(name="Pricey salmon", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Sear.", ingredients=[
                 MealIngredient(ingredient_id="salmon_fillet", grams=1400)])])
    fresh = Meal(name="Lentil bowls", slot="dinner", cook_time_minutes=25, servings=7,
                 instructions="1. Simmer.", ingredients=[
                     MealIngredient(ingredient_id="lentils", grams=1400)])
    monkeypatch.setattr(jobs, "generate_one", lambda *a, **k: fresh)
    monkeypatch.setattr(jobs.storage, "log_event", lambda *a, **k: None)
    job = jobs.Job(id="t3", inputs=_inputs(), catalog=CATALOG,
                   pinned=pinned, swap_slot="dinner",
                   protein_base=GeneratedPlan(meals=pinned.meals))
    jobs._run(job)
    assert job.state == "done"
    names = {m.name for m in job.budget_base.meals}
    assert "Lentil bowls" in names and "Oats" in names
    assert "Pricey salmon" not in names


def test_priciest_slot_picks_marginally_most_expensive():
    plan = GeneratedPlan(meals=[
        Meal(name="Oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700)]),
        Meal(name="Salmon", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Sear.", ingredients=[
                 MealIngredient(ingredient_id="salmon_fillet", grams=1400)])])
    assert jobs.priciest_slot(plan, BY_ID) == "dinner"


def test_start_registers_and_prunes(monkeypatch):
    ran = []
    monkeypatch.setattr(jobs, "_spawn", lambda job: ran.append(job.id))
    old_id = jobs.start(_inputs(), CATALOG)
    jobs._JOBS[old_id].created_at -= jobs.MAX_AGE_SECONDS + 1
    new_id = jobs.start(_inputs(), CATALOG)
    assert jobs.get(new_id) is not None
    assert jobs.get(old_id) is None          # pruned
    assert ran == [old_id, new_id]
