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


def _inputs(budget=35, target_protein=180):
    return PlanInputs(weekly_budget=budget, goal=Goal.bulk, bodyweight_lb=180,
                      max_cook_minutes=90, target_calories=3050, target_protein=target_protein)


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
    assert "1.10" in sp                  # price is now exposed to the model
    assert "PRIORITY" in sp              # priority-driven constraint
    assert "whey" in sp.lower()          # whey suggested as cheap protein
    assert "season" in sp.lower()        # flavor guidance present


def test_user_prompt_priority_directive():
    assert "3050" in build_user_prompt(INPUTS)
    assert "35" in build_user_prompt(INPUTS)
    assert "at or under the weekly budget" in build_user_prompt(INPUTS, "budget")
    assert "hit the daily protein target" in build_user_prompt(INPUTS, "protein")


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
    assert client.models.last_kwargs["model"] == "gemini-3.1-flash-lite"
    assert client.models.last_kwargs["config"]["response_schema"] is GeneratedPlan


def test_budget_priority_retries_until_under_budget():
    over = _plan("chicken_breast", 1000)   # 1000g * $1.10/100g = $11.00
    under = _plan("rice_white", 1000)      # 1000g * $0.10/100g = $1.00
    client = _Client(over, under)
    result = generate(_inputs(budget=5), CATALOG, client=client, priority="budget", max_retries=2)
    assert result.meals[0].ingredients[0].ingredient_id == "rice_white"
    assert client.models.call_count == 2   # retried once, stopped once under budget


def test_budget_priority_returns_cheapest_when_never_under_budget():
    over1 = _plan("chicken_breast", 1000)  # $11.00
    over2 = _plan("chicken_breast", 800)   # $8.80 (cheaper)
    client = _Client(over1, over2)
    result = generate(_inputs(budget=5), CATALOG, client=client, priority="budget", max_retries=1)
    assert client.models.call_count == 2          # max_retries + 1 attempts
    assert result.meals[0].ingredients[0].grams == 800   # cheapest attempt kept


def test_protein_priority_retries_when_short():
    short = _plan("rice_white", 700)         # 18.9g/wk -> 2.7g/day, under the 50 target
    in_band = _plan("chicken_breast", 1150)  # 356.5g/wk -> 50.9g/day, within [50, 55]
    client = _Client(short, in_band)
    result = generate(_inputs(budget=1000, target_protein=50), CATALOG,
                      client=client, priority="protein", max_retries=2)
    assert result.meals[0].ingredients[0].grams == 1150   # in-band plan accepted
    assert client.models.call_count == 2


def test_protein_priority_rejects_overshoot_for_in_band():
    overshoot = _plan("chicken_breast", 1400)  # 62g/day -> over the 55 ceiling (target*1.10)
    in_band = _plan("chicken_breast", 1150)    # 50.9g/day -> in band
    client = _Client(overshoot, in_band)
    result = generate(_inputs(budget=1000, target_protein=50), CATALOG,
                      client=client, priority="protein", max_retries=2)
    assert result.meals[0].ingredients[0].grams == 1150   # overshoot rejected for in-band
    assert client.models.call_count == 2


def test_protein_priority_returns_highest_protein_when_unreachable():
    small = _plan("chicken_breast", 100)   # 31g/wk -> 4.4g/day
    bigger = _plan("chicken_breast", 200)  # 62g/wk -> 8.9g/day
    client = _Client(small, bigger)
    result = generate(_inputs(budget=1000, target_protein=50), CATALOG,
                      client=client, priority="protein", max_retries=1)
    assert client.models.call_count == 2
    assert result.meals[0].ingredients[0].grams == 200   # highest-protein attempt kept


def test_protein_priority_keeps_cheapest_in_band_plan():
    # Both land in the [50, 55] band but both exceed the $10 budget, so neither is
    # "ideal" — the generator keeps the cheaper in-band plan.
    pricier = _plan("chicken_breast", 1200)   # 53.1g/day, $13.20
    cheaper = _plan("chicken_breast", 1150)   # 50.9g/day, $12.65
    client = _Client(pricier, cheaper)
    result = generate(_inputs(budget=10, target_protein=50), CATALOG,
                      client=client, priority="protein", max_retries=1)
    assert client.models.call_count == 2
    assert result.meals[0].ingredients[0].grams == 1150   # cheapest in-band kept
