"""In-memory per-IP rate limiting for the LLM-backed endpoints.

Same single-process design as app.jobs: at MVP traffic the app runs one uvicorn
worker, so a module-global dict is the whole store. Honest use is naturally slow
(each generation takes ~20s and the user waits on it), so the caps only bite
scripted abuse — the goal is bounding Gemini quota burn, not fairness. The global
cap backstops per-IP evasion (X-Forwarded-For can be spoofed behind the proxy).
"""
import threading
import time

WINDOW_SECONDS = 10 * 60
PER_IP_LIMIT = 8      # a real session rarely needs more generations per 10 min
GLOBAL_LIMIT = 40     # worst case: 40 grants x ~6 Gemini calls per 10 min

_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def allow(ip: str, now: float | None = None) -> bool:
    """Record one attempt from ip and return True if it fits both caps.

    Denied attempts are not recorded (a sliding window of granted ones only),
    so a limited user recovers as soon as old grants age out. `now` is
    injectable so tests can move the window without sleeping.
    """
    now = time.time() if now is None else now
    cutoff = now - WINDOW_SECONDS
    with _lock:
        for key, stamps in list(_hits.items()):
            fresh = [t for t in stamps if t > cutoff]
            if fresh:
                _hits[key] = fresh
            else:
                del _hits[key]          # drop idle IPs — keeps memory bounded
        if len(_hits.get(ip, ())) >= PER_IP_LIMIT:
            return False
        if sum(len(s) for s in _hits.values()) >= GLOBAL_LIMIT:
            return False
        _hits.setdefault(ip, []).append(now)
        return True
