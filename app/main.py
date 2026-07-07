import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.models import PlanInputs, Goal, GeneratedPlan, KitchenProfile, ComputedPlan
from app.catalog import load_catalog, catalog_by_id, filter_catalog
from app.kitchen import EQUIPMENT, EQUIPMENT_LABELS, PRESETS
from app.targets import compute_targets
from app.generator import generate, generate_one
from app.plan import compute_plan, adjust_to_targets, reconcile_seasonings, snap_units
from app.validate import validate_plan
from app.units import to_grams, owned_unit_options
from app import storage, auth

# Load .env so GEMINI_API_KEY and DATABASE_URL are available under `uvicorn`.
load_dotenv()

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent

CATALOG = load_catalog(ROOT / "data" / "ingredients.json")
CATALOG_BY_ID = catalog_by_id(CATALOG)

ALLERGENS = ["dairy", "eggs", "fish", "nuts", "soy", "gluten", "shellfish"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the events table on startup. This runs when the app actually serves,
    # not on bare import, so the offline test suite needs no database.
    storage.init_db()
    yield


app = FastAPI(lifespan=lifespan)
# Signed-cookie sessions hold only the user id. Set SESSION_SECRET in production.
app.add_middleware(SessionMiddleware,
                   secret_key=os.environ.get("SESSION_SECRET", "dev-insecure-change-me"))
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


# --- Coach wizard (A1–A3): landing + 3 steps, state in the session cookie ---

# Common items surfaced as starter pantry chips (mockup A3); the rest sit in a fold.
STARTER_OWNED = ["rice_white", "eggs", "oats", "peanut_butter", "pasta_ww", "black_beans"]

# Kitchen preset cards, in mockup order.
PRESET_CARDS = [
    {"key": "apartment", "emoji": "🏢", "label": "Apartment", "caption": "full kitchen"},
    {"key": "dorm", "emoji": "🛏️", "label": "Dorm", "caption": "microwave only"},
    {"key": "shared", "emoji": "👥", "label": "Shared kitchen", "caption": "quick sessions"},
    {"key": "athlete", "emoji": "🍱", "label": "Meal-prepper", "caption": "batch on Sunday"},
]

DEFAULT_WIZARD = {
    "weekly_budget": 40, "goal": "maintain", "bodyweight_lb": 170,
    "activity_level": "light", "max_cook_minutes": 120,
    "kitchen": None, "kitchen_preset": "apartment",
    "dietary_pattern": "none", "avoid_allergens": [], "owned": {},
}


def _wizard(request: Request) -> dict:
    """The wizard's working state: session values over defaults, DB prefill for logged-in
    users who haven't touched step 2 yet (kitchen profile + pantry)."""
    w = dict(DEFAULT_WIZARD)
    w.update(request.session.get("wizard") or {})
    if w["kitchen"] is None:
        user = auth.current_user(request)
        if user:
            prof = storage.get_kitchen_profile(user["id"])
            if prof:
                w["kitchen"] = KitchenProfile.model_validate(prof).model_dump(mode="json")
                w["kitchen_preset"] = None
            if not w["owned"]:
                owned = {}
                for p in storage.list_pantry(user["id"]):
                    key = p.get("normalized_item_key")
                    if key and key in CATALOG_BY_ID:
                        owned[key] = {"qty": p.get("quantity"), "unit": p.get("unit") or "g"}
                w["owned"] = owned
    if w["kitchen"] is None:
        w["kitchen"] = PRESETS["apartment"].model_dump(mode="json")
    return w


def _budget_caption(budget: float) -> str:
    per = budget / 21   # 3 meals a day, 7 days
    if per < 1.5:
        mood = "very lean — rice & beans territory"
    elif per < 2.25:
        mood = "tight but doable 💪"
    elif per < 3.25:
        mood = "comfortable — room for variety"
    else:
        mood = "roomy — some nice cuts on the menu 🎉"
    return f"≈ ${per:.2f} per meal — {mood}"


def _owned_grams_from_wizard(w: dict) -> dict[str, float]:
    """Owned amounts (id -> grams) from the wizard's {id: {qty, unit}} map. Items without a
    usable amount are skipped: owned-without-amount means "have enough"."""
    out: dict[str, float] = {}
    for iid, amt in (w.get("owned") or {}).items():
        ing = CATALOG_BY_ID.get(iid)
        if not ing or not isinstance(amt, dict):
            continue
        qty = _num(amt.get("qty"))
        if qty is None or qty <= 0:
            continue
        grams = to_grams(qty, amt.get("unit") or "", ing)
        if grams and grams > 0:
            out[iid] = round(grams, 1)
    return out


def _sync_pantry(user_id: int, owned: dict) -> None:
    """Make the DB pantry mirror the wizard's owned map (catalog items only)."""
    existing = {p["normalized_item_key"]: p for p in storage.list_pantry(user_id)
                if p.get("normalized_item_key")}
    for iid, amt in owned.items():
        ing = CATALOG_BY_ID.get(iid)
        if not ing:
            continue
        if iid in existing:
            storage.delete_pantry_item(existing[iid]["id"], user_id)
        qty = _num(amt.get("qty")) if isinstance(amt, dict) else None
        grams = to_grams(qty, amt.get("unit") or "", ing) if qty else None
        storage.add_pantry_item(user_id, {
            "item_name": ing.name, "normalized_item_key": iid,
            "quantity": round(grams, 1) if grams else None, "unit": "g" if grams else None,
            "source": "generate_form"})
    for iid, p in existing.items():
        if iid not in owned:
            storage.delete_pantry_item(p["id"], user_id)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {
        "logged_in": auth.current_user(request) is not None})


