# Grocery Helper Validation MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web app where a student enters budget + fitness goal + bodyweight + cook time + dietary restrictions and gets a week of meals, a grocery list, an estimated cost, and daily macros — and we capture email + completion rate as a demand signal.

**Architecture:** An LLM (`claude-opus-4-8`) composes meals using *only* a fixed, pre-priced ingredient catalog, returning structured output (ingredient IDs + gram quantities). All numbers — macros, cost, grocery list — are computed deterministically in Python from the catalog, so they are never hallucinated. A FastAPI app serves a two-step server-rendered flow (inputs → editable targets → plan + email CTA). Events log to SQLite.

**Tech Stack:** Python, FastAPI, Jinja2, Pydantic v2, Anthropic Python SDK (`messages.parse`), SQLite (stdlib), pytest.

**Design decision — targets formula:** The committed spec named Mifflin–St Jeor, but that needs height + age, which we deliberately do **not** collect (keeping the form short). We instead use the standard **bodyweight × activity-multiplier** heuristic for maintenance calories, then apply the goal adjustment. Targets are editable by the student, so an approximate default is fine. This is the one deviation from the spec; it is intentional.

---

## File Structure

```
grocery-helper/
  app/
    __init__.py
    models.py        # Pydantic models (inputs, LLM output, computed views)
    catalog.py       # load + index + filter the ingredient catalog
    targets.py       # bodyweight + goal -> default calorie & protein targets (pure)
    plan.py          # generated meals + catalog -> macros, grocery list, cost (pure)
    generator.py     # build prompt, call Claude (messages.parse), validate IDs
    storage.py       # SQLite event logging (plan_generated, email_captured)
    main.py          # FastAPI app + routes
    templates/       # form.html, targets.html, results.html, thanks.html
    static/styles.css
  data/
    ingredients.json # the catalog (seed ~20 items; expand to ~100-150 later)
  tests/
    test_catalog.py
    test_targets.py
    test_plan.py
    test_generator.py
    test_storage.py
    test_web.py
  requirements.txt
  pyproject.toml     # pytest pythonpath
  .env.example
  .gitignore
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `app/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
anthropic>=0.69.0
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
jinja2>=3.1.0
python-multipart>=0.0.9
pydantic>=2.6.0
python-dotenv>=1.0.0
pytest>=8.0.0
httpx>=0.27.0
```

- [ ] **Step 2: Create `pyproject.toml`** (lets `import app.x` work and loads `.env` style is manual)

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.env
events.db
.pytest_cache/
```

- [ ] **Step 4: Create `.env.example`**

```
# Copy to .env and fill in. Get a key at https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-...
```

- [ ] **Step 5: Create empty `app/__init__.py`**

```python
```

- [ ] **Step 6: Create and activate a virtualenv, install deps**

Run:
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```
Expected: installs without error.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pyproject.toml .gitignore .env.example app/__init__.py
git commit -m "chore: project scaffolding for grocery-helper MVP"
```

---

### Task 2: Pydantic models

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from app.models import (
    Goal, Macros, Ingredient, MealIngredient, Meal, GeneratedPlan,
    Targets, PlanInputs,
)


def test_goal_enum_values():
    assert {g.value for g in Goal} == {"bulk", "maintain", "cut"}


def test_ingredient_roundtrips_from_dict():
    ing = Ingredient(
        id="rice_white", name="White rice", category="grain",
        tags=["vegetarian", "vegan"], allergens=[],
        kcal_per_100g=130, protein_per_100g=2.7, carbs_per_100g=28,
        fat_per_100g=0.3, price_per_100g=0.10,
    )
    assert ing.id == "rice_white"
    assert "vegan" in ing.tags


def test_generated_plan_holds_meals():
    plan = GeneratedPlan(meals=[
        Meal(name="Bowl", ingredients=[MealIngredient(ingredient_id="rice_white", grams=200)],
             cook_time_minutes=15, servings=2, instructions="Cook rice."),
    ])
    assert plan.meals[0].ingredients[0].grams == 200


def test_plan_inputs_defaults():
    pi = PlanInputs(
        weekly_budget=40, goal=Goal.cut, bodyweight_lb=170,
        max_cook_minutes=120, target_calories=2200, target_protein=170,
    )
    assert pi.activity_level == "light"
    assert pi.dietary_pattern == "none"
    assert pi.avoid_allergens == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write `app/models.py`**

```python
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
    price_per_100g: float


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
    grams: float
    cost: float


class ComputedPlan(BaseModel):
    meals: list[MealView]
    grocery_list: list[GroceryItem]
    total_cost: float
    weekly_budget: float
    within_budget: bool
    daily_macros: Macros
    targets: Targets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: pydantic models for grocery-helper"
```

---

### Task 3: Ingredient catalog data + loader

**Files:**
- Create: `data/ingredients.json`
- Create: `app/catalog.py`
- Test: `tests/test_catalog.py`

- [ ] **Step 1: Create the seed catalog `data/ingredients.json`**

This is a ~20-item starter set so everything runs immediately. Expanding to ~100–150 items (more proteins, grains, veg, snacks) is the post-MVP data task — same schema, more rows. Macros are per-100g (USDA-style); prices are estimated USD per 100g.

