# Coach UI Frontend Rebuild — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the frontend of grocery-helper to match the Coach design (screens A1–A16 in `Coach UI.dc.html`), per `CLAUDE_CODE_SPEC.md`.

**Architecture:** Server-rendered Jinja templates on the existing **FastAPI** app (the spec says Flask; the intent — server-rendered + one CSS file + one small vanilla-JS file, no build step — carries over unchanged). The wizard's inputs move from hidden-field relaying into the signed-cookie session (`session["wizard"]`). Generation becomes async: a new in-memory job store (`app/jobs.py`) runs the two `generate()` calls on a background thread; the browser polls `GET /plan/status`. Generated plans live in the job store keyed by `session["job_id"]` (they no longer fit in cookies or hidden fields). Saving persists to Postgres exactly as today.

**Tech Stack:** FastAPI + Jinja2 + Starlette sessions (existing), plain CSS (`styles.css` rewritten), vanilla JS (`ui.js` rewritten, <10KB), self-hosted Bricolage Grotesque variable woff2. No new dependencies.

## Global Constraints (from spec)

- No React, no build step, no new pip/npm dependencies.
- Visual source of truth: `Coach UI.dc.html` A1–A16. Mockups win on pixels; `CLAUDE_CODE_SPEC.md` wins on behavior.
- Design tokens exactly as spec §1 (`--bg:#F7F8F5`, `--accent:#2F8E7B`, `--accent-dark:#22615A`, `--band:#22403A`, `--warn:#7A4A1E`, radii 24/16/14/999px, etc.).
- Headings/buttons: Bricolage Grotesque 700/800 self-hosted woff2 in `app/static/fonts/`, `font-display: swap`. Body: system stack. Inputs ≥16px. `tabular-nums` on all money/macro figures.
- Small text/links use `--accent-dark`, never `--accent` (contrast ≥4.5:1). Warn text `--warn` on `--warn-tint`.
- Copy tone: warm second person, concrete numbers, light emoji.
- Every wizard step is POST → redirect → GET. JS enhances, never gates: full anonymous flow must work with JS disabled.
- Landmarks, one `<h1>`/page, native inputs+labels for all chips/option-cards, `:focus-visible` ring (2px `--accent-dark`, offset 2px), touch targets ≥44px, emoji `aria-hidden`.
- Total JS < 10KB unminified. Hover styles inside `@media (hover: hover)` only. Reduced-motion variants per spec §8.
- Tests: `pytest` must stay offline-green (stub `generate`/`generate_one`/storage as `tests/test_web.py` does today; also stub `jobs._spawn` to run inline).
- Commit after each passing task (branch `grocery-helper-mvp`), messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Shared interfaces (referenced by every task)

**Session shapes** (all values JSON-serializable, kept small — cookie limit ~4KB):

```python
request.session["wizard"] = {
    "weekly_budget": 40.0, "goal": "maintain", "bodyweight_lb": 170.0,
    "activity_level": "light",           # sedentary|light|active
    "max_cook_minutes": 120,
    "kitchen": {...},                    # KitchenProfile.model_dump(mode="json")
    "kitchen_preset": "apartment",       # apartment|dorm|shared|athlete|None (re-render highlight)
    "dietary_pattern": "none", "avoid_allergens": [],
    "owned": {"rice_white": {"qty": 4.0, "unit": "cup"}},  # qty None => "have enough"
}
request.session["job_id"] = "<uuid4 hex>"   # current/last generation job
request.session["fb_pending"] = True        # set on a user's first save, cleared when dialog shown
```

**Job store** (`app/jobs.py`):

```python
@dataclass
class Job:
    id: str
    inputs: PlanInputs
    state: str = "queued"                 # queued|running|done|failed
    budget_base: GeneratedPlan | None = None
    protein_base: GeneratedPlan | None = None
    pinned: GeneratedPlan | None = None   # swap_priciest: keep these meals, regen one slot
    swap_slot: str | None = None
    over_budget: bool = False             # budget-first variant busted its cap
    created_at: float = <time.time()>

def start(inputs, *, pinned=None, swap_slot=None) -> str   # registers job, calls _spawn, returns id
def get(job_id) -> Job | None
def _spawn(job) -> None                   # threading.Thread(_run, daemon=True); tests monkeypatch to run inline
def _run(job) -> None                     # generate both bases (or pinned single-slot), set over_budget, state
def finalize(base, inputs, filtered_by_id, budget_cap) -> tuple[ComputedPlan, PlanValidation]
                                          # moved from main._finalize so main and jobs share it
def priciest_slot(base, by_id) -> str     # marginal-cost attribution, for swap_priciest
```

