from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cli import COMPANY_CODE, seed_company, sync_tally_masters
from app.modules.integrations.tally import xml_client
from app.modules.integrations.tally.xml_client import (
    TallyError,
    parse_amount,
    parse_days,
    parse_godowns,
    parse_ledgers,
)
from app.modules.masters.models import Dealer, Godown
from app.modules.masters.schemas import TallyGodownIn
from app.modules.masters.service import upsert_godowns_from_tally

FIX = Path(__file__).parent / "fixtures" / "tally"


def test_parse_ledgers_tolerates_bad_chars_and_missing_fields():
    ledgers = parse_ledgers((FIX / "ledgers.xml").read_text())
    assert [lg.name for lg in ledgers] == ["RBM (Bagdola)", "Sharma Building Material"]
    rbm, sharma = ledgers
    assert rbm.credit_limit == Decimal("500000.00") and rbm.credit_period_days == 15
    assert sharma.parent == "Dwarka Parties" and sharma.gstin == "07ABCDE1234F1Z5"
    assert sharma.credit_limit is None


def test_amount_and_day_parsing():
    assert parse_amount("₹ 1,00,000.00 Dr") == Decimal("100000.00")
    assert parse_amount("-2500.50") == Decimal("2500.50")
    assert parse_amount("") is None
    assert parse_days("30 Days") == 30 and parse_days("15-Apr-2026") is None


def test_tally_line_error_is_raised():
    with pytest.raises(TallyError, match="Could not find Company"):
        parse_ledgers("<ENVELOPE><BODY><DATA><LINEERROR>Could not find Company</LINEERROR></DATA></BODY></ENVELOPE>")


def test_godown_sync_skips_main_location_and_keeps_erp_switch(world, db):
    c = world.company()
    godowns = parse_godowns((FIX / "godowns.xml").read_text())
    stats = upsert_godowns_from_tally(db, c.id, godowns, None, full=True)
    db.commit()
    assert stats["created"] == 5
    rows = {g.name: g for g in db.execute(select(Godown).where(Godown.company_id == c.id)).scalars()}
    assert "Main Location" not in rows
    assert rows["Direct"].kind == "DIRECT" and rows["Siding"].kind == "SIDING" and rows["Nasirpur"].kind == "GODOWN"
    assert all(g.erp_enabled for g in rows.values())

    # switched off in ERP; re-sync must not switch it back on
    rows["Old Gurgaon Godown"].erp_enabled = False
    db.commit()
    upsert_godowns_from_tally(db, c.id, godowns, None, full=True)
    db.commit()
    db.refresh(rows["Old Gurgaon Godown"])
    assert rows["Old Gurgaon Godown"].erp_enabled is False

    # removed from Tally → flagged and switched off, never deleted
    stats = upsert_godowns_from_tally(db, c.id, [g for g in godowns if g.name != "Uttam Nagar"], None, full=True)
    db.commit()
    db.refresh(rows["Uttam Nagar"])
    assert stats["missing"] == 1 and rows["Uttam Nagar"].tally_deleted and not rows["Uttam Nagar"].erp_enabled


def test_rename_in_tally_follows_guid(world, db):
    c = world.company()
    upsert_godowns_from_tally(db, c.id, [TallyGodownIn(guid="x1", name="Palam")], None)
    upsert_godowns_from_tally(db, c.id, [TallyGodownIn(guid="x1", name="Nasirpur")], None)
    db.commit()
    [g] = db.execute(select(Godown).where(Godown.company_id == c.id)).scalars().all()
    assert g.name == "Nasirpur" and g.code == "PALAM"  # code is stable


