from pathlib import Path

from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.catalog import catalog_by_id, load_catalog
from app.plan import (
    macros_for_meal, macros_for_grams, compute_plan, weekly_grocery_cost,
    pantry_cost, daily_protein_grams, daily_calories, macro_policy,
    adjust_to_targets, _add_distribution_snacks, _calorie_snack, _clean,
    snap_units, reconcile_seasonings, format_amount, daily_carbs,
    SCOOP_GRAMS, PROTEIN_DRINK_NAME, package_cost,
)

# 1 kg packages keep the whole-package math easy to read.
RICE = Ingredient(id="rice_white", name="White rice", category="grain",
                  tags=["vegan"], allergens=[], kcal_per_100g=130,
                  protein_per_100g=2.7, carbs_per_100g=28, fat_per_100g=0.3,
                  package_price=1.00, package_size_g=1000, package_label="1 kg bag",
                  grams_per_cup=158)
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
                 package_label="1 L bottle", pantry_staple=True, grams_per_cup=216)
# Whey: high-protein lever for the correction pass, non-pantry so its cost counts.
WHEY = Ingredient(id="whey_protein", name="Whey protein", category="protein",
                  tags=["vegetarian"], allergens=["dairy"], kcal_per_100g=380,
                  protein_per_100g=80, carbs_per_100g=8, fat_per_100g=6,
                  package_price=24.20, package_size_g=1000, package_label="1 kg tub",
                  unit_label="scoop", unit_grams=32)
# Countable item: displayed and snapped to whole units ("1 banana"), not raw grams.
BANANA = Ingredient(id="banana", name="Banana", category="fruit", tags=["vegan"],
                    allergens=[], kcal_per_100g=89, protein_per_100g=1.1, carbs_per_100g=23,
                    fat_per_100g=0.3, package_price=0.79, package_size_g=600,
                    package_label="bunch (~5)", unit_label="banana", unit_grams=120)
# Parfait snack ingredients.
GREEK = Ingredient(id="greek_yogurt", name="Greek yogurt", category="dairy", tags=["vegetarian"],
                   allergens=["dairy"], kcal_per_100g=59, protein_per_100g=10, carbs_per_100g=3.6,
                   fat_per_100g=0.4, package_price=5.48, package_size_g=907, package_label="32 oz tub")
GRANOLA = Ingredient(id="granola", name="Granola", category="grain", tags=["vegetarian", "vegan"],
                     allergens=["gluten"], kcal_per_100g=471, protein_per_100g=10, carbs_per_100g=64,
                     fat_per_100g=20, package_price=3.98, package_size_g=340, package_label="12 oz box")
STRAW = Ingredient(id="strawberries", name="Strawberries", category="fruit",
                   tags=["vegetarian", "vegan"], allergens=[], kcal_per_100g=32, protein_per_100g=0.7,
                   carbs_per_100g=7.7, fat_per_100g=0.3, package_price=3.48, package_size_g=454,
                   package_label="1 lb clamshell")
BLUE = Ingredient(id="blueberries", name="Blueberries", category="fruit",
                  tags=["vegetarian", "vegan"], allergens=[], kcal_per_100g=57, protein_per_100g=0.7,
                  carbs_per_100g=14, fat_per_100g=0.3, package_price=3.98, package_size_g=340,
                  package_label="1 pint")
BY_ID = catalog_by_id([RICE, CHICKEN, SALT, OIL, WHEY, BANANA, GREEK, GRANOLA, STRAW, BLUE])

MEAL = Meal(name="Chicken & rice", cook_time_minutes=20, servings=2,
            instructions="Cook rice, grill chicken.",
            ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                         MealIngredient(ingredient_id="chicken_breast", grams=150),
                         MealIngredient(ingredient_id="salt", grams=5)])


