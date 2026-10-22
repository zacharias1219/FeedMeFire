from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


def utcnow() -> datetime:
    """Naive UTC. Every timestamp in the schema is naive UTC (portable across SQLite/PostgreSQL)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hours_from_now(h: float) -> datetime:
    return utcnow() + timedelta(hours=h)


def make_engine(url: str | None = None):
    url = url or settings.database_url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # FK cascades are the deletion mechanism; they are off by default in SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def rebind(url: str) -> None:
    """Point the module at another database (tests)."""
    global engine
    engine = make_engine(url)
    SessionLocal.configure(bind=engine)


@contextmanager
def session_scope():
    """Short-lived unit of work. Never hold one open across a provider call."""
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    """Request-scoped session. Handlers commit explicitly so a response is never sent before its writes land."""
    db: Session = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
