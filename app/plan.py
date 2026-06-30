import math
from dataclasses import dataclass

from app.models import (
    Goal, Macros, Meal, MealIngredient, Ingredient, GeneratedPlan, PlanInputs,
    MealIngredientView, MealView, GroceryItem, ComputedPlan, Targets,
)
from app.kitchen import meal_equipment

DAYS = 7
SLOT_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}
PAN_LOAD_G = 1400.0   # a home pan/pot reasonably holds ~1.4 kg of main ingredients per cook


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


# Seasoning keyword -> catalog id. Used to catch seasonings the recipe text mentions but the
# model forgot to list, so they still get priced and added to the grocery list.
SEASONING_KEYWORDS = {
    "salt": "salt", "black_pepper": "pepper", "garlic": "garlic", "onion": "onion",
    "soy_sauce": "soy sauce", "hot_sauce": "hot sauce", "mixed_herbs": "herb", "lemon": "lemon",
}

OIL_ID = "olive_oil"
OIL_COOK_PER_SERVING_G = 14.0   # ~1 tbsp/serving: counted in macros (real bulking calories)


def _seasoning_default_grams(ing: Ingredient) -> float:
    """A modest weekly amount for an auto-added seasoning (one unit if countable)."""
    return ing.unit_grams if ing.unit_grams else 5.0


def reconcile_seasonings(generated: GeneratedPlan,
                         by_id: dict[str, Ingredient]) -> GeneratedPlan:
    """Add any seasoning OR cooking oil a meal's steps mention but didn't list, so the flavor is
    priced and the oil's calories are actually counted — never 'season with X' or 'fry in oil'
    without listing it. Oil is added at a realistic ~1 tbsp/serving."""
    kw = {sid: word for sid, word in SEASONING_KEYWORDS.items() if sid in by_id}
    meals = []
    for meal in generated.meals:
        present = {mi.ingredient_id for mi in meal.ingredients}
        text = meal.instructions.lower()
        additions = [MealIngredient(ingredient_id=sid,
                                    grams=_seasoning_default_grams(by_id[sid]))
                     for sid, word in kw.items() if sid not in present and word in text]
        if OIL_ID in by_id and OIL_ID not in present and "oil" in text:
            additions.append(MealIngredient(ingredient_id=OIL_ID,
                                            grams=round(OIL_COOK_PER_SERVING_G * DAYS, 1)))
        if additions:
            meal = meal.model_copy(update={"ingredients": list(meal.ingredients) + additions})
        meals.append(meal)
    return GeneratedPlan(meals=meals)


def _plural(label: str) -> str:
    return label + ("es" if label.endswith(("o", "s", "ch", "sh", "x")) else "s")


GRAMS_PER_OZ = 28.3495
CUP_TBSP = 16    # tablespoons per cup
CUP_TSP = 48     # teaspoons per cup


def _qty(value: float, step: float) -> str:
    """Round to the nearest `step` and render without a trailing .0 (e.g. 1.5, 2, 0.25)."""
    r = round(round(value / step) * step, 2)
    return str(int(r)) if r == int(r) else f"{r:g}"


def format_amount(grams: float, ing: Ingredient) -> str:
    """Human kitchen display of an amount: whole units for countables ("2 bananas",
    "1 scoop"); cups/tbsp/tsp for volume ingredients; otherwise ounces (or pounds)."""
    if ing.unit_grams and ing.unit_grams > 0:                       # countable -> units
        n = grams / ing.unit_grams
        n_disp = int(round(n)) if abs(n - round(n)) < 0.05 else round(n, 1)
        return f"{n_disp} {ing.unit_label if n_disp == 1 else _plural(ing.unit_label)}"
    if ing.grams_per_cup and ing.grams_per_cup > 0:                 # volume -> cups/tbsp/tsp
        cups = grams / ing.grams_per_cup
        if cups >= 0.25:
            q = _qty(cups, 0.25)
            return f"{q} cup" if q == "1" else f"{q} cups"
        if cups * CUP_TBSP >= 1:
            return f"{_qty(cups * CUP_TBSP, 0.5)} tbsp"
        return f"{_qty(max(cups * CUP_TSP, 0.25), 0.25)} tsp"
    oz = grams / GRAMS_PER_OZ                                       # weight -> oz / lb
    if oz >= 16:
        return f"{_qty(oz / 16, 0.1)} lb"
    return f"{_qty(max(oz, 0.5), 0.5)} oz"