def _inputs(owned=None, owned_grams=None):
    return PlanInputs(weekly_budget=30, goal=Goal.maintain, bodyweight_lb=180,
                      max_cook_minutes=120, owned_ingredient_ids=owned or [],
                      owned_grams=owned_grams or {},
                      target_calories=2700, target_protein=180,
                      target_carbs=326, target_fat=75)


def _chicken_plan(grams):
    """A one-ingredient plan whose only grocery cost is chicken (test pack: 1000g for $10)."""
    return GeneratedPlan(meals=[Meal(name="Chicken", cook_time_minutes=10, servings=7,
        instructions="1. Cook the chicken.",
        ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=grams)])])


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


def test_partial_ownership_charges_whole_package_shortfall():
    plan = _chicken_plan(1500)                          # needs 2 packs = $20 with nothing owned
    assert weekly_grocery_cost(plan, BY_ID) == 20.00
    # own 600g -> shortfall 900g -> 1 pack -> $10 (whole-package reality reduces at the boundary)
    assert weekly_grocery_cost(plan, BY_ID, owned_grams={"chicken_breast": 600}) == 10.00


def test_full_ownership_by_quantity_is_free():
    plan = _chicken_plan(1500)
    assert weekly_grocery_cost(plan, BY_ID, owned_grams={"chicken_breast": 1500}) == 0.00
    assert weekly_grocery_cost(plan, BY_ID, owned_grams={"chicken_breast": 2000}) == 0.00


def test_owned_without_quantity_still_treated_as_enough():
    plan = _chicken_plan(1500)
    # Marked owned but no amount given -> "have enough" (preserves the original checkbox behavior).
    assert weekly_grocery_cost(plan, BY_ID, owned_ids=["chicken_breast"]) == 0.00


def test_small_partial_ownership_still_buys_needed_packages():
    plan = _chicken_plan(1500)
    # own only 100g -> shortfall 1400g -> still 2 packs -> $20
    assert weekly_grocery_cost(plan, BY_ID, owned_grams={"chicken_breast": 100}) == 20.00


def test_daily_protein_grams():
    plan = GeneratedPlan(meals=[MEAL])
    assert daily_protein_grams(plan, BY_ID) == 7.4   # 51.9g protein / 7 days


def test_daily_calories():
    plan = GeneratedPlan(meals=[MEAL])
    assert daily_calories(plan, BY_ID) == 72.5   # 507.5 kcal / 7 days


def test_daily_carbs():
    plan = GeneratedPlan(meals=[MEAL])
    assert daily_carbs(plan, BY_ID) == 8.0   # 56.0 carbs / 7 days


def test_calorie_snack_sized_by_calories_and_diet_safe():
    # BY_ID lacks oats/peanut butter, so the first recipe is skipped and the yogurt one is used.
    snack = _calorie_snack(300, BY_ID, used_names=set())
    assert snack.slot == "snack" and snack.name == "Yogurt, granola & berries"
    per_serving_kcal = macros_for_meal(snack, BY_ID).calories / 7
    assert 150 <= per_serving_kcal <= 450    # sized near the request, bounded by recipe limits


def test_calorie_snack_none_when_no_recipe_available():
    bare = {k: v for k, v in BY_ID.items()
            if k not in ("greek_yogurt", "granola", "blueberries", "oats",
                         "peanut_butter", "banana")}
    assert _calorie_snack(300, bare, used_names=set()) is None


def test_distribution_snacks_add_whey_then_calorie_snacks():
    base = [Meal(name="Dinner", slot="dinner", cook_time_minutes=10, servings=7,
                 instructions="x", ingredients=[
                     MealIngredient(ingredient_id="rice_white", grams=700)])]  # low protein & calories
    policy = macro_policy(Goal.bulk, 2500, 150, 300, 70)
    out = _add_distribution_snacks(base, BY_ID, policy, None, _inputs())
    names = [m.name for m in out]
    assert PROTEIN_DRINK_NAME in names                                     # protein gap -> whey
    assert any(m.slot == "snack" and m.name != PROTEIN_DRINK_NAME for m in out)  # + calorie snack


