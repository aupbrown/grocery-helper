"""In-memory plan-generation jobs.

Generation takes ~20s of LLM calls, so POST /plan/generate snapshots the inputs into a Job,
runs the two generate() passes on a daemon thread, and the browser polls GET /plan/status.
Results live here (keyed by the job id in the session cookie) because generated plans are
far too big for the cookie itself. Single-process by design — at MVP traffic the app runs
one uvicorn worker; a restart just sends the user back through a ~20s regeneration.
"""
import threading
import time
import uuid
from dataclasses import dataclass, field

from app import storage
from app.catalog import catalog_by_id, filter_catalog
from app.generator import generate, generate_one
from app.models import GeneratedPlan, Ingredient, PlanInputs
from app.plan import adjust_to_targets, compute_plan, reconcile_seasonings, snap_units
from app.validate import validate_plan

MAX_AGE_SECONDS = 2 * 60 * 60   # prune abandoned jobs after 2h

_JOBS: dict[str, "Job"] = {}
_LOCK = threading.Lock()


@dataclass
class Job:
    id: str
    inputs: PlanInputs
    catalog: list                       # full Ingredient catalog (filtered per-run)
    state: str = "queued"               # queued | running | done | failed
    budget_base: GeneratedPlan | None = None
    protein_base: GeneratedPlan | None = None
    pinned: GeneratedPlan | None = None  # swap_priciest: keep these meals, regen one slot
    swap_slot: str | None = None
    over_budget: bool = False           # the budget-first variant busted its cap
    keep_anyway: bool = False           # user chose "keep this plan anyway" (A12)
    created_at: float = field(default_factory=time.time)


def start(inputs: PlanInputs, catalog: list, *, pinned: GeneratedPlan | None = None,
          swap_slot: str | None = None, protein_base: GeneratedPlan | None = None) -> str:
    """Register a job and kick off generation; returns the job id for the session."""
    job = Job(id=uuid.uuid4().hex, inputs=inputs, catalog=catalog,
              pinned=pinned, swap_slot=swap_slot, protein_base=protein_base)
    with _LOCK:
        now = time.time()
        for jid, j in list(_JOBS.items()):
            if now - j.created_at > MAX_AGE_SECONDS:
                del _JOBS[jid]
        _JOBS[job.id] = job
    _spawn(job)
    return job.id


def get(job_id: str | None) -> Job | None:
    return _JOBS.get(job_id) if job_id else None


def _spawn(job: Job) -> None:
    # Tests monkeypatch this to `lambda job: _run(job)` so the flow runs inline.
    threading.Thread(target=_run, args=(job,), daemon=True).start()


def _run(job: Job) -> None:
    job.state = "running"
    try:
        filtered = filter_catalog(job.catalog, job.inputs.dietary_pattern,
                                  job.inputs.avoid_allergens)
        if job.pinned is not None and job.swap_slot:
            # Recovery "swap the priciest meal": regenerate one slot, keep the rest pinned.
            avoid = next((m.name for m in job.pinned.meals if m.slot == job.swap_slot), None)
            fresh = generate_one(job.inputs, filtered, job.swap_slot,
                                 priority="budget", avoid_name=avoid)
            meals = [m for m in job.pinned.meals if m.slot != job.swap_slot] + [fresh]
            job.budget_base = GeneratedPlan(meals=meals)
            if job.protein_base is None:
                job.protein_base = generate(job.inputs, filtered, priority="protein")
        else:
            job.budget_base = generate(job.inputs, filtered, priority="budget")
            job.protein_base = generate(job.inputs, filtered, priority="protein")
        by_id = catalog_by_id(filtered)
        _, val = finalize(job.budget_base, job.inputs, by_id, job.inputs.weekly_budget)
        job.over_budget = not val.within_budget
        try:
            storage.log_event("plan_generated")
        except Exception:
            pass                        # analytics must never fail a plan
        job.state = "done"
    except Exception:
        job.state = "failed"


def finalize(base: GeneratedPlan, inputs: PlanInputs, filtered_by_id: dict[str, Ingredient],
             budget_cap: float | None):
    """Correct macros, list mentioned seasonings, snap units, then compute AND validate.

    Returns (ComputedPlan, PlanValidation): the validation is the deterministic source of
    truth for budget/macro status and powers the summary band.
    """
    gen = reconcile_seasonings(base, filtered_by_id)
    gen = adjust_to_targets(gen, filtered_by_id, inputs, budget_cap=budget_cap)
    gen = snap_units(gen, filtered_by_id)
    return compute_plan(gen, filtered_by_id, inputs), validate_plan(gen, filtered_by_id, inputs)


def priciest_slot(base: GeneratedPlan, by_id: dict[str, Ingredient]) -> str:
    """Slot of the meal with the highest marginal ingredient cost (package rounding ignored —
    good enough to pick a swap candidate)."""
    def cost(meal):
        return sum(mi.grams * by_id[mi.ingredient_id].price_per_100g / 100
                   for mi in meal.ingredients if mi.ingredient_id in by_id)
    return max(base.meals, key=cost).slot
