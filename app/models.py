from enum import Enum
from pydantic import BaseModel, Field


class Goal(str, Enum):
    bulk = "bulk"
    maintain = "maintain"
    cut = "cut"


class Macros(BaseModel):
    calories: float
    protein: float
    carbs: float
    fat: float


class Ingredient(BaseModel):
    id: str
    name: str
    category: str
    tags: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    kcal_per_100g: float
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    package_price: float          # Walmart price of one package
    package_size_g: float         # grams per package, same cooked/dry basis as the macros
    package_label: str            # e.g. "16 oz jar", "dozen", "2 lb dry bag"
    pantry_staple: bool = False   # seasonings + oil: last months, tiny per-meal use
    unit_label: str | None = None # countable unit name, e.g. "banana", "egg", "clove"
    unit_grams: float | None = None  # avg grams per unit (for whole-unit display & snapping)
    grams_per_cup: float | None = None  # density for cups/tbsp/tsp display (volume items)

    @property
    def price_per_100g(self) -> float:
        """Marginal price per 100g, derived from the package so price has one source."""
        return self.package_price / self.package_size_g * 100


class MealIngredient(BaseModel):
    ingredient_id: str
    grams: float


class Meal(BaseModel):
    name: str
    slot: str = "dinner"          # "breakfast" | "lunch" | "dinner" | "snack"
    ingredients: list[MealIngredient]
    cook_time_minutes: int
    servings: int
    instructions: str
    # Equipment the recipe needs, e.g. ["oven"] or ["stove"]; [] means no-cook. Optional so old
    # plans and models that omit it still parse; the validator falls back to inferring from text.
    equipment_required: list[str] = Field(default_factory=list)


class GeneratedPlan(BaseModel):
    """The LLM's structured output. No length constraint on meals so a stray
    count never crashes parsing; the prompt asks for 3-5."""
    meals: list[Meal]


class Targets(BaseModel):
    calories: float
    protein: float
    carbs: float
    fat: float


class KitchenProfile(BaseModel):
    """A user's real cooking setup. Defaults are permissive (a full kitchen) so a user who
    never fills this in is never wrongly blocked; tightening it (e.g. a dorm microwave-only
    setup) lets the validator reject meals that don't fit. See app/kitchen.py for behavior."""
    microwave: bool = True
    stove: bool = True
    oven: bool = True
    air_fryer: bool = False
    blender: bool = False
    rice_cooker: bool = False
    mini_fridge: bool = False
    freezer: bool = True
    shared_kitchen: bool = False
    no_cook_preferred: bool = False
    meal_prep_containers: bool = True
    transport_mode: str = "car"            # walking | bike | bus | car | delivery
    max_single_session_minutes: int = 60   # cap on one cooking session
    preferred_prep_sessions: int = 2       # how many batch-cook sessions per week
    prioritize_time: bool = False          # bias toward batch-friendly (oven/one-pot) meals


class PlanInputs(BaseModel):
    weekly_budget: float
    goal: Goal
    bodyweight_lb: float
    activity_level: str = "light"
    max_cook_minutes: int                  # total cooking time budget for the week
    dietary_pattern: str = "none"  # "none" | "vegetarian" | "vegan"
    avoid_allergens: list[str] = Field(default_factory=list)
    owned_ingredient_ids: list[str] = Field(default_factory=list)
    target_calories: float
    target_protein: float
    target_carbs: float
    target_fat: float
    kitchen: KitchenProfile = Field(default_factory=KitchenProfile)


# --- Computed views (built by plan.py, rendered by templates) ---

class MealIngredientView(BaseModel):
    name: str
    grams: float                       # whole-recipe total (all servings)
    grams_per_serving: float
    macros_per_serving: Macros
    amount_total: str                  # human display, e.g. "2 bananas" or "300 g"
    amount_per_serving: str            # e.g. "1 banana" or "150 g"


class MealView(BaseModel):
    name: str
    slot: str                          # breakfast | lunch | dinner | snack
    ingredients: list[MealIngredientView]
    cook_time_minutes: int
    servings: int
    instructions: str
    macros_per_serving: Macros
    equipment_required: list[str] = []  # resolved equipment (declared or inferred) for display
    pan_loads: int = 1                  # cook the weekly batch in this many pan/pot loads


class GroceryItem(BaseModel):
    ingredient_id: str
    name: str
    grams: float                  # amount the week's recipes use
    uses_display: str             # human display of the weekly amount, e.g. "3 bananas"
    packages: int                 # whole packages to buy (rounded up)
    package_label: str
    package_price: float
    cost: float                   # packages * package_price
    pantry_staple: bool
    per_meal_cost: float | None = None  # amortized cost/meal for pantry items


class ComputedPlan(BaseModel):
    meals: list[MealView]
    grocery_list: list[GroceryItem]    # this week's groceries (counts toward budget)
    pantry_list: list[GroceryItem]     # one-time pantry staples (excluded from budget)
    owned_list: list[GroceryItem] = [] # ingredients the user already owns (shown at $0)
    total_cost: float                  # weekly groceries only
    pantry_total: float                # one-time pantry stock-up cost
    weekly_budget: float
    within_budget: bool
    daily_macros: Macros
    targets: Targets
    protein_met: bool                  # daily protein >= the goal's floor
    calories_met: bool                 # daily calories within the goal's band
    carbs_met: bool                    # daily carbs within the goal's band
    fat_met: bool                      # daily fat within the goal's band
    target_note: str = ""              # short human-readable gap summary ("" if on target)