def test_distribution_snacks_none_when_targets_met():
    base = [Meal(name="Big", slot="dinner", cook_time_minutes=10, servings=7, instructions="x",
                 ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=4000),
                              MealIngredient(ingredient_id="rice_white", grams=6000)])]
    policy = macro_policy(Goal.maintain, 500, 40, 50, 14)   # tiny targets, base far exceeds
    out = _add_distribution_snacks(base, BY_ID, policy, None, _inputs())
    assert all(m.slot != "snack" for m in out)       # nothing to add


def test_macro_policy_cut_has_strict_ceiling_at_target():
    p = macro_policy(Goal.cut, 2000, 150, 200, 67)
    assert p.cal_min == 1900.0      # 0.95 * 2000
    assert p.cal_max == 2000.0      # ceiling == target (strict, no overshoot)
    assert p.protein_min == 150.0
    assert p.method == "topup"


def test_macro_policy_maintain_is_tight_band():
    p = macro_policy(Goal.maintain, 2000, 150, 200, 56)
    assert p.cal_min == 1860.0      # 0.93 * 2000
    assert p.cal_max == 2140.0      # 1.07 * 2000
    assert p.protein_min == 150.0


def test_macro_policy_bulk_aims_at_target_with_slim_overshoot():
    p = macro_policy(Goal.bulk, 3000, 180, 430, 67)
    assert p.cal_min == 3000.0      # floor == target
    assert p.cal_max == 3150.0      # 1.05 * 3000 (only a slim overshoot)
    assert p.protein_min == 180.0


def test_macro_policy_protein_ceiling_near_target_each_goal():
    assert macro_policy(Goal.cut, 2000, 150, 200, 67).protein_max == 165.0       # 1.10*150
    assert macro_policy(Goal.maintain, 2000, 150, 200, 56).protein_max == 165.0  # 1.10*150
    assert macro_policy(Goal.bulk, 3000, 180, 430, 67).protein_max == 198.0      # 1.10*180


def test_macro_policy_includes_carb_and_fat_bands():
    p = macro_policy(Goal.maintain, 2000, 150, 200, 60)
    assert p.carb_aim == 200.0
    assert p.carb_min == 170.0      # 0.85 * 200
    assert p.carb_max == 230.0      # 1.15 * 200
    assert p.fat_aim == 60.0
    assert p.fat_min == 51.0        # 0.85 * 60
    assert p.fat_max == 69.0        # 1.15 * 60


def test_compute_plan_reports_carb_fat_met():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())  # tiny meal vs 326/75
    assert cp.carbs_met is False
    assert cp.fat_met is False
    assert "carbs" in cp.target_note and "fat" in cp.target_note


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
    # Weekly model: each meal is a 7-serving batch, so per-serving = whole-recipe / 7.
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())
    meal = cp.meals[0]
    assert meal.servings == 7
    assert meal.macros_per_serving.calories == 72.5    # 507.5 / 7
    assert meal.macros_per_serving.carbs == 8.0         # 56.0 / 7

    rice = meal.ingredients[0]
    assert rice.grams == 200.0                          # whole-recipe total preserved
    assert rice.grams_per_serving == 28.6               # 200 / 7
    assert rice.macros_per_serving.calories == 37.1     # 260 / 7

    chicken = meal.ingredients[1]
    assert chicken.grams_per_serving == 21.4            # 150 / 7
    assert chicken.macros_per_serving.protein == 6.6    # 46.5 / 7


def test_compute_plan_orders_and_labels_slots():
    def _m(slot, ing):
        return Meal(name=slot.title(), cook_time_minutes=10, servings=7, slot=slot,
                    instructions="...", ingredients=[MealIngredient(ingredient_id=ing, grams=200)])
    scrambled = GeneratedPlan(meals=[_m("dinner", "rice_white"), _m("breakfast", "rice_white"),
                                     _m("snack", "rice_white"), _m("lunch", "rice_white")])
    cp = compute_plan(scrambled, BY_ID, _inputs())
    assert [mv.slot for mv in cp.meals] == ["breakfast", "lunch", "dinner", "snack"]


