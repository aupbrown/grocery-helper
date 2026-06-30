from app.models import KitchenProfile, Meal, MealIngredient
from app.kitchen import (
    available_equipment, infer_equipment, meal_equipment, missing_equipment,
    recommend_sessions, split_sessions, freezer_warning, PRESETS,
)


def _meal(instructions, *, equipment=None, cook=20):
    return Meal(name="m", slot="dinner", cook_time_minutes=cook, servings=7,
                instructions=instructions, equipment_required=equipment or [],
                ingredients=[MealIngredient(ingredient_id="rice_white", grams=100)])


def test_infer_equipment_from_text():
    assert infer_equipment(_meal("1. Bake at 400F for 20 min.")) == ["oven"]
    assert infer_equipment(_meal("1. Blend everything in a blender.")) == ["blender"]
    assert infer_equipment(_meal("1. Sear in a skillet on the stove.")) == ["stove"]


def test_infer_equipment_no_cook_and_ambiguous():
    assert infer_equipment(_meal("1. Mix and serve.", cook=0)) == []
    # "boil" is ambiguous (could be microwaved) so it is deliberately NOT inferred as stove.
    assert infer_equipment(_meal("1. Boil water and add oats.")) == []


def test_declared_equipment_wins_over_inference():
    m = _meal("1. Bake in the oven.", equipment=["microwave"])
    assert meal_equipment(m) == ["microwave"]


def test_missing_equipment_against_dorm_kitchen():
    dorm = PRESETS["dorm"]   # microwave only
    assert available_equipment(dorm) == {"microwave"}
    assert missing_equipment(_meal("1. Roast in oven.", equipment=["oven"]), dorm) == ["oven"]
    assert missing_equipment(_meal("1. Microwave it.", equipment=["microwave"]), dorm) == []


def test_recommend_sessions_splits_to_fit_single_limit():
    # Three 30-min meals can't fit one 40-min session, but split three ways they do.
    assert recommend_sessions([30, 30, 30], 40, 1) == (3, [30, 30, 30])
    # Already fits the user's preferred single session — don't add sessions.
    assert recommend_sessions([20, 20], 60, 1) == (1, [40])


def test_split_sessions_balances_load():
    assert max(split_sessions([50, 30, 20], 2)) == 50   # [50],[30,20]


def test_freezer_warning_only_for_bulk_meat_without_freezer():
    no_freezer = KitchenProfile(freezer=False)
    assert freezer_warning({"chicken_breast": 2}, no_freezer) is not None
    assert freezer_warning({"chicken_breast": 1}, no_freezer) is None
    assert freezer_warning({"chicken_breast": 3}, KitchenProfile(freezer=True)) is None