def _wizard_step2_context(request: Request, w: dict, ret: str | None) -> dict:
    ownables = [i for i in CATALOG if not i.pantry_staple]
    starters = [CATALOG_BY_ID[i] for i in STARTER_OWNED if i in CATALOG_BY_ID]
    starter_ids = set(STARTER_OWNED)
    owned_ids = set(w["owned"])
    # Starter chips + anything already owned show up front; the rest fold away.
    front = starters + [i for i in ownables if i.id in owned_ids and i.id not in starter_ids]
    rest = [i for i in ownables if i.id not in starter_ids and i.id not in owned_ids]
    kitchen = KitchenProfile.model_validate(w["kitchen"])
    return {"w": w, "kitchen": kitchen, "presets": PRESET_CARDS,
            "equipment": EQUIPMENT, "equipment_labels": EQUIPMENT_LABELS,
            "front_items": front, "rest_items": rest,
            "unit_options": {i.id: owned_unit_options(i) for i in ownables},
            "allergens": ALLERGENS, "ret": ret}


@app.get("/plan/new", response_class=HTMLResponse)
def wizard_step(request: Request, step: int = 1,
                ret: str | None = Query(None, alias="return")):
    w = _wizard(request)
    if step == 2:
        return templates.TemplateResponse(request, "wizard_kitchen.html",
                                          _wizard_step2_context(request, w, ret))
    if step == 3:
        return _targets_step(request, w)
    return templates.TemplateResponse(request, "wizard_basics.html", {
        "w": w, "budget_caption": _budget_caption(w["weekly_budget"])})


def _targets_step(request: Request, w: dict):
    # Placeholder until the targets step (Task 3) lands; keeps "skip" links safe.
    return RedirectResponse("/plan/new?step=1", status_code=303)


