import uuid
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.platform.models import DocumentSequence


def fiscal_year(d: date) -> str:
    """Indian FY April–March: 2026-11-09 → '2627'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def next_number(db: Session, company_id: uuid.UUID, doc_type: str, on: date | None = None,
                per_fy: bool = True) -> int:
    """Gap-free per company/doc type/FY, safe under concurrency (row lock via upsert ... returning)."""
    fy = fiscal_year(on or date.today()) if per_fy else "0000"
    stmt = (
        insert(DocumentSequence)
        .values(company_id=company_id, doc_type=doc_type, fiscal_year=fy, next_value=2)
        .on_conflict_do_update(
            index_elements=["company_id", "doc_type", "fiscal_year"],
            set_={"next_value": DocumentSequence.next_value + 1},
        )
        .returning(DocumentSequence.next_value)
    )
    return db.execute(stmt).scalar_one() - 1


def format_doc_no(prefix: str, doc: str, number: int, on: date | None = None) -> str:
    return f"{prefix}/{doc}/{fiscal_year(on or date.today())}/{number:06d}"
