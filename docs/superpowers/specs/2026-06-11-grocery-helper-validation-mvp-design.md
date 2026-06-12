# Grocery Helper — Validation MVP Design

## Context

**The problem.** College students on a budget who are trying to hit fitness/macro goals
spend a lot of mental energy planning affordable, macro-friendly meals. The README frames
the app as relieving "mental, financial, and time burden for college students struggling to
meet diet goals on a strict budget and tight schedule."

**Why this build, framed as a test.** The core *assumption* — "students want a tool that
reduces the time and effort of planning affordable, macro-friendly meals" — is unproven.
So this is a **demand-validation MVP**, not a polished product. The goal is to get a
*believable* result in front of students fast and measure whether they actually want it.
Success = learning, cheaply.

**Decisions locked during brainstorming:**
- **Goal:** Validate demand (learning over polish).
- **Engine:** Hybrid. The LLM invents meals; **all numbers (macros, cost) are computed
  deterministically from a curated ingredient catalog** — not generated. This is what makes
  the output trustworthy and sidesteps the "AI gives bad approximations" failure mode.
- **Primary axis:** Fitness goal — **bulk / maintain / cut** — drives daily calorie & macro
  targets. Dietary restrictions are a secondary filter.
- **Targets:** Computed defaults (from bodyweight + goal via Mifflin–St Jeor), **editable** by
  the student.
- **Platform:** Web app, shareable link, **no accounts** (friction kills validation).
- **Stack:** **Python — FastAPI backend + minimal server-rendered frontend** (developer knows
  Python, is new to web dev and wants to learn it).
- **Demand signal:** Email capture + completion rate.

## The key architectural idea: a fixed, pre-priced ingredient catalog

The linchpin. We maintain a catalog of ~100–150 common, student-budget ingredients. Each entry
carries its macros (per 100 g), price (per 100 g), and tags (dietary + allergens). **The LLM is
constrained to compose meals only from this catalog**, returning ingredient IDs + gram
quantities. Because every ingredient is in the catalog:
- Macros and cost are a deterministic lookup + arithmetic — never hallucinated.
- Dietary filtering = filter the catalog before the LLM sees it.
- "Owned ingredients" = subtract from the grocery list.
- No fuzzy name-matching between LLM output and a nutrition DB.

This kills the accuracy concern while keeping LLM-driven variety. Macros are sourced once from
USDA FoodData Central (free) when building the catalog; prices are curated national-average
estimates (an afternoon of data entry), clearly labeled "estimated."

## Architecture / components

Small, single-purpose, independently testable modules:

```
grocery-helper/
  app/
    main.py          # FastAPI app + routes + Jinja2 rendering
    models.py        # Pydantic: form inputs, Meal, GeneratedPlan, ComputedPlan
    targets.py       # bodyweight+goal -> default calorie & protein targets (pure)
    catalog.py       # load catalog; filter by dietary restrictions
    generator.py     # build prompt, call Claude (messages.parse), validate IDs
    plan.py          # meals + catalog -> macros, grocery list, cost (pure)
    storage.py       # SQLite: log plan_generated + email_captured events
    templates/       # form.html, targets.html, results.html, thanks.html
    static/styles.css
  data/
    ingredients.json # the catalog (macros + price + tags); built once
  tests/
    test_targets.py
    test_plan.py
    test_generator.py   # uses a fixed mocked Claude response (no live API)
  requirements.txt
  .env.example          # ANTHROPIC_API_KEY
```

**Request flow (server-rendered, two-step so no JS is required):**
1. `GET /` → `form.html`: weekly budget, goal (bulk/maintain/cut), bodyweight (+ optional sex,
   activity level), max cooking time/week, dietary restrictions (veg/vegan/none + allergy/avoid
   multiselect), owned ingredients (multiselect from catalog).
2. `POST /targets` → `targets.compute_defaults(...)` → `targets.html` shows **editable**
   pre-filled daily calories + protein; other inputs carried in hidden fields.
3. `POST /plan` → `catalog.filter(dietary)` → `generator.generate(...)` → `plan.compute(...)` →
   `results.html` (3–5 meals, grocery list, total cost vs. budget, daily macros vs. targets) +
   log `plan_generated`. Ends with the email CTA: *"Want a fresh plan every week? Drop your email."*
4. `POST /signup` → `storage.save_email(...)` → log `email_captured` → `thanks.html`.

## Data model (Pydantic, in `models.py`)

- `PlanInputs`: budget, goal, bodyweight, sex?, activity?, max_cook_minutes, dietary tags,
  allergens/avoid, owned_ingredient_ids, target_calories, target_protein.
- `MealIngredient`: `ingredient_id: str`, `grams: float`.
- `Meal`: `name`, `ingredients: list[MealIngredient]`, `cook_time_minutes`, `servings`,
  `instructions` (brief).
- `GeneratedPlan`: `meals: list[Meal]` (3–5). ← **this is the LLM's structured output**.
- `ComputedPlan`: meals with per-meal + daily macros, aggregated grocery list (minus owned),
  total cost, and target/budget comparisons. ← computed by `plan.py`, never by the LLM.

