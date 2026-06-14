from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.catalog import catalog_by_id
from app.plan import macros_for_meal, compute_plan, weekly_grocery_cost

RICE = Ingredient(id="rice_white", name="White rice", category="grain",
                  tags=["vegan"], allergens=[], kcal_per_100g=130,
                  protein_per_100g=2.7, carbs_per_100g=28, fat_per_100g=0.3,
                  price_per_100g=0.10)
CHICKEN = Ingredient(id="chicken_breast", name="Chicken breast", category="protein",
                     tags=[], allergens=[], kcal_per_100g=165, protein_per_100g=31,
                     carbs_per_100g=0, fat_per_100g=3.6, price_per_100g=1.10)
BY_ID = catalog_by_id([RICE, CHICKEN])

MEAL = Meal(name="Chicken & rice", cook_time_minutes=20, servings=2,
            instructions="Cook rice, grill chicken.",
            ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                         MealIngredient(ingredient_id="chicken_breast", grams=150)])


def _inputs(owned=None):
    return PlanInputs(weekly_budget=30, goal=Goal.maintain, bodyweight_lb=180,
                      max_cook_minutes=120, owned_ingredient_ids=owned or [],
                      target_calories=2700, target_protein=180)


def test_macros_for_meal():
    m = macros_for_meal(MEAL, BY_ID)
    assert m.calories == 507.5   # 130*2 + 165*1.5
    assert m.protein == 51.9     # 2.7*2 + 31*1.5
    assert m.carbs == 56.0       # 28*2
    assert m.fat == 6.0          # 0.3*2 + 3.6*1.5


def test_weekly_grocery_cost():
    plan = GeneratedPlan(meals=[MEAL])
    assert weekly_grocery_cost(plan, BY_ID) == 1.85               # rice 0.20 + chicken 1.65
    assert weekly_grocery_cost(plan, BY_ID, owned_ids=["rice_white"]) == 1.65


def test_compute_plan_cost_and_daily_macros():
    plan = GeneratedPlan(meals=[MEAL])
    cp = compute_plan(plan, BY_ID, _inputs())
    assert cp.total_cost == 1.85   # rice 0.20 + chicken 1.65
    assert cp.within_budget is True
    assert len(cp.grocery_list) == 2
    assert cp.daily_macros.calories == 72.5   # 507.5 / 7
    assert cp.meals[0].ingredients[0].name == "White rice"


def test_owned_ingredients_excluded_from_grocery_list():
    plan = GeneratedPlan(meals=[MEAL])
    cp = compute_plan(plan, BY_ID, _inputs(owned=["rice_white"]))
    ids = {g.ingredient_id for g in cp.grocery_list}
    assert "rice_white" not in ids
    assert cp.total_cost == 1.65
