from pathlib import Path

from app.catalog import load_catalog, catalog_by_id, filter_catalog

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "ingredients.json"


def test_load_catalog_returns_ingredients():
    catalog = load_catalog(CATALOG_PATH)
    assert len(catalog) >= 15
    assert any(i.id == "chicken_breast" for i in catalog)


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
