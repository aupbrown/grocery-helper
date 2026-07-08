from app import jobs, repair
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


def _rec(inputs: PlanInputs, base: GeneratedPlan) -> dict:
    computed, validation = jobs.finalize(base, inputs, BY_ID, inputs.weekly_budget)
    return {
        "inputs": inputs.model_dump(mode="json"),
        "snapshot": computed.model_dump(mode="json"),
        "weekly_budget": inputs.weekly_budget,
        "validation_status": validation.severity,
    }


def _stove_and_oats() -> GeneratedPlan:
    return GeneratedPlan(meals=[
        Meal(name="Overnight oats", slot="breakfast", cook_time_minutes=0, servings=7,
             instructions="1. Soak the oats overnight.", ingredients=[
                 MealIngredient(ingredient_id="oats", grams=700),
                 MealIngredient(ingredient_id="peanut_butter", grams=210)]),
        Meal(name="Seared chicken and rice", slot="dinner", cook_time_minutes=25, servings=7,
             instructions="1. Sear the chicken in a skillet.", equipment_required=["stove"],
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1400),
                          MealIngredient(ingredient_id="rice_white", grams=1400)])])


def test_meal_issues_flags_missing_equipment():
    # The kitchen lost its stove after the plan was saved.
    inputs = _inputs(kitchen=KitchenProfile(stove=False, oven=False))
    rec = _rec(inputs, _stove_and_oats())
    issues = repair.meal_issues(rec, set(), BY_ID)
    assert any("stove" in reason for reason in issues.values())
    assert "Overnight oats" not in issues            # the no-cook meal is fine


def test_meal_issues_flags_lost_pantry_item():
    inputs = _inputs(owned_ingredient_ids=["oats"])
    rec = _rec(inputs, _stove_and_oats())
    issues = repair.meal_issues(rec, pantry_keys=set(), by_id=BY_ID)   # oats gone
    assert "Overnight oats" in issues
    assert "left your pantry" in issues["Overnight oats"]
    # Still in the pantry -> no issue.
    assert repair.meal_issues(rec, {"oats"}, BY_ID) == {}


def test_repair_regenerates_only_flagged_meals(monkeypatch):
    inputs = _inputs(kitchen=KitchenProfile(stove=False, oven=False))
    rec = _rec(inputs, _stove_and_oats())
    fresh = Meal(name="Microwave lentil bowl", slot="dinner", cook_time_minutes=10,
                 servings=7, instructions="1. Microwave the lentils.",
                 equipment_required=["microwave"],
                 ingredients=[MealIngredient(ingredient_id="lentils", grams=1400),
                              MealIngredient(ingredient_id="rice_white", grams=1400)])
    calls = []
    monkeypatch.setattr(jobs, "generate_one",
                        lambda *a, **k: calls.append(k.get("avoid_name")) or fresh)
    computed, validation, new_inputs = repair.repair(rec, CATALOG, set())
    names = {m.name for m in computed.meals}
    assert "Microwave lentil bowl" in names          # flagged meal replaced
    assert "Overnight oats" in names                 # valid meal kept pinned
    assert "Seared chicken and rice" not in names
    assert calls == ["Seared chicken and rice"]      # exactly one regeneration