def snap_units(generated: GeneratedPlan, by_id: dict[str, Ingredient]) -> GeneratedPlan:
    """Round each countable ingredient to a whole number of units (>=1) and recompute grams,
    so the displayed count, the macros, and the grocery list all agree (no '180.5 g banana').
    Whey is exempt — it's a 'scoop' for display but its amount is precisely gap-sized."""
    meals = []
    for meal in generated.meals:
        ings = []
        for mi in meal.ingredients:
            ing = by_id[mi.ingredient_id]
            if ing.unit_grams and ing.unit_grams > 0 and ing.id != WHEY_ID:
                units = max(1, round(mi.grams / ing.unit_grams))
                ings.append(MealIngredient(ingredient_id=mi.ingredient_id,
                                           grams=round(units * ing.unit_grams, 1)))
            else:
                ings.append(mi)
        meals.append(meal.model_copy(update={"ingredients": ings}))
    return GeneratedPlan(meals=meals)


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


def daily_carbs(generated: GeneratedPlan, by_id: dict[str, Ingredient]) -> float:
    """Average daily carbs for the plan (weekly total / 7)."""
    total = sum(macros_for_meal(meal, by_id).carbs for meal in generated.meals)
    return round(total / DAYS, 1)


# Per-goal acceptance bands and how the deterministic correction (adjust_to_targets) closes a
# gap. Bands are kept STRICT so plans aim at target instead of overshooting into giant portions;
# the remaining gap is distributed into snack occasions, not piled onto one meal.
CUT_CAL_FLOOR = 0.95
MAINTAIN_CAL_BAND = 0.07
BULK_CAL_CEILING = 1.05       # bulking aims at target, only a slim overshoot tolerated
PROTEIN_CEILING = 1.10
CARB_FAT_BAND = 0.15      # ±15% acceptance band for carbs and fat (harder to hit precisely)
BULK_PROTEIN_CEILING = 1.10   # keep protein near target for every goal


@dataclass(frozen=True)
class MacroPolicy:
    cal_min: float
    cal_max: float
    cal_aim: float          # where the calorie lever aims to land
    protein_min: float      # protein floor (the hard target)
    protein_max: float      # correction stops adding protein here
    carb_min: float
    carb_max: float
    carb_aim: float
    fat_min: float
    fat_max: float
    fat_aim: float
    method: str             # "scale" (bulk) | "topup" (cut/maintain)


def _target_status(daily: Macros, policy: MacroPolicy) -> tuple[bool, bool, bool, bool, str]:
    """Whether the plan meets each macro's band/floor, plus a gap note."""
    protein_met = daily.protein >= policy.protein_min
    calories_met = policy.cal_min <= daily.calories <= policy.cal_max
    carbs_met = policy.carb_min <= daily.carbs <= policy.carb_max
    fat_met = policy.fat_min <= daily.fat <= policy.fat_max
    parts = []
    if daily.calories < policy.cal_min:
        parts.append(f"{round(policy.cal_min - daily.calories)} kcal/day below target")
    elif daily.calories > policy.cal_max:
        parts.append(f"{round(daily.calories - policy.cal_max)} kcal/day above target")
    if not protein_met:
        parts.append(f"{round(policy.protein_min - daily.protein)}g/day protein short")
    elif daily.protein > policy.protein_max:
        parts.append(f"{round(daily.protein - policy.protein_max)}g/day protein over target")
    if daily.carbs < policy.carb_min:
        parts.append(f"{round(policy.carb_min - daily.carbs)}g/day carbs short")
    elif daily.carbs > policy.carb_max:
        parts.append(f"{round(daily.carbs - policy.carb_max)}g/day carbs over")
    if daily.fat < policy.fat_min:
        parts.append(f"{round(policy.fat_min - daily.fat)}g/day fat short")
    elif daily.fat > policy.fat_max:
        parts.append(f"{round(daily.fat - policy.fat_max)}g/day fat over")
    return protein_met, calories_met, carbs_met, fat_met, "; ".join(parts)


