from google import genai

from app.models import GeneratedPlan, Ingredient, PlanInputs
from app.plan import weekly_grocery_cost, daily_protein_grams, daily_calories

# Free-tier model. Flash-Lite has high daily request limits, so we use it to iterate on
# non-quality features without burning the ~20 RPD cap on more capable free models.
# For a meal-QUALITY run, bump this one constant (e.g. "gemini-3-flash-preview") and the
# matching assertion in tests/test_generator.py.
MODEL = "gemini-3.1-flash-lite"

# How many times to re-prompt with real cost/protein feedback before giving up.
MAX_RETRIES = 2

# Protein-mode accepts protein in [target, target * PROTEIN_CEILING]. Overshooting
# protein wastes budget on a tight grocery plan, so we treat the target as a band,
# not a floor, and reinvest the savings into variety and seasoning.
PROTEIN_CEILING = 1.10

BUDGET = "budget"
PROTEIN = "protein"


def build_system_prompt(catalog: list[Ingredient]) -> str:
    lines = [
        f"- {i.id}: {i.name} ({i.category}; "
        f"{i.kcal_per_100g} kcal, {i.protein_per_100g}g protein; "
        f"sold as {i.package_label} for ${i.package_price:.2f} ≈ ${i.price_per_100g:.2f}/100g"
        f"{'; PANTRY STAPLE' if i.pantry_staple else ''})"
        for i in catalog
    ]
    catalog_block = "\n".join(lines)
    return (
        "You are a meal planner for college students on a budget. "
        "Compose a week of meals using ONLY the ingredients in the catalog below, "
        "referencing each by its exact id. Express every quantity in grams. "
        "Produce 3 to 5 distinct meals (meal-prep style) that together cover the week. "
        "Hit the daily calorie and protein targets across the week CLOSELY (within about "
        "10%) — do NOT greatly exceed them; overshooting protein wastes a tight budget. "
        "When protein is comfortably met, spend any remaining room on variety and flavor, "
        "not more protein. Use realistic per-serving portions (roughly 150-250g cooked "
        "protein per serving, sensible grain and vegetable amounts) and respect the "
        "per-week cooking-time limit. The request states a PRIORITY telling you whether "
        "budget or protein is the hard constraint — honor it. For cheap protein, lean on "
        "eggs, beans, lentils, canned tuna, and Greek yogurt; for cheap calories, on rice, "
        "oats, and potatoes. Do NOT put whey protein powder into meals — a single daily "
        "protein shake is added separately as a supplement.\n\n"
        "HOW PRICING WORKS: every ingredient is sold ONLY as a whole package (shown below), "
        "so buying any amount costs at least one full package — using 30g of peanut butter "
        "still costs a whole jar. So prefer fewer distinct pricey packages and reuse what "
        "you introduce across several meals rather than buying many items for a single dab. "
        "Items marked PANTRY STAPLE (seasonings and oil) last for months and are NOT counted "
        "against the weekly budget, so season every meal freely.\n\n"
        "MAKE THE FOOD SOUND GOOD: season every meal using the seasoning ingredients "
        "(salt, black pepper, garlic, onion, soy sauce, hot sauce, mixed herbs, lemon), "
        "give each meal an appealing, specific name, and write a brief, appetizing "
        "instruction describing how to cook it and what it tastes like. Avoid bland, "
        "repetitive plain-ingredient combos.\n\n"
        "Do not invent ingredients or output any nutrition or price numbers — only ids and "
        "gram amounts.\n\n"
        "INGREDIENT CATALOG (id: name, macros, package price):\n"
        f"{catalog_block}"
    )


def build_user_prompt(inputs: PlanInputs, priority: str = BUDGET) -> str:
    owned = ", ".join(inputs.owned_ingredient_ids) or "none"
    avoid = ", ".join(inputs.avoid_allergens) or "none"
    if priority == PROTEIN:
        directive = (
            "PRIORITY: hit the daily protein target as closely as possible without large "
            "overshoot (aim for the target, not far above it). Spend as little as possible "
            "while meeting it — it is OK to go slightly over the weekly budget if necessary, "
            "but stay as close to the budget as you can."
        )
    else:
        directive = (
            "PRIORITY: keep total grocery cost at or under the weekly budget. If you cannot "
            "also hit the protein target within budget, get protein as close as the budget allows."
        )
    return (
        f"Goal: {inputs.goal.value}\n"
        f"Daily calorie target: {inputs.target_calories} kcal\n"
        f"Daily protein target: {inputs.target_protein} g\n"
        f"The plan is ALL the food for the 7-day week, so every meal and serving together "
        f"should total about {inputs.target_calories * 7:.0f} kcal and "
        f"{inputs.target_protein * 7:.0f} g protein for the week.\n"
        f"Weekly grocery budget: ${inputs.weekly_budget}\n"
        f"Max total cooking time for the week: {inputs.max_cook_minutes} minutes\n"
        f"Dietary pattern: {inputs.dietary_pattern}\n"
        f"Allergens to avoid: {avoid}\n"
        f"Ingredients already owned (still usable, no need to buy): {owned}\n"
        f"{directive}\n\n"
        "Return the meal plan."
    )


def _over_budget_feedback(inputs: PlanInputs, cost: float) -> str:
    return (
        f"\n\nYour previous plan cost ${cost:.2f} for the week — "
        f"${cost - inputs.weekly_budget:.2f} over the ${inputs.weekly_budget} budget. "
        "Produce a cheaper plan: cut or shrink the most expensive ingredients, lean on cheap "
        "staples (rice, oats, eggs, beans, lentils, potatoes), and keep protein near target."
    )


