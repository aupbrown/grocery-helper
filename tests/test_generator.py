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


# --- Stub mirroring the google-genai client shape: client.models.generate_content(...) ---

class _Resp:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = parsed.model_dump_json()


class _Models:
    def __init__(self, parsed):
        self._parsed = parsed
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp(self._parsed)


class _Client:
    def __init__(self, parsed):
        self.models = _Models(parsed)


def test_system_prompt_lists_ids_and_constrains():
    sp = build_system_prompt(CATALOG)
    assert "rice_white" in sp
    assert "chicken_breast" in sp
    assert "ONLY" in sp


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
    # confirm we passed our schema + model to Gemini
    assert client.models.last_kwargs["model"] == "gemini-2.5-flash"
    assert client.models.last_kwargs["config"]["response_schema"] is GeneratedPlan
