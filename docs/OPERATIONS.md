# Operations

## Run
- Local, no keys: `pip install -r requirements.txt && EMBEDDED_WORKER=true uvicorn app.main:app` → http://localhost:8000
- Full stack: `cp .env.example .env && docker compose up --build` (PostgreSQL, migrations, web, dedicated worker)
- Tests: `pip install -r requirements-dev.txt && pytest` (SQLite). For PostgreSQL set `TEST_DATABASE_URL`.
  Browser tests: `playwright install chromium && pytest -m e2e tests/e2e`.

## GitHub Pages (static preview only)
GitHub Pages serves static files, so it can host the landing page, `/demo` and the info pages, **not** the app itself
(sessions, job queue, AI provider need a server: use Docker Compose, Render, Fly.io, Railway or similar).
- Build locally: `PAGES_BASE=/FeedMeFire python scripts/build_pages.py dist` (set `APP_URL` to send "Start roast" to a hosted backend).
- Deploy: `.github/workflows/pages.yml`. One-time setup: repo **Settings -> Pages -> Source: GitHub Actions**
  (private repos need a paid GitHub plan, and the published site is public), then either run the workflow manually
  or set the repository variable `PAGES_ENABLED=true` so every push to `main` redeploys.
- Without `APP_URL`, `/start` shows a "static preview" notice instead of the builder.

## Production checklist
1. `ENV=prod`, a PostgreSQL `DATABASE_URL`, `APP_SECRET` (32+ chars), `ENCRYPTION_KEYS` from a managed secret store. The app refuses to start otherwise.
2. `PROVIDER=gemini` with a **paid** arrangement whose data-use terms match `/privacy`. Review provider retention and
   subprocessors, then update the privacy copy and bump `PROVIDER_DISCLOSURE_VERSION`.
3. Run `alembic upgrade head`, one or more `python -m app.worker`, and web behind TLS (`--proxy-headers`; set `TRUST_PROXY=true` only behind a proxy you control).
4. Set `SUPPORT_CONTACT`, `PRICING_VERSION` and prices, `METRICS_TOKEN`.
5. Jurisdiction-specific privacy/legal review of `/privacy` and `/terms` (they are drafts) before public launch.
6. Load test (100 concurrent browsing sessions) and tune `WORKER_CONCURRENCY` to the provider's rate limits.

## Kill switches (env, restart to apply; deletion is never gated)
`GENERATION_ENABLED`, `REGENERATION_ENABLED`, `DISABLED_PROVIDERS`, `DISABLED_PROMPT_VERSIONS`, `YOUTUBE_CONNECT_ENABLED` (must stay false: the app refuses to start otherwise, see below).

## Metrics and alerts
`GET /internal/metrics` (Bearer `METRICS_TOKEN`; `?format=prom` for Prometheus text): queue depth, oldest queued age,
success rate, p50/p95 latency, retry rate, error codes, tokens and estimated cost, cleanup lag, feedback rates. Counts only, never content.
Suggested alerts: success rate drop, `oldest_queued_age_s` > 120, `estimated_cost_24h` near budget, `cleanup_lag_s` > 300,
`possible_duplicate_cost_jobs` rising, spike in `SESSION_EXPIRED`/404s.

## Data lifecycle
Reads refuse expired data immediately; the worker's cleanup loop (every 60 s) physically removes it. Feedback lives 30 days; the
owning session row is kept only until then. Backups ≤ 7 days: after any restore run
`python -c "from app.db import session_scope; from app.services.retention import reapply_deletions as r; \
with session_scope() as db: print(r(db))"` to re-apply the deletion ledger (HMAC of session ids only).

## Connected YouTube release (disabled)
Not implemented and deliberately unreachable: no OAuth or import routes exist, and `YOUTUBE_CONNECT_ENABLED=true` aborts startup.
Before building it, complete the PRD §16.5 gates (policy review of derived content/AI processing, OAuth verification, consent and
revocation tests).

## Known limits to address before launch
- Safety is rule-based plus optional `SAFETY_JUDGE_ENABLED` provider moderation. It has no slur lexicon and cannot judge whether evidence
  truly supports a joke. The PRD §20 human-reviewed evaluation set (100+ samples) must be built and run against the real model.
- Sensitive-input screening is keyword-based and will miss things (the UI says so).
- IP rate limiting is a weak, shared-network signal; add a bot challenge if abuse appears.