def test_compute_plan_normalizes_servings_to_week():
    # Whatever servings the LLM supplied, the plan is a 7-serving weekly batch (one/day).
    meal = Meal(name="Whole batch", cook_time_minutes=10, servings=0,
                instructions="...", ingredients=[
                    MealIngredient(ingredient_id="rice_white", grams=700)])
    cp = compute_plan(GeneratedPlan(meals=[meal]), BY_ID, _inputs())
    m = cp.meals[0]
    assert m.servings == 7
    assert m.ingredients[0].grams_per_serving == 100.0     # 700 / 7


# --- Deterministic goal-aware correction (adjust_to_targets) ---

def _goal_inputs(goal, cal, prot, budget=100000, carbs=None, fat=None):
    fat = fat if fat is not None else round(0.25 * cal / 9)
    carbs = carbs if carbs is not None else round((cal - 4 * prot - 9 * fat) / 4)
    return PlanInputs(weekly_budget=budget, goal=goal, bodyweight_lb=180,
                      max_cook_minutes=120, target_calories=cal, target_protein=prot,
                      target_carbs=carbs, target_fat=fat)


# A low-protein, sub-target weekly base so the correction has to distribute into snacks.
_CUT_BASE = GeneratedPlan(meals=[Meal(name="Base", cook_time_minutes=20, servings=7,
    instructions="...", ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=700),
                                     MealIngredient(ingredient_id="rice_white", grams=1000)])])


def _real_base():
    """A realistic 3-meal base (whole-week grams), modestly under target, over the full catalog."""
    return GeneratedPlan(meals=[
        Meal(name="Yogurt bowl", slot="breakfast", cook_time_minutes=5, servings=7,
             instructions="1. Layer the yogurt, granola, and banana.", ingredients=[
                 MealIngredient(ingredient_id="greek_yogurt", grams=1400),
                 MealIngredient(ingredient_id="granola", grams=350),
                 MealIngredient(ingredient_id="banana", grams=840)]),
        Meal(name="Chicken & rice", slot="lunch", cook_time_minutes=25, servings=7,
             instructions="1. Sear the chicken in olive oil. 2. Serve over rice.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1190),
                 MealIngredient(ingredient_id="rice_white", grams=1400)]),
        Meal(name="Beef & pasta", slot="dinner", cook_time_minutes=25, servings=7,
             instructions="1. Brown the beef. 2. Toss with the pasta.", ingredients=[
                 MealIngredient(ingredient_id="ground_beef_90", grams=910),
                 MealIngredient(ingredient_id="pasta_ww", grams=1400)])])


def _portion_violations(plan, by_id):
    """(name, amount, unit) for any single ingredient whose per-serving size is unrealistic —
    keyed off the catalog's own density fields, so it scales as the catalog grows."""
    bad = []
    for meal in plan.meals:
        for mi in meal.ingredients:
            ing = by_id[mi.ingredient_id]
            per = mi.grams / 7
            if ing.grams_per_cup and per / ing.grams_per_cup > 2.5:
                bad.append((ing.name, round(per / ing.grams_per_cup, 1), "cups"))
            elif ing.unit_grams and per / ing.unit_grams > 3.0:
                bad.append((ing.name, round(per / ing.unit_grams, 1), "units"))
            elif not ing.grams_per_cup and not ing.unit_grams and per / 28.3495 > 10.0:
                bad.append((ing.name, round(per / 28.3495, 1), "oz"))
    return bad


def test_adjust_realistic_no_giant_portions_and_respects_ceiling():
    for goal, cal, prot in [(Goal.bulk, 3000, 180), (Goal.maintain, 2400, 150),
                            (Goal.cut, 1800, 160)]:
        inputs = _goal_inputs(goal, cal, prot)
        p = macro_policy(goal, cal, prot, inputs.target_carbs, inputs.target_fat)
        adj = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=None)
        assert _portion_violations(adj, _REAL) == [], goal.value          # no giant portions
        assert daily_calories(adj, _REAL) <= p.cal_max + 1, goal.value    # strict ceiling held
        assert daily_protein_grams(adj, _REAL) >= p.protein_min * 0.9, goal.value  # protein high


