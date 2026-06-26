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
