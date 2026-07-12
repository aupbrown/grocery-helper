"""Launch-hardening tests: session secret handling.

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