def _protein_short_feedback(inputs: PlanInputs, protein: float) -> str:
    return (
        f"\n\nYour previous plan provided {protein:.0f}g protein/day — "
        f"{inputs.target_protein - protein:.0f}g short of the {inputs.target_protein}g target. "
        "Add more protein at the lowest cost: lean on whey protein, eggs, chicken, Greek "
        "yogurt, beans/lentils."
    )


def _too_much_protein_feedback(inputs: PlanInputs, protein: float) -> str:
    return (
        f"\n\nYour previous plan provided {protein:.0f}g protein/day, overshooting the "
        f"{inputs.target_protein}g target by {protein - inputs.target_protein:.0f}g — that "
        "wastes money on a budget. Dial protein down toward the target and spend the freed "
        "budget on more variety and seasoning instead."
    )


def _trim_cost_feedback(inputs: PlanInputs, cost: float, protein: float) -> str:
    return (
        f"\n\nYour previous plan hit protein ({protein:.0f}g/day) but cost ${cost:.2f} — "
        f"${cost - inputs.weekly_budget:.2f} over the ${inputs.weekly_budget} budget. "
        f"Bring the cost down toward the budget while keeping protein near "
        f"{inputs.target_protein}g/day: swap pricey items for cheaper protein (whey, eggs, "
        "beans, lentils) and trim portions of expensive ingredients."
    )


def _macro_status_feedback(inputs: PlanInputs, cal: float, protein: float) -> str:
    """Report the measured daily calories/protein so the retry can correct both."""
    return (
        f"\n\nMeasured: about {cal:.0f} kcal/day and {protein:.0f}g protein/day "
        f"(targets: {inputs.target_calories} kcal, {inputs.target_protein}g). "
        "Adjust portions to move both toward target."
    )


def _dropped_feedback(dropped_ids: list[str]) -> str:
    ids = ", ".join(sorted(set(dropped_ids)))
    return (
        f"\n\nThese ingredient ids are not in the catalog and were removed: {ids}. "
        "Replace them with valid catalog ids so that protein and calories are not lost."
    )


def _ask(client, catalog: list[Ingredient], user_prompt: str) -> GeneratedPlan:
    response = client.models.generate_content(
        model=MODEL,
        contents=user_prompt,
        config={
            "system_instruction": build_system_prompt(catalog),
            "response_mime_type": "application/json",
            "response_schema": GeneratedPlan,
        },
    )
    # With a Pydantic response_schema the SDK populates `.parsed`; fall back to
    # parsing the raw JSON text if it doesn't.
    plan = response.parsed
    if plan is None:
        plan = GeneratedPlan.model_validate_json(response.text)
    return plan


def generate(
    inputs: PlanInputs,
    catalog: list[Ingredient],
    client=None,
    priority: str = BUDGET,
    max_retries: int = MAX_RETRIES,
) -> GeneratedPlan:
    """Generate a weekly plan from the catalog.

    priority="budget":  budget is the hard cap; protein may fall short.
    priority="protein": land protein inside [target, target*PROTEIN_CEILING] at the lowest
                        cost (may run slightly over budget); do not overshoot protein.

    A ground-truth retry loop checks the real cost/protein after each attempt and re-prompts
    with the exact gap, keeping the best attempt:
      - budget mode: any plan under budget wins; otherwise the cheapest.
      - protein mode: in-band plans win (ranked by lowest cost); else the closest to the
        band — overshoots ranked by lowest protein, shortfalls by highest protein.
    """
    # genai.Client() reads the API key from GEMINI_API_KEY (or GOOGLE_API_KEY).
    client = client or genai.Client()
    by_id = {i.id: i for i in catalog}
    valid_ids = set(by_id)

    user_prompt = build_user_prompt(inputs, priority)
    best_plan: GeneratedPlan | None = None
    best_score: tuple[int, float] | None = None

    for _ in range(max_retries + 1):
        plan = _ask(client, catalog, user_prompt)
        # The model only supplies ids + grams; drop anything not in the catalog.
        dropped_ids: list[str] = []
        for meal in plan.meals:
            dropped_ids += [mi.ingredient_id for mi in meal.ingredients
                            if mi.ingredient_id not in valid_ids]
            meal.ingredients = [mi for mi in meal.ingredients if mi.ingredient_id in valid_ids]

        cost = weekly_grocery_cost(plan, by_id, inputs.owned_ingredient_ids)
        protein = daily_protein_grams(plan, by_id)
        calories = daily_calories(plan, by_id)
        under_budget = cost <= inputs.weekly_budget

        if priority == PROTEIN:
            target = inputs.target_protein
            in_band = target <= protein <= target * PROTEIN_CEILING
            if in_band:
                score = (0, cost)        # in band: minimize cost
            elif protein > target:
                score = (1, protein)     # overshoot: prefer lower (closer to band)
            else:
                score = (2, -protein)    # shortfall: prefer higher (closer to target)
            ideal = in_band and under_budget
        else:
            score = (0, cost) if under_budget else (1, cost)
            ideal = under_budget

        if best_score is None or score < best_score:
            best_plan, best_score = plan, score
        if ideal:
            return plan

        if priority == PROTEIN:
            if protein < inputs.target_protein:
                fb = _protein_short_feedback(inputs, protein)
            elif protein > inputs.target_protein * PROTEIN_CEILING:
                fb = _too_much_protein_feedback(inputs, protein)
            else:  # in band but over budget
                fb = _trim_cost_feedback(inputs, cost, protein)
        else:
            fb = _over_budget_feedback(inputs, cost)
        fb += _macro_status_feedback(inputs, calories, protein)
        if dropped_ids:
            fb += _dropped_feedback(dropped_ids)
        user_prompt = build_user_prompt(inputs, priority) + fb

    # Target never perfectly met — return the best attempt; the results page honestly
    # reports cost-vs-budget and protein-vs-target either way.
    return best_plan