**Route map after rebuild** (main.py):

| Route | Handler behavior |
|---|---|
| `GET /` | `home.html` (A1) |
| `GET /plan/new?step=1..3[&return=account]` | wizard pages, prefilled from session (+ DB for logged-in) |
| `POST /plan/new?step=1` | parse basics → session → 303 to step 2 |
| `POST /plan/new?step=2` | parse kitchen/pantry → session; if `return=account` also persist to DB and 303 `/account`, else 303 step 3 |
| `POST /plan/generate` | targets from form + session wizard → `PlanInputs` → `jobs.start` → 303 `/plan/generating` |
| `GET /plan/generating` | `generating.html`; if job done → 303 `/plan`; failed → 303 `/plan/failed` (no-JS meta refresh) |
| `GET /plan/status` | JSON `{"state": ..., "over_budget": bool}` |
| `GET /plan` | `plan.html` (A5/A12) from job; queued/running → 303 generating; no job → 303 `/` |
| `GET /plan/failed` | `plan_failed.html` (A13) |
| `POST /plan/recover` | `action=swap_priciest\|relax_protein\|raise_budget` → mutate + requeue → 303 generating |
| `POST /regenerate` | `plan_kind`,`slot`; mutate job base, return `_meal_card.html` partial (fetch) or 303 `/plan` |
| `GET/POST /login` | A11 |
| `POST /register` | from save sheet: create user, save chosen plan from job, persist kitchen+pantry, `fb_pending`, 303 `/plans/{id}` |
| `POST /plans/save` | logged-in one-tap save from job → 303 `/plans/{id}` |
| `GET /account` | A8 / A14 empty states |
| `GET /plans/{id}` | `saved_plan.html` (A5 minus tabs / A15) |
| `POST /plans/{id}/repair` | re-solve keeping valid meals pinned → update row → 303 back |
| `POST /feedback` | `{rating, comment, newsletter_optin, email?}` → feedback table + flag |
| `GET /settings`, `GET /pantry` | 301 → `/plan/new?step=2&return=account` |
| `GET /stats` | unchanged |

Removed routes: `POST /targets`, `POST /plan`, `POST /settings`, `POST /pantry`, `POST /pantry/{id}/delete`, `POST /signup`.

**Template inventory:** `base.html` (head + header block + main), `home.html`, `wizard_basics.html`, `wizard_kitchen.html`, `targets.html`, `generating.html`, `plan.html`, `plan_failed.html`, `saved_plan.html`, `account.html`, `login.html`, partials `_summary_band.html`, `_meal_card.html`, `_meal_detail.html`, `_shopping_list.html`, `_save_sheet.html`, `_feedback_dialog.html`. Deleted: `form.html`, `_kitchen_fields.html`, `settings.html`, `pantry.html`, `thanks.html`, `results.html`, `_head.html`.

**Activity label mapping:** Desk life=`sedentary`, Somewhat=`light`, Very=`active`. **Slot icons:** breakfast 🍳, lunch 🥗, dinner 🍽️, snack 🍎.

---

### Task 1: Design foundation — fonts, styles.css, base template, ui.js scaffold

**Files:**
- Create: `app/static/fonts/bricolage-grotesque-latin.woff2` (downloaded)
- Create: `app/templates/base.html`
- Modify: `app/static/styles.css` (replace wholesale), `app/static/ui.js` (replace wholesale)
- Delete: `app/templates/_head.html` (after all pages move to base.html — actually deleted in Task 2 with the page rewrites; here base.html is added alongside)

