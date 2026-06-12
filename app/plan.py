from app.models import (
    Macros, Meal, Ingredient, GeneratedPlan, PlanInputs,
    MealIngredientView, MealView, GroceryItem, ComputedPlan, Targets,
)

DAYS = 7


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


def compute_plan(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    inputs: PlanInputs,
) -> ComputedPlan:
    owned = set(inputs.owned_ingredient_ids)

    meal_views: list[MealView] = []
    total = Macros(calories=0, protein=0, carbs=0, fat=0)
    grams_by_ing: dict[str, float] = {}

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

    grocery_list: list[GroceryItem] = []
    total_cost = 0.0
    for ing_id, grams in grams_by_ing.items():
        if ing_id in owned:
            continue
        ing = by_id[ing_id]
        cost = round(grams / 100.0 * ing.price_per_100g, 2)
        total_cost += cost
        grocery_list.append(GroceryItem(ingredient_id=ing_id, name=ing.name,
                                        grams=round(grams, 1), cost=cost))
    total_cost = round(total_cost, 2)

    daily = Macros(
        calories=round(total.calories / DAYS, 1),
        protein=round(total.protein / DAYS, 1),
        carbs=round(total.carbs / DAYS, 1),
        fat=round(total.fat / DAYS, 1),
    )

    return ComputedPlan(
        meals=meal_views,
        grocery_list=grocery_list,
        total_cost=total_cost,
        weekly_budget=inputs.weekly_budget,
        within_budget=total_cost <= inputs.weekly_budget,
        daily_macros=daily,
        targets=Targets(calories=inputs.target_calories, protein=inputs.target_protein),
    )