def test_adjust_distributes_a_big_day_into_snacks():
    inputs = _goal_inputs(Goal.bulk, 3200, 180)
    adj = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=None)
    assert any(m.slot == "snack" for m in adj.meals)   # a high-calorie day gets snack occasions


def test_plan_b_lands_every_macro_in_band():
    # Plan B (budget_cap=None) hits calories, protein, carbs AND fat for each goal.
    for goal, cal, prot in [(Goal.bulk, 3000, 180), (Goal.maintain, 2400, 150),
                            (Goal.cut, 2350, 170)]:
        inputs = _goal_inputs(goal, cal, prot)
        adj = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=None)
        cp = compute_plan(adj, _REAL, inputs)
        assert cp.calories_met and cp.protein_met and cp.carbs_met and cp.fat_met, \
            f"{goal.value}: {cp.target_note}"


def test_plan_a_stays_budget_first_for_macro_topups():
    # Plan A's budget cap eases the (budget-aware) carb/protein top-ups, so it carries fewer/
    # cheaper additions than the uncapped Plan B from the same base.
    inputs = _goal_inputs(Goal.bulk, 3000, 180, budget=12)
    capped = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=12)
    uncapped = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=None)
    assert weekly_grocery_cost(capped, _REAL) <= weekly_grocery_cost(uncapped, _REAL)


def test_plan_keeps_snacks_few_on_a_big_day():
    # A high-calorie bulk must stay a few eating occasions (whey + ~1 food snack), not fan out
    # into 4-6 small snacks — the meals carry most of the calories.
    inputs = _goal_inputs(Goal.bulk, 3200, 190)
    adj = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=None)
    snacks = [m for m in adj.meals if m.slot == "snack"]
    assert len(snacks) <= 3, [s.name for s in snacks]


def test_adjust_adds_whey_shake_meal():
    inputs = _goal_inputs(Goal.cut, 400, 50)
    adj = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=None)
    assert any(mi.ingredient_id == "whey_protein"
               for meal in adj.meals for mi in meal.ingredients)


def test_adjust_fills_gaps_with_snacks_before_big_scaling():
    # Base modestly under target: snacks should do the heavy lifting, not a ~1.75x blow-up.
    base = GeneratedPlan(meals=[
        Meal(name="Eggs", slot="breakfast", cook_time_minutes=10, servings=7, instructions="x",
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1000)]),
        Meal(name="Rice bowl", slot="lunch", cook_time_minutes=10, servings=7, instructions="x",
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=2000),
                          MealIngredient(ingredient_id="olive_oil", grams=80)]),
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=10, servings=7,
             instructions="x", ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1100),
                                            MealIngredient(ingredient_id="rice_white", grams=2000)])])
    inputs = _goal_inputs(Goal.maintain, 2000, 150, carbs=250, fat=56)
    adj = adjust_to_targets(base, BY_ID, inputs, budget_cap=None)
    p = macro_policy(Goal.maintain, 2000, 150, 250, 56)
    assert any(m.slot == "snack" for m in adj.meals)                  # a snack was added
    chicken = sum(mi.grams for m in adj.meals for mi in m.ingredients
                  if mi.ingredient_id == "chicken_breast")
    assert chicken < 2100 * 1.4                                       # no giant scale-up
    assert daily_protein_grams(adj, BY_ID) >= p.protein_min           # protein floor met


def test_adjust_caps_whey_at_one_scoop_per_day():
    # High protein target vs a low-protein base would want several scoops; cap at one/day.
    inputs = _goal_inputs(Goal.cut, 1500, 200)
    adj = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=None)
    whey_total = sum(mi.grams for meal in adj.meals for mi in meal.ingredients
                     if mi.ingredient_id == "whey_protein")
    assert whey_total <= SCOOP_GRAMS * 7 + 0.1   # at most one scoop/day across the week


