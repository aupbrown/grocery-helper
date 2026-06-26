from pathlib import Path

from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.catalog import catalog_by_id, load_catalog
from app.plan import (
    macros_for_meal, macros_for_grams, compute_plan, weekly_grocery_cost,
    pantry_cost, daily_protein_grams, daily_calories, macro_policy,
    adjust_to_targets, package_cost,
)

# 1 kg packages keep the whole-package math easy to read.
RICE = Ingredient(id="rice_white", name="White rice", category="grain",
                  tags=["vegan"], allergens=[], kcal_per_100g=130,
                  protein_per_100g=2.7, carbs_per_100g=28, fat_per_100g=0.3,
                  package_price=1.00, package_size_g=1000, package_label="1 kg bag")
CHICKEN = Ingredient(id="chicken_breast", name="Chicken breast", category="protein",
                     tags=[], allergens=[], kcal_per_100g=165, protein_per_100g=31,
                     carbs_per_100g=0, fat_per_100g=3.6,
                     package_price=10.00, package_size_g=1000, package_label="1 kg pack")
SALT = Ingredient(id="salt", name="Salt", category="seasoning", tags=["vegan"],
                  allergens=[], kcal_per_100g=0, protein_per_100g=0, carbs_per_100g=0,
                  fat_per_100g=0, package_price=0.50, package_size_g=500,
                  package_label="500g canister", pantry_staple=True)
OIL = Ingredient(id="olive_oil", name="Olive oil", category="fat", tags=["vegan"],
                 allergens=[], kcal_per_100g=884, protein_per_100g=0, carbs_per_100g=0,
                 fat_per_100g=100, package_price=10.00, package_size_g=1000,
                 package_label="1 L bottle", pantry_staple=True)
# Whey: high-protein lever for the correction pass, non-pantry so its cost counts.
WHEY = Ingredient(id="whey_protein", name="Whey protein", category="protein",
                  tags=["vegetarian"], allergens=["dairy"], kcal_per_100g=380,
                  protein_per_100g=80, carbs_per_100g=8, fat_per_100g=6,
                  package_price=24.20, package_size_g=1000, package_label="1 kg tub")
BY_ID = catalog_by_id([RICE, CHICKEN, SALT, OIL, WHEY])

MEAL = Meal(name="Chicken & rice", cook_time_minutes=20, servings=2,
            instructions="Cook rice, grill chicken.",
            ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                         MealIngredient(ingredient_id="chicken_breast", grams=150),
                         MealIngredient(ingredient_id="salt", grams=5)])


def _inputs(owned=None):
    return PlanInputs(weekly_budget=30, goal=Goal.maintain, bodyweight_lb=180,
                      max_cook_minutes=120, owned_ingredient_ids=owned or [],
                      target_calories=2700, target_protein=180)


def test_macros_for_meal():
    m = macros_for_meal(MEAL, BY_ID)
    assert m.calories == 507.5   # 130*2 + 165*1.5 (salt adds nothing)
    assert m.protein == 51.9     # 2.7*2 + 31*1.5
    assert m.carbs == 56.0       # 28*2
    assert m.fat == 6.0          # 0.3*2 + 3.6*1.5


def test_macros_for_grams():
    m = macros_for_grams(CHICKEN, 150)
    assert m.calories == 247.5   # 165 * 1.5
    assert m.protein == 46.5     # 31 * 1.5
    assert m.carbs == 0.0


def test_package_cost_rounds_up_to_whole_packages():
    assert package_cost(150, CHICKEN) == (1, 10.00)    # any amount -> a whole pack
    assert package_cost(1000, CHICKEN) == (1, 10.00)   # exact fill, still one pack
    assert package_cost(1200, CHICKEN) == (2, 20.00)   # spills into a second pack


def test_weekly_grocery_cost_charges_whole_packages():
    plan = GeneratedPlan(meals=[MEAL])
    # rice 200g -> 1 bag $1.00, chicken 150g -> 1 pack $10.00; salt is pantry (excluded)
    assert weekly_grocery_cost(plan, BY_ID) == 11.00
    assert weekly_grocery_cost(plan, BY_ID, owned_ids=["rice_white"]) == 10.00


def test_pantry_cost_separate_and_excludes_owned():
    plan = GeneratedPlan(meals=[MEAL])
    assert pantry_cost(plan, BY_ID) == 0.50            # salt: one canister
    assert pantry_cost(plan, BY_ID, owned_ids=["salt"]) == 0.00


def test_daily_protein_grams():
    plan = GeneratedPlan(meals=[MEAL])
    assert daily_protein_grams(plan, BY_ID) == 7.4   # 51.9g protein / 7 days


def test_daily_calories():
    plan = GeneratedPlan(meals=[MEAL])
    assert daily_calories(plan, BY_ID) == 72.5   # 507.5 kcal / 7 days


