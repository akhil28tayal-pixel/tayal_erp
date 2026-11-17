"""Tests run against a real PostgreSQL database (ERP_TEST_DATABASE_URL), never mocks.

Session start: migrate down to base and back up to head (proves migrations are reversible), seed base data.
Each test: business tables truncated, so tests are independent.
"""

import os
import uuid

os.environ.setdefault(
    "ERP_TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/tayal_erp_test"
)
os.environ["ERP_DATABASE_URL"] = os.environ["ERP_TEST_DATABASE_URL"]
os.environ["ERP_ENV"] = "test"
os.environ["ERP_JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-0123456789"

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from alembic import command  # noqa: E402
from app.cli import seed_base, seed_sample_products  # noqa: E402
from app.core.db import SessionLocal, get_engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.identity.models import Role, User, UserGodownScope, UserRole  # noqa: E402
from app.modules.masters.models import Company, Employee, InventoryOwner  # noqa: E402

REFERENCE_TABLES = {"permissions", "roles", "role_permissions", "uoms", "alembic_version"}
PASSWORD = "secret-pass-1"


@pytest.fixture(scope="session", autouse=True)
def _database():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["ERP_DATABASE_URL"])
    # Leftover rows from a previous run can block downgrades (e.g. products of a newer category): clear first.
    with get_engine().begin() as conn:
        tables = [r[0] for r in conn.execute(text(
            "select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'"))]
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    db = SessionLocal()
    seed_base(db)
    db.commit()
    db.close()
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean():
    with get_engine().begin() as conn:
        tables = [
            r[0] for r in conn.execute(text("select tablename from pg_tables where schemaname='public'"))
            if r[0] not in REFERENCE_TABLES
        ]
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
        # keep the global conversions seeded by seed_base
    db = SessionLocal()
    seed_base(db)
    db.commit()
    db.close()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def client():
    return TestClient(app)


class World:
    """Helpers to build companies/users quickly inside a test."""

    def __init__(self, db, client):
        self.db, self.client = db, client

    def company(self, code: str = "ITD", name: str | None = None) -> Company:
        c = Company(code=code, legal_name=name or f"{code} Co", doc_prefix=code)
        self.db.add(c)
        self.db.flush()
        self.db.add(InventoryOwner(company_id=c.id, kind="OWN_TRADING", name=f"{code} own"))
        self.db.commit()
        return c

    def employee(self, company: Company, code: str = "E1", name: str = "Emp") -> Employee:
        e = Employee(company_id=company.id, code=code, full_name=name)
        self.db.add(e)
        self.db.commit()
        return e

    def user(self, company: Company, roles: list[str], phone: str | None = None, employee: Employee | None = None,
             godown_ids: list[uuid.UUID] | None = None) -> User:
        u = User(phone=phone or str(uuid.uuid4().int)[:10], full_name="Test User",
                 password_hash=hash_password(PASSWORD), employee_id=employee.id if employee else None)
        self.db.add(u)
        self.db.flush()
        for code in roles:
            role = self.db.execute(select(Role).where(Role.code == code)).scalar_one()
            self.db.add(UserRole(user_id=u.id, role_id=role.id, company_id=company.id))
        for g in godown_ids or []:
            self.db.add(UserGodownScope(user_id=u.id, godown_id=g))
        self.db.commit()
        return u

    def login(self, user: User) -> dict:
        r = self.client.post("/api/v1/auth/login", json={"phone": user.phone, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()

    def headers(self, user: User, company: Company) -> dict:
        return {"Authorization": f"Bearer {self.login(user)['access_token']}", "X-Company-Id": str(company.id)}

    def godown(self, company: Company, name: str, guid: str | None = None):
        from app.modules.masters.models import Godown
        from app.modules.masters.schemas import TallyGodownIn
        from app.modules.masters.service import upsert_godowns_from_tally

        upsert_godowns_from_tally(self.db, company.id, [TallyGodownIn(guid=guid or f"g-{name}", name=name)], None)
        self.db.commit()
        return self.db.execute(select(Godown).where(Godown.company_id == company.id, Godown.name == name)).scalar_one()

    def catalog(self, company: Company | None = None) -> None:
        """Sample products linked to `company` (default: the first company created in the test)."""
        company = company or self.db.execute(select(Company).order_by(Company.created_at)).scalars().first()
        seed_sample_products(self.db, company)
        self.db.commit()


@pytest.fixture
def world(db, client):
    return World(db, client)
