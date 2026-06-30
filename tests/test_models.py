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
        fat_per_100g=0.3, package_price=1.42, package_size_g=2720,
        package_label="2 lb dry bag",
    )
    assert ing.id == "rice_white"
    assert "vegan" in ing.tags
    assert ing.pantry_staple is False


def test_ingredient_price_per_100g_derived_from_package():
    ing = Ingredient(
        id="oil", name="Oil", category="fat",
        kcal_per_100g=884, protein_per_100g=0, carbs_per_100g=0, fat_per_100g=100,
        package_price=5.00, package_size_g=500, package_label="500g bottle",
        pantry_staple=True,
    )
    assert ing.price_per_100g == 1.00


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
        target_carbs=205, target_fat=73,
    )
    assert pi.activity_level == "light"
    assert pi.dietary_pattern == "none"
    assert pi.avoid_allergens == []