@app.post("/plan/new")
async def wizard_post(request: Request, step: int = 1,
                      ret: str | None = Query(None, alias="return")):
    form = await request.form()
    w = _wizard(request)
    if step == 1:
        w.update(
            weekly_budget=_num(form.get("weekly_budget")) or 40,
            goal=form.get("goal") if form.get("goal") in Goal._value2member_map_ else "maintain",
            bodyweight_lb=_num(form.get("bodyweight_lb")) or 170,
            max_cook_minutes=int(_num(form.get("max_cook_minutes")) or 120),
            activity_level=form.get("activity_level", "light"),
        )
        request.session["wizard"] = w
        return RedirectResponse("/plan/new?step=2", status_code=303)

    # Step 2: kitchen preset + equipment overrides + owned shelf + diet.
    preset = form.get("kitchen_preset") or w.get("kitchen_preset") or "apartment"
    if preset not in PRESETS:
        preset = "apartment"
    kitchen = PRESETS[preset].model_copy()
    # Equipment chips override the preset — but only when the user saw chips rendered for
    # this same preset. Switching presets makes the preset's own defaults win.
    if form.get("rendered_preset") == preset:
        checked = set(form.getlist("equipment"))
        for eq in EQUIPMENT:
            setattr(kitchen, eq, eq in checked)
    owned: dict[str, dict] = {}
    for iid in form.getlist("owned_ids"):
        if iid not in CATALOG_BY_ID:
            continue
        qty = _num(form.get(f"owned_qty_{iid}"))
        unit = (form.get(f"owned_unit_{iid}") or "").strip()
        owned[iid] = {"qty": qty if qty and qty > 0 else None, "unit": unit or None}
    # Free-text "+ something else" entries: keep the ones that name a catalog item.
    by_name = {i.name.lower(): i.id for i in CATALOG}
    for raw in form.getlist("owned_custom"):
        iid = by_name.get((raw or "").strip().lower())
        if iid and iid not in owned:
            owned[iid] = {"qty": None, "unit": None}
    w.update(
        kitchen=kitchen.model_dump(mode="json"), kitchen_preset=preset, owned=owned,
        dietary_pattern=form.get("dietary_pattern", w["dietary_pattern"]),
        avoid_allergens=[a for a in form.getlist("avoid_allergens") if a in ALLERGENS],
    )
    request.session["wizard"] = w
    user = auth.current_user(request)
    if ret == "account" and user:
        storage.save_kitchen_profile(user["id"], kitchen.model_dump(mode="json"))
        _sync_pantry(user["id"], owned)
        return RedirectResponse("/account", status_code=303)
    return RedirectResponse("/plan/new?step=3", status_code=303)


def _kitchen_from_form(
    microwave: bool, stove: bool, oven: bool, air_fryer: bool, blender: bool,
    rice_cooker: bool, freezer: bool, mini_fridge: bool, no_cook_preferred: bool,
    prioritize_time: bool, max_single_session_minutes: int, preferred_prep_sessions: int,
) -> KitchenProfile:
    """Build a KitchenProfile from the flat form checkboxes (unchecked box = absent = False)."""
    return KitchenProfile(
        microwave=microwave, stove=stove, oven=oven, air_fryer=air_fryer, blender=blender,
        rice_cooker=rice_cooker, freezer=freezer, mini_fridge=mini_fridge,
        no_cook_preferred=no_cook_preferred, prioritize_time=prioritize_time,
        max_single_session_minutes=max_single_session_minutes,
        preferred_prep_sessions=preferred_prep_sessions)


def _parse_kitchen(kitchen_json: str) -> KitchenProfile:
    """Rebuild the KitchenProfile carried as a hidden field; fall back to a default kitchen."""
    return KitchenProfile.model_validate_json(kitchen_json) if kitchen_json else KitchenProfile()


def _num(s) -> float | None:
    """Parse an optional numeric form field; blank/invalid -> None."""
    try:
        return float(s) if s not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_owned_grams(owned_grams_json: str) -> dict[str, float]:
    """Decode the {id: grams} hidden field carried through the form -> targets -> plan hop."""
    if not owned_grams_json:
        return {}
    try:
        data = json.loads(owned_grams_json)
    except (ValueError, TypeError):
        return {}
    out: dict[str, float] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            g = _num(v)
            if k in CATALOG_BY_ID and g is not None and g > 0:
                out[k] = g
    return out


def _owned_grams_from_form(form, owned_ids) -> dict[str, float]:
    """Read the per-ingredient amount + unit fields the user filled in and convert each to grams."""
    out: dict[str, float] = {}
    for iid in owned_ids:
        ing = CATALOG_BY_ID.get(iid)
        if not ing:
            continue
        qty = _num(form.get(f"owned_qty_{iid}"))
        unit = (form.get(f"owned_unit_{iid}") or "").strip()
        if qty is None or qty <= 0 or not unit:
            continue                       # no amount given -> "have enough" (skip entirely)
        grams = to_grams(qty, unit, ing)
        if grams and grams > 0:
            out[iid] = round(grams, 1)
    return out


