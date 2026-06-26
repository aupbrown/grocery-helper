from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.catalog import catalog_by_id
from app.plan import (
    macros_for_meal, compute_plan, weekly_grocery_cost, pantry_cost,
    daily_protein_grams, package_cost,
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
BY_ID = catalog_by_id([RICE, CHICKEN, SALT, OIL])

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


def test_compute_plan_splits_weekly_and_pantry():
    cp = compute_plan(GeneratedPlan(meals=[MEAL]), BY_ID, _inputs())
    assert cp.total_cost == 11.00       # weekly groceries only
    assert cp.pantry_total == 0.50      # one-time pantry stock-up
    assert cp.within_budget is True     # 11.00 <= 30 budget
    assert {g.ingredient_id for g in cp.grocery_list} == {"rice_white", "chicken_breast"}
    assert {g.ingredient_id for g in cp.pantry_list} == {"salt"}
    assert cp.daily_macros.calories == 72.5   # 507.5 / 7
    assert cp.meals[0].ingredients[0].name == "White rice"


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
