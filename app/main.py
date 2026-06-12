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
CATALOG_BY_ID = catalog_by_id(CATALOG)
DB_PATH = ROOT / "events.db"
storage.init_db(DB_PATH)

ALLERGENS = ["dairy", "eggs", "fish", "nuts", "soy", "gluten", "shellfish"]

app = FastAPI()
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
    generated = generate(inputs, filtered)
    computed = compute_plan(generated, CATALOG_BY_ID, inputs)
    storage.log_event(DB_PATH, "plan_generated")
    return templates.TemplateResponse(
        request, "results.html", {"plan": computed}
    )


@app.post("/signup", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(...)):
    storage.log_event(DB_PATH, "email_captured", email=email)
    return templates.TemplateResponse(request, "thanks.html", {})


@app.get("/stats")
def stats():
    return storage.get_stats(DB_PATH)