def test_macro_policy_cut_has_strict_ceiling_at_target():
    p = macro_policy(Goal.cut, 2000, 150)
    assert p.cal_min == 1900.0      # 0.95 * 2000
    assert p.cal_max == 2000.0      # ceiling == target (strict, no overshoot)
    assert p.protein_min == 150.0
    assert p.method == "topup"


def test_macro_policy_maintain_is_ten_percent_band():
    p = macro_policy(Goal.maintain, 2000, 150)
    assert p.cal_min == 1800.0      # 0.90 * 2000
    assert p.cal_max == 2200.0      # 1.10 * 2000
    assert p.protein_min == 150.0
    assert p.method == "topup"


def test_macro_policy_bulk_floors_and_allows_overshoot():
    p = macro_policy(Goal.bulk, 3000, 180)
    assert p.cal_min == 3000.0      # floor == target
    assert p.cal_max == 3600.0      # 1.20 * 3000 (overshoot within reason)
    assert p.protein_min == 180.0
    assert p.method == "scale"


def test_compute_plan_splits_weekly_and_pantry():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())
    assert cp.total_cost == 11.00       # weekly groceries only
    assert cp.pantry_total == 0.50      # one-time pantry stock-up
    assert cp.within_budget is True     # 11.00 <= 30 budget
    assert {g.ingredient_id for g in cp.grocery_list} == {"rice_white", "chicken_breast"}
    assert {g.ingredient_id for g in cp.pantry_list} == {"salt"}
    assert cp.daily_macros.calories == 72.5   # 507.5 / 7
    assert cp.meals[0].ingredients[0].name == "White rice"


def test_compute_plan_per_serving_macros():
    # MEAL serves 2: per-serving = whole-recipe / 2.
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())
    meal = cp.meals[0]
    assert meal.macros_per_serving.calories == 253.8   # 507.5 / 2
    assert meal.macros_per_serving.carbs == 28.0        # 56.0 / 2

    rice = meal.ingredients[0]
    assert rice.grams == 200.0                          # whole-recipe total preserved
    assert rice.grams_per_serving == 100.0              # 200 / 2
    assert rice.macros_per_serving.calories == 130.0    # 260 / 2
    assert rice.macros_per_serving.carbs == 28.0        # 56 / 2

    chicken = meal.ingredients[1]
    assert chicken.grams_per_serving == 75.0            # 150 / 2
    assert chicken.macros_per_serving.protein == 23.2   # 46.5 / 2


def test_per_serving_guards_zero_servings():
    # The LLM supplies `servings`; a stray 0 must not divide-by-zero (treat as 1).
    meal = Meal(name="Whole batch", cook_time_minutes=10, servings=0,
                instructions="...", ingredients=[
                    MealIngredient(ingredient_id="rice_white", grams=200)])
    cp = compute_plan(GeneratedPlan(meals=[meal]), BY_ID, _inputs())
    m = cp.meals[0]
    assert m.macros_per_serving.calories == 260.0          # 200g rice, treated as 1 serving
    assert m.ingredients[0].grams_per_serving == 200.0


# --- Deterministic goal-aware correction (adjust_to_targets) ---

def _goal_inputs(goal, cal, prot, budget=100000):
    return PlanInputs(weekly_budget=budget, goal=goal, bodyweight_lb=180,
                      max_cook_minutes=120, target_calories=cal, target_protein=prot)


# Weekly base amounts deliberately below target so the correction has to act.
_BULK_BASE = GeneratedPlan(meals=[Meal(name="Base", cook_time_minutes=20, servings=7,
    instructions="...", ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1400),
                                     MealIngredient(ingredient_id="rice_white", grams=700)])])
_CUT_BASE = GeneratedPlan(meals=[Meal(name="Base", cook_time_minutes=20, servings=7,
    instructions="...", ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=700),
                                     MealIngredient(ingredient_id="rice_white", grams=1000)])])
_MAINTAIN_BASE = GeneratedPlan(meals=[Meal(name="Base", cook_time_minutes=20, servings=7,
    instructions="...", ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=800),
                                     MealIngredient(ingredient_id="rice_white", grams=1200)])])


def test_adjust_bulk_scales_up_to_calorie_floor_and_protein_floor():
    inputs = _goal_inputs(Goal.bulk, 500, 80)
    p = macro_policy(Goal.bulk, 500, 80)
    adj = adjust_to_targets(_BULK_BASE, BY_ID, inputs, budget_cap=None)
    assert daily_calories(adj, BY_ID) >= p.cal_min - 1        # floor reached (scaled up)
    assert daily_calories(adj, BY_ID) <= p.cal_max + 1        # within reason (overshoot capped)
    assert daily_protein_grams(adj, BY_ID) >= p.protein_min - 0.5


