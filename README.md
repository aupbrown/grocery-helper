An app designed to relieve mental, financial, and time burden for college students
struggling to meet diet goals on a strict budget and tight schedule.

## Deploy

Hosted on Render's free tier via the `render.yaml` blueprint (deploys from `main`).
Env vars: `DATABASE_URL` (Neon Postgres), `GEMINI_API_KEY`, `SESSION_SECRET` and
`STATS_TOKEN` (Render-generated). Free-tier caveats, accepted for the MVP:

- The service spins down after ~15 min idle; the next visitor waits ~30-60s.
  Restarts drop in-flight generations (in-memory job store) — users are simply
  redirected back through the wizard.
- Run exactly **one** uvicorn worker: `app/jobs.py` and `app/ratelimit.py` keep
  state in process memory. Never add `--workers` or horizontal scaling.
- Neon free tier autosuspends and wakes in ~1s; Gemini stays on its free tier
  with billing disabled, so endpoint abuse exhausts quota rather than money.