def test_erp_switch_api(world, client):
    c = world.company()
    g = world.godown(c, "Siding")
    admin, sales = world.user(c, ["SUPER_ADMIN"]), world.user(c, ["SALES_EXECUTIVE"])
    h = world.headers(admin, c)
    assert client.post(f"/api/v1/godowns/{g.id}/erp-disable", headers=world.headers(sales, c)).status_code == 403
    r = client.post(f"/api/v1/godowns/{g.id}/erp-disable", headers=h)
    assert r.status_code == 200 and r.json()["erp_enabled"] is False
    assert client.get("/api/v1/godowns", params={"erp_enabled": True}, headers=h).json()["total"] == 0
    assert client.get("/api/v1/godowns", headers=h).json()["total"] == 1
    assert client.post("/api/v1/godowns", json={"code": "X", "name": "Manual"}, headers=h).status_code == 405
    # name is Tally's: PATCH ignores it (not in the update schema)
    r = client.patch(f"/api/v1/godowns/{g.id}", json={"name": "Renamed", "city": "Delhi"}, headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Siding" and r.json()["city"] == "Delhi"


def test_sync_command_end_to_end_with_fake_tally(world, db, monkeypatch):
    seed_company(db)
    db.commit()
    responses = {
        "ERPCompanies": "<ENVELOPE><BODY><DATA><COLLECTION><COMPANY NAME='Ishika Traders Daily.'/></COLLECTION></DATA></BODY></ENVELOPE>",
        "ERPDebtors": (FIX / "ledgers.xml").read_text(),
        "ERPGodowns": (FIX / "godowns.xml").read_text(),
        "ERPStockItems": (FIX / "stockitems.xml").read_text(),
    }

    def fake_post(self, body):
        for key, value in responses.items():
            if f"<ID>{key}</ID>" in body.replace("\n", ""):
                return value
        raise AssertionError("unexpected request")

    monkeypatch.setattr(xml_client.TallyClient, "post", fake_post)
    sync_tally_masters(db, "http://tally.local:9000")
    db.commit()
    company_dealers = db.execute(select(Dealer)).scalars().all()
    assert {d.name for d in company_dealers} == {"RBM (Bagdola)", "Sharma Building Material"}
    from app.modules.masters.models import Company

    cid = db.execute(select(Company.id).where(Company.code == COMPANY_CODE)).scalar_one()
    assert db.execute(select(Godown).where(Godown.company_id == cid)).scalars().all().__len__() == 5
    # running again is a no-op
    sync_tally_masters(db, "http://tally.local:9000")
    db.commit()
    assert len(db.execute(select(Dealer)).scalars().all()) == 2


def test_company_name_matching_tolerates_trailing_dot():
    from app.cli import match_company_name

    assert match_company_name("Ishika Traders Daily", ["Ishika Traders Daily."]) == "Ishika Traders Daily."
    assert match_company_name("Ishika Traders Daily", ["ishika  traders daily "]) == "ishika  traders daily "
    assert match_company_name("Ishika Traders Daily", ["Ishika Traders"]) is None
    assert match_company_name("Ishika Traders Daily", ["Ishika Traders Daily", "Ishika Traders Daily."]) == "Ishika Traders Daily"


def test_vehicle_number_godowns_arrive_switched_off(world, db):
    from app.modules.masters.service import looks_like_vehicle

    for v in ["DL01MA5579", "HR38AB2126 (ROCK)", "UP17AT3451", "HR38T3383", "DL01M9401 (ROCK)", "RJ05GB3663"]:
        assert looks_like_vehicle(v), v
    for g in ["Nasirpur", "Palam Godown", "SHREE KAKROLA", "W1", "Siding Rock (TQ1)", "Kakrola Dump Rock (TE8)"]:
        assert not looks_like_vehicle(g), g
    c = world.company()
    upsert_godowns_from_tally(db, c.id, [TallyGodownIn(guid="a", name="HR38AB2126 (ROCK)"),
                                         TallyGodownIn(guid="b", name="Palam Godown")], None)
    db.commit()
    rows = {g.name: g.erp_enabled for g in db.execute(select(Godown)).scalars()}
    assert rows == {"HR38AB2126 (ROCK)": False, "Palam Godown": True}


def test_doubtful_debts_ledgers_are_blocked_but_manual_unblock_sticks(world, db, client):
    from app.modules.masters.models import Dealer
    from app.modules.masters.schemas import TallyLedgerIn
    from app.modules.masters.service import upsert_dealers_from_tally

    c = world.company()
    L = lambda guid, name, parent: TallyLedgerIn(guid=guid, name=name, parent=parent)  # noqa: E731
    upsert_dealers_from_tally(db, c.id, [L("1", "A-62", "Doubtful Debts"), L("2", "1569 Driver", "Sundry Debtors"),
                                         L("3", "Abhay Jyoti", "Sundry Debtors")], None)
    db.commit()
    st = {d.name: (d.status, d.blocked_reason) for d in db.execute(select(Dealer)).scalars()}
    assert st["A-62"] == ("BLOCKED", "Doubtful Debts group in Tally")
    assert st["1569 Driver"] == ("ACTIVE", None)

    # moved into Doubtful Debts later → blocked on next sync
    upsert_dealers_from_tally(db, c.id, [L("3", "Abhay Jyoti", "Doubtful Debts")], None)
    db.commit()
    abhay = db.execute(select(Dealer).where(Dealer.name == "Abhay Jyoti")).scalar_one()
    assert abhay.status == "BLOCKED"

    # admin unblocks by hand → a re-sync does not block again
    h = world.headers(world.user(c, ["SUPER_ADMIN"]), c)
    assert client.post(f"/api/v1/dealers/{abhay.id}/unblock", headers=h).status_code == 200
    upsert_dealers_from_tally(db, c.id, [L("3", "Abhay Jyoti", "Doubtful Debts")], None)
    db.commit()
    db.refresh(abhay)
    assert abhay.status == "ACTIVE"

    # moved back out of Doubtful Debts → the automatic block is lifted
    upsert_dealers_from_tally(db, c.id, [L("1", "A-62", "Sundry Debtors")], None)
    db.commit()
    assert db.execute(select(Dealer).where(Dealer.name == "A-62")).scalar_one().status == "ACTIVE"