def macro_policy(goal: Goal, target_cal: float, target_protein: float,
                 target_carbs: float, target_fat: float) -> MacroPolicy:
    # Carbs/fat share a symmetric ±band; calorie/protein bands stay goal-specific.
    cf = dict(
        carb_min=round((1 - CARB_FAT_BAND) * target_carbs, 1),
        carb_max=round((1 + CARB_FAT_BAND) * target_carbs, 1),
        carb_aim=float(target_carbs),
        fat_min=round((1 - CARB_FAT_BAND) * target_fat, 1),
        fat_max=round((1 + CARB_FAT_BAND) * target_fat, 1),
        fat_aim=float(target_fat),
    )
    if goal == Goal.cut:
        return MacroPolicy(
            cal_min=round(CUT_CAL_FLOOR * target_cal, 1), cal_max=float(target_cal),
            cal_aim=float(target_cal), protein_min=float(target_protein),
            protein_max=round(PROTEIN_CEILING * target_protein, 1), method="topup", **cf)
    if goal == Goal.bulk:
        return MacroPolicy(
            cal_min=float(target_cal), cal_max=round(BULK_CAL_CEILING * target_cal, 1),
            cal_aim=float(target_cal), protein_min=float(target_protein),
            protein_max=round(BULK_PROTEIN_CEILING * target_protein, 1), method="scale", **cf)
    return MacroPolicy(   # maintain
        cal_min=round((1 - MAINTAIN_CAL_BAND) * target_cal, 1),
        cal_max=round((1 + MAINTAIN_CAL_BAND) * target_cal, 1),
        cal_aim=float(target_cal), protein_min=float(target_protein),
        protein_max=round(PROTEIN_CEILING * target_protein, 1), method="topup", **cf)


# --- Deterministic goal-aware correction ---
# After the LLM proposes a plan we close the residual macro gap deterministically, since
# the (free-tier) model can't reliably hit numeric targets. Two levers: scale the existing
# non-pantry ingredients (calories) and add a whey-protein shake (protein). Pantry staples
# (seasonings, oil) are never scaled.

WHEY_ID = "whey_protein"
SCOOP_GRAMS = 32.0                  # one scoop of whey (per the catalog product)
PROTEIN_DRINK_NAME = "Daily protein shake"
FMIN, FMAX = 0.6, 1.25             # gentle ±25% scaling; the rest is distributed into snacks
# Whey is a supplement: at most one scoop/day, taken as a daily drink — never mixed into meals.
WHEY_MAX_WEEKLY_G = SCOOP_GRAMS * DAYS

# Distribution policy (all in calories, so it scales with the catalog — no per-ingredient caps):
# a single main-meal serving is capped, and the day's remaining calories are carried by snacks.
MEAL_MAX_KCAL = 800.0      # per-serving calorie cap for one main meal
SNACK_MAX_KCAL = 450.0     # per-serving calorie cap for one snack
CAL_SNACK_MIN = 120.0      # don't bother adding a snack for a gap smaller than this (kcal/day)
MAX_CAL_SNACKS = 2         # at most two calorie snacks on top of the protein shake


def _protein_drink(grams: float) -> Meal:
    """A once-a-day supplemental whey drink (servings=DAYS -> one per day)."""
    scoops_per_day = grams / DAYS / SCOOP_GRAMS
    return Meal(name=PROTEIN_DRINK_NAME, slot="snack", cook_time_minutes=0, servings=DAYS,
                instructions=(f"A supplemental protein drink: about {scoops_per_day:.1f} scoop "
                              "of whey blended with water or milk, once a day."),
                ingredients=[MealIngredient(ingredient_id=WHEY_ID, grams=round(grams, 1))])


