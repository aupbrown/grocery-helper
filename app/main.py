from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models import PlanInputs, Goal
from app.catalog import load_catalog, catalog_by_id, filter_catalog
from app.targets import compute_targets
from app.generator import generate
from app.plan import compute_plan, adjust_to_targets
from app import storage

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
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.get("/", response_class=HTMLResponse)
def form(request: Request):
    return templates.TemplateResponse(
        request,
        "form.html",
        {"catalog": CATALOG, "allergens": ALLERGENS},
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
    # Use the filtered catalog for correction too, so the whey top-up is only used when the
    # user's diet/allergens allow it (e.g. no whey for vegan or dairy-allergic plans).
    filtered_by_id = catalog_by_id(filtered)

    # Plan A is budget-first (corrections capped by the budget); Plan B is target-first
    # (uncapped — guarantees the protein floor, may run over budget). See the two-plan design.
    budget_gen = adjust_to_targets(
        generate(inputs, filtered, priority="budget"), filtered_by_id, inputs,
        budget_cap=inputs.weekly_budget)
    protein_gen = adjust_to_targets(
        generate(inputs, filtered, priority="protein"), filtered_by_id, inputs,
        budget_cap=None)
    budget_plan = compute_plan(budget_gen, filtered_by_id, inputs)
    protein_plan = compute_plan(protein_gen, filtered_by_id, inputs)
    storage.log_event("plan_generated")
    return templates.TemplateResponse(request, "results.html", {
        "plans": [
            {"label": "Plan A — Fits your budget",
             "blurb": "Cheapest plan under your budget. Protein may fall short of target.",
             "data": budget_plan},
            {"label": "Plan B — Hits your protein",
             "blurb": "Reaches your protein target at the lowest cost. May run just over budget.",
             "data": protein_plan},
        ],
    })


@app.post("/signup", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(...)):
    storage.log_event("email_captured", email=email)
    return templates.TemplateResponse(request, "thanks.html", {})


@app.get("/stats")
def stats():
    return storage.get_stats()
