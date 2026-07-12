import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def _fresh_ratelimit():
    # Every TestClient request shares one client IP ("testclient"), so leftover
    # hits from one test would rate-limit the next.
    ratelimit._hits.clear()