GAP_EPS = 2.0   # g/day; ignore trivial macro gaps

# Snack recipes (base per-serving grams). Scaled by CALORIES to carry the day's overflow into
# extra eating occasions instead of oversizing meals. Keyed off macros only, so new catalog foods
# need no code change; the first recipe whose ingredients are all available (and unused) is taken.
_SNACK_RECIPES: tuple[tuple[str, dict[str, float]], ...] = (
    ("Peanut butter banana oats", {"oats": 40.0, "peanut_butter": 16.0, "banana": 60.0}),
    ("Yogurt, granola & berries", {"greek_yogurt": 170.0, "granola": 30.0, "blueberries": 40.0}),
)
SNACK_MIN_MULT, SNACK_MAX_MULT = 0.5, 2.0


def _calorie_snack(target_kcal_per_serving: float, by_id, used_names: set[str]) -> Meal | None:
    """A simple no-cook snack sized BY CALORIES to carry `target_kcal_per_serving`, using the
    first available recipe not already used. Returns None if none fit (e.g. vegan/dairy-free)."""
    for name, base in _SNACK_RECIPES:
        if name in used_names or not all(i in by_id for i in base):
            continue
        base_kcal = sum(by_id[i].kcal_per_100g * g / 100 for i, g in base.items())
        if base_kcal <= 0:
            continue
        mult = min(SNACK_MAX_MULT, max(SNACK_MIN_MULT, target_kcal_per_serving / base_kcal))
        ings = [MealIngredient(ingredient_id=i, grams=round(g * mult * DAYS, 1))
                for i, g in base.items()]
        return Meal(name=name, slot="snack", cook_time_minutes=0, servings=DAYS,
                    instructions="1. Combine the ingredients.\n2. Chill and eat one portion a day.",
                    ingredients=ings)
    return None


def _add_distribution_snacks(meals: list[Meal], by_id, policy: MacroPolicy,
                             budget_cap, inputs) -> list[Meal]:
    """Carry the day's remaining protein and calories in snack occasions instead of oversizing
    meals. First a whey shake closes any protein gap, then up to MAX_CAL_SNACKS calorie snacks
    (each capped per serving) close the calorie gap. Skips anything unaffordable for Plan A."""
    snacks: list[Meal] = []

    def affordable(extra):
        return budget_cap is None or weekly_grocery_cost(
            GeneratedPlan(meals=meals + snacks + [extra]), by_id,
            inputs.owned_ingredient_ids) <= budget_cap

    prot_gap = policy.protein_min - daily_protein_grams(GeneratedPlan(meals=meals), by_id)
    if prot_gap > GAP_EPS and WHEY_ID in by_id:
        whey_g = min(prot_gap * DAYS / (by_id[WHEY_ID].protein_per_100g / 100), WHEY_MAX_WEEKLY_G)
        drink = _protein_drink(whey_g)
        if whey_g >= 1.0 and affordable(drink):
            snacks.append(drink)

    used: set[str] = set()
    for _ in range(MAX_CAL_SNACKS):
        cal_gap = policy.cal_aim - daily_calories(GeneratedPlan(meals=meals + snacks), by_id)
        if cal_gap <= CAL_SNACK_MIN:
            break
        snack = _calorie_snack(min(cal_gap, SNACK_MAX_KCAL), by_id, used)
        if snack is None or not affordable(snack):
            break
        snacks.append(snack)
        used.add(snack.name)
    return meals + snacks


