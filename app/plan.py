import math

from app.models import (
    Macros, Meal, Ingredient, GeneratedPlan, PlanInputs,
    MealIngredientView, MealView, GroceryItem, ComputedPlan, Targets,
)

DAYS = 7


def package_cost(grams: float, ing: Ingredient) -> tuple[int, float]:
    """Whole packages to buy and their cost: you pay for full jars/bags, not grams.

    Callers only pass ingredients the recipes actually use (grams > 0), so this
    always buys at least one package.
    """
    packages = math.ceil(grams / ing.package_size_g)
    return packages, round(packages * ing.package_price, 2)


def macros_for_meal(meal: Meal, by_id: dict[str, Ingredient]) -> Macros:
    cals = prot = carb = fat = 0.0
    for mi in meal.ingredients:
        ing = by_id[mi.ingredient_id]
        f = mi.grams / 100.0
        cals += ing.kcal_per_100g * f
        prot += ing.protein_per_100g * f
        carb += ing.carbs_per_100g * f
        fat += ing.fat_per_100g * f
    return Macros(calories=round(cals, 1), protein=round(prot, 1),
                  carbs=round(carb, 1), fat=round(fat, 1))


def _grams_by_ingredient(generated: GeneratedPlan) -> dict[str, float]:
    grams_by_ing: dict[str, float] = {}
    for meal in generated.meals:
        for mi in meal.ingredients:
            grams_by_ing[mi.ingredient_id] = grams_by_ing.get(mi.ingredient_id, 0.0) + mi.grams
    return grams_by_ing


def weekly_grocery_cost(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    owned_ids=(),
) -> float:
    """Whole-package cost of this week's groceries (owned and pantry staples excluded).

    Single source of truth for the weekly budget — used both to render the plan and
    to decide budget retries in the generator, so the two never disagree. Pantry
    staples (seasonings, oil) are a separate one-time stock-up, see pantry_cost().
    """
    owned = set(owned_ids)
    total = 0.0
    for ing_id, grams in _grams_by_ingredient(generated).items():
        ing = by_id[ing_id]
        if ing_id in owned or ing.pantry_staple:
            continue
        total += package_cost(grams, ing)[1]
    return round(total, 2)


def pantry_cost(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    owned_ids=(),
) -> float:
    """One-time whole-package cost of pantry staples (owned excluded).

    Shown separately from the weekly budget because these last for months.
    """
    owned = set(owned_ids)
    total = 0.0
    for ing_id, grams in _grams_by_ingredient(generated).items():
        ing = by_id[ing_id]
        if ing_id in owned or not ing.pantry_staple:
            continue
        total += package_cost(grams, ing)[1]
    return round(total, 2)


def daily_protein_grams(generated: GeneratedPlan, by_id: dict[str, Ingredient]) -> float:
    """Average daily protein for the plan (weekly total / 7).

    Used by the generator to decide protein-priority retries, matching how the
    results page reports daily protein.
    """
    total = sum(macros_for_meal(meal, by_id).protein for meal in generated.meals)
    return round(total / DAYS, 1)


def compute_plan(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    inputs: PlanInputs,
) -> ComputedPlan:
    owned = set(inputs.owned_ingredient_ids)

    meal_views: list[MealView] = []
    total = Macros(calories=0, protein=0, carbs=0, fat=0)
    grams_by_ing: dict[str, float] = {}
    meals_by_ing: dict[str, int] = {}  # distinct meals using each ingredient

    for meal in generated.meals:
        m = macros_for_meal(meal, by_id)
        total = Macros(
            calories=round(total.calories + m.calories, 1),
            protein=round(total.protein + m.protein, 1),
            carbs=round(total.carbs + m.carbs, 1),
            fat=round(total.fat + m.fat, 1),
        )
        meal_views.append(MealView(
            name=meal.name,
            ingredients=[MealIngredientView(name=by_id[mi.ingredient_id].name, grams=mi.grams)
                         for mi in meal.ingredients],
            cook_time_minutes=meal.cook_time_minutes,
            servings=meal.servings,
            instructions=meal.instructions,
            macros=m,
        ))
        for mi in meal.ingredients:
            grams_by_ing[mi.ingredient_id] = grams_by_ing.get(mi.ingredient_id, 0.0) + mi.grams
        for ing_id in {mi.ingredient_id for mi in meal.ingredients}:
            meals_by_ing[ing_id] = meals_by_ing.get(ing_id, 0) + 1

    grocery_list: list[GroceryItem] = []
    pantry_list: list[GroceryItem] = []
    for ing_id, grams in grams_by_ing.items():
        if ing_id in owned:
            continue
        ing = by_id[ing_id]
        packages, cost = package_cost(grams, ing)
        per_meal = None
        if ing.pantry_staple:
            # Prorated value used this week, spread across the meals that use it —
            # frames a months-lasting jar as pennies per meal.
            per_meal = round(grams / 100.0 * ing.price_per_100g / meals_by_ing[ing_id], 2)
        item = GroceryItem(
            ingredient_id=ing_id, name=ing.name, grams=round(grams, 1),
            packages=packages, package_label=ing.package_label,
            package_price=ing.package_price, cost=cost,
            pantry_staple=ing.pantry_staple, per_meal_cost=per_meal,
        )
        (pantry_list if ing.pantry_staple else grocery_list).append(item)

    total_cost = weekly_grocery_cost(generated, by_id, inputs.owned_ingredient_ids)
    pantry_total = pantry_cost(generated, by_id, inputs.owned_ingredient_ids)

    daily = Macros(
        calories=round(total.calories / DAYS, 1),
        protein=round(total.protein / DAYS, 1),
        carbs=round(total.carbs / DAYS, 1),
        fat=round(total.fat / DAYS, 1),
    )

    return ComputedPlan(
        meals=meal_views,
        grocery_list=grocery_list,
        pantry_list=pantry_list,
        total_cost=total_cost,
        pantry_total=pantry_total,
        weekly_budget=inputs.weekly_budget,
        within_budget=total_cost <= inputs.weekly_budget,
        daily_macros=daily,
        targets=Targets(calories=inputs.target_calories, protein=inputs.target_protein),
    )
