"""Rate limiter tests: the sliding-window store, then the guarded routes.

app.main is imported inside the route tests (not at module level): importing it
runs load_dotenv() during collection, which would un-gate the DATABASE_URL-skipped
tests in modules collected after this one (test_storage.py).
"""
import time

from app import ratelimit


def test_allows_under_the_per_ip_limit():
    for _ in range(ratelimit.PER_IP_LIMIT):
        assert ratelimit.allow("1.2.3.4") is True


def test_denies_ip_at_the_cap():
    for _ in range(ratelimit.PER_IP_LIMIT):
        ratelimit.allow("1.2.3.4")
    assert ratelimit.allow("1.2.3.4") is False


def test_other_ips_unaffected_by_a_capped_ip():
    for _ in range(ratelimit.PER_IP_LIMIT):
        ratelimit.allow("1.2.3.4")
    assert ratelimit.allow("5.6.7.8") is True


def test_window_expiry_frees_the_ip():
    now = time.time()
    for _ in range(ratelimit.PER_IP_LIMIT):
        ratelimit.allow("1.2.3.4", now=now)
    assert ratelimit.allow("1.2.3.4", now=now) is False
    assert ratelimit.allow("1.2.3.4", now=now + ratelimit.WINDOW_SECONDS + 1) is True


def test_global_cap_denies_even_a_fresh_ip():
    # Spread hits across distinct IPs so no single one trips its own cap.
    for i in range(ratelimit.GLOBAL_LIMIT // ratelimit.PER_IP_LIMIT):
        for _ in range(ratelimit.PER_IP_LIMIT):
            assert ratelimit.allow(f"10.0.0.{i}") is True
    assert ratelimit.allow("99.99.99.99") is False


# --- The three Gemini-calling routes reject over-limit requests with a 429 page ---

TARGETS = {"target_calories": "2700", "target_protein": "180",
           "target_carbs": "326", "target_fat": "75"}


def _fill_wizard(c):
    c.post("/plan/new?step=1", data={
        "weekly_budget": "40", "goal": "maintain", "bodyweight_lb": "180",
        "max_cook_minutes": "120", "activity_level": "light"})
    c.post("/plan/new?step=2", data={"kitchen_preset": "apartment",
                                     "rendered_preset": "apartment"})


def _client_with_done_job(monkeypatch):
    """Wizard + one allowed synchronous generation, mirroring test_web's helpers."""
    import app.main as main
    from fastapi.testclient import TestClient
    from app import jobs
    from app.models import GeneratedPlan, Meal, MealIngredient

    fake = GeneratedPlan(meals=[
        Meal(name="Chicken & rice", slot="dinner", cook_time_minutes=20, servings=7,
             instructions="1. Cook.", ingredients=[
                 MealIngredient(ingredient_id="chicken_breast", grams=1500),
                 MealIngredient(ingredient_id="rice_white", grams=1500)])])
    monkeypatch.setattr(jobs, "generate", lambda *a, **k: fake)
    monkeypatch.setattr(jobs, "generate_one", lambda *a, **k: fake.meals[0])
    monkeypatch.setattr(jobs, "_spawn", lambda job: jobs._run(job))
    monkeypatch.setattr(jobs.storage, "log_event", lambda *a, **k: None)
    c = TestClient(main.app)
    _fill_wizard(c)
    c.post("/plan/generate", data=TARGETS)
    return c


def test_plan_generate_returns_429_page_when_limited(monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ratelimit, "PER_IP_LIMIT", 0)
    c = TestClient(main.app)
    _fill_wizard(c)
    r = c.post("/plan/generate", data=TARGETS)
    assert r.status_code == 429
    assert "your answers are safe" in r.text
    assert "/plan/new?step=3" in r.text          # a way back into the flow


def test_regenerate_returns_429_when_limited(monkeypatch):
    c = _client_with_done_job(monkeypatch)
    monkeypatch.setattr(ratelimit, "PER_IP_LIMIT", 0)
    r = c.post("/regenerate", data={"plan_kind": "budget", "slot": "dinner", "idx": "0"})
    assert r.status_code == 429


def test_recover_limited_except_free_keep_action(monkeypatch):
    c = _client_with_done_job(monkeypatch)
    monkeypatch.setattr(ratelimit, "PER_IP_LIMIT", 0)
    r = c.post("/plan/recover", data={"action": "raise_budget"}, follow_redirects=False)
    assert r.status_code == 429
    # "keep this plan anyway" costs no LLM call — it must survive the limit.
    r2 = c.post("/plan/recover", data={"action": "keep"}, follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/plan"