def _scale_meals(meals: list[Meal], f: float, by_id) -> list[Meal]:
    """Scale non-pantry ingredients of non-snack meals by an even factor f. Snacks are left
    as-is, whey is kept out of meals, and a meal left with no ingredients is dropped."""
    out = []
    for meal in meals:
        if meal.slot == "snack":
            out.append(meal)
            continue
        ings = [MealIngredient(
            ingredient_id=mi.ingredient_id,
            grams=mi.grams if by_id[mi.ingredient_id].pantry_staple else round(mi.grams * f, 1),
        ) for mi in meal.ingredients if mi.ingredient_id != WHEY_ID]
        if ings:
            out.append(meal.model_copy(update={"ingredients": ings}))
    return out


def _even_scale_to_aim(meals: list[Meal], by_id, policy: MacroPolicy,
                       budget_cap, inputs, aim_daily_cal: float) -> list[Meal]:
    """Scale every non-snack, non-pantry ingredient by ONE shared factor toward `aim_daily_cal`,
    bounded to ±25% (FMIN/FMAX). Two-way for EVERY goal: an over-target plan is trimmed down and
    an under-target one nudged up — the remaining gap is carried by snacks, not by ballooning a
    single meal. Plan A eases an up-scale back toward 1.0 to stay within budget."""
    scalable_cal = fixed_cal = 0.0
    for meal in meals:
        for mi in meal.ingredients:
            ing = by_id[mi.ingredient_id]
            c = macros_for_grams(ing, mi.grams).calories
            if meal.slot != "snack" and not ing.pantry_staple and ing.id != WHEY_ID:
                scalable_cal += c
            else:
                fixed_cal += c   # pantry + snacks are not scaled
    if scalable_cal <= 0:
        return meals
    f = min(FMAX, max(FMIN, (aim_daily_cal * DAYS - fixed_cal) / scalable_cal))
    scaled = _scale_meals(meals, f, by_id)
    if budget_cap is not None:
        while f > 1.0 and weekly_grocery_cost(GeneratedPlan(meals=scaled), by_id,
                                              inputs.owned_ingredient_ids) > budget_cap:
            f = max(1.0, round(f - 0.05, 3))
            scaled = _scale_meals(meals, f, by_id)
    return scaled


def _trim_overshoot(meals: list[Meal], by_id, policy: MacroPolicy) -> list[Meal]:
    """If the protein shake / snacks pushed daily calories above the goal's ceiling, shave the
    MEALS (never the snacks) back down so the total respects the strict cap. Bounded by FMIN."""
    excess = daily_calories(GeneratedPlan(meals=meals), by_id) - policy.cal_max
    if excess <= 0:
        return meals
    scalable = sum(macros_for_grams(by_id[mi.ingredient_id], mi.grams).calories
                   for m in meals if m.slot != "snack" for mi in m.ingredients
                   if not by_id[mi.ingredient_id].pantry_staple
                   and mi.ingredient_id != WHEY_ID) / DAYS
    if scalable <= 0:
        return meals
    return _scale_meals(meals, max(FMIN, (scalable - excess) / scalable), by_id)


def _clean(plan: GeneratedPlan) -> GeneratedPlan:
    """Drop any non-positive-gram ingredient and any meal left empty, so downstream pricing,
    display, and validation never see a 0 g artifact."""
    meals = []
    for meal in plan.meals:
        ings = [mi for mi in meal.ingredients if mi.grams > 0]
        if ings:
            meals.append(meal.model_copy(update={"ingredients": ings}))
    return GeneratedPlan(meals=meals)


