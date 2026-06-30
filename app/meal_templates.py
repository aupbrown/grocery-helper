"""Deterministic fallback meal templates.

When Gemini is unavailable or returns garbage, the app still has to produce a real plan. These
cheap, college-friendly, high-protein templates use only catalog ingredients; macros and prices
are computed downstream by app.plan (the templates carry no numbers), and the same goal-aware
correction runs on top. Also reused by repair/substitution and the "need protein" action.
"""
from dataclasses import dataclass, field

from app.models import GeneratedPlan, Meal, MealIngredient, PlanInputs
from app.plan import DAYS
from app.kitchen import available_equipment


@dataclass(frozen=True)
class MealTemplate:
    name: str
    slot: str                       # breakfast | lunch | dinner | snack
    equipment: tuple[str, ...]      # [] means no-cook
    prep_minutes: int
    instructions: str
    per_serving: dict[str, float]   # ingredient_id -> grams for ONE serving
    tags: tuple[str, ...] = field(default_factory=tuple)


# A small library spanning diets (vegan-safe ones use no animal ids) and equipment levels
# (no-cook / microwave / stove / oven), with at least one no-cook or microwave option per slot.
TEMPLATES: list[MealTemplate] = [
    MealTemplate("Protein oatmeal", "breakfast", ("microwave",), 8,
                 "1. Microwave the oats with milk for 2 minutes.\n2. Stir in peanut butter and "
                 "sliced banana.", {"oats": 80, "milk_2pct": 240, "peanut_butter": 16, "banana": 120},
                 ("cheap", "high_protein", "microwave", "bulk")),
    MealTemplate("Greek yogurt bowl", "breakfast", (), 3,
                 "1. Spoon Greek yogurt into a bowl.\n2. Top with granola and blueberries.",
                 {"greek_yogurt": 227, "granola": 40, "blueberries": 40},
                 ("no_cook", "dorm", "high_protein", "cut")),
    MealTemplate("Peanut butter banana overnight oats", "breakfast", (), 3,
                 "1. Stir oats, peanut butter, and mashed banana with a splash of water.\n"
                 "2. Chill overnight and eat cold.",
                 {"oats": 80, "peanut_butter": 24, "banana": 120},
                 ("no_cook", "cheap", "vegan", "dorm")),
    MealTemplate("Egg & cheese scramble", "breakfast", ("stove",), 10,
                 "1. Scramble the eggs in a nonstick pan on the stove.\n2. Add cheddar and "
                 "spinach until melted.", {"eggs": 150, "cheddar": 28, "spinach": 30},
                 ("high_protein", "cheap")),
    MealTemplate("Tuna sandwich", "lunch", (), 5,
                 "1. Mix the tuna with a squeeze of lemon.\n2. Layer with spinach between bread.",
                 {"bread_ww": 56, "canned_tuna": 142, "spinach": 20, "lemon": 10},
                 ("no_cook", "cheap", "high_protein")),
    MealTemplate("Microwave potato & cheddar", "lunch", ("microwave",), 8,
                 "1. Microwave the potato until soft, about 6 minutes.\n2. Top with cheddar and "
                 "steamed broccoli.", {"potato": 250, "cheddar": 40, "broccoli": 80},
                 ("microwave", "dorm", "cheap")),
    MealTemplate("Burrito rice bowl", "dinner", ("microwave",), 8,
                 "1. Microwave the rice and black beans together.\n2. Top with cheddar, spinach, "
                 "and hot sauce.", {"rice_white": 200, "black_beans": 250, "cheddar": 40,
                 "spinach": 20, "hot_sauce": 10}, ("microwave", "dorm", "vegetarian", "cheap")),
    MealTemplate("Chicken, rice & broccoli", "dinner", ("stove",), 25,
                 "1. Sear the chicken in olive oil with garlic and salt on the stove.\n"
                 "2. Serve over rice with steamed broccoli.",
                 {"chicken_breast": 170, "rice_white": 200, "broccoli": 100, "olive_oil": 8,
                  "garlic": 5, "salt": 2}, ("meal_prep", "high_protein")),
    MealTemplate("Sheet-pan chicken & potato", "dinner", ("oven",), 40,
                 "1. Toss chicken, potato, and broccoli with olive oil, herbs, and salt.\n"
                 "2. Roast on a sheet pan at 425F for 30 minutes.",
                 {"chicken_breast": 170, "potato": 200, "broccoli": 100, "olive_oil": 10,
                  "mixed_herbs": 2, "salt": 2}, ("meal_prep", "oven", "high_protein", "bulk")),
    MealTemplate("Pasta & ground beef", "dinner", ("stove",), 25,
                 "1. Brown the ground beef with onion and garlic on the stove.\n"
                 "2. Toss with cooked pasta and herbs.",
                 {"pasta_ww": 140, "ground_beef_90": 113, "onion": 40, "garlic": 5,
                  "mixed_herbs": 2}, ("high_protein", "meal_prep")),
    MealTemplate("Lentil & rice bowl", "dinner", ("stove",), 25,
                 "1. Simmer the lentils until tender on the stove.\n2. Serve over rice with "
                 "spinach, garlic, olive oil, and salt.",
                 {"lentils": 100, "rice_white": 150, "spinach": 30, "olive_oil": 8, "garlic": 5,
                  "salt": 2}, ("vegan", "cheap", "high_protein")),
    MealTemplate("Tofu soy rice bowl", "dinner", ("stove",), 20,
                 "1. Pan-fry the tofu with garlic and soy sauce on the stove.\n"
                 "2. Serve over rice with broccoli.",
                 {"tofu": 150, "rice_white": 200, "broccoli": 100, "soy_sauce": 10, "garlic": 5},
                 ("vegan", "high_protein")),
]


