import json
from pathlib import Path

from app.models import Ingredient


def load_catalog(path) -> list[Ingredient]:
    data = json.loads(Path(path).read_text())
    return [Ingredient(**row) for row in data]


def catalog_by_id(catalog: list[Ingredient]) -> dict[str, Ingredient]:
    return {i.id: i for i in catalog}


def filter_catalog(
    catalog: list[Ingredient],
    dietary_pattern: str = "none",
    avoid_allergens=(),
) -> list[Ingredient]:
    avoid = set(avoid_allergens)
    result = []
    for ing in catalog:
        if dietary_pattern == "vegetarian" and "vegetarian" not in ing.tags:
            continue
        if dietary_pattern == "vegan" and "vegan" not in ing.tags:
            continue
        if avoid.intersection(ing.allergens):
            continue
        result.append(ing)
    return result