**Interfaces:**
- Produces: `base.html` with blocks `title`, `header`, `content`, `scripts`; body class hook `html.js`; CSS classes exactly as spec §2 (`.btn .btn-dark .btn-secondary .btn-ghost .btn-warn .chip .chip-own .option-card .progress-steps .slider-row .unit-input .stat-edit .meal-card .action-card .stat-chip .summary-band .tabs .sheet .check-row .ingredient-row .badge .callout .empty-card .action-bar .avatar`).
- Produces (ui.js API, all bound via `data-*` attributes): dialog open/close (`data-sheet-open="#id"`), tabs (`role=tablist` keyboard support), budget slider output, pantry chip reveal, generation polling (`body[data-poll]`), list checkbox persistence (`data-list-key`), copy list (`data-copy-target`), feedback trigger (`data-feedback`), meal-swap fetch (`form[data-swap]`).

- [ ] **Step 1: Download the variable font**

```bash
mkdir -p app/static/fonts
curl -sL -A "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120 Safari/537.36" \
  "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" \
  | grep -o "https://fonts.gstatic.com/[^)]*latin[^)]*.woff2" | head -1
# curl that URL to app/static/fonts/bricolage-grotesque-latin.woff2
```

Verify: `file app/static/fonts/*.woff2` reports "Web Open Font Format 2".

- [ ] **Step 2: Write `styles.css`** — token block verbatim from spec §1, then components in spec §2 order. Key implementation notes:
  - `@font-face { font-family:'Bricolage Grotesque'; src:url('/static/fonts/bricolage-grotesque-latin.woff2') format('woff2'); font-weight:200 800; font-display:swap; }`
  - `.chip`/`.option-card` selection via `:has(:checked)`; inputs visually hidden but focusable (`.vh` class + `:focus-visible` ring on the label via `:has(:focus-visible)`).
  - `.sheet` = `dialog` bottom sheet: `margin:auto auto 0; width:100%; max-width:640px; border-radius:24px 24px 0 0; translate + transition`; `@media (min-width:768px)` centered modal max-width 420px, radius 24px. `::backdrop` dimmed. `html:not(.js) dialog.sheet { display:block; position:static; box-shadow:none; }` (no-JS fallback: sheets render as in-page sections).
  - `.action-bar { position:sticky; bottom:0; background:linear-gradient(transparent, var(--bg) 30%); }`
  - `@media (prefers-reduced-motion: reduce)`: kill sheet slide + spinner rotation (pulsing dots via opacity keyframes).
  - Responsive per spec §7 (640px column, 768px grids/modals, 1024px handled in Task 10).
- [ ] **Step 3: Write `base.html`**

```jinja
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}grocery buddy{% endblock %}</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script>document.documentElement.className='js';</script>
</head>
<body{% block body_attrs %}{% endblock %}>
  {% block header %}{% endblock %}
  <main>{% block content %}{% endblock %}</main>
  {% block sheets %}{% endblock %}
  <script src="/static/ui.js" defer></script>
</body>
</html>
```

- [ ] **Step 4: Write `ui.js` scaffold** — IIFE, one `DOMContentLoaded`-free init (script deferred), each feature no-ops when its `data-*` hook is absent. Implement now: dialog helpers (showModal/close/ESC/backdrop click/focus return), tabs (click + ArrowLeft/Right, `aria-selected`, hidden panels — no-JS shows both stacked), budget slider→output+caption, pantry chip reveal + "+ something else" row append. Stub-register the rest (polling, persistence, copy, feedback, swap) as functions filled by later tasks — but write them fully here if trivial (~all are <15 lines; write everything now, later tasks only wire markup).
- [ ] **Step 5: Verify** — `pytest` still green (old templates untouched); `wc -c app/static/ui.js` < 10240.
- [ ] **Step 6: Commit** `feat: Coach design foundation — tokens, components, fonts, base template, ui.js`

### Task 2: Home + wizard steps 1–2 (A1, A2, A3) with session round-trip

**Files:**
- Create: `app/templates/home.html`, `app/templates/wizard_basics.html`, `app/templates/wizard_kitchen.html`
- Modify: `app/main.py` (new routes; delete `GET /` form logic, `POST /targets` stays until Task 3), `tests/test_web.py`
- Delete: `app/templates/form.html`, `app/templates/_kitchen_fields.html`, `app/templates/settings.html`, `app/templates/pantry.html`, `app/templates/thanks.html`, `app/templates/_head.html` (move remaining pages in later tasks — keep `_head.html` until Task 7 removes the last user)

