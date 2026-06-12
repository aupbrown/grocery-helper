from app.models import (
    Goal, Macros, Ingredient, MealIngredient, Meal, GeneratedPlan,
    Targets, PlanInputs,
)


def test_goal_enum_values():
    assert {g.value for g in Goal} == {"bulk", "maintain", "cut"}


def test_ingredient_roundtrips_from_dict():
    ing = Ingredient(
        id="rice_white", name="White rice", category="grain",
        tags=["vegetarian", "vegan"], allergens=[],
        kcal_per_100g=130, protein_per_100g=2.7, carbs_per_100g=28,
        fat_per_100g=0.3, price_per_100g=0.10,
    )
    assert ing.id == "rice_white"
    assert "vegan" in ing.tags


def test_generated_plan_holds_meals():
    plan = GeneratedPlan(meals=[
        Meal(name="Bowl", ingredients=[MealIngredient(ingredient_id="rice_white", grams=200)],
             cook_time_minutes=15, servings=2, instructions="Cook rice."),
    ])
    assert plan.meals[0].ingredients[0].grams == 200


def test_plan_inputs_defaults():
    pi = PlanInputs(
        weekly_budget=40, goal=Goal.cut, bodyweight_lb=170,
        max_cook_minutes=120, target_calories=2200, target_protein=170,
    )
    assert pi.activity_level == "light"
    assert pi.dietary_pattern == "none"
    assert pi.avoid_allergens == []
