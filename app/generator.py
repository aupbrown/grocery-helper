from google import genai

from app.models import GeneratedPlan, Ingredient, PlanInputs
from app.plan import weekly_grocery_cost, daily_protein_grams

# Gemini 2.5 Flash is on Google's free tier — good for cost-free validation testing.
# Bump this one constant to a newer/larger model later if quality needs it.
MODEL = "gemini-2.5-flash"

# How many times to re-prompt with real cost/protein feedback before giving up.
MAX_RETRIES = 2

BUDGET = "budget"
PROTEIN = "protein"


def build_system_prompt(catalog: list[Ingredient]) -> str:
    lines = [
        f"- {i.id}: {i.name} ({i.category}; "
        f"{i.kcal_per_100g} kcal, {i.protein_per_100g}g protein, "
        f"${i.price_per_100g:.2f} per 100g)"
        for i in catalog
    ]
    catalog_block = "\n".join(lines)
    return (
        "You are a meal planner for college students on a budget. "
        "Compose a week of meals using ONLY the ingredients in the catalog below, "
        "referencing each by its exact id. Express every quantity in grams. "
        "Produce 3 to 5 distinct meals (meal-prep style) that together cover the week. "
        "Aim for the daily calorie and protein targets across the week "
        "(week total ~= 7x the daily target) and respect the per-week cooking-time limit. "
        "Each ingredient's price per 100g is shown below, so you can plan to a budget. "
        "The request states a PRIORITY telling you whether budget or protein is the hard "
        "constraint — honor it. When you need cheap protein, lean on protein-dense, "
        "low-cost options (whey protein, eggs, beans, lentils, Greek yogurt) and cheap "
        "calorie staples (rice, oats, potatoes). Maximize variety; avoid repeating the same "
        "meal. Do not invent ingredients or output any nutrition or price numbers — only "
        "ids and gram amounts.\n\n"
        "INGREDIENT CATALOG (id: name, macros, price):\n"
        f"{catalog_block}"
    )


def build_user_prompt(inputs: PlanInputs, priority: str = BUDGET) -> str:
    owned = ", ".join(inputs.owned_ingredient_ids) or "none"
    avoid = ", ".join(inputs.avoid_allergens) or "none"
    if priority == PROTEIN:
        directive = (
            "PRIORITY: hit the daily protein target. Spend as little as possible while "
            "meeting it — it is OK to go slightly over the weekly budget if that is the only "
            "way to reach the protein target, but stay as close to the budget as you can."
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


def _trim_cost_feedback(inputs: PlanInputs, cost: float, protein: float) -> str:
    return (
        f"\n\nYour previous plan hit protein ({protein:.0f}g/day) but cost ${cost:.2f} — "
        f"${cost - inputs.weekly_budget:.2f} over the ${inputs.weekly_budget} budget. "
        f"Bring the cost down toward the budget while keeping protein at or above "
        f"{inputs.target_protein}g/day: swap pricey items for cheaper protein (whey, eggs, "
        "beans, lentils) and trim portions of expensive ingredients."
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
    priority="protein": hit the protein target, then minimize cost (may run over budget).

    A ground-truth retry loop checks the real cost/protein after each attempt and
    re-prompts with the exact gap. Plans are ranked so the best attempt is kept:
      - budget mode: any plan under budget wins; otherwise the cheapest.
      - protein mode: protein-hitting plans win, ranked by lowest cost; if none hit,
        the highest-protein attempt is kept.
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
        for meal in plan.meals:
            meal.ingredients = [mi for mi in meal.ingredients if mi.ingredient_id in valid_ids]

        cost = weekly_grocery_cost(plan, by_id, inputs.owned_ingredient_ids)
        protein = daily_protein_grams(plan, by_id)
        under_budget = cost <= inputs.weekly_budget
        hits_protein = protein >= inputs.target_protein

        if priority == PROTEIN:
            # Tier 0 = hits protein (ranked by lowest cost); tier 1 = misses (ranked
            # by highest protein). Lower tuple is better.
            score = (0, cost) if hits_protein else (1, -protein)
            ideal = hits_protein and under_budget
        else:
            score = (0, cost) if under_budget else (1, cost)
            ideal = under_budget

        if best_score is None or score < best_score:
            best_plan, best_score = plan, score
        if ideal:
            return plan

        if priority == PROTEIN:
            user_prompt = build_user_prompt(inputs, priority) + (
                _protein_short_feedback(inputs, protein) if not hits_protein
                else _trim_cost_feedback(inputs, cost, protein)
            )
        else:
            user_prompt = build_user_prompt(inputs, priority) + _over_budget_feedback(inputs, cost)

    # Target never perfectly met — return the best attempt; the results page honestly
    # reports cost-vs-budget and protein-vs-target either way.
    return best_plan
