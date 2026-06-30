# Architecture

Grocery Helper is a budget-aware weekly meal-plan generator for college lifters. It is being
extended from a one-shot generator into a recurring weekly tool that remembers constraints,
enforces them deterministically, repairs plans mid-week, and adapts week-to-week.

## Stack

- **Python 3.13 + FastAPI + Jinja2** (server-rendered HTML; not an SPA).
- **Pydantic** models throughout (`app/models.py`).
- **Gemini** (`gemini-3.1-flash-lite`, `google-genai`) proposes meals as structured JSON. It is
  **never** the source of truth for numbers.
- **Neon Postgres** (`psycopg`) for persistence.
- **pytest** (`pyproject.toml` sets `pythonpath=["."]`).

## Modules (`app/`)

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI routes; orchestrates generate → correct → validate → render. |
| `models.py` | Pydantic models (`Ingredient`, `Meal`, `GeneratedPlan`, `PlanInputs`, `ComputedPlan`, …). |
| `targets.py` | Deterministic calorie/macro targets from bodyweight, activity, goal. |
| `catalog.py` | Load/filter the ingredient catalog (`data/ingredients.json`). |
| `generator.py` | Gemini prompt + structured-output parsing + ground-truth retry loop. |
| `plan.py` | Deterministic engine: pricing, macro math, goal-aware correction, grocery lists. |
| `validate.py` | Deterministic plan validator (budget/macro/time/equipment) + Budget Guarantee metrics. |
| `meal_templates.py` | Deterministic fallback/repair meal templates. |
| `repair.py` | Deterministic mid-week plan repair. |
| `auth.py` | Password hashing + session helpers (guest-first; optional accounts). |
| `storage.py` | Postgres: analytics events + per-user data (users, profiles, pantry, plans, repairs, check-ins). |

## Key principles

- **Gemini proposes; Python decides.** All price, macro, time, budget, and equipment validation
  is deterministic in `plan.py` / `validate.py`. A plan is only shown as "valid" after the
  deterministic validator confirms it.
- **Whole-package pricing.** Ingredients are sold as whole Walmart packages; cost is
  `ceil(grams / package_size_g) * package_price` (`plan.package_cost`). Pantry staples
  (seasonings, oil) are a separate one-time stock-up, excluded from the weekly budget.
- **Batch-cook model.** Each meal is cooked once for the week (7 servings, one per day).
  Cook-time transparency exposes total weekly prep, a per-session estimate, and the largest
  single session; "save time" biases toward batch-friendly (oven/sheet-pan/one-pot) meals.

## Identity & persistence

- **Guest-first.** Generation works with no account. After a plan is generated the user may
  register; guests get no persisted data. Registering turns on persistence and saves the
  just-generated plan.
- **Auth** is email + password (bcrypt) with a signed-cookie session (Starlette
  `SessionMiddleware`).
- **No Postgres RLS.** The app connects with a single shared Neon role, so per-user isolation is
  enforced in application code (every query filters by the session `user_id`).
- **Plans persist as JSON snapshots** (`weekly_plans.snapshot`/`inputs`/`progress` as `jsonb`)
  rather than fully normalized day/meal tables — matching the app's existing serialize-to-JSON
  pattern.

## Running

```bash
pip install -r requirements.txt        # needs a .env with GEMINI_API_KEY and DATABASE_URL
uvicorn app.main:app --reload
pytest                                 # offline; DB integration tests skip unless DATABASE_URL is set
```
