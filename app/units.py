"""Convert a user-entered pantry/owned amount into grams, the unit the plan math works in.

Users say what they own in whatever unit is natural ("2 cups of rice", "1 lb chicken", "3 eggs",
"1 can beans"). The plan measures everything in grams, so shortfall costing (app/plan.py) needs a
single reliable conversion. We lean on data the catalog already carries — `unit_grams` for
countables, `grams_per_cup` for volume items, `package_size_g` for whole packages — and return None
for anything we cannot convert so the caller can fall back to treating the item as "have enough".
"""
from app.models import Ingredient

# Weight units -> grams. These convert regardless of the ingredient.
_WEIGHT_G = {
    "g": 1.0, "gram": 1.0, "grams": 1.0, "gm": 1.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "oz": 28.3495, "ounce": 28.3495, "ounces": 28.3495,
    "lb": 453.592, "lbs": 453.592, "pound": 453.592, "pounds": 453.592,
}

# Volume units -> fraction of a cup (needs the ingredient's grams_per_cup density).
_VOLUME_CUPS = {
    "cup": 1.0, "cups": 1.0,
    "tbsp": 1 / 16, "tablespoon": 1 / 16, "tablespoons": 1 / 16,
    "tsp": 1 / 48, "teaspoon": 1 / 48, "teaspoons": 1 / 48,
}

# Generic "one whole package" words (uses the ingredient's package_size_g).
_PACKAGE_WORDS = {
    "package", "packages", "pack", "packs", "bag", "bags", "box", "boxes", "jar", "jars",
    "bottle", "bottles", "can", "cans", "container", "containers", "loaf", "loaves",
    "bunch", "clamshell", "pint", "gallon", "block", "tub",
}

# Generic countable words that mean "one whole unit" (uses the ingredient's unit_grams).
_COUNT_WORDS = {"each", "count", "unit", "units", "whole", "piece", "pieces"}


def to_grams(quantity, unit: str, ing: Ingredient) -> float | None:
    """Grams equivalent of `quantity` `unit` of `ing`, or None if it cannot be converted."""
    if quantity is None or unit is None:
        return None
    u = str(unit).strip().lower()
    if not u:
        return None
    q = float(quantity)

    if u in _WEIGHT_G:
        return q * _WEIGHT_G[u]

    # Countable: the ingredient's own unit label (e.g. "egg", "can", "tortilla") or a generic
    # count word. Checked before volume so butter's "tbsp" label wins over the cup fraction.
    if ing.unit_grams and (u in _COUNT_WORDS or (ing.unit_label and u in _unit_label_forms(ing))):
        return q * ing.unit_grams

    if u in _VOLUME_CUPS and ing.grams_per_cup:
        return q * ing.grams_per_cup * _VOLUME_CUPS[u]

    if u in _PACKAGE_WORDS:
        return q * ing.package_size_g

    return None


def _unit_label_forms(ing: Ingredient) -> set[str]:
    label = ing.unit_label.strip().lower()
    return {label, label + "s"}


def owned_unit_options(ing: Ingredient) -> list[str]:
    """Units to offer for this ingredient in the 'already own' form — every one is convertible by
    to_grams, so the amount always resolves to grams. Most natural unit first."""
    opts: list[str] = []
    if ing.unit_label and ing.unit_grams:
        opts.append(ing.unit_label)          # e.g. "egg", "can", "tortilla", "tbsp"
    opts += ["g", "oz", "lb"]
    if ing.grams_per_cup:
        opts.append("cup")
    opts.append("package")
    return opts