def test_adjust_cut_meets_protein_without_exceeding_calorie_ceiling():
    inputs = _goal_inputs(Goal.cut, 400, 50)
    p = macro_policy(Goal.cut, 400, 50)
    adj = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=None)
    assert daily_calories(adj, BY_ID) <= p.cal_max + 1        # strict ceiling at target
    assert daily_calories(adj, BY_ID) >= p.cal_min - 1
    assert daily_protein_grams(adj, BY_ID) >= p.protein_min - 0.5


def test_adjust_maintain_lands_both_in_band():
    inputs = _goal_inputs(Goal.maintain, 500, 60)
    p = macro_policy(Goal.maintain, 500, 60)
    adj = adjust_to_targets(_MAINTAIN_BASE, BY_ID, inputs, budget_cap=None)
    assert p.cal_min - 1 <= daily_calories(adj, BY_ID) <= p.cal_max + 1
    assert daily_protein_grams(adj, BY_ID) >= p.protein_min - 0.5


def test_adjust_adds_whey_shake_meal():
    inputs = _goal_inputs(Goal.cut, 400, 50)
    adj = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=None)
    assert any(mi.ingredient_id == "whey_protein"
               for meal in adj.meals for mi in meal.ingredients)


def test_adjust_plan_a_respects_budget_cap_with_residual():
    inputs = _goal_inputs(Goal.cut, 400, 50, budget=15)
    capped = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=15)
    uncapped = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=None)
    # The cap blocks the (whole-package) whey top-up, so protein falls short...
    assert weekly_grocery_cost(capped, BY_ID) <= 15
    assert daily_protein_grams(capped, BY_ID) < 50
    # ...whereas without the cap the protein floor is met (proves the cap is the blocker).
    assert daily_protein_grams(uncapped, BY_ID) >= 49.5


def test_compute_plan_reports_targets_met_when_in_band():
    inputs = _goal_inputs(Goal.maintain, 500, 60)
    adj = adjust_to_targets(_MAINTAIN_BASE, BY_ID, inputs, budget_cap=None)
    cp = compute_plan(adj, BY_ID, inputs)
    assert cp.calories_met is True
    assert cp.protein_met is True
    assert cp.target_note == ""


def test_compute_plan_reports_gap_when_targets_missed():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())  # tiny meal vs 2700/180
    assert cp.calories_met is False
    assert cp.protein_met is False
    assert "kcal" in cp.target_note and "protein" in cp.target_note


_REAL = catalog_by_id(load_catalog(
    Path(__file__).resolve().parent.parent / "data" / "ingredients.json"))


def test_correction_meets_protein_floor_real_catalog_each_goal():
    # End-to-end against the real catalog: Plan B (uncapped) meets the protein floor for
    # every goal, from a base that is well short of target.
    base = GeneratedPlan(meals=[Meal(name="Base", cook_time_minutes=20, servings=7,
        instructions="...", ingredients=[
            MealIngredient(ingredient_id="chicken_breast", grams=1400),
            MealIngredient(ingredient_id="rice_white", grams=1400)])])
    for goal in (Goal.bulk, Goal.cut, Goal.maintain):
        inputs = PlanInputs(weekly_budget=100000, goal=goal, bodyweight_lb=180,
                            max_cook_minutes=120, target_calories=2200, target_protein=100)
        adj = adjust_to_targets(base, _REAL, inputs, budget_cap=None)
        cp = compute_plan(adj, _REAL, inputs)
        assert cp.protein_met, f"{goal.value}: protein floor not met"


def test_grocery_item_carries_package_fields():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())
    chicken = next(g for g in cp.grocery_list if g.ingredient_id == "chicken_breast")
    assert chicken.packages == 1
    assert chicken.package_label == "1 kg pack"
    assert chicken.cost == 10.00
    assert chicken.per_meal_cost is None   # only pantry items get a per-meal figure


def test_pantry_item_shows_per_meal_cost():
    # 100g oil split across two meals; price_per_100g = $1.00 -> $1.00 used / 2 meals
    m1 = Meal(name="A", cook_time_minutes=10, servings=2, instructions="...",
              ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=500),
                           MealIngredient(ingredient_id="olive_oil", grams=50)])
    m2 = Meal(name="B", cook_time_minutes=10, servings=2, instructions="...",
              ingredients=[MealIngredient(ingredient_id="rice_white", grams=500),
                           MealIngredient(ingredient_id="olive_oil", grams=50)])
    cp = compute_plan(GeneratedPlan(meals=[m1, m2]), BY_ID, _inputs())
    oil = next(g for g in cp.pantry_list if g.ingredient_id == "olive_oil")
    assert oil.packages == 1 and oil.cost == 10.00   # one bottle covers the week
    assert oil.per_meal_cost == 0.50                 # pennies per meal, not a full bottle


def test_owned_ingredients_excluded_from_grocery_list():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs(owned=["rice_white"]))
    ids = {g.ingredient_id for g in cp.grocery_list}
    assert "rice_white" not in ids
    assert cp.total_cost == 10.00   # only the chicken pack remains
