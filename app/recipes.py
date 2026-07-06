"""The curated recipe library.

Hand-vetted, college-friendly recipes written with real cooking method — technique, temperatures,
doneness cues, staged seasoning, and a finishing brightener — loaded from data/recipes.json.

This single asset does three jobs:
  - few-shot exemplars for the Gemini prompt (app/generator.py),
  - the deterministic fallback plan when Gemini is unavailable (app/meal_templates.py),
  - the single-meal regenerate fallback.

Recipes carry only ingredient ids and per-serving grams; macros and prices are computed downstream
by app/plan.py and are never stored here, so the library has one source of truth for numbers.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

RECIPES_PATH = Path(__file__).resolve().parent.parent / "data" / "recipes.json"


@dataclass(frozen=True)
class Recipe:
    name: str
    slot: str                       # breakfast | lunch | dinner | snack
    equipment: tuple[str, ...]      # () means no-cook
    prep_minutes: int
    instructions: str               # numbered, method-only (no ingredient amounts)
    per_serving: dict[str, float]   # ingredient_id -> grams for ONE serving
    tags: tuple[str, ...] = field(default_factory=tuple)
    flavor_note: str = ""


def load_recipes(path=RECIPES_PATH) -> list[Recipe]:
    data = json.loads(Path(path).read_text())
    return [Recipe(
        name=r["name"], slot=r["slot"], equipment=tuple(r.get("equipment", [])),
        prep_minutes=r["prep_minutes"], instructions=r["instructions"],
        per_serving=r["per_serving"], tags=tuple(r.get("tags", [])),
        flavor_note=r.get("flavor_note", ""),
    ) for r in data]


RECIPES: list[Recipe] = load_recipes()
