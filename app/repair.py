"""Saved-plan repair (A15): explain what broke in a saved week, then fix only that.

A saved week can drift out of validity: the kitchen profile changes, or owned ingredients
leave the pantry (prices are a fixed catalog, so cost drift isn't modeled). `meal_issues`
names each problem meal with a plain-language reason; `repair` regenerates exactly those
slots (everything valid stays pinned) and re-finalizes under the stored budget.
"""
from app import jobs
from app.catalog import catalog_by_id, filter_catalog
from app.kitchen import EQUIPMENT_LABELS, available_equipment
from app.models import (
    ComputedPlan, GeneratedPlan, Ingredient, Meal, MealIngredient, PlanInputs,
)
from app.plan import PROTEIN_DRINK_NAME


def _lost_owned_names(inputs: PlanInputs, pantry_keys: set[str],
                      by_id: dict[str, Ingredient]) -> set[str]:
    """Display names of owned ingredients the plan counted on that left the pantry."""
    return {by_id[iid].name for iid in inputs.owned_ingredient_ids
            if iid in by_id and not by_id[iid].pantry_staple and iid not in pantry_keys}


def meal_issues(rec: dict, pantry_keys: set[str],
                by_id: dict[str, Ingredient]) -> dict[str, str]:
    """Meal name -> plain-language reason it needs fixing (empty when the week is fine)."""
    inputs = PlanInputs.model_validate(rec["inputs"])
    snapshot = ComputedPlan.model_validate(rec["snapshot"])
    avail = available_equipment(inputs.kitchen)
    lost = _lost_owned_names(inputs, pantry_keys, by_id)
    issues: dict[str, str] = {}
    for mv in snapshot.meals:
        missing = sorted(set(mv.equipment_required) - avail)
        if missing:
            labels = ", ".join(EQUIPMENT_LABELS.get(e, e) for e in missing)
            issues[mv.name] = f"needs a {labels} your kitchen doesn't have"
            continue
        used_lost = sorted(ing.name for ing in mv.ingredients if ing.name in lost)
        if used_lost:
            issues[mv.name] = f"uses {used_lost[0].lower()} that left your pantry"
    return issues


def _base_from_snapshot(snapshot: ComputedPlan,
                        by_id: dict[str, Ingredient]) -> GeneratedPlan:
    """Rebuild a GeneratedPlan from the stored view. The whey top-up is skipped — the
    finalize pass re-adds it as needed, so keeping it would double it."""
    name_to_id = {i.name: i.id for i in by_id.values()}
    meals = []
    for mv in snapshot.meals:
        if mv.name == PROTEIN_DRINK_NAME:
            continue
        ingredients = [MealIngredient(ingredient_id=name_to_id[ing.name], grams=ing.grams)
                       for ing in mv.ingredients if ing.name in name_to_id]
        if not ingredients:
            continue
        meals.append(Meal(name=mv.name, slot=mv.slot, ingredients=ingredients,
                          cook_time_minutes=mv.cook_time_minutes, servings=mv.servings,
                          instructions=mv.instructions,
                          equipment_required=mv.equipment_required))
    return GeneratedPlan(meals=meals)


def repair(rec: dict, catalog: list[Ingredient], pantry_keys: set[str]):
    """Regenerate every flagged meal (others pinned) and re-finalize under the stored budget.

    Returns (ComputedPlan, PlanValidation, PlanInputs) — the refreshed snapshot, its
    validation, and the inputs (with ownership synced to the current pantry) to store back.
    """
    by_id = catalog_by_id(catalog)
    issues = meal_issues(rec, pantry_keys, by_id)
    inputs = PlanInputs.model_validate(rec["inputs"])
    # Ownership reflects the pantry as it is now, not as it was at save time.
    inputs.owned_ingredient_ids = [iid for iid in inputs.owned_ingredient_ids
                                   if iid in pantry_keys]
    inputs.owned_grams = {iid: g for iid, g in inputs.owned_grams.items()
                          if iid in pantry_keys}
    snapshot = ComputedPlan.model_validate(rec["snapshot"])
    base = _base_from_snapshot(snapshot, by_id)
    filtered = filter_catalog(catalog, inputs.dietary_pattern, inputs.avoid_allergens)
    kept, meals = [], []
    for meal in base.meals:
        if meal.name in issues:
            fresh = jobs.generate_one(inputs, filtered, meal.slot, priority="budget",
                                      avoid_name=meal.name)
            meals.append(fresh)
        else:
            kept.append(meal)
    new_base = GeneratedPlan(meals=kept + meals)
    filtered_by_id = catalog_by_id(filtered)
    computed, validation = jobs.finalize(new_base, inputs, filtered_by_id,
                                         inputs.weekly_budget)
    return computed, validation, inputs
