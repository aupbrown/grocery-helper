import math
from dataclasses import dataclass

from app.models import (
    Goal, Macros, Meal, MealIngredient, Ingredient, GeneratedPlan, PlanInputs,
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


def macros_for_grams(ing: Ingredient, grams: float) -> Macros:
    """One ingredient's macro contribution for `grams`, unrounded.

    Kept unrounded so callers can sum first and round once (matching how the
    plan totals and per-serving figures are computed).
    """
    f = grams / 100.0
    return Macros(
        calories=ing.kcal_per_100g * f,
        protein=ing.protein_per_100g * f,
        carbs=ing.carbs_per_100g * f,
        fat=ing.fat_per_100g * f,
    )


def macros_for_meal(meal: Meal, by_id: dict[str, Ingredient]) -> Macros:
    cals = prot = carb = fat = 0.0
    for mi in meal.ingredients:
        m = macros_for_grams(by_id[mi.ingredient_id], mi.grams)
        cals += m.calories
        prot += m.protein
        carb += m.carbs
        fat += m.fat
    return Macros(calories=round(cals, 1), protein=round(prot, 1),
                  carbs=round(carb, 1), fat=round(fat, 1))


def _per_serving(m: Macros, servings: int) -> Macros:
    """Macros for one serving. Guards a 0/missing serving count (LLM-supplied)."""
    s = servings if servings and servings > 0 else 1
    return Macros(
        calories=round(m.calories / s, 1),
        protein=round(m.protein / s, 1),
        carbs=round(m.carbs / s, 1),
        fat=round(m.fat / s, 1),
    )


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


def daily_calories(generated: GeneratedPlan, by_id: dict[str, Ingredient]) -> float:
    """Average daily calories for the plan (weekly total / 7)."""
    total = sum(macros_for_meal(meal, by_id).calories for meal in generated.meals)
    return round(total / DAYS, 1)


# Per-goal acceptance bands and how the deterministic correction (adjust_to_targets)
# closes a gap. Cutting is strict on calories (never above target); bulking floors both
# and tolerates overshoot; maintaining is a symmetric ±10% band.
CUT_CAL_FLOOR = 0.95
MAINTAIN_CAL_BAND = 0.10
BULK_CAL_CEILING = 1.20
PROTEIN_CEILING = 1.10


@dataclass(frozen=True)
class MacroPolicy:
    cal_min: float
    cal_max: float
    cal_aim: float          # where the calorie lever aims to land
    protein_min: float      # protein floor (the hard target)
    protein_max: float      # correction stops adding protein here
    method: str             # "scale" (bulk) | "topup" (cut/maintain)


def _target_status(daily: Macros, policy: MacroPolicy) -> tuple[bool, bool, str]:
    """Whether the plan meets the goal's protein floor / calorie band, plus a gap note."""
    protein_met = daily.protein >= policy.protein_min
    calories_met = policy.cal_min <= daily.calories <= policy.cal_max
    parts = []
    if daily.calories < policy.cal_min:
        parts.append(f"{round(policy.cal_min - daily.calories)} kcal/day below target")
    elif daily.calories > policy.cal_max:
        parts.append(f"{round(daily.calories - policy.cal_max)} kcal/day above target")
    if not protein_met:
        parts.append(f"{round(policy.protein_min - daily.protein)}g/day protein short")
    return protein_met, calories_met, "; ".join(parts)


def macro_policy(goal: Goal, target_cal: float, target_protein: float) -> MacroPolicy:
    if goal == Goal.cut:
        return MacroPolicy(
            cal_min=round(CUT_CAL_FLOOR * target_cal, 1), cal_max=float(target_cal),
            cal_aim=float(target_cal), protein_min=float(target_protein),
            protein_max=round(PROTEIN_CEILING * target_protein, 1), method="topup")
    if goal == Goal.bulk:
        return MacroPolicy(
            cal_min=float(target_cal), cal_max=round(BULK_CAL_CEILING * target_cal, 1),
            cal_aim=float(target_cal), protein_min=float(target_protein),
            protein_max=float("inf"), method="scale")
    return MacroPolicy(   # maintain
        cal_min=round((1 - MAINTAIN_CAL_BAND) * target_cal, 1),
        cal_max=round((1 + MAINTAIN_CAL_BAND) * target_cal, 1),
        cal_aim=float(target_cal), protein_min=float(target_protein),
        protein_max=round(PROTEIN_CEILING * target_protein, 1), method="topup")


# --- Deterministic goal-aware correction ---
# After the LLM proposes a plan we close the residual macro gap deterministically, since
# the (free-tier) model can't reliably hit numeric targets. Two levers: scale the existing
# non-pantry ingredients (calories) and add a whey-protein shake (protein). Pantry staples
# (seasonings, oil) are never scaled.

WHEY_ID = "whey_protein"
FMIN, FMAX = 0.6, 1.75              # how far portions may be scaled, "within reason"
WHEY_DAILY_PROTEIN_CAP = 60.0      # max grams of protein/day added as whey


def _scalable_totals(meals, by_id) -> tuple[float, float, float, float]:
    """Weekly calories/protein split into non-pantry (scalable) and pantry (fixed)."""
    ce = pe = cp = pp = 0.0
    for meal in meals:
        for mi in meal.ingredients:
            ing = by_id[mi.ingredient_id]
            m = macros_for_grams(ing, mi.grams)
            if ing.pantry_staple:
                cp += m.calories
                pp += m.protein
            else:
                ce += m.calories
                pe += m.protein
    return ce, pe, cp, pp


def _whey_shake(grams: float) -> Meal:
    return Meal(name="Whey protein shake", cook_time_minutes=0, servings=DAYS,
                instructions="Blend whey protein with water or milk — one shake per day.",
                ingredients=[MealIngredient(ingredient_id=WHEY_ID, grams=round(grams, 1))])


def _rebuild(base_meals, f: float, whey_g: float, by_id) -> GeneratedPlan:
    """Base meals with non-pantry portions scaled by f, plus a whey shake if whey_g >= 1."""
    meals = []
    for meal in base_meals:
        ings = [MealIngredient(
            ingredient_id=mi.ingredient_id,
            grams=mi.grams if by_id[mi.ingredient_id].pantry_staple
            else round(mi.grams * f, 1),
        ) for mi in meal.ingredients]
        meals.append(meal.model_copy(update={"ingredients": ings}))
    if whey_g >= 1.0 and WHEY_ID in by_id:
        meals.append(_whey_shake(whey_g))
    return GeneratedPlan(meals=meals)


def adjust_to_targets(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    inputs: PlanInputs,
    budget_cap: float | None = None,
) -> GeneratedPlan:
    """Scale portions + add whey so the plan lands in the goal's macro bands.

    budget_cap=None (Plan B) corrects fully — it guarantees the protein floor. A numeric
    cap (Plan A) backs the additions off to stay within budget, accepting a residual gap.
    """
    policy = macro_policy(inputs.goal, inputs.target_calories, inputs.target_protein)
    base = generated.meals
    ce, pe, cp, pp = _scalable_totals(base, by_id)

    has_whey = WHEY_ID in by_id
    cwhey = by_id[WHEY_ID].kcal_per_100g / 100 if has_whey else 0.0
    pwhey = by_id[WHEY_ID].protein_per_100g / 100 if has_whey else 0.0

    cal_aim_wk = policy.cal_aim * DAYS
    prot_floor_wk = policy.protein_min * DAYS
    cal_only_f = (cal_aim_wk - cp) / ce if ce > 0 else 1.0

    if policy.method == "scale":          # bulk: scale up to the calorie floor only
        f = max(1.0, cal_only_f)
    else:                                 # cut/maintain: solve f,w to hit cal aim + protein floor
        det = ce * pwhey - cwhey * pe
        if has_whey and abs(det) > 1e-9:
            w_solver = (ce * prot_floor_wk - pe * cal_aim_wk) / det
            f = (((cal_aim_wk - cp) * pwhey - cwhey * (prot_floor_wk - pp)) / det
                 if w_solver >= 0 else cal_only_f)
        else:
            f = cal_only_f
    f = min(FMAX, max(FMIN, f))

    if has_whey:
        w = (prot_floor_wk - (f * pe + pp)) / pwhey
        w = max(0.0, min(w, WHEY_DAILY_PROTEIN_CAP * DAYS / pwhey))
    else:
        w = 0.0

    plan = _rebuild(base, f, w, by_id)

    # Plan A: back off the additions (whey first, then scaling) until within budget.
    if budget_cap is not None:
        while (w > 0 or f > 1.0) and \
                weekly_grocery_cost(plan, by_id, inputs.owned_ingredient_ids) > budget_cap:
            if w > 0:
                w = max(0.0, w - 25.0)
            else:
                f = max(1.0, round(f - 0.05, 3))
            plan = _rebuild(base, f, w, by_id)
    return plan


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
        servings = meal.servings if meal.servings and meal.servings > 0 else 1
        meal_views.append(MealView(
            name=meal.name,
            ingredients=[MealIngredientView(
                name=by_id[mi.ingredient_id].name,
                grams=round(mi.grams, 1),
                grams_per_serving=round(mi.grams / servings, 1),
                macros_per_serving=_per_serving(
                    macros_for_grams(by_id[mi.ingredient_id], mi.grams), servings),
            ) for mi in meal.ingredients],
            cook_time_minutes=meal.cook_time_minutes,
            servings=meal.servings,
            instructions=meal.instructions,
            macros_per_serving=_per_serving(m, servings),
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
    policy = macro_policy(inputs.goal, inputs.target_calories, inputs.target_protein)
    protein_met, calories_met, target_note = _target_status(daily, policy)

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
        protein_met=protein_met,
        calories_met=calories_met,
        target_note=target_note,
    )
