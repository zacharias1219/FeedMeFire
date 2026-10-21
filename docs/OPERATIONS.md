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

## Sarvam AI and Indian languages
- `PROVIDER` defaults to `auto`: with `SARVAM_API_KEY` set the app uses Sarvam (else Gemini if `GEMINI_API_KEY`, else the built-in demo
  generator). `SARVAM_MODEL` defaults to `sarvam-105b-conversations`. Check the key/model first with `python scripts/sarvam_hello.py`.

### Troubleshooting: "every roast has the same opening/closing lines"
That is the built-in demo generator (fixed template text). Check, in order:
1. `GET /health/ready` -> `"provider"` must be `sarvam` and `"demo_generator"` false. The `/start` page shows a yellow "Demo mode" banner when
   the web process is on the demo generator, and each result says **"Written by <provider> · <model>"**: that line is the truth for that report.
2. **The worker is a separate process.** The provider is chosen inside the worker, so it needs `SARVAM_API_KEY` too (same `.env`/secrets as the web
   service). Key on web only = banner hidden but results say "built-in demo generator".
3. `.env` is read from the directory the process starts in; hosted platforms must set the variable in their dashboard, not a local `.env`.
4. An explicit `PROVIDER=fake` (the old `.env.example` default) overrides the key. Remove it or set `PROVIDER=auto`.
5. The "Use the fictional demo sample" button always submits the same 15 items, so even a real model sees identical input.
The startup log prints `AI provider = ...` (a WARNING in demo mode); Sarvam calls log `finish_reason` and token counts (never content).
- The preferences screen offers English, Hindi, Tamil, Telugu, Bengali and Marathi (`app/languages.py`). The language travels with the job,
  is stored with the report, and is kept on regeneration. The built-in `fake` provider always writes English.
- **Safety outside English:** the rule-based checks are English-only, so for any other language a provider moderation pass
  (`moderate()`) is mandatory and a rejection fails the job with `UNSAFE_OUTPUT`. This is weaker than the English path. Sensitive-input
  screening of the user's own items is also English-keyword based. Build a native-speaker review set per language before launch.
- Indic text needs ~3x the output tokens: `MAX_OUTPUT_TOKENS_INDIC` (default 6000). `SARVAM_JSON_MODE=true` turns on `response_format=json_object`
  once you have verified the live model honours it.
- The disclaimer translations in `app/languages.py` need native-speaker review. The UI chrome itself is still English.

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