```json
[
  {"id": "chicken_breast", "name": "Chicken breast", "category": "protein", "tags": [], "allergens": [], "kcal_per_100g": 165, "protein_per_100g": 31, "carbs_per_100g": 0, "fat_per_100g": 3.6, "price_per_100g": 1.10},
  {"id": "ground_beef_90", "name": "Lean ground beef (90/10)", "category": "protein", "tags": [], "allergens": [], "kcal_per_100g": 176, "protein_per_100g": 20, "carbs_per_100g": 0, "fat_per_100g": 10, "price_per_100g": 1.30},
  {"id": "canned_tuna", "name": "Canned tuna", "category": "protein", "tags": [], "allergens": ["fish"], "kcal_per_100g": 116, "protein_per_100g": 26, "carbs_per_100g": 0, "fat_per_100g": 1, "price_per_100g": 1.40},
  {"id": "eggs", "name": "Eggs", "category": "protein", "tags": ["vegetarian"], "allergens": ["eggs"], "kcal_per_100g": 143, "protein_per_100g": 13, "carbs_per_100g": 1.1, "fat_per_100g": 9.5, "price_per_100g": 0.55},
  {"id": "greek_yogurt", "name": "Greek yogurt (nonfat)", "category": "dairy", "tags": ["vegetarian"], "allergens": ["dairy"], "kcal_per_100g": 59, "protein_per_100g": 10, "carbs_per_100g": 3.6, "fat_per_100g": 0.4, "price_per_100g": 0.70},
  {"id": "milk_2pct", "name": "2% milk", "category": "dairy", "tags": ["vegetarian"], "allergens": ["dairy"], "kcal_per_100g": 50, "protein_per_100g": 3.4, "carbs_per_100g": 4.8, "fat_per_100g": 2, "price_per_100g": 0.12},
  {"id": "cheddar", "name": "Cheddar cheese", "category": "dairy", "tags": ["vegetarian"], "allergens": ["dairy"], "kcal_per_100g": 403, "protein_per_100g": 25, "carbs_per_100g": 1.3, "fat_per_100g": 33, "price_per_100g": 1.00},
  {"id": "tofu", "name": "Firm tofu", "category": "protein", "tags": ["vegetarian", "vegan"], "allergens": ["soy"], "kcal_per_100g": 76, "protein_per_100g": 8, "carbs_per_100g": 1.9, "fat_per_100g": 4.8, "price_per_100g": 0.45},
  {"id": "black_beans", "name": "Black beans (canned)", "category": "legume", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 132, "protein_per_100g": 8.9, "carbs_per_100g": 24, "fat_per_100g": 0.5, "price_per_100g": 0.30},
  {"id": "lentils", "name": "Lentils (dry)", "category": "legume", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 116, "protein_per_100g": 9, "carbs_per_100g": 20, "fat_per_100g": 0.4, "price_per_100g": 0.25},
  {"id": "rice_white", "name": "White rice (cooked)", "category": "grain", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 130, "protein_per_100g": 2.7, "carbs_per_100g": 28, "fat_per_100g": 0.3, "price_per_100g": 0.10},
  {"id": "oats", "name": "Rolled oats (dry)", "category": "grain", "tags": ["vegetarian", "vegan"], "allergens": ["gluten"], "kcal_per_100g": 389, "protein_per_100g": 16.9, "carbs_per_100g": 66, "fat_per_100g": 6.9, "price_per_100g": 0.20},
  {"id": "pasta_ww", "name": "Whole wheat pasta (cooked)", "category": "grain", "tags": ["vegetarian", "vegan"], "allergens": ["gluten"], "kcal_per_100g": 124, "protein_per_100g": 5, "carbs_per_100g": 26, "fat_per_100g": 0.5, "price_per_100g": 0.20},
  {"id": "bread_ww", "name": "Whole wheat bread", "category": "grain", "tags": ["vegetarian", "vegan"], "allergens": ["gluten"], "kcal_per_100g": 247, "protein_per_100g": 13, "carbs_per_100g": 41, "fat_per_100g": 3.4, "price_per_100g": 0.30},
  {"id": "potato", "name": "Potato", "category": "vegetable", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 77, "protein_per_100g": 2, "carbs_per_100g": 17, "fat_per_100g": 0.1, "price_per_100g": 0.10},
  {"id": "broccoli", "name": "Broccoli", "category": "vegetable", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 34, "protein_per_100g": 2.8, "carbs_per_100g": 7, "fat_per_100g": 0.4, "price_per_100g": 0.40},
  {"id": "spinach", "name": "Spinach", "category": "vegetable", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 23, "protein_per_100g": 2.9, "carbs_per_100g": 3.6, "fat_per_100g": 0.4, "price_per_100g": 0.60},
  {"id": "banana", "name": "Banana", "category": "fruit", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 89, "protein_per_100g": 1.1, "carbs_per_100g": 23, "fat_per_100g": 0.3, "price_per_100g": 0.25},
  {"id": "peanut_butter", "name": "Peanut butter", "category": "fat", "tags": ["vegetarian", "vegan"], "allergens": ["nuts"], "kcal_per_100g": 588, "protein_per_100g": 25, "carbs_per_100g": 20, "fat_per_100g": 50, "price_per_100g": 0.60},
  {"id": "olive_oil", "name": "Olive oil", "category": "fat", "tags": ["vegetarian", "vegan"], "allergens": [], "kcal_per_100g": 884, "protein_per_100g": 0, "carbs_per_100g": 0, "fat_per_100g": 100, "price_per_100g": 0.80}
]
```

