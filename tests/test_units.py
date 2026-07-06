from pathlib import Path

from app.catalog import load_catalog, catalog_by_id
from app.units import to_grams, owned_unit_options

BY_ID = catalog_by_id(load_catalog(
    Path(__file__).resolve().parent.parent / "data" / "ingredients.json"))
CHICKEN = BY_ID["chicken_breast"]   # weight-only (no unit_grams / grams_per_cup)
EGGS = BY_ID["eggs"]                # unit_label "egg", unit_grams 50
RICE = BY_ID["rice_white"]          # grams_per_cup 158, no countable unit
BEANS = BY_ID["black_beans"]        # grams_per_cup 172, package_size_g 253
BUTTER = BY_ID["butter"]            # unit_label "tbsp" (14g) AND grams_per_cup 227


def test_grams_passthrough():
    assert to_grams(500, "g", CHICKEN) == 500
    assert to_grams(2, "grams", CHICKEN) == 2


def test_weight_units_convert():
    assert to_grams(1, "kg", CHICKEN) == 1000
    assert round(to_grams(1, "lb", CHICKEN), 1) == 453.6
    assert round(to_grams(8, "oz", CHICKEN), 1) == 226.8


def test_countable_uses_unit_grams():
    assert to_grams(3, "egg", EGGS) == 150      # 3 * 50g
    assert to_grams(2, "eggs", EGGS) == 100     # plural form
    assert to_grams(1, "each", EGGS) == 50      # generic countable word


def test_cup_uses_density():
    assert to_grams(2, "cup", RICE) == 316      # 2 * 158
    assert to_grams(1, "cups", RICE) == 158


def test_tbsp_prefers_unit_label_then_density():
    # Butter's unit_label IS "tbsp" -> use its unit_grams, not the cup fraction.
    assert to_grams(2, "tbsp", BUTTER) == 28
    oil = BY_ID["olive_oil"]                    # density only -> tbsp = cup/16
    assert round(to_grams(1, "tbsp", oil), 1) == round(oil.grams_per_cup / 16, 1)


def test_package_word_uses_package_size():
    assert to_grams(1, "can", BEANS) == BEANS.package_size_g
    assert to_grams(2, "package", CHICKEN) == 2 * CHICKEN.package_size_g


def test_unconvertible_returns_none():
    assert to_grams(1, "cup", CHICKEN) is None      # no density on chicken
    assert to_grams(1, "handful", CHICKEN) is None  # unknown unit
    assert to_grams(None, "g", CHICKEN) is None     # missing quantity
    assert to_grams(5, "", CHICKEN) is None          # missing unit


def test_owned_unit_options_are_all_convertible():
    for ing in (CHICKEN, EGGS, RICE, BEANS, BUTTER):
        opts = owned_unit_options(ing)
        assert "g" in opts
        for u in opts:
            assert to_grams(1, u, ing) is not None, (ing.id, u)


def test_owned_unit_options_include_natural_units():
    assert "egg" in owned_unit_options(EGGS)     # countable label offered
    assert "cup" in owned_unit_options(RICE)     # density -> cups offered
    assert "package" in owned_unit_options(CHICKEN)
