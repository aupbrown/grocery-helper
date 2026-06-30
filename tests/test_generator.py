from pathlib import Path

from app.models import (
    Goal, Ingredient, KitchenProfile, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.catalog import load_catalog
from app.generator import build_system_prompt, build_user_prompt, generate, generate_one

# The full catalog, so fallback templates (which need real ingredients) can match.
FULL = load_catalog(Path(__file__).resolve().parent.parent / "data" / "ingredients.json")

# 50g packages priced at half the old per-100g, so whole-package cost equals the old
# per-gram cost for these (50g-multiple) test amounts — keeping the cost math readable.
CATALOG = [
    Ingredient(id="rice_white", name="White rice", category="grain", tags=["vegan"],
               allergens=[], kcal_per_100g=130, protein_per_100g=2.7, carbs_per_100g=28,
               fat_per_100g=0.3, package_price=0.05, package_size_g=50, package_label="50g"),
    Ingredient(id="chicken_breast", name="Chicken breast", category="protein", tags=[],
               allergens=[], kcal_per_100g=165, protein_per_100g=31, carbs_per_100g=0,
               fat_per_100g=3.6, package_price=0.55, package_size_g=50, package_label="50g"),
]

INPUTS = PlanInputs(weekly_budget=35, goal=Goal.bulk, bodyweight_lb=180,
                    max_cook_minutes=90, target_calories=3050, target_protein=180,
                    target_carbs=430, target_fat=68)


def _plan(ingredient_id, grams):
    return GeneratedPlan(meals=[
        Meal(name="Bowl", cook_time_minutes=20, servings=2, instructions="...",
             ingredients=[MealIngredient(ingredient_id=ingredient_id, grams=grams)]),
    ])


def _inputs(budget=35, target_protein=180):
    return PlanInputs(weekly_budget=budget, goal=Goal.bulk, bodyweight_lb=180,
                      max_cook_minutes=90, target_calories=3050, target_protein=target_protein,
                      target_carbs=430, target_fat=68)


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


class _RaisingModels:
    def __init__(self):
        self.call_count = 0

    def generate_content(self, **kwargs):
        self.call_count += 1
        raise ValueError("simulated API / invalid-JSON failure")


class _RaisingClient:
    def __init__(self):
        self.models = _RaisingModels()


def _full_inputs(**kw):
    base = dict(weekly_budget=50, goal=Goal.maintain, bodyweight_lb=180, max_cook_minutes=180,
                target_calories=2400, target_protein=150, target_carbs=250, target_fat=70)
    base.update(kw)
    return PlanInputs(**base)


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


def test_user_prompt_states_weekly_totals():
    # Anchors the per-serving vs per-day ambiguity: the plan is the whole week's food.
    p = build_user_prompt(INPUTS)
    assert "21350" in p   # 3050 kcal * 7 days
    assert "1260" in p    # 180 g protein * 7 days


def test_user_prompt_includes_carb_and_fat_targets():
    p = build_user_prompt(INPUTS)
    assert "430" in p     # daily carb target
    assert "68" in p      # daily fat target


def test_system_prompt_requires_detailed_recipes_and_listed_seasonings():
    sp = build_system_prompt(CATALOG).lower()
    assert "step" in sp                       # numbered/step-by-step instructions
    assert "every seasoning" in sp or "list every" in sp   # seasonings must be listed


def test_system_prompt_wants_method_only_steps_and_batch_cooking():
    sp = build_system_prompt(CATALOG).lower()
    assert "method-only" in sp          # steps are method, amounts live in the ingredient list
    assert "no grams" in sp             # quantities are forbidden in the steps
    assert "pan-loads" in sp            # tell the cook to work in batches for a big weekly batch
    assert "realistic" in sp            # portion-realism guidance present


def test_system_prompt_asks_for_weekly_slots():
    sp = build_system_prompt(CATALOG).lower()
    assert "breakfast" in sp and "lunch" in sp and "dinner" in sp
    assert "snack" in sp
    assert "slot" in sp


def test_system_prompt_declares_equipment_field():
    sp = build_system_prompt(CATALOG)
    assert "equipment_required" in sp
    assert "microwave" in sp and "oven" in sp


def test_user_prompt_includes_kitchen_equipment():
    assert "Kitchen equipment available" in build_user_prompt(INPUTS)


def test_user_prompt_reflects_no_cook_and_time_preferences():
    inp = INPUTS.model_copy(update={"kitchen": KitchenProfile(
        stove=False, oven=False, no_cook_preferred=True, prioritize_time=True)})
    p = build_user_prompt(inp).lower()
    assert "no-cook" in p
    assert "save time" in p


def test_equipment_tokens_are_normalized():
    raw = GeneratedPlan(meals=[
        Meal(name="Bowl", cook_time_minutes=20, servings=2, instructions="...",
             equipment_required=["stove", "blowtorch", "OVEN"],
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=100)])])
    result = generate(_inputs(budget=35), CATALOG, client=_Client(raw), priority="budget")
    assert result.meals[0].equipment_required == ["stove"]   # unknown/wrong-case dropped


def test_generation_failure_falls_back_to_templates():
    plan = generate(_full_inputs(), FULL, client=_RaisingClient(), max_retries=2)
    assert plan.meals and all(m.ingredients for m in plan.meals)


def test_fallback_respects_microwave_only_kitchen():
    inp = _full_inputs(kitchen=KitchenProfile(microwave=True, stove=False, oven=False))
    plan = generate(inp, FULL, client=_RaisingClient(), max_retries=1)
    assert plan.meals
    for m in plan.meals:
        assert "stove" not in m.equipment_required
        assert "oven" not in m.equipment_required


def test_generate_feeds_back_dropped_ids():
    bad = GeneratedPlan(meals=[
        Meal(name="Bowl", cook_time_minutes=20, servings=2, instructions="...",
             ingredients=[MealIngredient(ingredient_id="chicken_breast", grams=1000),
                          MealIngredient(ingredient_id="not_in_catalog", grams=100)]),
    ])
    good = _plan("rice_white", 100)   # cheap, lands under the $5 budget -> stops the loop
    client = _Client(bad, good)
    generate(_inputs(budget=5), CATALOG, client=client, priority="budget", max_retries=2)
    # The retry prompt names the dropped id so the model can replace it.
    assert "not_in_catalog" in client.models.last_kwargs["contents"]


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


def test_generate_one_returns_single_slot_meal():
    meal = Meal(name="Oatmeal bowl", slot="breakfast", cook_time_minutes=10, servings=7,
                instructions="1. Cook oats.", ingredients=[
                    MealIngredient(ingredient_id="rice_white", grams=700),
                    MealIngredient(ingredient_id="not_in_catalog", grams=50)])
    client = _Client(meal)
    result = generate_one(INPUTS, CATALOG, "breakfast", client=client)
    assert result.slot == "breakfast"
    assert [mi.ingredient_id for mi in result.ingredients] == ["rice_white"]   # invalid dropped
    assert client.models.last_kwargs["config"]["response_schema"] is Meal


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