def _plan_inputs(*, weekly_budget, goal, bodyweight_lb, activity_level, max_cook_minutes,
                 dietary_pattern, avoid_allergens, owned_ingredient_ids, target_calories,
                 target_protein, target_carbs, target_fat, kitchen_json,
                 owned_grams=None) -> PlanInputs:
    """Assemble PlanInputs from the form fields shared by /plan, /regenerate, and /register."""
    return PlanInputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids, owned_grams=owned_grams or {},
        target_calories=target_calories,
        target_protein=target_protein, target_carbs=target_carbs, target_fat=target_fat,
        kitchen=_parse_kitchen(kitchen_json))


@app.post("/targets", response_class=HTMLResponse)
async def targets(
    request: Request,
    weekly_budget: float = Form(...),
    goal: Goal = Form(...),
    bodyweight_lb: float = Form(...),
    activity_level: str = Form("light"),
    max_cook_minutes: int = Form(...),
    dietary_pattern: str = Form("none"),
    avoid_allergens: list[str] = Form(default=[]),
    owned_ingredient_ids: list[str] = Form(default=[]),
    microwave: bool = Form(False),
    stove: bool = Form(False),
    oven: bool = Form(False),
    air_fryer: bool = Form(False),
    blender: bool = Form(False),
    rice_cooker: bool = Form(False),
    freezer: bool = Form(False),
    mini_fridge: bool = Form(False),
    no_cook_preferred: bool = Form(False),
    prioritize_time: bool = Form(False),
    max_single_session_minutes: int = Form(60),
    preferred_prep_sessions: int = Form(2),
):
    t = compute_targets(bodyweight_lb, goal, activity_level)
    kitchen = _kitchen_from_form(
        microwave, stove, oven, air_fryer, blender, rice_cooker, freezer, mini_fridge,
        no_cook_preferred, prioritize_time, max_single_session_minutes, preferred_prep_sessions)
    # Convert the per-ingredient amount/unit fields to grams once here, then carry them forward as a
    # single JSON hidden field (mirrors kitchen_json) so /plan needs no dynamic-field parsing.
    owned_grams = _owned_grams_from_form(await request.form(), owned_ingredient_ids)
    return templates.TemplateResponse(request, "targets.html", {
        "targets": t,
        "weekly_budget": weekly_budget,
        "goal": goal.value,
        "bodyweight_lb": bodyweight_lb,
        "activity_level": activity_level,
        "max_cook_minutes": max_cook_minutes,
        "dietary_pattern": dietary_pattern,
        "avoid_allergens": avoid_allergens,
        "owned_ingredient_ids": owned_ingredient_ids,
        "owned_grams_json": json.dumps(owned_grams),
        "kitchen_json": kitchen.model_dump_json(),
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
    target_carbs: float = Form(...),
    target_fat: float = Form(...),
    kitchen_json: str = Form(""),
    owned_grams_json: str = Form(""),
):
    inputs = _plan_inputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids, owned_grams=_parse_owned_grams(owned_grams_json),
        target_calories=target_calories, target_protein=target_protein,
        target_carbs=target_carbs, target_fat=target_fat, kitchen_json=kitchen_json,
    )
    filtered = filter_catalog(CATALOG, dietary_pattern, avoid_allergens)
    # Plan A is budget-first; Plan B is target-first. Keep the base (pre-correction) plans so
    # a single recipe can be regenerated later without re-rolling the whole week.
    budget_base = generate(inputs, filtered, priority="budget")
    protein_base = generate(inputs, filtered, priority="protein")
    storage.log_event("plan_generated")
    return _render_results(request, inputs, budget_base, protein_base)


def _finalize(base: GeneratedPlan, inputs: PlanInputs, filtered_by_id, budget_cap):
    """Correct macros, list mentioned seasonings, snap units, then compute AND validate.

    Returns (ComputedPlan, PlanValidation): the validation is the deterministic source of truth
    for budget/macro status and powers the Budget Guarantee summary.
    """
    gen = reconcile_seasonings(base, filtered_by_id)   # list mentioned seasonings + cooking oil
    gen = adjust_to_targets(gen, filtered_by_id, inputs, budget_cap=budget_cap)
    gen = snap_units(gen, filtered_by_id)
    return compute_plan(gen, filtered_by_id, inputs), validate_plan(gen, filtered_by_id, inputs)