- [ ] **Step 2: Write the failing test**

`tests/test_catalog.py`:
```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.catalog'`

- [ ] **Step 4: Write `app/catalog.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_catalog.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add data/ingredients.json app/catalog.py tests/test_catalog.py
git commit -m "feat: ingredient catalog seed data + loader/filter"
```

---

### Task 4: Target calculation

**Files:**
- Create: `app/targets.py`
- Test: `tests/test_targets.py`

- [ ] **Step 1: Write the failing test**

`tests/test_targets.py`:
```python
from app.models import Goal
from app.targets import compute_targets


def test_maintain_light_activity():
    # 180 lb * 15 (light) + 0 = 2700; protein 1g/lb = 180
    t = compute_targets(bodyweight_lb=180, goal=Goal.maintain, activity_level="light")
    assert t.calories == 2700
    assert t.protein == 180


def test_bulk_adds_surplus():
    t = compute_targets(bodyweight_lb=180, goal=Goal.bulk, activity_level="light")
    assert t.calories == 3050  # 2700 + 350


def test_cut_applies_deficit():
    t = compute_targets(bodyweight_lb=180, goal=Goal.cut, activity_level="light")
    assert t.calories == 2200  # 2700 - 500


def test_activity_level_changes_maintenance():
    sed = compute_targets(bodyweight_lb=200, goal=Goal.maintain, activity_level="sedentary")
    act = compute_targets(bodyweight_lb=200, goal=Goal.maintain, activity_level="active")
    assert sed.calories == 2600   # 200 * 13
    assert act.calories == 3400   # 200 * 17
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_targets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.targets'`

- [ ] **Step 3: Write `app/targets.py`**

```python
from app.models import Goal, Targets

ACTIVITY_FACTORS = {"sedentary": 13, "light": 15, "active": 17}
GOAL_ADJUSTMENT = {Goal.bulk: 350, Goal.maintain: 0, Goal.cut: -500}
PROTEIN_PER_LB = 1.0


def compute_targets(bodyweight_lb: float, goal: Goal, activity_level: str = "light") -> Targets:
    factor = ACTIVITY_FACTORS.get(activity_level, ACTIVITY_FACTORS["light"])
    maintenance = bodyweight_lb * factor
    calories = maintenance + GOAL_ADJUSTMENT[goal]
    protein = bodyweight_lb * PROTEIN_PER_LB
    return Targets(calories=round(calories), protein=round(protein))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_targets.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/targets.py tests/test_targets.py
git commit -m "feat: bodyweight+goal target calculation"
```

---

### Task 5: Plan computation (macros, grocery list, cost)

**Files:**
- Create: `app/plan.py`
- Test: `tests/test_plan.py`

- [ ] **Step 1: Write the failing test**

`tests/test_plan.py`:
```python
from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.catalog import catalog_by_id
from app.plan import macros_for_meal, compute_plan

RICE = Ingredient(id="rice_white", name="White rice", category="grain",
                  tags=["vegan"], allergens=[], kcal_per_100g=130,
                  protein_per_100g=2.7, carbs_per_100g=28, fat_per_100g=0.3,
                  price_per_100g=0.10)
CHICKEN = Ingredient(id="chicken_breast", name="Chicken breast", category="protein",
                     tags=[], allergens=[], kcal_per_100g=165, protein_per_100g=31,
                     carbs_per_100g=0, fat_per_100g=3.6, price_per_100g=1.10)
BY_ID = catalog_by_id([RICE, CHICKEN])

MEAL = Meal(name="Chicken & rice", cook_time_minutes=20, servings=2,
            instructions="Cook rice, grill chicken.",
            ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                         MealIngredient(ingredient_id="chicken_breast", grams=150)])


def _inputs(owned=None):
    return PlanInputs(weekly_budget=30, goal=Goal.maintain, bodyweight_lb=180,
                      max_cook_minutes=120, owned_ingredient_ids=owned or [],
                      target_calories=2700, target_protein=180)


def test_macros_for_meal():
    m = macros_for_meal(MEAL, BY_ID)
    assert m.calories == 507.5   # 130*2 + 165*1.5
    assert m.protein == 51.9     # 2.7*2 + 31*1.5
    assert m.carbs == 56.0       # 28*2
    assert m.fat == 6.0          # 0.3*2 + 3.6*1.5


def test_compute_plan_cost_and_daily_macros():
    plan = GeneratedPlan(meals=[MEAL])
    cp = compute_plan(plan, BY_ID, _inputs())
    assert cp.total_cost == 1.85   # rice 0.20 + chicken 1.65
    assert cp.within_budget is True
    assert len(cp.grocery_list) == 2
    assert cp.daily_macros.calories == 72.5   # 507.5 / 7
    assert cp.meals[0].ingredients[0].name == "White rice"


def test_owned_ingredients_excluded_from_grocery_list():
    plan = GeneratedPlan(meals=[MEAL])
    cp = compute_plan(plan, BY_ID, _inputs(owned=["rice_white"]))
    ids = {g.ingredient_id for g in cp.grocery_list}
    assert "rice_white" not in ids
    assert cp.total_cost == 1.65
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.plan'`

