"""Deterministic plan validation + Budget Guarantee metrics.

Gemini proposes meals; this module (with app.plan) is the source of truth for whether a plan
actually meets the user's budget and macro constraints. A plan is shown as "valid" only after
validate_plan() confirms it. Every number here is computed from the fixed catalog, never taken
from the model.
"""
from pydantic import BaseModel

from app.models import GeneratedPlan, Ingredient, KitchenProfile, Macros, PlanInputs, Targets
from app.plan import (
    DAYS, _grams_by_ingredient, _target_status, macro_policy, macros_for_meal,
    package_cost, pantry_cost, weekly_grocery_cost,
)
from app.kitchen import (
    EQUIPMENT_LABELS, freezer_warning, missing_equipment, recommend_sessions,
)

BUDGET_EPS = 0.005   # sub-cent slack so rounding never reads as "over budget"


class MacroSummary(BaseModel):
    daily: Macros
    targets: Targets
    protein_met: bool
    calories_met: bool
    carbs_met: bool
    fat_met: bool


class PlanValidation(BaseModel):
    is_valid: bool
    severity: str                      # "valid" | "warning" | "invalid"
    total_cost: float
    weekly_budget: float
    within_budget: bool
    budget_remaining: float            # budget - cost (negative when over)
    over_budget_by: float              # max(0, cost - budget)
    cost_per_day: float
    cost_per_meal: float
    cost_per_g_protein: float | None   # weekly grocery cost / weekly protein grams
    pantry_savings: float              # value of owned items used this week
    pantry_total: float                # one-time staple stock-up cost
    total_prep_minutes: int
    prep_sessions: int                 # recommended number of batch-cook sessions
    largest_session_minutes: int       # longest single session at that split
    macro: MacroSummary
    warnings: list[str]
    errors: list[str]
    suggested_fixes: list[str]


def _daily_macros(generated: GeneratedPlan, by_id: dict[str, Ingredient]) -> Macros:
    """Average daily macros, summed the same way compute_plan does so the two agree."""
    cals = prot = carb = fat = 0.0
    for meal in generated.meals:
        m = macros_for_meal(meal, by_id)
        cals += m.calories
        prot += m.protein
        carb += m.carbs
        fat += m.fat
    return Macros(calories=round(cals / DAYS, 1), protein=round(prot / DAYS, 1),
                  carbs=round(carb / DAYS, 1), fat=round(fat / DAYS, 1))


def _owned_savings(generated: GeneratedPlan, by_id, owned_ids) -> float:
    """Whole-package value of owned ingredients the plan uses — i.e. money not spent."""
    owned = set(owned_ids)
    total = sum(package_cost(grams, by_id[ing_id])[1]
                for ing_id, grams in _grams_by_ingredient(generated).items() if ing_id in owned)
    return round(total, 2)