**Interfaces:**
- Produces: `session["wizard"]` shape (see Shared interfaces); helpers `_wizard(request) -> dict` (session get-or-default, merging DB prefill for logged-in users), `_owned_grams(wizard) -> dict[str,float]` (via `units.to_grams`, qty None → skip = "have enough").
- Form field names — step 1: `weekly_budget` (range 20–120), `goal` (radio bulk|maintain|cut), `bodyweight_lb`, `max_cook_minutes`, `activity_level` (radio). Step 2: `kitchen_preset` (radio apartment|dorm|shared|athlete), `equipment` (checkbox list of `kitchen.EQUIPMENT` tokens), `owned_ids` (checkboxes), per-id `owned_qty_<id>` + `owned_unit_<id>`, `owned_custom` (free text), `dietary_pattern`, `avoid_allergens`.

- [ ] **Step 1: Write failing tests** in `tests/test_web.py` (replace the file's form/targets/signup tests; keep the plan/regenerate tests untouched until Tasks 4–5):

```python
def test_home_is_coach_landing():
    r = client.get("/")
    assert r.status_code == 200
    assert "Eat well on a college budget" in r.text
    assert "/plan/new?step=1" in r.text

def test_wizard_step1_roundtrip():
    r = client.post("/plan/new?step=1", data={
        "weekly_budget": "45", "goal": "cut", "bodyweight_lb": "160",
        "max_cook_minutes": "90", "activity_level": "active"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/plan/new?step=2"
    r2 = client.get("/plan/new?step=2")
    assert "Your kitchen" in r2.text
    r_back = client.get("/plan/new?step=1")
    assert 'value="45"' in r_back.text          # session prefill survives back-nav

def test_wizard_step2_stores_kitchen_and_owned():
    client.post("/plan/new?step=1", data={"weekly_budget": "40", "goal": "maintain",
        "bodyweight_lb": "170", "max_cook_minutes": "120", "activity_level": "light"})
    r = client.post("/plan/new?step=2", data={
        "kitchen_preset": "dorm", "equipment": "microwave",
        "owned_ids": "rice_white", "owned_qty_rice_white": "4",
        "owned_unit_rice_white": "cup"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/plan/new?step=3"

def test_settings_and_pantry_redirect():
    for path in ("/settings", "/pantry"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/plan/new?step=2&return=account"
```

- [ ] **Step 2: Run** `pytest tests/test_web.py -x -q` — expect FAIL (routes missing).
- [ ] **Step 3: Implement routes + templates.** Route sketch:

```python
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html",
        {"logged_in": auth.current_user(request) is not None})

@app.get("/plan/new", response_class=HTMLResponse)
def wizard(request: Request, step: int = 1, ret: str | None = Query(None, alias="return")):
    w = _wizard(request)
    if step == 2:
        ctx = {"w": w, "presets": PRESET_CARDS, "equipment": EQUIPMENT,
               "ownables": [i for i in CATALOG if not i.pantry_staple],
               "unit_options": {i.id: owned_unit_options(i) for i in CATALOG if not i.pantry_staple},
               "allergens": ALLERGENS, "ret": ret}
        return templates.TemplateResponse(request, "wizard_kitchen.html", ctx)
    if step == 3:
        ...  # Task 3
    return templates.TemplateResponse(request, "wizard_basics.html", {"w": w})

@app.post("/plan/new")
async def wizard_post(request: Request, step: int = 1, ...):
    form = await request.form()
    w = _wizard(request)
    if step == 1:
        w.update(weekly_budget=..., goal=..., bodyweight_lb=..., max_cook_minutes=...,
                 activity_level=form.get("activity_level", "light"))
        request.session["wizard"] = w
        return RedirectResponse("/plan/new?step=2", status_code=303)
    # step 2: preset -> PRESETS[name] copy, override equipment from checkboxes,
    # collect owned {id: {qty, unit}}, diet/allergens; persist to DB when return=account & logged in
```

Templates follow mockup anchors `#a2`/`#a3` exactly: progress bar (`role="progressbar"`, visually-hidden "Step n of 3"), budget slider card with `<output>`, `.option-card` goal grid, `.unit-input` weight/time, activity pills, kitchen preset 2×2 cards, equipment chips in `<details>`, pantry chips with inline amount rows, diet/allergies `<details>`, "Skip, I'll use defaults" link (plain `<a href="/plan/new?step=3">`).
- [ ] **Step 4: Run tests** — PASS. Also `pytest -q` fully green (old `/plan` tests still pass since `POST /plan` remains until Task 4).
- [ ] **Step 5: Commit** `feat: Coach landing + wizard steps 1-2 with session round-trip`

### Task 3: Targets step (A4)

**Files:**
- Modify: `app/templates/targets.html` (rebuild), `app/main.py` (step-3 GET; delete `POST /targets`), `tests/test_web.py`

**Interfaces:**
- Consumes: `session["wizard"]`, `compute_targets(bodyweight_lb, goal, activity)`.
- Produces: step-3 form POSTs `target_calories/protein/carbs/fat` to `/plan/generate` (Task 4). For returning users, `_wizard()` prefarmed from DB (kitchen profile, pantry, last plan inputs) so `/plan/new?step=3` works straight from `/account`.

- [ ] **Step 1: Failing test**

```python
def test_wizard_step3_shows_computed_targets():
    client.post("/plan/new?step=1", data={"weekly_budget": "40", "goal": "maintain",
        "bodyweight_lb": "180", "max_cook_minutes": "120", "activity_level": "light"})
    r = client.get("/plan/new?step=3")
    assert r.status_code == 200
    assert "2700" in r.text and 'name="target_calories"' in r.text
    assert 'action="/plan/generate"' in r.text
```

- [ ] **Step 2: Run — FAIL.** **Step 3: Implement.** GET step 3: no wizard basics in session and not logged in → 303 step 1; logged-in with empty session → build from `storage.get_kitchen_profile` + `storage.list_pantry` + latest `storage.list_plans` inputs. Template: calories hero card + 3 tinted macro cards as `.stat-edit` number inputs (mockup `#a4`), helper line, CTA "Looks right — build my week 🍳".
- [ ] **Step 4: PASS + full suite.** **Step 5: Commit** `feat: targets confirmation step (A4)`

### Task 4: Async generation — jobs store, generating page, status, failure (A13)

**Files:**
- Create: `app/jobs.py`, `app/templates/generating.html`, `app/templates/plan_failed.html`, `tests/test_jobs.py`
- Modify: `app/main.py` (add `/plan/generate`, `/plan/generating`, `/plan/status`, `/plan/failed`; **delete** `POST /plan` and `POST /targets` remnants), `tests/test_web.py`

**Interfaces:**
- Produces: `jobs.start/get/_spawn/_run/finalize/priciest_slot` per Shared interfaces. `finalize` is the moved `main._finalize` (same body). `_run`: build filtered catalog, `generate(inputs, filtered, priority=...)` ×2 (or pinned single-slot via `generate_one`), compute `over_budget = not finalize(budget_base,...)[1].within_budget`, set `state`. On exception → `state="failed"`.
- Tests stub: `monkeypatch.setattr(jobs, "generate", fake_fn)` and `monkeypatch.setattr(jobs, "_spawn", lambda job: jobs._run(job))`.

- [ ] **Step 1: Failing tests** (`tests/test_jobs.py` — sync-run unit tests; `tests/test_web.py` — flow test):

```python
def test_generate_flow_polls_to_plan(monkeypatch, fake_plan):
    monkeypatch.setattr(jobs, "generate", lambda *a, **k: fake_plan)
    monkeypatch.setattr(jobs, "_spawn", lambda job: jobs._run(job))
    monkeypatch.setattr(main.storage, "log_event", lambda *a, **k: None)
    _fill_wizard(client)                          # helper: steps 1+2 posts
    r = client.post("/plan/generate", data={"target_calories": "2700",
        "target_protein": "180", "target_carbs": "326", "target_fat": "75"},
        follow_redirects=False)
    assert r.headers["location"] == "/plan/generating"
    assert client.get("/plan/status").json()["state"] == "done"
    r2 = client.get("/plan/generating", follow_redirects=False)
    assert r2.headers["location"] == "/plan"      # server-side no-JS redirect when done
```

- [ ] **Step 2: FAIL.** **Step 3: Implement** `app/jobs.py` (~90 lines; prune jobs >2h old in `start`), routes, `generating.html` (spinner, `role="status" aria-live="polite"`, rotating status lines from a fixed 5-line list swapped every 4s by JS, "usually ~20 seconds", `<noscript><meta http-equiv="refresh" content="3"></noscript>` — put the meta refresh in the page unconditionally; JS polling replaces navigation before it fires... simpler: always `<meta http-equiv="refresh" content="3">`; JS `history`-safe polling redirects sooner), `plan_failed.html` per mockup `#a13` ("That one stumped us", Try again form re-POSTs `/plan/generate` with targets carried in hidden fields from session copy, "← Back to my targets").
- [ ] **Step 4: PASS + full suite** (old `POST /plan` tests replaced by flow tests; port the stove-violation and owned-grams assertions onto the new flow). **Step 5: Commit** `feat: async plan generation with job store, generating + failure states`

### Task 5: Plan page (A5) + meal detail (A10) + shopping list (A6) + swap partial

**Files:**
- Create: `app/templates/plan.html`, `app/templates/_summary_band.html`, `app/templates/_meal_card.html`, `app/templates/_meal_detail.html`, `app/templates/_shopping_list.html`
- Modify: `app/main.py` (`GET /plan`, rework `POST /regenerate`), `tests/test_web.py`
- Delete: `app/templates/results.html`

**Interfaces:**
- `GET /plan` context: `{"variants": [{"kind","label","data": ComputedPlan,"val": PlanValidation}...], "inputs", "over_budget", "plan_key": job.id, "logged_in", "swap_estimates" (Task 6)}`.
- `_meal_card.html` context: `{"meal": MealView, "kind": str, "idx": int}` — card is a `<button>`-like link opening `dialog#meal-{{kind}}-{{idx}}`; no-JS: plain in-page anchor.
- `POST /regenerate` form: `plan_kind`, `slot` only (bases come from the session's job). JS fetch sends header `X-Partial: 1` → response is the fresh `_meal_card.html` HTML; without header → 303 `/plan`.
- `_shopping_list.html`: `.check-row`s with `data-list-key="list:{{plan_key}}"`; plain-text copy payload in `<textarea hidden id="copy-src-{{kind}}">`; badges "you own some"; `<details>` "Pantry stock-up (one-time buys) · $X".

- [ ] **Step 1: Failing tests** — port/replace old results assertions:

```python
def test_plan_page_renders_variants_and_sheets(...):
    # fill wizard, run sync job, then:
    r = client.get("/plan")
    assert "Your week, sorted" in r.text
    assert r.text.count('role="tab"') == 2 and "Budget-first" in r.text
    assert "summary-band" in r.text and "meal-card" in r.text
    assert "Shopping list" in r.text and "Save week" in r.text
    assert "<dialog" in r.text                    # server-rendered sheets

def test_regenerate_returns_partial(...):
    monkeypatch.setattr(jobs, "generate_one", lambda *a, **k: fresh_meal)
    r = client.post("/regenerate", data={"plan_kind": "budget", "slot": "dinner"},
                    headers={"X-Partial": "1"})
    assert "Fresh stir fry" in r.text and "<dialog" not in r.text
```

- [ ] **Step 2: FAIL.** **Step 3: Implement.** `plan.html`: summary band partial (band bg, `$X of $Y`, ↺ start-over link → clears wizard+job, stat-chips protein/day · cooking min · $ left), tabs Budget-first/Protein-first (both panels in DOM; no-JS stacked), meal cards per mockup `#a5`, ghost "↻ Swap a meal I don't like" (opens the meal sheets), sticky `.action-bar` (🛒 Shopping list · $X / Save week). Meal detail sheet per `#a10` (stat-chips, "You'll use" `.ingredient-row`s with "from your shelf" badges, "How to make it" `<ol>`, Swap + Done buttons, reassurance line). Swap shimmer class during fetch. `generate_one` import moves into jobs (regen mutates `job.budget_base`/`protein_base` then re-finalizes on render).
- [ ] **Step 4: PASS + full suite.** **Step 5: Commit** `feat: Coach plan page with meal detail + shopping list sheets`

### Task 6: Over-budget recovery (A12)

**Files:**
- Modify: `app/main.py` (`POST /plan/recover`), `app/templates/plan.html` + `_summary_band.html` (warn variant + `.action-card`s), `app/jobs.py` (pinned/swap support already scaffolded — wire it), `tests/test_web.py`

**Interfaces:**
- Recovery forms POST `/plan/recover` with `action` in `swap_priciest|relax_protein|raise_budget`. Mutations: `relax_protein` → `inputs.target_protein -= 10` (floor 60); `raise_budget` → `inputs.weekly_budget += 5`; `swap_priciest` → `jobs.start(inputs, pinned=budget_base, swap_slot=jobs.priciest_slot(budget_base, by_id))`. All update `session["wizard"]` numbers too, then 303 `/plan/generating`. "Keep this plan anyway" is a link to `/plan#plan-budget` (band stays warn, cards hidden via `?keep=1` query flag stored in session).

- [ ] **Step 1: Failing test** — force an over-budget fake plan (huge grams), assert `/plan` shows `summary-band is-warn`, three `action-card`s with exact labels ("Swap the priciest meal", "Relax protein to {{target-10}}g", "Bump budget to ${{budget+5}}"), and that POSTing `action=raise_budget` re-queues (status returns queued/running→done with new budget in band).
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit** `feat: over-budget recovery actions (A12)`

### Task 7: Save sheet, login, account (A7, A11, A8, A14)

**Files:**
- Create: `app/templates/_save_sheet.html`
- Modify: `app/templates/login.html` (rebuild A11), `app/templates/account.html` (rebuild A8+A14), `app/main.py` (`POST /register` reads job store; new `POST /plans/save`; account context), `tests/test_web.py`, `tests/test_auth.py` (only if selectors changed)
- Delete: `app/templates/_head.html` (last consumer gone)

**Interfaces:**
- `_save_sheet.html` (guest): email+password+hidden `plan_kind` (synced to active tab by JS; default `budget`), "Saving: {{label}} · ${{total}}" summary row, submit → `POST /register`. Logged-in: `.action-bar` "Save week" becomes a direct `POST /plans/save` form with `plan_kind`.
- `POST /register`: validate creds → `storage.create_user` → save plan from job (`jobs.finalize` + existing `_plan_record`) → `storage.save_kitchen_profile` + pantry sync from `session["wizard"]["owned"]` (grams via `to_grams`) → `session["fb_pending"]=True` → 303 `/plans/{id}`. Errors re-render `plan.html` with the sheet open + inline error (never clear inputs).
- `/account` context: `{"user", "plans", "pantry_count", "first_name" (email local-part capitalized), "initial"}`. Empty states per `#a14` (no plans card + empty pantry card with starter chips linking to step 2).

- [ ] **Step 1: Failing tests** (stub all storage fns like `tests/test_auth.py` does): register-from-job saves plan and redirects to `/plans/{id}`; `/account` renders "Welcome back" + saved-week cards with `✓ ready`/`needs fixes` badges; empty account shows "No weeks saved yet"; `/login` shows "Plan a week first — no account needed".
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit** `feat: save sheet + account + login (A7/A8/A11/A14)`

### Task 8: Saved plan view + repair (A15)

**Files:**
- Create: `app/repair.py`, `tests/test_repair.py`
- Modify: `app/templates/saved_plan.html` (rebuild reusing `_meal_card/_meal_detail/_shopping_list`; no tabs), `app/main.py` (`POST /plans/{id}/repair`), `app/storage.py` (`update_plan_snapshot`), `tests/test_web.py`

**Interfaces:**
```python
# app/repair.py
def meal_issues(rec: dict, catalog_by_id, current_pantry_keys: set[str]) -> dict[str, str]
    # meal name -> plain-language reason; checks: equipment vs stored kitchen,
    # owned ingredient no longer in pantry (uses inputs.owned_ingredient_ids ∩ pantry),
    # over budget -> priciest meal flagged "priciest meal — swap to get under budget"
def repair(rec, catalog_by_id, filtered, current_pantry_keys) -> tuple[ComputedPlan, PlanValidation, GeneratedPlan]
    # regenerate the problem slots via generate_one (others pinned), refresh owned_ids
    # from current pantry, re-finalize with budget cap
# app/storage.py
def update_plan_snapshot(plan_id, user_id, snapshot: dict, validation_status: str,
                         validation_notes: dict, estimated_total_cost) -> None
```
- `saved_plan.html`: A15 `.callout` when `validation_status == "invalid"` or `meal_issues` non-empty ("⚠ This week needs N fixes", "Fix it for me →" = the repair POST); problem meals `.is-warn` with reason line, valid meals `.is-dim`.

- [ ] **Step 1: Failing tests** — `test_repair.py`: meal using equipment the stored kitchen lacks is flagged; repair (with `generate_one` stubbed) replaces exactly the flagged meals. Web test: saved plan with invalid status renders callout + "Fix it for me".
- [ ] **Step 2: FAIL. Step 3: Implement. Step 4: PASS. Step 5: Commit** `feat: saved plan repair flow (A15)`

### Task 9: Feedback dialog (A9)

**Files:**
- Create: `app/templates/_feedback_dialog.html`
- Modify: `app/storage.py` (`feedback` table in `init_db`: `id, user_id NULL, rating INT NULL, comment TEXT, newsletter_optin BOOL, email TEXT NULL, created_at`; `save_feedback(...)`; `users` gains `feedback_done BOOLEAN NOT NULL DEFAULT FALSE` via `ALTER TABLE users ADD COLUMN IF NOT EXISTS`; `set_feedback_done(user_id)`), `app/main.py` (`POST /feedback`; render dialog on first page after first save), `app/static/ui.js` (trigger after 800ms, `localStorage.fb_done` belt-and-suspenders), `tests/test_storage.py` (gated DB test), `tests/test_web.py`

**Interfaces:**
- Dialog rendered only when `session.pop("fb_pending")` truthy AND user's `feedback_done` false. `POST /feedback` body `{rating?, comment?, newsletter_optin?, email?, dismissed?}` → `save_feedback` + `set_feedback_done` → 204. Dismiss (✕ / ESC) also posts with `dismissed=1`.

- [ ] **Step 1: Failing tests** — after register-with-save (storage stubbed), the next `/plans/{id}` response contains `_feedback_dialog` markup with 4 `aria-label`ed rating buttons; `POST /feedback` returns 204 and calls the stubs; second page load has no dialog.
- [ ] **Step 2: FAIL. Step 3: Implement (dialog per mockup A9: 🫶, 4 emoji ratings, optional textarea, newsletter checkbox, "Send it"). Step 4: PASS. Step 5: Commit** `feat: first-save feedback dialog (A9)`

### Task 10: Desktop layout (A16), responsive polish, a11y + reduced-motion sweep

**Files:**
- Modify: `app/static/styles.css`, `app/templates/base.html` (desktop header nav "Saved weeks" + `.avatar`), `app/templates/plan.html` (right-rail structure: shopping list + Save CTA in an `<aside>`, `.action-bar` hidden ≥1024px), `tests/test_web.py` (structure assertions only)

- [ ] **Step 1: Implement §7**: ≥768px content column 640px, option-card grids 3–4 across; ≥1024px plan grid `1fr 340px`, meal cards 2-up, sticky rail (`align-self:start; position:sticky; top:24px`), landing two-column, saved plans 2-up, wizard stays ≤560px.
- [ ] **Step 2: Acceptance sweep** (spec §10) — run the app (`uvicorn app.main:app`) and verify with curl/browser:
  - Full anonymous flow with JS disabled (stacked panels, meta-refresh generating page, visible sheet sections).
  - 390px viewport flow with JS on; plan page matches A5/A16 at 390/1024 (screenshot).
  - Keyboard-only pass; visually-hidden step announcements; all dialogs focus-trapped by native `<dialog>`.
  - `wc -c app/static/ui.js` < 10240; no console errors; every money/macro figure `tabular-nums`.
- [ ] **Step 3: Full suite** `pytest -q` green. **Step 4: Commit** `feat: desktop plan layout (A16) + responsive/a11y polish`

## Self-review notes

- Spec coverage: A1→T2, A2→T2, A3→T2, A4→T3, generating→T4, A5→T5, A6→T5, A7→T7, A8→T7, A9→T9, A10→T5, A11→T7, A12→T6, A13→T4, A14→T7, A15→T8, A16→T10; §0 change map: deletions spread across T2/T5/T7; §5 async→T4; §6 JS→T1 (wired T4–T9); §7→T1+T10; §8 states→T4/T6/T8/T10; §9→T1+T10.
- Old `/signup`+`thanks.html` removed in T2 (feedback dialog supersedes; `storage.log_event("email_captured")` moves to newsletter opt-in in T9).
- `plan_generated` event logging moves into `jobs._run`.