- [ ] **Step 3: Write `app/plan.py`**

```python
from app.models import (
    Macros, Meal, Ingredient, GeneratedPlan, PlanInputs,
    MealIngredientView, MealView, GroceryItem, ComputedPlan, Targets,
)

DAYS = 7


def macros_for_meal(meal: Meal, by_id: dict[str, Ingredient]) -> Macros:
    cals = prot = carb = fat = 0.0
    for mi in meal.ingredients:
        ing = by_id[mi.ingredient_id]
        f = mi.grams / 100.0
        cals += ing.kcal_per_100g * f
        prot += ing.protein_per_100g * f
        carb += ing.carbs_per_100g * f
        fat += ing.fat_per_100g * f
    return Macros(calories=round(cals, 1), protein=round(prot, 1),
                  carbs=round(carb, 1), fat=round(fat, 1))


def compute_plan(
    generated: GeneratedPlan,
    by_id: dict[str, Ingredient],
    inputs: PlanInputs,
) -> ComputedPlan:
    owned = set(inputs.owned_ingredient_ids)

    meal_views: list[MealView] = []
    total = Macros(calories=0, protein=0, carbs=0, fat=0)
    grams_by_ing: dict[str, float] = {}

    for meal in generated.meals:
        m = macros_for_meal(meal, by_id)
        total = Macros(
            calories=round(total.calories + m.calories, 1),
            protein=round(total.protein + m.protein, 1),
            carbs=round(total.carbs + m.carbs, 1),
            fat=round(total.fat + m.fat, 1),
        )
        meal_views.append(MealView(
            name=meal.name,
            ingredients=[MealIngredientView(name=by_id[mi.ingredient_id].name, grams=mi.grams)
                         for mi in meal.ingredients],
            cook_time_minutes=meal.cook_time_minutes,
            servings=meal.servings,
            instructions=meal.instructions,
            macros=m,
        ))
        for mi in meal.ingredients:
            grams_by_ing[mi.ingredient_id] = grams_by_ing.get(mi.ingredient_id, 0.0) + mi.grams

    grocery_list: list[GroceryItem] = []
    total_cost = 0.0
    for ing_id, grams in grams_by_ing.items():
        if ing_id in owned:
            continue
        ing = by_id[ing_id]
        cost = round(grams / 100.0 * ing.price_per_100g, 2)
        total_cost += cost
        grocery_list.append(GroceryItem(ingredient_id=ing_id, name=ing.name,
                                        grams=round(grams, 1), cost=cost))
    total_cost = round(total_cost, 2)

    daily = Macros(
        calories=round(total.calories / DAYS, 1),
        protein=round(total.protein / DAYS, 1),
        carbs=round(total.carbs / DAYS, 1),
        fat=round(total.fat / DAYS, 1),
    )

    return ComputedPlan(
        meals=meal_views,
        grocery_list=grocery_list,
        total_cost=total_cost,
        weekly_budget=inputs.weekly_budget,
        within_budget=total_cost <= inputs.weekly_budget,
        daily_macros=daily,
        targets=Targets(calories=inputs.target_calories, protein=inputs.target_protein),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_plan.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/plan.py tests/test_plan.py
git commit -m "feat: deterministic macro/cost/grocery computation"
```

---

### Task 6: Meal generator (Claude call + ID validation)

**Files:**
- Create: `app/generator.py`
- Test: `tests/test_generator.py`

- [ ] **Step 1: Write the failing test**

`tests/test_generator.py`:
```python
from app.models import (
    Goal, Ingredient, Meal, MealIngredient, GeneratedPlan, PlanInputs,
)
from app.generator import build_system_prompt, build_user_prompt, generate

CATALOG = [
    Ingredient(id="rice_white", name="White rice", category="grain", tags=["vegan"],
               allergens=[], kcal_per_100g=130, protein_per_100g=2.7, carbs_per_100g=28,
               fat_per_100g=0.3, price_per_100g=0.10),
    Ingredient(id="chicken_breast", name="Chicken breast", category="protein", tags=[],
               allergens=[], kcal_per_100g=165, protein_per_100g=31, carbs_per_100g=0,
               fat_per_100g=3.6, price_per_100g=1.10),
]

INPUTS = PlanInputs(weekly_budget=35, goal=Goal.bulk, bodyweight_lb=180,
                    max_cook_minutes=90, target_calories=3050, target_protein=180)


class _Resp:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _Messages:
    def __init__(self, parsed):
        self._parsed = parsed
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp(self._parsed)


class _Client:
    def __init__(self, parsed):
        self.messages = _Messages(parsed)


def test_system_prompt_lists_ids_and_constrains():
    sp = build_system_prompt(CATALOG)
    assert "rice_white" in sp
    assert "chicken_breast" in sp
    assert "ONLY" in sp


def test_user_prompt_includes_inputs():
    up = build_user_prompt(INPUTS)
    assert "3050" in up
    assert "35" in up
    assert "90" in up


def test_generate_drops_invalid_ingredient_ids():
    parsed = GeneratedPlan(meals=[
        Meal(name="Bowl", cook_time_minutes=20, servings=2, instructions="...",
             ingredients=[MealIngredient(ingredient_id="rice_white", grams=200),
                          MealIngredient(ingredient_id="not_in_catalog", grams=100)]),
    ])
    client = _Client(parsed)
    result = generate(INPUTS, CATALOG, client=client)
    ids = [mi.ingredient_id for mi in result.meals[0].ingredients]
    assert ids == ["rice_white"]
    # confirm we passed our schema + model
    assert client.messages.last_kwargs["model"] == "claude-opus-4-8"
    assert client.messages.last_kwargs["output_format"] is GeneratedPlan
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.generator'`

