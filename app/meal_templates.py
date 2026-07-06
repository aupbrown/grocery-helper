"""Deterministic fallback meal plans, built from the curated recipe library.

When Gemini is unavailable or returns garbage, the app still has to produce a real plan. The
vetted recipes in app.recipes (data/recipes.json) use only catalog ingredients; macros and prices
are computed downstream by app.plan (the recipes carry no numbers), and the same goal-aware
correction runs on top. Also reused by repair/substitution and the "need protein" action.
"""
from app.models import GeneratedPlan, Meal, MealIngredient, PlanInputs
from app.plan import DAYS
from app.kitchen import available_equipment
from app.recipes import Recipe, RECIPES

# The fallback pulls from the same hand-vetted library that seeds the Gemini few-shot examples,
# so the offline plan and the AI plan share one quality bar.
TEMPLATES: list[Recipe] = RECIPES


def _to_meal(t: Recipe) -> Meal:
    """Instantiate a recipe as a whole-week batch (per-serving grams x 7 days)."""
    return Meal(name=t.name, slot=t.slot, cook_time_minutes=t.prep_minutes, servings=DAYS,
                instructions=t.instructions, equipment_required=list(t.equipment),
                ingredients=[MealIngredient(ingredient_id=iid, grams=round(g * DAYS, 1))
                             for iid, g in t.per_serving.items()])


def usable_templates(inputs: PlanInputs, catalog) -> list[Recipe]:
    """Recipes whose ingredients are all in the (already diet/allergen-filtered) catalog and
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
    """A deterministic plan from the library when generation fails. One meal per slot where
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