def _to_meal(t: MealTemplate) -> Meal:
    """Instantiate a template as a whole-week batch (per-serving grams x 7 days)."""
    return Meal(name=t.name, slot=t.slot, cook_time_minutes=t.prep_minutes, servings=DAYS,
                instructions=t.instructions, equipment_required=list(t.equipment),
                ingredients=[MealIngredient(ingredient_id=iid, grams=round(g * DAYS, 1))
                             for iid, g in t.per_serving.items()])


def usable_templates(inputs: PlanInputs, catalog) -> list[MealTemplate]:
    """Templates whose ingredients are all in the (already diet/allergen-filtered) catalog and
    whose equipment the kitchen has, ordered to honor no-cook / time-saving preferences."""
    by_id = {i.id for i in catalog}
    avail = available_equipment(inputs.kitchen)
    cands = [t for t in TEMPLATES
             if set(t.per_serving) <= by_id and set(t.equipment) <= avail]
    prefer_simple = inputs.kitchen.no_cook_preferred or inputs.kitchen.prioritize_time
    cands.sort(key=lambda t: ((1 if t.equipment else 0) if prefer_simple else 0,
                              len(t.equipment), t.prep_minutes, t.name))
    return cands


def fallback_plan(inputs: PlanInputs, catalog) -> GeneratedPlan:
    """A deterministic plan from templates when generation fails. One meal per slot where
    possible, padded to at least three meals; the usual macro correction runs afterward."""
    cands = usable_templates(inputs, catalog)
    used: set[str] = set()
    meals: list[Meal] = []
    for slot in ("breakfast", "lunch", "dinner"):
        for t in cands:
            if t.slot == slot and t.name not in used:
                meals.append(_to_meal(t))
                used.add(t.name)
                break
    for t in cands:                       # pad thin kitchens up to 3 meals from any slot
        if len(meals) >= 3:
            break
        if t.name not in used:
            meals.append(_to_meal(t))
            used.add(t.name)
    return GeneratedPlan(meals=meals)
