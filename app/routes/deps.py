from __future__ import annotations

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SessionModel
from ..services import sessions


def session_read(request: Request, response: Response, db: Session = Depends(get_db)) -> SessionModel:
    s = sessions.authenticate(request, response, db)
    db.commit()  # persists secret rotation
    return s


def session_write(request: Request, response: Response, db: Session = Depends(get_db)) -> SessionModel:
    s = sessions.authenticate(request, response, db)
    sessions.check_csrf(request, s)
    db.commit()
    return s


def session_write_allow_expired(request: Request, response: Response, db: Session = Depends(get_db)) -> SessionModel:
    s = sessions.authenticate(request, response, db, allow_expired=True)
    sessions.check_csrf(request, s)
    return s
