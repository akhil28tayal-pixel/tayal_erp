"""reset-data: start again from an empty ERP, keeping one super admin."""

from sqlalchemy import func, select, text

from app.cli import reset_data
from app.modules.identity.models import User
from app.modules.masters.models import Company, Dealer, Product
from tests.test_orders_dispatch import Shop


def _n(db, table):
    return db.execute(text(f'select count(*) from "{table}"')).scalar_one()


def _busy_shop(world):
    shop = Shop(world)
    o = shop.order(shop.sales).json()
    o = shop.post(shop.ops, f"/orders/{o['id']}/approve", {}).json()
    shop.plan(shop.ops, o, "100", send_now=True)
    boss = world.user(shop.co, ["SUPER_ADMIN"])
    world.db.commit()
    return shop, boss


def test_reset_everything_keeps_one_super_admin(world):
    shop, boss = _busy_shop(world)
    db = world.db
    assert _n(db, "sales_orders") == 1 and _n(db, "delivery_orders") == 1
    reset_data(db, boss.phone, keep_masters=False, yes=True, no_backup=True)
    db.commit()
    for t in ("sales_orders", "delivery_orders", "dealers", "godowns", "vehicles", "drivers", "audit_logs"):
        assert _n(db, t) == 0, t
    assert [u.phone for u in db.execute(select(User)).scalars()] == [boss.phone]
    assert db.execute(select(func.count()).select_from(Product)).scalar_one() > 0  # catalogue stays
    assert db.get(Company, shop.co.id) is not None


def test_reset_keeping_masters(world):
    shop, boss = _busy_shop(world)
    db = world.db
    reset_data(db, boss.phone, keep_masters=True, yes=True, no_backup=True)
    db.commit()
    assert _n(db, "sales_orders") == 0 and _n(db, "delivery_orders") == 0
    assert db.execute(select(func.count()).select_from(Dealer)).scalar_one() == 2
    assert _n(db, "vehicles") == 2 and _n(db, "drivers") == 1 and _n(db, "users") == 1


def test_refuses_someone_who_is_not_super_admin(world):
    shop, _ = _busy_shop(world)
    try:
        reset_data(world.db, shop.ops.phone, keep_masters=False, yes=True, no_backup=True)
        raise AssertionError("should have stopped")
    except SystemExit as e:
        assert "not a super admin" in str(e)
    assert _n(world.db, "sales_orders") == 1
