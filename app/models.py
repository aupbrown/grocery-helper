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

    @property
    def price_per_100g(self) -> float:
        """Marginal price per 100g, derived from the package so price has one source."""
        return self.package_price / self.package_size_g * 100


class MealIngredient(BaseModel):
    ingredient_id: str
    grams: float


class Meal(BaseModel):
    name: str
    ingredients: list[MealIngredient]
    cook_time_minutes: int
    servings: int
    instructions: str


class GeneratedPlan(BaseModel):
    """The LLM's structured output. No length constraint on meals so a stray
    count never crashes parsing; the prompt asks for 3-5."""
    meals: list[Meal]


class Targets(BaseModel):
    calories: float
    protein: float


class PlanInputs(BaseModel):
    weekly_budget: float
    goal: Goal
    bodyweight_lb: float
    activity_level: str = "light"
    max_cook_minutes: int
    dietary_pattern: str = "none"  # "none" | "vegetarian" | "vegan"
    avoid_allergens: list[str] = Field(default_factory=list)
    owned_ingredient_ids: list[str] = Field(default_factory=list)
    target_calories: float
    target_protein: float


# --- Computed views (built by plan.py, rendered by templates) ---

class MealIngredientView(BaseModel):
    name: str
    grams: float


class MealView(BaseModel):
    name: str
    ingredients: list[MealIngredientView]
    cook_time_minutes: int
    servings: int
    instructions: str
    macros: Macros


class GroceryItem(BaseModel):
    ingredient_id: str
    name: str
    grams: float                  # amount the week's recipes use
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
    total_cost: float                  # weekly groceries only
    pantry_total: float                # one-time pantry stock-up cost
    weekly_budget: float
    within_budget: bool
    daily_macros: Macros
    targets: Targets