def test_adjust_keeps_whey_out_of_meals():
    # Even if the LLM dumps whey powder into a meal, the correction pulls it into the drink.
    polluted = GeneratedPlan(meals=[Meal(name="Shake bowl", cook_time_minutes=5, servings=7,
        instructions="...", ingredients=[
            MealIngredient(ingredient_id="chicken_breast", grams=700),
            MealIngredient(ingredient_id="whey_protein", grams=500)])])
    adj = adjust_to_targets(polluted, BY_ID, _goal_inputs(Goal.maintain, 2000, 120),
                            budget_cap=None)
    offenders = [m.name for m in adj.meals if m.name != PROTEIN_DRINK_NAME
                 and any(mi.ingredient_id == "whey_protein" for mi in m.ingredients)]
    assert offenders == []                       # no whey in regular meals
    assert any(mi.ingredient_id == "whey_protein"
               for m in adj.meals if m.name == PROTEIN_DRINK_NAME
               for mi in m.ingredients)          # whey lives only in the daily drink


def test_adjust_plan_a_respects_budget_cap_with_residual():
    inputs = _goal_inputs(Goal.cut, 400, 50, budget=15)
    capped = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=15)
    uncapped = adjust_to_targets(_CUT_BASE, BY_ID, inputs, budget_cap=None)
    # The cap blocks the (whole-package) whey top-up, so capped protein falls short of uncapped.
    assert weekly_grocery_cost(capped, BY_ID) <= 15
    assert daily_protein_grams(capped, BY_ID) < daily_protein_grams(uncapped, BY_ID)


def test_compute_plan_reports_targets_met_when_in_band():
    inputs = _goal_inputs(Goal.maintain, 2400, 150)
    adj = adjust_to_targets(_real_base(), _REAL, inputs, budget_cap=None)
    cp = compute_plan(adj, _REAL, inputs)
    assert cp.calories_met is True
    assert cp.protein_met is True


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
                            max_cook_minutes=120, target_calories=2200, target_protein=100,
                            target_carbs=250, target_fat=70)
        adj = adjust_to_targets(base, _REAL, inputs, budget_cap=None)
        cp = compute_plan(adj, _REAL, inputs)
        assert cp.protein_met, f"{goal.value}: protein floor not met"


def test_reconcile_seasonings_adds_mentioned_but_missing():
    meal = Meal(name="Garlic chicken", cook_time_minutes=15, servings=2,
                instructions="Season the chicken generously with salt, then sear.",
                ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=300)])
    out = reconcile_seasonings(GeneratedPlan(meals=[meal]), BY_ID)
    ids = {mi.ingredient_id for mi in out.meals[0].ingredients}
    assert "salt" in ids        # mentioned in the steps but unlisted -> added (priced later)


def test_reconcile_seasonings_adds_new_spices():
    # The expanded catalog's spices are also caught when the steps name them but the model
    # forgot to list them, so the flavor is priced like the original seasonings.
    meal = Meal(name="Cumin beans", cook_time_minutes=10, servings=7,
                instructions="1. Toast the cumin and paprika, then stir in the beans.",
                ingredients=[MealIngredient(ingredient_id="black_beans", grams=700)])
    out = reconcile_seasonings(GeneratedPlan(meals=[meal]), _REAL)
    ids = {mi.ingredient_id for mi in out.meals[0].ingredients}
    assert "cumin" in ids and "paprika" in ids


def test_reconcile_seasonings_skips_already_listed():
    meal = Meal(name="Salty", cook_time_minutes=5, servings=1, instructions="Add salt.",
                ingredients=[MealIngredient(ingredient_id="salt", grams=4)])
    out = reconcile_seasonings(GeneratedPlan(meals=[meal]), BY_ID)
    salts = [mi for mi in out.meals[0].ingredients if mi.ingredient_id == "salt"]
    assert len(salts) == 1 and salts[0].grams == 4   # untouched, not duplicated


