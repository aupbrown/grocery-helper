from google import genai

from app.models import GeneratedPlan, Ingredient, PlanInputs
from app.plan import weekly_grocery_cost

# Gemini 2.5 Flash is on Google's free tier — good for cost-free validation testing.
# Bump this one constant to a newer/larger model later if quality needs it.
MODEL = "gemini-2.5-flash"

# How many times to re-prompt with real cost feedback when a plan is over budget.
MAX_BUDGET_RETRIES = 2


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
        "Treat the weekly grocery budget as a HARD limit: the total cost of all "
        "ingredients (each priced per 100g below) must come in at or under it. When cost "
        "and macros are in tension, favor cheap, calorie- and protein-dense staples "
        "(rice, oats, eggs, beans, lentils, potatoes) over pricier items, and keep protein "
        "near target. Maximize variety; avoid repeating the same meal. Do not invent "
        "ingredients or output any nutrition or price numbers — only ids and gram amounts.\n\n"
        "INGREDIENT CATALOG (id: name, macros, price):\n"
        f"{catalog_block}"
    )


def build_user_prompt(inputs: PlanInputs) -> str:
    owned = ", ".join(inputs.owned_ingredient_ids) or "none"
    avoid = ", ".join(inputs.avoid_allergens) or "none"
    return (
        f"Goal: {inputs.goal.value}\n"
        f"Daily calorie target: {inputs.target_calories} kcal\n"
        f"Daily protein target: {inputs.target_protein} g\n"
        f"Weekly grocery budget: ${inputs.weekly_budget}\n"
        f"Max total cooking time for the week: {inputs.max_cook_minutes} minutes\n"
        f"Dietary pattern: {inputs.dietary_pattern}\n"
        f"Allergens to avoid: {avoid}\n"
        f"Ingredients already owned (still usable, no need to buy): {owned}\n\n"
        "Return the meal plan."
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
    max_retries: int = MAX_BUDGET_RETRIES,
) -> GeneratedPlan:
    # genai.Client() reads the API key from GEMINI_API_KEY (or GOOGLE_API_KEY).
    client = client or genai.Client()
    by_id = {i.id: i for i in catalog}
    valid_ids = set(by_id)

    user_prompt = build_user_prompt(inputs)
    best_plan: GeneratedPlan | None = None
    best_cost: float | None = None

    for _ in range(max_retries + 1):
        plan = _ask(client, catalog, user_prompt)
        # The model only supplies ids + grams; drop anything not in the catalog.
        for meal in plan.meals:
            meal.ingredients = [mi for mi in meal.ingredients if mi.ingredient_id in valid_ids]

        cost = weekly_grocery_cost(plan, by_id, inputs.owned_ingredient_ids)
        if best_cost is None or cost < best_cost:
            best_plan, best_cost = plan, cost

        if cost <= inputs.weekly_budget:
            return plan

        # Over budget: re-prompt with the exact overage so the model can correct
        # using real numbers rather than guessing at cost.
        user_prompt = (
            build_user_prompt(inputs)
            + f"\n\nYour previous plan cost ${cost:.2f} for the week — "
            f"${cost - inputs.weekly_budget:.2f} over the ${inputs.weekly_budget} budget. "
            "Produce a cheaper plan: cut or shrink the most expensive ingredients, lean on "
            "cheap staples (rice, oats, eggs, beans, lentils, potatoes), and keep protein "
            "near target."
        )

    # Never got under budget — return the cheapest attempt; the results page
    # honestly flags it as over budget.
    return best_plan