- [ ] **Step 3: Write `app/generator.py`**

```python
import anthropic

from app.models import GeneratedPlan, Ingredient, PlanInputs

MODEL = "claude-opus-4-8"


def build_system_prompt(catalog: list[Ingredient]) -> str:
    lines = [
        f"- {i.id}: {i.name} ({i.category}; "
        f"{i.kcal_per_100g} kcal, {i.protein_per_100g}g protein per 100g)"
        for i in catalog
    ]
    catalog_block = "\n".join(lines)
    return (
        "You are a meal planner for college students on a budget. "
        "Compose a week of meals using ONLY the ingredients in the catalog below, "
        "referencing each by its exact id. Express every quantity in grams. "
        "Produce 3 to 5 distinct meals (meal-prep style) that together cover the week. "
        "Respect the per-week cooking-time limit, aim for the daily calorie and protein "
        "targets across the week (week total ~= 7x the daily target), and keep the total "
        "grocery cost at or under the weekly budget. Maximize variety; avoid repeating the "
        "same meal. Do not invent ingredients or output any nutrition or price numbers — "
        "only ids and gram amounts.\n\n"
        "INGREDIENT CATALOG (id: name):\n"
        f"{catalog_block}"
    )


def build_user_prompt(inputs: PlanInputs) -> str:
    owned = ", ".join(inputs.owned_ingredient_ids) or "none"
    avoid = ", ".join(inputs.avoid_allergens) or "none"
    return (
        f"Goal: {inputs.goal.value}\n"
        f"Daily calorie target: {inputs.target_calories} kcal\n"
        f"Daily protein target: {inputs.target_protein} g\n"
        f"Weekly grocery budget: ${inputs.weekly_budget}\n"
        f"Max total cooking time for the week: {inputs.max_cook_minutes} minutes\n"
        f"Dietary pattern: {inputs.dietary_pattern}\n"
        f"Allergens to avoid: {avoid}\n"
        f"Ingredients already owned (still usable, no need to buy): {owned}\n\n"
        "Return the meal plan."
    )


def generate(inputs: PlanInputs, catalog: list[Ingredient], client=None) -> GeneratedPlan:
    client = client or anthropic.Anthropic()
    system = [{
        "type": "text",
        "text": build_system_prompt(catalog),
        "cache_control": {"type": "ephemeral"},
    }]
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": build_user_prompt(inputs)}],
        output_format=GeneratedPlan,
    )
    plan = resp.parsed_output

    valid_ids = {i.id for i in catalog}
    for meal in plan.meals:
        meal.ingredients = [mi for mi in meal.ingredients if mi.ingredient_id in valid_ids]
    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_generator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/generator.py tests/test_generator.py
git commit -m "feat: catalog-constrained meal generator via messages.parse"
```

---

### Task 7: Event storage (demand signal)

**Files:**
- Create: `app/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

`tests/test_storage.py`:
```python
from app.storage import init_db, log_event, get_stats


def test_log_and_stats(tmp_path):
    db = tmp_path / "events.db"
    init_db(db)
    log_event(db, "plan_generated")
    log_event(db, "plan_generated")
    log_event(db, "email_captured", email="a@b.com")
    stats = get_stats(db)
    assert stats["plans_generated"] == 2
    assert stats["emails_captured"] == 1
    assert stats["conversion"] == 0.5