def test_reconcile_adds_cooking_oil_so_its_calories_count():
    meal = Meal(name="Seared chicken", cook_time_minutes=15, servings=7,
                instructions="1. Sear the chicken in olive oil until golden.",
                ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1000)])
    out = reconcile_seasonings(GeneratedPlan(meals=[meal]), BY_ID)
    oil = [mi for mi in out.meals[0].ingredients if mi.ingredient_id == "olive_oil"]
    assert oil and oil[0].grams > 0       # mentioned oil is listed -> its calories are counted


def test_clean_drops_zero_gram_ingredients():
    plan = GeneratedPlan(meals=[Meal(name="X", cook_time_minutes=5, servings=7, instructions="x",
        ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                     MealIngredient(ingredient_id="olive_oil", grams=0)])])
    ids = [mi.ingredient_id for mi in _clean(plan).meals[0].ingredients]
    assert "olive_oil" not in ids and "rice_white" in ids   # no 0 g artifacts survive


def test_format_amount_units_and_plurals():
    potato = Ingredient(id="potato", name="Potato", category="vegetable", tags=[], allergens=[],
                        kcal_per_100g=77, protein_per_100g=2, carbs_per_100g=17, fat_per_100g=0.1,
                        package_price=3.97, package_size_g=2268, package_label="5 lb bag",
                        unit_label="potato", unit_grams=170)
    assert format_amount(170, potato) == "1 potato"
    assert format_amount(510, potato) == "3 potatoes"      # "o" -> "es", not "potatos"
    assert format_amount(120, BANANA) == "1 banana"
    assert format_amount(240, BANANA) == "2 bananas"


def test_format_amount_partial_counts_read_as_plain_words():
    potato = Ingredient(id="potato", name="Potato", category="vegetable", tags=[], allergens=[],
                        kcal_per_100g=77, protein_per_100g=2, carbs_per_100g=17, fat_per_100g=0.1,
                        package_price=3.97, package_size_g=2268, package_label="5 lb bag",
                        unit_label="potato", unit_grams=170)
    assert format_amount(102.9, BANANA) == "about 1 banana"      # 6 bananas / 7 servings
    assert format_amount(172, BANANA) == "1 or 2 bananas"        # 1.43 — between wholes
    assert format_amount(260, BANANA) == "about 2 bananas"       # 2.17
    assert format_amount(73, potato) == "about half a potato"    # 0.43
    assert format_amount(51, potato) == "about a quarter potato" # 0.30
    assert format_amount(28, WHEY) == "about 1 scoop"            # 0.875 scoops


def test_format_amount_tiny_counts_fall_back_to_honest_grams():
    garlic = Ingredient(id="garlic", name="Garlic", category="seasoning", tags=["vegan"],
                        allergens=[], kcal_per_100g=149, protein_per_100g=6.4,
                        carbs_per_100g=33, fat_per_100g=0.5, package_price=0.68,
                        package_size_g=60, package_label="1 bulb", pantry_staple=True,
                        unit_label="clove", unit_grams=5)
    # A tenth of a clove is not a count; and 0.5 g must not read as a padded "0.5 oz".
    assert format_amount(0.5, garlic) == "1 g"
    assert format_amount(4, CHICKEN) == "4 g"


def test_format_amount_volume_cups_tbsp_tsp():
    assert format_amount(158, RICE) == "1 cup"             # 158 g/cup
    assert format_amount(237, RICE) == "1.5 cups"          # 1.5 cups
    assert format_amount(13.5, OIL) == "1 tbsp"            # 216 g/cup -> small -> tbsp
    assert format_amount(4.5, OIL) == "1 tsp"              # even smaller -> tsp


