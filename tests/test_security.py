"""Launch-hardening tests: session secret handling and the /stats gate.

The RENDER env var (set automatically on every Render service) is the production
signal — absent locally and under pytest, so these tests drive it explicitly.

app.main is imported inside each test: importing it at module level runs
load_dotenv() during collection, which would un-gate the DATABASE_URL-skipped
tests in modules collected after this one (test_storage.py).
"""
import pytest


def test_session_secret_uses_env_value(monkeypatch):
    import app.main as main
    monkeypatch.setenv("SESSION_SECRET", "s3kr1t")
    assert main._session_secret() == "s3kr1t"


def test_session_secret_falls_back_for_local_dev(monkeypatch):
    import app.main as main
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert main._session_secret() == "dev-insecure-change-me"


def test_session_secret_required_in_production(monkeypatch):
    # Sessions are the only user isolation, so production must refuse to start
    # rather than sign cookies with a public default.
    import app.main as main
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError):
        main._session_secret()


# --- /stats hides behind STATS_TOKEN (404, not 403, so it stays invisible) ---

def _stats_client(monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main.storage, "get_stats",
                        lambda: {"plans_generated": 3, "emails_captured": 1})
    return TestClient(main.app)


def test_stats_404_when_token_env_unset(monkeypatch):
    c = _stats_client(monkeypatch)
    monkeypatch.delenv("STATS_TOKEN", raising=False)
    assert c.get("/stats").status_code == 404
    assert c.get("/stats?token=anything").status_code == 404


def test_stats_404_with_wrong_token(monkeypatch):
    c = _stats_client(monkeypatch)
    monkeypatch.setenv("STATS_TOKEN", "hunter2")
    assert c.get("/stats").status_code == 404
    assert c.get("/stats?token=wrong").status_code == 404


def test_stats_returns_json_with_right_token(monkeypatch):
    c = _stats_client(monkeypatch)
    monkeypatch.setenv("STATS_TOKEN", "hunter2")
    r = c.get("/stats?token=hunter2")
    assert r.status_code == 200
    assert r.json()["plans_generated"] == 3
