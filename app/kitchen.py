"""Kitchen/equipment logic: what a meal needs, what a kitchen has, and how to split batch
cooking into sessions. Deterministic — Gemini may *declare* a meal's equipment, but this module
validates it and infers equipment when the model (or a fallback template) didn't say.
"""
from app.models import KitchenProfile, Meal

# The equipment we model + reason about. Storage/preference flags (mini_fridge, freezer,
# shared_kitchen, no_cook_preferred) live on KitchenProfile but are not "equipment" you cook with.
EQUIPMENT = ["microwave", "stove", "oven", "air_fryer", "blender", "rice_cooker"]

EQUIPMENT_LABELS = {
    "microwave": "microwave", "stove": "stove", "oven": "oven",
    "air_fryer": "air fryer", "blender": "blender", "rice_cooker": "rice cooker",
}

# Words that imply a piece of equipment when a meal doesn't declare one. Deliberately
# conservative: ambiguous verbs (boil/heat/cook) are excluded so a microwave-only user isn't
# wrongly blocked from a meal they could microwave.
_EQUIP_WORDS = {
    "oven": ("oven", "bake", "baked", "roast", "roasted", "broil", "sheet pan", "sheet-pan"),
    "air_fryer": ("air fryer", "air-fry", "air fry", "airfry"),
    "blender": ("blender", "blend ", "smoothie", "puree", "purée"),
    "rice_cooker": ("rice cooker", "rice-cooker"),
    "stove": ("stove", "stovetop", "skillet", "saucepan", "frying pan", "sauté", "saute",
              "pan-fry", "pan fry", "sear", "simmer"),
    "microwave": ("microwave",),
}

# Fresh proteins that realistically need a freezer to keep a full week's batch.
FREEZER_SENSITIVE = {"chicken_breast", "ground_beef_90"}

# Named starting points users can apply, then tweak.
PRESETS: dict[str, KitchenProfile] = {
    "apartment": KitchenProfile(microwave=True, stove=True, oven=True, freezer=True,
                                meal_prep_containers=True, max_single_session_minutes=60,
                                preferred_prep_sessions=2),
    "shared": KitchenProfile(microwave=True, stove=True, oven=True, shared_kitchen=True,
                             freezer=True, max_single_session_minutes=45,
                             preferred_prep_sessions=2),
    "athlete": KitchenProfile(microwave=True, stove=True, oven=True, air_fryer=True,
                              freezer=True, meal_prep_containers=True,
                              max_single_session_minutes=90, preferred_prep_sessions=1),
    "dorm": KitchenProfile(microwave=True, stove=False, oven=False, mini_fridge=True,
                           freezer=False, no_cook_preferred=True, meal_prep_containers=True,
                           transport_mode="walking", max_single_session_minutes=20,
                           preferred_prep_sessions=3),
}


def available_equipment(kitchen: KitchenProfile) -> set[str]:
    return {e for e in EQUIPMENT if getattr(kitchen, e)}


def infer_equipment(meal: Meal) -> list[str]:
    """Best-effort equipment for a meal that didn't declare any. A 0-minute meal is no-cook."""
    if meal.cook_time_minutes == 0:
        return []
    text = " " + meal.instructions.lower() + " "
    return sorted(eq for eq, words in _EQUIP_WORDS.items() if any(w in text for w in words))


def meal_equipment(meal: Meal) -> list[str]:
    """Equipment a meal needs: what it declares, else inferred from its instructions."""
    declared = [e for e in meal.equipment_required if e in EQUIPMENT]
    return declared if declared else infer_equipment(meal)


def missing_equipment(meal: Meal, kitchen: KitchenProfile) -> list[str]:
    """Equipment the meal needs that the kitchen doesn't have."""
    return sorted(set(meal_equipment(meal)) - available_equipment(kitchen))


def meal_fit_tags(meal: Meal, kitchen: KitchenProfile) -> list[str]:
    """Short human chips explaining how a meal fits the kitchen (for the results page)."""
    need = meal_equipment(meal)
    if not need:
        return ["No-cook"]
    tags = ["Needs " + ", ".join(EQUIPMENT_LABELS.get(e, e) for e in need)]
    if "stove" not in need and not kitchen.stove:
        tags.append("No stove required")
    if "oven" in need:
        tags.append("Oven batch-friendly")
    return tags


def split_sessions(cook_times: list[int], n: int) -> list[int]:
    """Greedily pack meals into n cooking sessions (longest-first), balancing total time."""
    bins = [0] * max(1, n)
    for t in sorted(cook_times, reverse=True):
        i = min(range(len(bins)), key=lambda j: bins[j])
        bins[i] += t
    return bins


def recommend_sessions(cook_times: list[int], max_single: int, start: int) -> tuple[int, list[int]]:
    """Fewest sessions (>= the user's preference) so no session exceeds max_single, when possible.
    A single meal longer than max_single can't be split, so the session count tops out at the
    number of meals."""
    n = max(1, min(start, len(cook_times) or 1))
    while n < len(cook_times) and max(split_sessions(cook_times, n)) > max_single:
        n += 1
    return n, split_sessions(cook_times, n)


def freezer_warning(packages_by_id: dict[str, int], kitchen: KitchenProfile) -> str | None:
    """Warn when a no-freezer plan buys bulk fresh meat that won't keep a week unfrozen."""
    if kitchen.freezer:
        return None
    bulk = [iid for iid in FREEZER_SENSITIVE if packages_by_id.get(iid, 0) >= 2]
    if not bulk:
        return None
    return ("No freezer set: this plan buys bulk fresh meat — cook it early in the week or shop "
            "twice so it doesn't spoil.")
