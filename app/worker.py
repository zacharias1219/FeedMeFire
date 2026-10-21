"""Dedicated generation worker: `python -m app.worker` (PRD §11.3)."""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid

from . import db as dbm, logging_setup
from .config import settings
from .services import retention
from .services.generation import run_job
from .services.jobs import claim_next

log = logging.getLogger("rmf.worker")


def process_one(owner: str | None = None) -> bool:
    """Claim and run a single eligible job. Returns False when the queue is empty. Used by tests too."""
    owner = owner or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    db = dbm.SessionLocal()
    try:
        job = claim_next(db, owner)
    finally:
        db.close()
    if job is None:
        return False
    run_job(job.id, owner)
    return True


def _loop(stop: threading.Event) -> None:
    owner = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    while not stop.is_set():
        try:
            if not process_one(owner):
                stop.wait(settings.poll_interval_s)
        except Exception as e:  # noqa: BLE001 - keep the worker alive; log type only
            log.error("worker loop error (%s)", type(e).__name__)
            stop.wait(settings.poll_interval_s * 2)


def _cleanup_loop(stop: threading.Event) -> None:
    while not stop.wait(60):
        try:
            with dbm.session_scope() as db:
                retention.cleanup(db)
        except Exception as e:  # noqa: BLE001
            log.error("cleanup error (%s)", type(e).__name__)


def start_workers(stop: threading.Event) -> list[threading.Thread]:
    threads = [threading.Thread(target=_loop, args=(stop,), daemon=True, name=f"worker-{i}")
               for i in range(settings.worker_concurrency)]
    threads.append(threading.Thread(target=_cleanup_loop, args=(stop,), daemon=True, name="cleanup"))
    for t in threads:
        t.start()
    return threads


def main() -> None:
    logging_setup.setup()
    settings.validate_for_runtime()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker starting (concurrency=%d, provider=%s)", settings.worker_concurrency, settings.resolved_provider)
    threads = start_workers(stop)
    stop.wait()
    for t in threads:
        t.join(timeout=10)


if __name__ == "__main__":
    main()
