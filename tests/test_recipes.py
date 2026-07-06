from pathlib import Path

from app.recipes import Recipe, load_recipes, RECIPES
from app.catalog import load_catalog, catalog_by_id
from app.kitchen import EQUIPMENT
from app.models import PlanInputs, Goal

DATA = Path(__file__).resolve().parent.parent / "data" / "ingredients.json"
CATALOG = catalog_by_id(load_catalog(DATA))
VALID_SLOTS = {"breakfast", "lunch", "dinner", "snack"}


def test_library_has_enough_recipes():
    assert len(RECIPES) >= 30


def test_recipes_are_well_formed():
    for r in RECIPES:
        assert r.name, r
        assert r.slot in VALID_SLOTS, r.name
        assert r.prep_minutes >= 0, r.name
        assert r.per_serving, r.name
        assert all(g > 0 for g in r.per_serving.values()), r.name
        assert r.instructions.strip(), r.name
        assert "1." in r.instructions, r.name  # numbered method steps


def test_recipes_reference_only_catalog_ids():
    for r in RECIPES:
        for iid in r.per_serving:
            assert iid in CATALOG, f"{r.name}: unknown ingredient {iid}"


def test_recipe_equipment_tokens_are_valid():
    for r in RECIPES:
        for e in r.equipment:
            assert e in EQUIPMENT, f"{r.name}: bad equipment {e}"


def test_all_slots_are_covered():
    assert {r.slot for r in RECIPES} == VALID_SLOTS


def test_has_no_cook_and_vegan_dinner_options():
    assert any(not r.equipment for r in RECIPES)                       # a no-cook option
    assert any("vegan" in r.tags and r.slot == "dinner" for r in RECIPES)


def test_load_recipes_reads_the_json_file():
    recipes = load_recipes()
    assert len(recipes) == len(RECIPES)
    assert isinstance(recipes[0], Recipe)


def test_fallback_plan_uses_the_library():
    from app.meal_templates import fallback_plan
    inputs = PlanInputs(weekly_budget=40, goal=Goal.maintain, bodyweight_lb=170,
                        max_cook_minutes=120, target_calories=2400, target_protein=150,
                        target_carbs=250, target_fat=70)
    plan = fallback_plan(inputs, load_catalog(DATA))
    assert len(plan.meals) >= 3
    assert all(m.ingredients for m in plan.meals)