def _dedup(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def validate_plan(generated: GeneratedPlan, by_id: dict[str, Ingredient],
                  inputs: PlanInputs, kitchen: KitchenProfile | None = None) -> PlanValidation:
    """Confirm a (corrected) plan against budget, macro, equipment, time, and coherence limits.

    Severity: 'invalid' if any hard violation (over budget, protein below the goal's floor, a
    non-positive quantity, or a meal needing equipment the kitchen lacks); 'warning' if only the
    softer calorie/carb/fat bands or time limits are missed; else 'valid'. The macro bands and
    protein floor are the goal-aware ones from app.plan.macro_policy.
    """
    kit = kitchen or inputs.kitchen
    owned_ids = inputs.owned_ingredient_ids
    budget = float(inputs.weekly_budget)
    total_cost = weekly_grocery_cost(generated, by_id, owned_ids)

    within_budget = total_cost <= budget + BUDGET_EPS
    over_budget_by = round(max(0.0, total_cost - budget), 2)

    occasions = len(generated.meals) * DAYS
    cost_per_meal = round(total_cost / occasions, 2) if occasions else 0.0

    daily = _daily_macros(generated, by_id)
    weekly_protein = daily.protein * DAYS
    cost_per_g_protein = round(total_cost / weekly_protein, 3) if weekly_protein > 0 else None

    policy = macro_policy(inputs.goal, inputs.target_calories, inputs.target_protein,
                          inputs.target_carbs, inputs.target_fat)
    protein_met, calories_met, carbs_met, fat_met, _ = _target_status(daily, policy)

    warnings: list[str] = []
    errors: list[str] = []
    fixes: list[str] = []

    for meal in generated.meals:
        for mi in meal.ingredients:
            if mi.grams <= 0:
                errors.append(f"{by_id[mi.ingredient_id].name} has a non-positive amount.")
    if not within_budget:
        errors.append(f"Estimated ${total_cost:.2f} is ${over_budget_by:.2f} over your "
                      f"${budget:.0f} budget.")
        fixes += ["Make cheaper", "Use more pantry items", "Lower calorie target slightly",
                  "Increase budget"]
    if not protein_met:
        short = round(policy.protein_min - daily.protein)
        errors.append(f"Protein is {short} g/day short of your "
                      f"{round(inputs.target_protein)} g target.")
        fixes += ["Add a protein top-up", "Increase budget"]
    if not calories_met:
        warnings.append("Daily calories fall outside your goal's target band.")
    if not carbs_met:
        warnings.append("Daily carbs fall outside the target band.")
    if not fat_met:
        warnings.append("Daily fat falls outside the target band.")

    # Equipment: a meal needing gear the kitchen lacks is a hard violation.
    for meal in generated.meals:
        miss = missing_equipment(meal, kit)
        if miss:
            labels = ", ".join(EQUIPMENT_LABELS.get(e, e) for e in miss)
            errors.append(f"“{meal.name}” needs {labels}, which your kitchen lacks.")
            fixes.append("Swap to a no-cook or microwave-friendly meal")

    # Time: enforce the weekly budget and the single-session cap; recommend how to split sessions.
    total_prep = sum(int(m.cook_time_minutes) for m in generated.meals)
    cook_times = [int(m.cook_time_minutes) for m in generated.meals if m.cook_time_minutes > 0]
    prep_sessions, bins = recommend_sessions(cook_times, kit.max_single_session_minutes,
                                             kit.preferred_prep_sessions)
    largest_session = max(bins) if bins else 0
    if total_prep > inputs.max_cook_minutes:
        warnings.append(f"Cooking runs about {total_prep} min/week, over your "
                        f"{inputs.max_cook_minutes}-min limit.")
        fixes.append("Use faster, batch-friendly meals")
    if largest_session > kit.max_single_session_minutes:
        warnings.append(f"The longest cooking session is about {largest_session} min, over your "
                        f"{kit.max_single_session_minutes}-min single-session limit.")

    # Storage: warn a no-freezer kitchen about bulk fresh meat.
    packages_by_id = {iid: package_cost(g, by_id[iid])[0]
                      for iid, g in _grams_by_ingredient(generated).items()}
    fw = freezer_warning(packages_by_id, kit)
    if fw:
        warnings.append(fw)

    severity = "invalid" if errors else ("warning" if warnings else "valid")
    return PlanValidation(
        is_valid=(severity != "invalid"),
        severity=severity,
        total_cost=total_cost,
        weekly_budget=budget,
        within_budget=within_budget,
        budget_remaining=round(budget - total_cost, 2),
        over_budget_by=over_budget_by,
        cost_per_day=round(total_cost / DAYS, 2),
        cost_per_meal=cost_per_meal,
        cost_per_g_protein=cost_per_g_protein,
        pantry_savings=_owned_savings(generated, by_id, owned_ids),
        pantry_total=pantry_cost(generated, by_id, owned_ids),
        total_prep_minutes=total_prep,
        prep_sessions=prep_sessions,
        largest_session_minutes=largest_session,
        macro=MacroSummary(
            daily=daily,
            targets=Targets(calories=inputs.target_calories, protein=inputs.target_protein,
                            carbs=inputs.target_carbs, fat=inputs.target_fat),
            protein_met=protein_met, calories_met=calories_met,
            carbs_met=carbs_met, fat_met=fat_met),
        warnings=warnings,
        errors=errors,
        suggested_fixes=_dedup(fixes),
    )
