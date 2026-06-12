from google import genai

from app.models import GeneratedPlan, Ingredient, PlanInputs

# Gemini 2.5 Flash is on Google's free tier — good for cost-free validation testing.
# Bump this one constant to a newer/larger model later if quality needs it.
MODEL = "gemini-2.5-flash"


def build_system_prompt(catalog: list[Ingredient]) -> str:
    lines = [
        f"- {i.id}: {i.name} ({i.category}; "
        f"{i.kcal_per_100g} kcal, {i.protein_per_100g}g protein per 100g)"
        for i in catalog
    ]
    catalog_block = "\n".join(lines)
    return (
        "You are a meal planner for college students on a budget. "
        "Compose a week of meals using ONLY the ingredients in the catalog below, "
        "referencing each by its exact id. Express every quantity in grams. "
        "Produce 3 to 5 distinct meals (meal-prep style) that together cover the week. "
        "Respect the per-week cooking-time limit, aim for the daily calorie and protein "
        "targets across the week (week total ~= 7x the daily target), and keep the total "
        "grocery cost at or under the weekly budget. Maximize variety; avoid repeating the "
        "same meal. Do not invent ingredients or output any nutrition or price numbers — "
        "only ids and gram amounts.\n\n"
        "INGREDIENT CATALOG (id: name):\n"
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


def generate(inputs: PlanInputs, catalog: list[Ingredient], client=None) -> GeneratedPlan:
    # genai.Client() reads the API key from GEMINI_API_KEY (or GOOGLE_API_KEY).
    client = client or genai.Client()
    response = client.models.generate_content(
        model=MODEL,
        contents=build_user_prompt(inputs),
        config={
            "system_instruction": build_system_prompt(catalog),
            "response_mime_type": "application/json",
            "response_schema": GeneratedPlan,
        },
    )
    # With a Pydantic response_schema, the SDK populates `.parsed`; fall back to
    # parsing the raw JSON text if it doesn't.
    plan = response.parsed
    if plan is None:
        plan = GeneratedPlan.model_validate_json(response.text)

    valid_ids = {i.id for i in catalog}
    for meal in plan.meals:
        meal.ingredients = [mi for mi in meal.ingredients if mi.ingredient_id in valid_ids]
    return plan