def _render_results(request, inputs: PlanInputs,
                    budget_base: GeneratedPlan, protein_base: GeneratedPlan):
    # Filtered catalog so the whey top-up respects diet/allergens (no whey for vegan/dairy).
    filtered_by_id = catalog_by_id(filter_catalog(CATALOG, inputs.dietary_pattern,
                                                  inputs.avoid_allergens))
    budget_data, budget_val = _finalize(budget_base, inputs, filtered_by_id, inputs.weekly_budget)
    protein_data, protein_val = _finalize(protein_base, inputs, filtered_by_id, None)
    return templates.TemplateResponse(request, "results.html", {
        "plans": [
            {"kind": "budget", "label": "Plan A — Fits your budget",
             "blurb": "Cheapest plan under your budget. Protein may fall short of target.",
             "data": budget_data, "validation": budget_val},
            {"kind": "protein", "label": "Plan B — Hits your protein",
             "blurb": "Reaches your protein target at the lowest cost. May run just over budget.",
             "data": protein_data, "validation": protein_val},
        ],
        "inputs": inputs,
        "budget_base_json": budget_base.model_dump_json(),
        "protein_base_json": protein_base.model_dump_json(),
        "kitchen_json": inputs.kitchen.model_dump_json(),
        "owned_grams_json": json.dumps(inputs.owned_grams),
        "logged_in": "user_id" in request.session,
    })


@app.post("/regenerate", response_class=HTMLResponse)
def regenerate(
    request: Request,
    plan_kind: str = Form(...),
    slot: str = Form(...),
    budget_base_json: str = Form(...),
    protein_base_json: str = Form(...),
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
    target_carbs: float = Form(...),
    target_fat: float = Form(...),
    kitchen_json: str = Form(""),
    owned_grams_json: str = Form(""),
):
    inputs = _plan_inputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids, owned_grams=_parse_owned_grams(owned_grams_json),
        target_calories=target_calories, target_protein=target_protein,
        target_carbs=target_carbs, target_fat=target_fat, kitchen_json=kitchen_json,
    )
    filtered = filter_catalog(CATALOG, dietary_pattern, avoid_allergens)
    budget_base = GeneratedPlan.model_validate_json(budget_base_json)
    protein_base = GeneratedPlan.model_validate_json(protein_base_json)
    target = budget_base if plan_kind == "budget" else protein_base

    # Regenerate just this slot, keeping the rest; re-finalizing re-meets the macro targets.
    avoid = next((m.name for m in target.meals if m.slot == slot), None)
    new_meal = generate_one(inputs, filtered, slot, priority=plan_kind, avoid_name=avoid)
    meals = [m for m in target.meals if m.slot != slot] + [new_meal]
    new_base = GeneratedPlan(meals=meals)
    if plan_kind == "budget":
        budget_base = new_base
    else:
        protein_base = new_base
    storage.log_event("recipe_regenerated")
    return _render_results(request, inputs, budget_base, protein_base)


def _plan_record(inputs: PlanInputs, computed: ComputedPlan, validation, plan_kind: str) -> dict:
    """Shape a saved-plan row from the finalized plan + its validation."""
    return {
        "goal": inputs.goal.value,
        "calorie_target": round(inputs.target_calories),
        "protein_target": round(inputs.target_protein),
        "carb_target": round(inputs.target_carbs),
        "fat_target": round(inputs.target_fat),
        "weekly_budget": inputs.weekly_budget,
        "estimated_total_cost": computed.total_cost,
        "estimated_total_prep_minutes": validation.total_prep_minutes,
        "status": "active",
        "validation_status": validation.severity,
        "validation_notes": {"warnings": validation.warnings, "errors": validation.errors,
                             "suggested_fixes": validation.suggested_fixes},
        "inputs": inputs.model_dump(mode="json"),
        "snapshot": computed.model_dump(mode="json"),
        "progress": {},
        "generated_by": "initial_generation",
    }


