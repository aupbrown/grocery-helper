from app.models import Goal, Ingredient, KitchenProfile, Meal, MealIngredient, GeneratedPlan, PlanInputs
from app.catalog import catalog_by_id
from app.plan import package_cost
from app.validate import validate_plan

# Round-number packages keep the whole-package math easy to verify by hand.
RICE = Ingredient(id="rice_white", name="White rice", category="grain", tags=["vegan"],
                  allergens=[], kcal_per_100g=130, protein_per_100g=2.7, carbs_per_100g=28,
                  fat_per_100g=0.3, package_price=1.00, package_size_g=1000,
                  package_label="1 kg bag")
CHICKEN = Ingredient(id="chicken_breast", name="Chicken breast", category="protein", tags=[],
                     allergens=[], kcal_per_100g=165, protein_per_100g=31, carbs_per_100g=0,
                     fat_per_100g=3.6, package_price=10.00, package_size_g=1000,
                     package_label="1 kg pack")
BY_ID = catalog_by_id([RICE, CHICKEN])

# chicken 1400g + rice 1400g for the whole week. Daily (÷7): 590 kcal, 67.4 g protein,
# 56 g carbs, 7.8 g fat. Cost = 2 pkg chicken ($20) + 2 pkg rice ($2) = $22.
PLAN = GeneratedPlan(meals=[
    Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=30, servings=7,
         instructions="1. Cook.", ingredients=[
             MealIngredient(ingredient_id="chicken_breast", grams=1400),
             MealIngredient(ingredient_id="rice_white", grams=1400)])])


def _inputs(*, budget=30, protein=60, owned=None, goal=Goal.maintain):
    # Targets set near the plan's actual daily macros so the bands are met by default.
    return PlanInputs(weekly_budget=budget, goal=goal, bodyweight_lb=170, max_cook_minutes=120,
                      owned_ingredient_ids=owned or [], target_calories=590,
                      target_protein=protein, target_carbs=56, target_fat=8)


def test_within_budget_and_protein_met_is_valid():
    v = validate_plan(PLAN, BY_ID, _inputs())
    assert v.is_valid and v.severity == "valid"
    assert v.within_budget and v.total_cost == 22.0
    assert v.budget_remaining == 8.0 and v.over_budget_by == 0.0
    assert not v.errors and not v.warnings


def test_over_budget_is_invalid_with_fixes():
    v = validate_plan(PLAN, BY_ID, _inputs(budget=10))
    assert not v.is_valid and v.severity == "invalid"
    assert v.over_budget_by == 12.0 and v.budget_remaining == -12.0
    assert any("over your $10 budget" in e for e in v.errors)
    assert "Make cheaper" in v.suggested_fixes


def test_protein_short_is_invalid():
    v = validate_plan(PLAN, BY_ID, _inputs(protein=200))
    assert not v.is_valid and v.severity == "invalid"
    assert any("133 g/day short" in e for e in v.errors)   # 200 - 67.4 = 132.6 -> 133
    assert "Add a protein top-up" in v.suggested_fixes


def test_cost_per_gram_protein_and_per_day():
    v = validate_plan(PLAN, BY_ID, _inputs())
    assert v.cost_per_g_protein == 0.047   # 22 / (67.4*7 = 471.8)
    assert v.cost_per_day == 3.14          # 22 / 7
    assert v.cost_per_meal == 3.14         # 22 / (1 meal * 7 days)


def test_pantry_savings_excludes_owned_from_cost():
    v = validate_plan(PLAN, BY_ID, _inputs(owned=["rice_white"]))
    assert v.total_cost == 20.0                       # rice no longer purchased
    assert v.pantry_savings == package_cost(1400, RICE)[1] == 2.0


def test_grocery_total_matches_item_sum():
    v = validate_plan(PLAN, BY_ID, _inputs())
    expected = package_cost(1400, CHICKEN)[1] + package_cost(1400, RICE)[1]
    assert v.total_cost == expected


DORM = KitchenProfile(microwave=True, stove=False, oven=False)


def test_equipment_violation_is_invalid():
    plan = GeneratedPlan(meals=[
        Meal(name="Stir fry", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Stir fry on the stove.", equipment_required=["stove"],
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1400),
                          MealIngredient(ingredient_id="rice_white", grams=1400)])])
    v = validate_plan(plan, BY_ID, _inputs(), kitchen=DORM)
    assert not v.is_valid and v.severity == "invalid"
    assert any("stove" in e for e in v.errors)


def test_time_over_weekly_limit_warns_but_stays_valid():
    plan = GeneratedPlan(meals=[
        Meal(name="Slow roast", slot="dinner", cook_time_minutes=200, servings=7,
             instructions="1. Roast.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1400),
                 MealIngredient(ingredient_id="rice_white", grams=1400)])])
    v = validate_plan(plan, BY_ID, _inputs())   # default max_cook_minutes = 120
    assert v.is_valid and v.severity == "warning"
    assert v.total_prep_minutes == 200
    assert any("over your 120-min limit" in w for w in v.warnings)


def test_batch_sessions_reported():
    v = validate_plan(PLAN, BY_ID, _inputs())
    assert v.total_prep_minutes == 30
    assert v.prep_sessions == 1 and v.largest_session_minutes == 30


def test_non_positive_quantity_is_an_error():
    bad = GeneratedPlan(meals=[
        Meal(name="Broken", slot="dinner", cook_time_minutes=10, servings=7, instructions="1.",
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=0)])])
    v = validate_plan(bad, BY_ID, _inputs())
    assert not v.is_valid
    assert any("non-positive" in e for e in v.errors)
