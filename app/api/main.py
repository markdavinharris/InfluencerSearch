"""FastAPI app. Only the Next.js server calls this (BFF pattern): every route except
/health requires the X-Internal-Key header."""

from fastapi import Depends, FastAPI
from sqlalchemy import text

from app.api.deps import require_internal_key
from app.db import get_engine
from app.log import setup_logging

setup_logging()

app = FastAPI(title="Topic Influence Mapper", version="0.1.0", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
        db = "ok"
    except Exception as exc:  # health should report, not raise
        db = f"error: {type(exc).__name__}"
    return {"status": "ok", "db": db}


protected = [Depends(require_internal_key)]