def adjust_to_targets(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    inputs: PlanInputs,
    budget_cap: float | None = None,
) -> GeneratedPlan:
    """Land the plan in the goal's macro bands with REALISTIC portions.

    Strategy: cap how much one meal serving carries (MEAL_MAX_KCAL) and gently scale the meals
    toward that (bounded ±25%, two-way), then carry the day's remaining protein and calories in
    snack occasions rather than oversizing a single meal. budget_cap=None (Plan B) distributes
    fully; a numeric cap (Plan A) eases scaling/snacks back to stay within budget.
    """
    policy = macro_policy(inputs.goal, inputs.target_calories, inputs.target_protein,
                          inputs.target_carbs, inputs.target_fat)
    meals = list(generated.meals)

    # 1) Aim each main meal at a sane per-serving size, not the whole day's calories.
    num_meals = sum(1 for m in meals if m.slot != "snack") or 1
    meal_aim = min(policy.cal_aim, num_meals * MEAL_MAX_KCAL)
    meals = _even_scale_to_aim(meals, by_id, policy, budget_cap, inputs, meal_aim)

    # 2) Distribute the rest of the day's protein/calories into snack occasions.
    meals = _add_distribution_snacks(meals, by_id, policy, budget_cap, inputs)
    # 3) Keep the strict ceiling: if the shake/snacks overshot, shave the meals back.
    meals = _trim_overshoot(meals, by_id, policy)
    return _clean(GeneratedPlan(meals=meals))


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
        servings = DAYS   # every meal is a weekly batch: 7 servings, one per day
        cookable_g = sum(mi.grams for mi in meal.ingredients
                         if not by_id[mi.ingredient_id].pantry_staple)
        pan_loads = 1 if meal.cook_time_minutes == 0 else max(1, math.ceil(cookable_g / PAN_LOAD_G))
        meal_views.append(MealView(
            name=meal.name,
            slot=meal.slot,
            ingredients=[MealIngredientView(
                name=by_id[mi.ingredient_id].name,
                grams=round(mi.grams, 1),
                grams_per_serving=round(mi.grams / servings, 1),
                macros_per_serving=_per_serving(
                    macros_for_grams(by_id[mi.ingredient_id], mi.grams), servings),
                amount_total=format_amount(mi.grams, by_id[mi.ingredient_id]),
                amount_per_serving=format_amount(mi.grams / servings, by_id[mi.ingredient_id]),
            ) for mi in meal.ingredients],
            cook_time_minutes=meal.cook_time_minutes,
            servings=servings,
            instructions=meal.instructions,
            macros_per_serving=_per_serving(m, servings),
            equipment_required=meal_equipment(meal),
            pan_loads=pan_loads,
        ))
        for mi in meal.ingredients:
            grams_by_ing[mi.ingredient_id] = grams_by_ing.get(mi.ingredient_id, 0.0) + mi.grams
        for ing_id in {mi.ingredient_id for mi in meal.ingredients}:
            meals_by_ing[ing_id] = meals_by_ing.get(ing_id, 0) + 1

    meal_views.sort(key=lambda mv: SLOT_ORDER.get(mv.slot, 99))   # breakfast -> snack

    grocery_list: list[GroceryItem] = []
    pantry_list: list[GroceryItem] = []
    owned_list: list[GroceryItem] = []
    for ing_id, grams in grams_by_ing.items():
        if ing_id in owned:
            ing = by_id[ing_id]
            owned_list.append(GroceryItem(
                ingredient_id=ing_id, name=ing.name, grams=round(grams, 1),
                uses_display=format_amount(grams, ing),
                packages=0, package_label=ing.package_label,
                package_price=ing.package_price, cost=0.0,
                pantry_staple=ing.pantry_staple, per_meal_cost=None,
            ))
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
            uses_display=format_amount(grams, ing),
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
    policy = macro_policy(inputs.goal, inputs.target_calories, inputs.target_protein,
                          inputs.target_carbs, inputs.target_fat)
    protein_met, calories_met, carbs_met, fat_met, target_note = _target_status(daily, policy)

    return ComputedPlan(
        meals=meal_views,
        grocery_list=grocery_list,
        pantry_list=pantry_list,
        owned_list=owned_list,
        total_cost=total_cost,
        pantry_total=pantry_total,
        weekly_budget=inputs.weekly_budget,
        within_budget=total_cost <= inputs.weekly_budget,
        daily_macros=daily,
        targets=Targets(calories=inputs.target_calories, protein=inputs.target_protein,
                        carbs=inputs.target_carbs, fat=inputs.target_fat),
        protein_met=protein_met,
        calories_met=calories_met,
        carbs_met=carbs_met,
        fat_met=fat_met,
        target_note=target_note,
    )
