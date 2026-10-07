from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, text

from app.api.deps import get_conn
from app.schemas import MergeRequest, MergeResponse

router = APIRouter(prefix="/persons", tags=["persons"])
Conn = Annotated[Connection, Depends(get_conn)]


@router.post("/merge", response_model=MergeResponse)
def merge(body: MergeRequest, conn: Conn) -> MergeResponse:
    """Attach authors to one person. If any of them already belongs to a person, that person
    is reused (and renamed); accounts of other persons involved are moved over."""
    ids = sorted(set(body.author_ids))
    found = conn.execute(
        text("SELECT id, person_id FROM authors WHERE id = ANY(:ids)"), {"ids": ids}
    ).all()
    if len(found) != len(ids):
        missing = set(ids) - {r.id for r in found}
        raise HTTPException(404, f"unknown author ids: {sorted(missing)}")
    existing = sorted({r.person_id for r in found if r.person_id})
    if existing:
        person_id = existing[0]
        conn.execute(
            text("UPDATE persons SET display_name = :n WHERE id = :p"),
            {"n": body.display_name, "p": person_id},
        )
        if len(existing) > 1:
            conn.execute(
                text("UPDATE authors SET person_id = :p WHERE person_id = ANY(:o)"),
                {"p": person_id, "o": existing[1:]},
            )
            conn.execute(text("DELETE FROM persons WHERE id = ANY(:o)"), {"o": existing[1:]})
    else:
        person_id = conn.execute(
            text("INSERT INTO persons (display_name) VALUES (:n) RETURNING id"),
            {"n": body.display_name},
        ).scalar_one()
    conn.execute(
        text("UPDATE authors SET person_id = :p WHERE id = ANY(:ids)"), {"p": person_id, "ids": ids}
    )
    all_ids = (
        conn.execute(
            text("SELECT id FROM authors WHERE person_id = :p ORDER BY id"), {"p": person_id}
        )
        .scalars()
        .all()
    )
    conn.commit()
    return MergeResponse(
        person_id=person_id, display_name=body.display_name, author_ids=list(all_ids)
    )


@router.post("/unmerge/{author_id}", status_code=204)
def unmerge(author_id: int, conn: Conn) -> None:
    """Detach one account from its person (the person row stays if others remain)."""
    pid = conn.execute(
        text("SELECT person_id FROM authors WHERE id = :id"), {"id": author_id}
    ).scalar()
    conn.execute(text("UPDATE authors SET person_id = NULL WHERE id = :id"), {"id": author_id})
    if pid:
        conn.execute(
            text(
                "DELETE FROM persons p WHERE id = :p AND NOT EXISTS "
                "(SELECT 1 FROM authors a WHERE a.person_id = p.id)"
            ),
            {"p": pid},
        )
    conn.commit()