def test_get_stats_empty(tmp_path):
    db = tmp_path / "events.db"
    init_db(db)
    stats = get_stats(db)
    assert stats["plans_generated"] == 0
    assert stats["conversion"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.storage'`

- [ ] **Step 3: Write `app/storage.py`**

```python
import sqlite3
from datetime import datetime, timezone


def _conn(path):
    return sqlite3.connect(str(path))


def init_db(path) -> None:
    conn = _conn(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "type TEXT NOT NULL, "
        "email TEXT, "
        "created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def log_event(path, event_type: str, email: str | None = None) -> None:
    conn = _conn(path)
    conn.execute(
        "INSERT INTO events (type, email, created_at) VALUES (?, ?, ?)",
        (event_type, email, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_stats(path) -> dict:
    conn = _conn(path)
    plans = conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'plan_generated'"
    ).fetchone()[0]
    emails = conn.execute(
        "SELECT COUNT(*) FROM events WHERE type = 'email_captured'"
    ).fetchone()[0]
    conn.close()
    conversion = round(emails / plans, 3) if plans else 0.0
    return {"plans_generated": plans, "emails_captured": emails, "conversion": conversion}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/storage.py tests/test_storage.py
git commit -m "feat: SQLite event logging for demand metrics"
```

---

### Task 8: FastAPI app + templates

**Files:**
- Create: `app/main.py`
- Create: `app/templates/form.html`
- Create: `app/templates/targets.html`
- Create: `app/templates/results.html`
- Create: `app/templates/thanks.html`
- Create: `app/static/styles.css`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing test**

`tests/test_web.py`:
```python
from fastapi.testclient import TestClient

import app.main as main
from app.models import Goal, GeneratedPlan, Meal, MealIngredient

client = TestClient(main.app)


def test_get_form():
    r = client.get("/")
    assert r.status_code == 200
    assert "Weekly budget" in r.text


def test_post_targets_computes_defaults():
    r = client.post("/targets", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120",
        "dietary_pattern": "none",
    })
    assert r.status_code == 200
    assert "2700" in r.text  # computed calorie default
    assert "180" in r.text   # computed protein default


def test_post_plan_renders_results(monkeypatch):
    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", cook_time_minutes=20, servings=2,
             instructions="Cook.", ingredients=[
                 MealIngredient(ingredient_id="rice_white", grams=200),
                 MealIngredient(ingredient_id="chicken_breast", grams=150)]),
    ])
    monkeypatch.setattr(main, "generate", lambda inputs, catalog, client=None: fake)
    r = client.post("/plan", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "activity_level": "light", "max_cook_minutes": "120",
        "dietary_pattern": "none", "target_calories": "2700", "target_protein": "180",
    })
    assert r.status_code == 200
    assert "Chicken &amp; rice" in r.text or "Chicken & rice" in r.text
    assert "Grocery list" in r.text


def test_post_signup_thanks():
    r = client.post("/signup", data={"email": "student@example.com"})
    assert r.status_code == 200
    assert "Thanks" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `app/main.py`**

```python
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models import PlanInputs, Goal
from app.catalog import load_catalog, catalog_by_id, filter_catalog
from app.targets import compute_targets
from app.generator import generate
from app.plan import compute_plan
from app import storage

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent

CATALOG = load_catalog(ROOT / "data" / "ingredients.json")
DB_PATH = ROOT / "events.db"
storage.init_db(DB_PATH)

ALLERGENS = ["dairy", "eggs", "fish", "nuts", "soy", "gluten", "shellfish"]

app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.get("/", response_class=HTMLResponse)
def form(request: Request):
    return templates.TemplateResponse(
        "form.html",
        {"request": request, "catalog": CATALOG, "allergens": ALLERGENS},
    )


@app.post("/targets", response_class=HTMLResponse)
def targets(
    request: Request,
    weekly_budget: float = Form(...),
    goal: Goal = Form(...),
    bodyweight_lb: float = Form(...),
    activity_level: str = Form("light"),
    max_cook_minutes: int = Form(...),
    dietary_pattern: str = Form("none"),
    avoid_allergens: list[str] = Form(default=[]),
    owned_ingredient_ids: list[str] = Form(default=[]),
):
    t = compute_targets(bodyweight_lb, goal, activity_level)
    return templates.TemplateResponse("targets.html", {
        "request": request,
        "targets": t,
        "weekly_budget": weekly_budget,
        "goal": goal.value,
        "bodyweight_lb": bodyweight_lb,
        "activity_level": activity_level,
        "max_cook_minutes": max_cook_minutes,
        "dietary_pattern": dietary_pattern,
        "avoid_allergens": avoid_allergens,
        "owned_ingredient_ids": owned_ingredient_ids,
    })


@app.post("/plan", response_class=HTMLResponse)
def plan(
    request: Request,
    weekly_budget: float = Form(...),
    goal: Goal = Form(...),
    bodyweight_lb: float = Form(...),
    activity_level: str = Form("light"),
    max_cook_minutes: int = Form(...),
    dietary_pattern: str = Form("none"),
    avoid_allergens: list[str] = Form(default=[]),
    owned_ingredient_ids: list[str] = Form(default=[]),
    target_calories: float = Form(...),
    target_protein: float = Form(...),
):
    inputs = PlanInputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids,
        target_calories=target_calories, target_protein=target_protein,
    )
    filtered = filter_catalog(CATALOG, dietary_pattern, avoid_allergens)
    generated = generate(inputs, filtered)
    computed = compute_plan(generated, catalog_by_id(CATALOG), inputs)
    storage.log_event(DB_PATH, "plan_generated")
    return templates.TemplateResponse(
        "results.html", {"request": request, "plan": computed}
    )


@app.post("/signup", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(...)):
    storage.log_event(DB_PATH, "email_captured", email=email)
    return templates.TemplateResponse("thanks.html", {"request": request})


@app.get("/stats")
def stats():
    return storage.get_stats(DB_PATH)
