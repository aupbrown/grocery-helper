from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.generator import build_system_prompt, build_user_prompt, generate

CATALOG = [
    Ingredient(id="rice_white", name="White rice", category="grain", tags=["vegan"],
               allergens=[], kcal_per_100g=130, protein_per_100g=2.7, carbs_per_100g=28,
               fat_per_100g=0.3, price_per_100g=0.10),
    Ingredient(id="chicken_breast", name="Chicken breast", category="protein", tags=[],
               allergens=[], kcal_per_100g=165, protein_per_100g=31, carbs_per_100g=0,
               fat_per_100g=3.6, price_per_100g=1.10),
]

INPUTS = PlanInputs(weekly_budget=35, goal=Goal.bulk, bodyweight_lb=180,
                    max_cook_minutes=90, target_calories=3050, target_protein=180)


def _plan(ingredient_id, grams):
    return GeneratedPlan(meals=[
        Meal(name="Bowl", cook_time_minutes=20, servings=2, instructions="...",
             ingredients=[MealIngredient(ingredient_id=ingredient_id, grams=grams)]),
    ])


def _inputs(budget):
    return PlanInputs(weekly_budget=budget, goal=Goal.bulk, bodyweight_lb=180,
                      max_cook_minutes=90, target_calories=3050, target_protein=180)


# --- Stub mirroring google-genai: client.models.generate_content(...). Returns
# queued plans in order (repeating the last), recording each call. ---

class _Resp:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = parsed.model_dump_json()


class _Models:
    def __init__(self, plans):
        self._plans = list(plans)
        self.last_kwargs = None
        self.call_count = 0

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        idx = min(self.call_count, len(self._plans) - 1)
        self.call_count += 1
        return _Resp(self._plans[idx])


class _Client:
    def __init__(self, *plans):
        self.models = _Models(plans)


def test_system_prompt_lists_ids_prices_and_constrains():
    sp = build_system_prompt(CATALOG)
    assert "rice_white" in sp
    assert "chicken_breast" in sp
    assert "ONLY" in sp
    assert "1.10" in sp   # price is now exposed to the model
    assert "HARD" in sp   # budget framed as a hard constraint


def test_user_prompt_includes_inputs():
    up = build_user_prompt(INPUTS)
    assert "3050" in up
    assert "35" in up
    assert "90" in up


def test_generate_drops_invalid_ingredient_ids():
    parsed = GeneratedPlan(meals=[
        Meal(name="Bowl", cook_time_minutes=20, servings=2, instructions="...",
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                          MealIngredient(ingredient_id="not_in_catalog", grams=100)]),
    ])
    client = _Client(parsed)
    result = generate(INPUTS, CATALOG, client=client)
    ids = [mi.ingredient_id for mi in result.meals[0].ingredients]
    assert ids == ["rice_white"]
    assert client.models.last_kwargs["model"] == "gemini-2.5-flash"
    assert client.models.last_kwargs["config"]["response_schema"] is GeneratedPlan


def test_generate_retries_until_under_budget():
    over = _plan("chicken_breast", 1000)   # 1000g * $1.10/100g = $11.00
    under = _plan("rice_white", 1000)      # 1000g * $0.10/100g = $1.00
    client = _Client(over, under)
    result = generate(_inputs(budget=5), CATALOG, client=client, max_retries=2)
    assert result.meals[0].ingredients[0].ingredient_id == "rice_white"
    assert client.models.call_count == 2   # retried once, stopped once under budget


def test_generate_returns_cheapest_when_never_under_budget():
    over1 = _plan("chicken_breast", 1000)  # $11.00
    over2 = _plan("chicken_breast", 800)   # $8.80 (cheaper)
    client = _Client(over1, over2)
    result = generate(_inputs(budget=5), CATALOG, client=client, max_retries=1)
    assert client.models.call_count == 2          # max_retries + 1 attempts
    assert result.meals[0].ingredients[0].grams == 800   # cheapest attempt kept