def test_format_amount_weight_in_ounces():
    assert format_amount(227, CHICKEN) == "8 oz"           # 227 / 28.35
    assert format_amount(907, CHICKEN) == "2 lb"           # >= 16 oz -> pounds


def test_format_amount_whey_in_scoops():
    assert format_amount(32, WHEY) == "1 scoop"
    assert format_amount(64, WHEY) == "2 scoops"


def test_snap_units_does_not_round_whey():
    # Whey is a "scoop" countable but its amount is precisely sized by the correction, so it
    # must stay unrounded (still displayed in scoops).
    plan = GeneratedPlan(meals=[Meal(name="Shake", slot="snack", cook_time_minutes=0, servings=7,
        instructions="x", ingredients=[MealIngredient(ingredient_id="whey_protein", grams=89.0)])])
    out = snap_units(plan, BY_ID)
    assert out.meals[0].ingredients[0].grams == 89.0


def test_snap_units_rounds_countables_to_whole_units():
    plan = GeneratedPlan(meals=[Meal(name="Snack", cook_time_minutes=2, servings=2,
        instructions="...", ingredients=[
            MealIngredient(ingredient_id="banana", grams=180.5),   # ~1.5 bananas
            MealIngredient(ingredient_id="rice_white", grams=200)])])
    snapped = snap_units(plan, BY_ID)
    g = {mi.ingredient_id: mi.grams for mi in snapped.meals[0].ingredients}
    assert g["banana"] == 240.0     # round(180.5/120)=2 -> 2*120
    assert g["rice_white"] == 200   # non-countable unchanged


def test_compute_plan_renders_countables_as_units():
    plan = GeneratedPlan(meals=[Meal(name="Snack", cook_time_minutes=2, servings=7,
        instructions="...", ingredients=[MealIngredient(ingredient_id="banana", grams=840)])])
    cp = compute_plan(plan, BY_ID, _inputs())
    iv = cp.meals[0].ingredients[0]
    assert iv.amount_total == "7 bananas"
    assert iv.amount_per_serving == "1 banana"     # 840/7 = 120g = 1 banana
    item = next(g for g in cp.grocery_list if g.ingredient_id == "banana")
    assert item.uses_display == "7 bananas"


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


def test_owned_ingredients_appear_in_owned_list():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs(owned=["rice_white"]))
    # owned_list contains the ingredient with zero cost
    owned_ids = {g.ingredient_id for g in cp.owned_list}
    assert "rice_white" in owned_ids
    rice = next(g for g in cp.owned_list if g.ingredient_id == "rice_white")
    assert rice.cost == 0.0
    assert rice.packages == 0
    assert rice.per_meal_cost is None
    assert rice.uses_display == format_amount(200.0, RICE)   # "200.0 g"
    # still excluded from grocery_list and not counted in total_cost
    assert "rice_white" not in {g.ingredient_id for g in cp.grocery_list}
    assert cp.total_cost == 10.00


def test_compute_plan_partial_ownership_shows_shortfall():
    plan = _chicken_plan(1500)                             # 2 packs if buying all
    cp = compute_plan(plan, BY_ID, _inputs(owned_grams={"chicken_breast": 600}))
    # Partially owned -> stays in the grocery list, priced at the shortfall, with owned recorded.
    chicken = next(g for g in cp.grocery_list if g.ingredient_id == "chicken_breast")
    assert chicken.owned_grams == 600
    assert chicken.cost == 10.00                           # shortfall 900g -> 1 pack
    assert cp.total_cost == 10.00
    assert "chicken_breast" not in {g.ingredient_id for g in cp.owned_list}


def test_compute_plan_full_ownership_by_quantity_lists_as_owned():
    plan = _chicken_plan(1500)
    cp = compute_plan(plan, BY_ID, _inputs(owned_grams={"chicken_breast": 1500}))
    assert "chicken_breast" in {g.ingredient_id for g in cp.owned_list}
    assert "chicken_breast" not in {g.ingredient_id for g in cp.grocery_list}
    assert cp.total_cost == 0.00