```

- [ ] **Step 4: Write `app/templates/form.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Grocery Helper</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
<main>
  <h1>Plan a week of affordable, macro-friendly meals</h1>
  <form method="post" action="/targets">
    <label>Weekly budget ($)
      <input type="number" name="weekly_budget" min="1" step="1" value="40" required>
    </label>

    <label>Fitness goal
      <select name="goal">
        <option value="bulk">Bulk (build muscle)</option>
        <option value="maintain" selected>Maintain</option>
        <option value="cut">Cut (lean out)</option>
      </select>
    </label>

    <label>Bodyweight (lb)
      <input type="number" name="bodyweight_lb" min="50" step="1" value="170" required>
    </label>

    <label>Activity level
      <select name="activity_level">
        <option value="sedentary">Sedentary</option>
        <option value="light" selected>Lightly active</option>
        <option value="active">Active</option>
      </select>
    </label>

    <label>Max cooking time per week (minutes)
      <input type="number" name="max_cook_minutes" min="15" step="15" value="120" required>
    </label>

    <label>Dietary pattern
      <select name="dietary_pattern">
        <option value="none" selected>No restriction</option>
        <option value="vegetarian">Vegetarian</option>
        <option value="vegan">Vegan</option>
      </select>
    </label>

    <fieldset>
      <legend>Avoid allergens</legend>
      {% for a in allergens %}
        <label class="inline"><input type="checkbox" name="avoid_allergens" value="{{ a }}"> {{ a }}</label>
      {% endfor %}
    </fieldset>

    <label>Ingredients you already own (optional)
      <select name="owned_ingredient_ids" multiple size="6">
        {% for i in catalog %}
          <option value="{{ i.id }}">{{ i.name }}</option>
        {% endfor %}
      </select>
    </label>

    <button type="submit">Next: review my targets</button>
  </form>
</main>
</body>
</html>
```

- [ ] **Step 5: Write `app/templates/targets.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your targets</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
<main>
  <h1>Your daily targets</h1>
  <p>We estimated these from your bodyweight and goal. Adjust them if you like.</p>
  <form method="post" action="/plan">
    <label>Daily calories
      <input type="number" name="target_calories" step="10" value="{{ targets.calories }}" required>
    </label>
    <label>Daily protein (g)
      <input type="number" name="target_protein" step="5" value="{{ targets.protein }}" required>
    </label>

    <!-- carry step-1 inputs forward -->
    <input type="hidden" name="weekly_budget" value="{{ weekly_budget }}">
    <input type="hidden" name="goal" value="{{ goal }}">
    <input type="hidden" name="bodyweight_lb" value="{{ bodyweight_lb }}">
    <input type="hidden" name="activity_level" value="{{ activity_level }}">
    <input type="hidden" name="max_cook_minutes" value="{{ max_cook_minutes }}">
    <input type="hidden" name="dietary_pattern" value="{{ dietary_pattern }}">
    {% for a in avoid_allergens %}
      <input type="hidden" name="avoid_allergens" value="{{ a }}">
    {% endfor %}
    {% for oid in owned_ingredient_ids %}
      <input type="hidden" name="owned_ingredient_ids" value="{{ oid }}">
    {% endfor %}

    <button type="submit">Generate my meal plan</button>
  </form>
</main>
</body>
</html>
```

- [ ] **Step 6: Write `app/templates/results.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your meal plan</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
<main>
  <h1>Your week of meals</h1>

  <section class="summary">
    <p><strong>Estimated total cost:</strong> ${{ "%.2f"|format(plan.total_cost) }}
       / ${{ "%.0f"|format(plan.weekly_budget) }} budget
       {% if plan.within_budget %}<span class="ok">(within budget)</span>
       {% else %}<span class="over">(over budget)</span>{% endif %}</p>
    <p><strong>Daily macros:</strong>
       {{ plan.daily_macros.calories }} kcal
       (target {{ plan.targets.calories }}),
       {{ plan.daily_macros.protein }}g protein
       (target {{ plan.targets.protein }}),
       {{ plan.daily_macros.carbs }}g carbs,
       {{ plan.daily_macros.fat }}g fat</p>
    <p class="note">Cost and macros are estimates computed from a fixed ingredient catalog.</p>
  </section>

  <h2>Meals</h2>
  {% for meal in plan.meals %}
    <article class="meal">
      <h3>{{ meal.name }}</h3>
      <p class="meta">{{ meal.cook_time_minutes }} min · {{ meal.servings }} servings ·
         {{ meal.macros.calories }} kcal · {{ meal.macros.protein }}g protein</p>
      <ul>
        {% for ing in meal.ingredients %}
          <li>{{ ing.name }} — {{ ing.grams }} g</li>
        {% endfor %}
      </ul>
      <p>{{ meal.instructions }}</p>
    </article>
  {% endfor %}

  <h2>Grocery list</h2>
  <table>
    <thead><tr><th>Item</th><th>Amount</th><th>Est. cost</th></tr></thead>
    <tbody>
      {% for g in plan.grocery_list %}
        <tr><td>{{ g.name }}</td><td>{{ g.grams }} g</td><td>${{ "%.2f"|format(g.cost) }}</td></tr>
      {% endfor %}
    </tbody>
  </table>

  <section class="cta">
    <h2>Want a fresh plan every week?</h2>
    <form method="post" action="/signup">
      <label>Drop your email and we'll send new plans
        <input type="email" name="email" placeholder="you@school.edu" required>
      </label>
      <button type="submit">Keep me posted</button>
    </form>
  </section>

  <p><a href="/">Start over</a></p>
