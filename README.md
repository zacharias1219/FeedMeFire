# RoastMyFeed

*Your taste. On trial.* Users hand-pick a small sample of video titles, channels and interests and get a funny, private,
evidence-grounded roast, a fictional persona and a downloadable share card. It never touches YouTube or watch history.

## What's here
| Area | Where |
|---|---|
| Landing page (fixed-canvas motion template) | `app/static/landing.html`, served at `/` |
| App: sample → review → consent → generate → result → export/regenerate/feedback/delete | `/start`, `app/static/js/` |
| Demo (no AI call) and info pages | `/demo`, `/how-it-works`, `/safety`, `/privacy`, `/terms`, `/support` |
| API (PRD §13) | `app/routes/api.py` |
| Job queue, worker, pipeline | `app/services/jobs.py`, `generation.py`, `app/worker.py` |
| Providers (Gemini + offline fake) | `app/providers/` |
| Safety, evidence validation, retention, metrics | `app/services/` |
| Migrations | `alembic/` |
| Tests (unit, API/acceptance AC-01–12, browser E2E) | `tests/` |

Stack: FastAPI, Pydantic, SQLAlchemy + Alembic, PostgreSQL job table with a worker, Jinja templates and dependency-free
browser JS (no build step). The PRD suggests TypeScript; plain ES modules were used to keep the project build-free.

## Quick start
```bash
pip install -r requirements.txt
EMBEDDED_WORKER=true uvicorn app.main:app        # http://localhost:8000, offline fake provider, SQLite
```
See `docs/OPERATIONS.md` for Docker Compose, production configuration, kill switches, metrics and the launch checklist.

## Static preview on GitHub Pages
The landing page, demo and info pages can be published as a static site (`scripts/build_pages.py`, `.github/workflows/pages.yml`);
the app itself needs a server. See `docs/OPERATIONS.md`.

## Status
The manual-input MVP (Release A) is implemented. The connected YouTube release (B) is intentionally **not** built and cannot be
enabled. Items needing human work before a public beta are listed at the end of `docs/OPERATIONS.md`.