def _save_current_plan(user_id: int, form) -> int:
    """Reconstruct the chosen plan from the results-page form fields and persist it."""
    inputs = _plan_inputs(
        weekly_budget=float(form["weekly_budget"]), goal=Goal(form["goal"]),
        bodyweight_lb=float(form["bodyweight_lb"]),
        activity_level=form.get("activity_level", "light"),
        max_cook_minutes=int(form["max_cook_minutes"]),
        dietary_pattern=form.get("dietary_pattern", "none"),
        avoid_allergens=form.getlist("avoid_allergens"),
        owned_ingredient_ids=form.getlist("owned_ingredient_ids"),
        owned_grams=_parse_owned_grams(form.get("owned_grams_json", "")),
        target_calories=float(form["target_calories"]), target_protein=float(form["target_protein"]),
        target_carbs=float(form["target_carbs"]), target_fat=float(form["target_fat"]),
        kitchen_json=form.get("kitchen_json", ""))
    plan_kind = form.get("plan_kind", "budget")
    base_json = form.get("budget_base_json") if plan_kind == "budget" \
        else form.get("protein_base_json")
    base = GeneratedPlan.model_validate_json(base_json)
    cap = inputs.weekly_budget if plan_kind == "budget" else None
    filtered_by_id = catalog_by_id(filter_catalog(CATALOG, inputs.dietary_pattern,
                                                  inputs.avoid_allergens))
    computed, validation = _finalize(base, inputs, filtered_by_id, cap)
    return storage.save_plan(user_id, _plan_record(inputs, computed, validation, plan_kind))


def _persist_owned_to_pantry(user_id: int, form) -> None:
    """Save a registering guest's entered owned amounts to their pantry (as grams) so the generate
    form pre-fills them next week. Best-effort and idempotent against already-saved catalog items."""
    owned = _parse_owned_grams(form.get("owned_grams_json", ""))
    if not owned:
        return
    existing = {p.get("normalized_item_key") for p in storage.list_pantry(user_id)}
    for iid, grams in owned.items():
        if iid in existing:
            continue
        storage.add_pantry_item(user_id, {
            "item_name": CATALOG_BY_ID[iid].name, "normalized_item_key": iid,
            "quantity": grams, "unit": "g", "source": "generate_form"})


def _auth_page(request: Request, error: str | None = None, email: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error, "email": email})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _auth_page(request)


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()
    password = form.get("password") or ""
    user = storage.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return _auth_page(request, error="Email or password is incorrect.", email=email)
    auth.login(request, user["id"])
    return RedirectResponse("/account", status_code=303)


@app.post("/register")
async def register(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()
    password = form.get("password") or ""
    err = auth.validate_credentials(email, password)
    if err:
        return _auth_page(request, error=err, email=email)
    try:
        user_id = storage.create_user(email, auth.hash_password(password))
    except storage.EmailTaken:
        return _auth_page(request, error="That email already has an account — log in instead.",
                          email=email)
    auth.login(request, user_id)
    # Save the plan that was on the results page, if one came along. Best-effort: a malformed
    # plan shouldn't undo the account creation.
    if form.get("budget_base_json") or form.get("protein_base_json"):
        try:
            _save_current_plan(user_id, form)
        except Exception:
            pass
    try:
        _persist_owned_to_pantry(user_id, form)
    except Exception:
        pass
    return RedirectResponse("/account", status_code=303)


@app.post("/logout")
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "account.html",
                                      {"user": user, "plans": storage.list_plans(user["id"])})


@app.get("/plans/{plan_id}", response_class=HTMLResponse)
def view_plan(request: Request, plan_id: int):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    rec = storage.get_plan(plan_id, user["id"])
    if not rec:
        return RedirectResponse("/account", status_code=303)
    return templates.TemplateResponse(request, "saved_plan.html", {
        "user": user, "rec": rec, "plan": ComputedPlan.model_validate(rec["snapshot"])})


# Old standalone kitchen/pantry pages folded into wizard step 2 (kept prefilled for
# logged-in users; posting with ?return=account persists and returns to the account page).
@app.get("/settings")
@app.get("/pantry")
def legacy_settings_pantry():
    return RedirectResponse("/plan/new?step=2&return=account", status_code=301)


@app.get("/stats")
def stats():
    return storage.get_stats()