</main>
</body>
</html>
```

- [ ] **Step 7: Write `app/templates/thanks.html`**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Thanks</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
<main>
  <h1>Thanks — you're on the list!</h1>
  <p>We'll be in touch with fresh weekly plans.</p>
  <p><a href="/">Make another plan</a></p>
</main>
</body>
</html>
```

- [ ] **Step 8: Write `app/static/styles.css`**

```css
body { font-family: system-ui, sans-serif; margin: 0; background: #fafafa; color: #1a1a1a; }
main { max-width: 640px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.5rem; }
label { display: block; margin: 12px 0; font-weight: 600; }
label.inline { display: inline-block; margin-right: 12px; font-weight: 400; }
input, select { display: block; width: 100%; padding: 8px; margin-top: 4px;
  font-size: 1rem; box-sizing: border-box; }
label.inline input { display: inline-block; width: auto; }
fieldset { margin: 12px 0; }
button { margin-top: 16px; padding: 10px 16px; font-size: 1rem; cursor: pointer;
  background: #1a1a1a; color: #fff; border: 0; border-radius: 6px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #ddd; }
.meal { border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px 16px; margin: 12px 0; }
.meta { color: #555; font-size: 0.9rem; }
.summary { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px 16px; }
.note { color: #777; font-size: 0.85rem; }
.ok { color: #137333; } .over { color: #b00020; }
.cta { background: #eef6ff; border-radius: 8px; padding: 16px; margin-top: 24px; }
```

- [ ] **Step 9: Run the web tests to verify they pass**

Run: `pytest tests/test_web.py -v`
Expected: PASS (4 passed)

- [ ] **Step 10: Commit**

```bash
git add app/main.py app/templates app/static tests/test_web.py
git commit -m "feat: FastAPI two-step flow + templates"
```

---

### Task 9: Full-suite + live verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -v`
Expected: all tests pass (models, catalog, targets, plan, generator, storage, web).

- [ ] **Step 2: Live smoke-test the generator against the real API**

Create `.env` from `.env.example` with a real `ANTHROPIC_API_KEY`, then run:
```bash
. .venv/bin/activate
python -c "
import os
from dotenv import load_dotenv; load_dotenv()
from app.catalog import load_catalog, catalog_by_id, filter_catalog
from app.generator import generate
from app.plan import compute_plan
from app.models import PlanInputs, Goal
cat = load_catalog('data/ingredients.json')
inp = PlanInputs(weekly_budget=40, goal=Goal.bulk, bodyweight_lb=180,
                 max_cook_minutes=120, target_calories=3050, target_protein=180)
g = generate(inp, filter_catalog(cat))
cp = compute_plan(g, catalog_by_id(cat), inp)
print('meals:', [m.name for m in cp.meals])
print('total cost:', cp.total_cost, 'within budget:', cp.within_budget)
print('daily:', cp.daily_macros)
"
```
Expected: 3–5 meals printed, all ingredients resolve (no KeyError), cost and daily macros look plausible vs. the targets/budget.

- [ ] **Step 3: Run the app locally and click through**

Run: `uvicorn app.main:app --reload`
Then open `http://127.0.0.1:8000/`, complete the form → review targets → generate a plan → submit an email. Confirm each page renders.

- [ ] **Step 4: Confirm the demand events were recorded**

Run: `curl -s http://127.0.0.1:8000/stats`
Expected: JSON like `{"plans_generated": 1, "emails_captured": 1, "conversion": 1.0}`.

- [ ] **Step 5: Commit any fixes from verification, then summarize**

```bash
git add -A && git commit -m "chore: verification fixes" || echo "nothing to commit"
```

---

## Post-MVP (out of scope, do only if validation succeeds)

Expand the catalog to ~100–150 ingredients (pull macros from USDA FoodData Central, curate prices); deploy to a free host (Render/Railway/Fly) with `ANTHROPIC_API_KEY` set and confirm the public URL works on a phone, ready to share. Everything else (accounts, payments, images, saved history, live price APIs, multi-week planning) stays out until the demand signal justifies it.

---

## Self-Review

**Spec coverage:** inputs (budget/goal/bodyweight→targets/cook-time/dietary/owned) — Tasks 2,4,8; catalog-constrained hybrid engine — Tasks 3,6; deterministic macros/cost/grocery — Task 5; editable targets two-step flow — Task 8; demand signal (email + completion) — Tasks 7,8; verification — Task 9. The Mifflin–St Jeor deviation is documented at the top and in Task 4. No spec requirement is left without a task.

**Placeholder scan:** every code/test step shows complete code; no TBD/TODO; commands have expected output.

**Type consistency:** `compute_targets(bodyweight_lb, goal, activity_level)`, `filter_catalog(catalog, dietary_pattern, avoid_allergens)`, `catalog_by_id`, `macros_for_meal(meal, by_id)`, `compute_plan(generated, by_id, inputs)`, `generate(inputs, catalog, client=None)`, `log_event(path, event_type, email=None)`, `get_stats(path)`, and the model class/field names (`GeneratedPlan.meals`, `ComputedPlan.*`, `MealView`, `GroceryItem`) are used identically everywhere they appear.
