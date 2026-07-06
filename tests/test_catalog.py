from pathlib import Path

from app.catalog import load_catalog, catalog_by_id, filter_catalog

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "ingredients.json"


def test_load_catalog_returns_ingredients():
    catalog = load_catalog(CATALOG_PATH)
    assert len(catalog) >= 15
    assert any(i.id == "chicken_breast" for i in catalog)


def test_every_entry_has_package_pricing():
    catalog = load_catalog(CATALOG_PATH)
    for i in catalog:
        assert i.package_price > 0, i.id
        assert i.package_size_g > 0, i.id
        assert i.package_label, i.id


def test_seasonings_and_oil_are_pantry_staples():
    by_id = catalog_by_id(load_catalog(CATALOG_PATH))
    assert by_id["salt"].pantry_staple is True
    assert by_id["olive_oil"].pantry_staple is True
    # primary foods are weekly groceries, not pantry staples
    assert by_id["chicken_breast"].pantry_staple is False
    assert by_id["peanut_butter"].pantry_staple is False


def test_catalog_by_id_indexes():
    catalog = load_catalog(CATALOG_PATH)
    by_id = catalog_by_id(catalog)
    assert by_id["rice_white"].name == "White rice (cooked)"


def test_filter_vegan_excludes_meat_and_dairy():
    catalog = load_catalog(CATALOG_PATH)
    vegan = filter_catalog(catalog, dietary_pattern="vegan")
    ids = {i.id for i in vegan}
    assert "tofu" in ids
    assert "chicken_breast" not in ids
    assert "cheddar" not in ids


def test_filter_allergen_excludes_matching():
    catalog = load_catalog(CATALOG_PATH)
    no_dairy = filter_catalog(catalog, avoid_allergens=["dairy"])
    ids = {i.id for i in no_dairy}
    assert "milk_2pct" not in ids
    assert "chicken_breast" in ids


def test_catalog_ids_are_unique():
    ids = [i.id for i in load_catalog(CATALOG_PATH)]
    assert len(ids) == len(set(ids)), "duplicate ingredient ids in catalog"


def test_vegan_items_are_also_vegetarian():
    # vegan implies vegetarian; filter_catalog relies on the vegetarian tag being present too.
    for i in load_catalog(CATALOG_PATH):
        if "vegan" in i.tags:
            assert "vegetarian" in i.tags, i.id


def test_macros_are_non_negative():
    for i in load_catalog(CATALOG_PATH):
        for m in (i.kcal_per_100g, i.protein_per_100g, i.carbs_per_100g, i.fat_per_100g):
            assert m >= 0, i.id


def test_expanded_catalog_has_flavor_builders_and_new_proteins():
    by_id = catalog_by_id(load_catalog(CATALOG_PATH))
    # Cheap spices: pantry staples tagged veg+vegan so diet filters never strip seasoning.
    for sid in ("cumin", "paprika", "chili_powder", "oregano"):
        assert sid in by_id, sid
        assert by_id[sid].pantry_staple is True, sid
        assert {"vegetarian", "vegan"} <= set(by_id[sid].tags), sid
    # New proteins carry the right allergens and are weekly groceries, not pantry staples.
    assert "fish" in by_id["canned_salmon"].allergens
    assert "shellfish" in by_id["shrimp"].allergens
    assert by_id["canned_salmon"].pantry_staple is False
    # New carbs/wrappers.
    assert "gluten" in by_id["tortillas"].allergens