**Catalog entry** (`ingredients.json`): `id`, `name`, `category`, `tags` (e.g. `vegetarian`,
`vegan`), `allergens` (e.g. `dairy`, `nuts`, `gluten`), `kcal_per_100g`, `protein_per_100g`,
`carbs_per_100g`, `fat_per_100g`, `price_per_100g`.

## Targets (`targets.py`, pure + unit-tested)

- Maintenance kcal via **Mifflin–St Jeor** (needs bodyweight; sex/activity optional, sensible
  defaults when omitted).
- Goal adjustment: **bulk** = +~350 kcal, **cut** = −~500 kcal, **maintain** = 0.
- Default protein ≈ **1 g/lb bodyweight**.
- Returns pre-filled defaults the student can overwrite on the `targets` page. Meals are fit to
  the *final* (possibly edited) numbers.

## Meal generation (`generator.py`)

Per the claude-api skill:
- Model **`claude-opus-4-8`**; `client.messages.parse(..., output_format=GeneratedPlan)` for
  schema-guaranteed structured output; `thinking={"type": "adaptive"}`.
- **System prompt** holds the stable instructions + the (dietary-filtered) catalog, with
  `cache_control: {"type": "ephemeral"}` so the catalog is prompt-cached across requests
  (cost/latency win). Instructions: use ONLY catalog ingredient IDs; quantities in grams;
  produce 3–5 meals covering the week; respect max cook time and the daily targets
  (week total ≈ 7 × daily target) and weekly budget; maximize variety / avoid repetition.
- **User message** holds the per-request inputs (targets, budget, cook time, owned IDs).
- **Validation:** every returned `ingredient_id` must exist in the (filtered) catalog. On a bad
  ID, retry once with an error note; if still bad, drop that ingredient. The model never supplies
  numbers, so it cannot corrupt macros/cost.

Reuse the SDK's `messages.parse()` + Pydantic path (per the skill's structured-output guidance) —
do not hand-roll JSON parsing or a manual schema.

## Plan computation (`plan.py`, pure + unit-tested)

- Per-meal macros = Σ `grams/100 × per-100g macro` from catalog. Per-meal cost =
  Σ `grams/100 × price_per_100g`.
- **Grocery list** = aggregate grams per ingredient across all meals, **minus owned**, with cost.
- **Total cost** vs. weekly budget; **daily macros** = total plan macros / 7, vs. targets.
- Interpretation (stated explicitly for the MVP): the 3–5 meals are the week's recipes
  (meal-prep style); ingredient grams are totals for all servings of that meal; daily macros =
  weekly total / 7.

## Demand signal (`storage.py`)

SQLite, two event types with timestamps: `plan_generated` and `email_captured` (email + optional
reference to inputs). Metrics: **completion rate** (plans generated) and **email conversion**
(emails / plans). A trivial `GET /stats` (or a direct DB query) reads them. No accounts, no PII
beyond the volunteered email.

## LLM/config specifics

- `claude-opus-4-8` is the default per the claude-api skill. (If request volume makes cost a
  concern later, `claude-haiku-4-5` is the cheap swap — the developer's call, not a silent
  downgrade.)
- API key via `ANTHROPIC_API_KEY` env var (`.env.example` documents it); never hardcoded.
- Adaptive thinking + prompt-cached catalog as above.

## Build order (milestones, each independently verifiable)

1. **Catalog** — build `data/ingredients.json` (~100–150 items): macros from USDA FDC, curated
   prices, dietary/allergen tags. *Verify:* loads, schema-valid, covers protein/carb/fat/veg/dairy
   staples. (Biggest data task; ~an afternoon.)
2. **`targets.py`** + `test_targets.py` — known bodyweight/goal → expected kcal/protein. *Verify:*
   tests pass.
3. **`plan.py`** + `test_plan.py` — fixed meals + catalog → expected macros, grocery aggregation,
   owned subtraction, total cost. *Verify:* tests pass.
4. **`generator.py`** + `test_generator.py` — prompt build + ID validation against a **mocked**
   Claude response fixture (no live calls in tests). *Verify:* parses + validates the fixture.
5. **FastAPI app + templates** — wire the 4 routes and the two-step flow. *Verify:* run locally,
   complete form → targets → plan → email end-to-end.
6. **`storage.py`** — log the two events; confirm rows land. *Verify:* generate a plan + submit an
   email, see both events in SQLite.
7. **Deploy** — push to a free host (Render/Railway/Fly) with `ANTHROPIC_API_KEY` set; confirm the
   public URL works on a phone browser, ready to share in a group chat / Discord.

## Verification (end-to-end)

- `pytest` green for targets, plan, generator (mocked).
- One **live** `generate()` call against `claude-opus-4-8` with a sample input; manually confirm
  meals use only catalog IDs and the computed macros/cost look sane vs. targets/budget.
- Manual browser run of the full flow locally, then on the deployed URL.
- Confirm `plan_generated` and `email_captured` events are recorded (the demand metrics).

## Out of scope (YAGNI for the validation MVP)

Accounts/auth, payments, recipe images, saved history, live grocery-price APIs or store
integration, mobile app, multi-week planning, training a custom/local model, fuzzy ingredient
matching. Add only if validation succeeds.
