"""Shared FastAPI dependencies."""

import hmac
from collections.abc import Iterator

from fastapi import Header, HTTPException, status
from sqlalchemy import Connection

from app.config import get_settings
from app.db import get_engine


def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_api_key
    if not expected or not x_internal_key or not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


def get_conn() -> Iterator[Connection]:
    with get_engine().connect() as conn:
        yield conn
