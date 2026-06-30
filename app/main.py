import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.models import PlanInputs, Goal, GeneratedPlan, KitchenProfile, ComputedPlan
from app.catalog import load_catalog, catalog_by_id, filter_catalog
from app.targets import compute_targets
from app.generator import generate, generate_one
from app.plan import compute_plan, adjust_to_targets, reconcile_seasonings, snap_units
from app.validate import validate_plan
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


@app.get("/", response_class=HTMLResponse)
def form(request: Request):
    # Logged-in users get their saved kitchen + pantry pre-filled so they don't re-enter them.
    user = auth.current_user(request)
    kitchen = KitchenProfile()
    owned_prechecked: set[str] = set()
    if user:
        prof = storage.get_kitchen_profile(user["id"])
        if prof:
            kitchen = KitchenProfile.model_validate(prof)
        owned_prechecked = {p["normalized_item_key"] for p in storage.list_pantry(user["id"])
                            if p["normalized_item_key"]}
    return templates.TemplateResponse(request, "form.html", {
        "catalog": CATALOG, "allergens": ALLERGENS, "kitchen": kitchen,
        "owned_prechecked": owned_prechecked, "logged_in": user is not None,
    })


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


def _plan_inputs(*, weekly_budget, goal, bodyweight_lb, activity_level, max_cook_minutes,
                 dietary_pattern, avoid_allergens, owned_ingredient_ids, target_calories,
                 target_protein, target_carbs, target_fat, kitchen_json) -> PlanInputs:
    """Assemble PlanInputs from the form fields shared by /plan, /regenerate, and /register."""
    return PlanInputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids, target_calories=target_calories,
        target_protein=target_protein, target_carbs=target_carbs, target_fat=target_fat,
        kitchen=_parse_kitchen(kitchen_json))


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
):
    inputs = _plan_inputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids,
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
):
    inputs = _plan_inputs(
        weekly_budget=weekly_budget, goal=goal, bodyweight_lb=bodyweight_lb,
        activity_level=activity_level, max_cook_minutes=max_cook_minutes,
        dietary_pattern=dietary_pattern, avoid_allergens=avoid_allergens,
        owned_ingredient_ids=owned_ingredient_ids,
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


@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    prof = storage.get_kitchen_profile(user["id"])
    kitchen = KitchenProfile.model_validate(prof) if prof else KitchenProfile()
    return templates.TemplateResponse(request, "settings.html", {"user": user, "kitchen": kitchen})


@app.post("/settings")
def save_settings(
    request: Request,
    microwave: bool = Form(False), stove: bool = Form(False), oven: bool = Form(False),
    air_fryer: bool = Form(False), blender: bool = Form(False), rice_cooker: bool = Form(False),
    freezer: bool = Form(False), mini_fridge: bool = Form(False),
    no_cook_preferred: bool = Form(False), prioritize_time: bool = Form(False),
    max_single_session_minutes: int = Form(60), preferred_prep_sessions: int = Form(2),
):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    kitchen = _kitchen_from_form(
        microwave, stove, oven, air_fryer, blender, rice_cooker, freezer, mini_fridge,
        no_cook_preferred, prioritize_time, max_single_session_minutes, preferred_prep_sessions)
    storage.save_kitchen_profile(user["id"], kitchen.model_dump(mode="json"))
    return RedirectResponse("/settings", status_code=303)


@app.get("/pantry", response_class=HTMLResponse)
def pantry_page(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    pantry = storage.list_pantry(user["id"])
    owned_keys = {p["normalized_item_key"] for p in pantry if p["normalized_item_key"]}
    return templates.TemplateResponse(request, "pantry.html", {
        "user": user, "pantry": pantry, "catalog": CATALOG, "owned_keys": owned_keys})


@app.post("/pantry")
async def add_pantry(request: Request):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    existing = {p["normalized_item_key"] for p in storage.list_pantry(user["id"])}
    for iid in form.getlist("add_ids"):           # fast path: catalog checkboxes
        ing = CATALOG_BY_ID.get(iid)
        if ing and iid not in existing:
            storage.add_pantry_item(user["id"], {
                "item_name": ing.name, "normalized_item_key": iid, "source": "manually_added"})
    name = (form.get("item_name") or "").strip()  # advanced path: a single detailed item
    if name:
        storage.add_pantry_item(user["id"], {
            "item_name": name, "normalized_item_key": (form.get("normalized_item_key") or None),
            "quantity": _num(form.get("quantity")), "unit": (form.get("unit") or None),
            "expiration_date": (form.get("expiration_date") or None),
            "priority": form.get("priority", "normal"), "source": "manually_added"})
    return RedirectResponse("/pantry", status_code=303)


@app.post("/pantry/{item_id}/delete")
def delete_pantry(request: Request, item_id: int):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    storage.delete_pantry_item(item_id, user["id"])
    return RedirectResponse("/pantry", status_code=303)


@app.post("/signup", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(...)):
    storage.log_event("email_captured", email=email)
    return templates.TemplateResponse(request, "thanks.html", {})


@app.get("/stats")
def stats():
    return storage.get_stats()
