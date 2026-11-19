"""export-dealer-contacts / import-dealer-contacts: copy dealers' mobile numbers between two copies of the ERP."""

import json

from sqlalchemy import delete, select

from app.cli import export_dealer_contacts, import_dealer_contacts
from app.modules.masters.models import Dealer, DealerContact


def _dealer(db, co, code):
    d = Dealer(company_id=co.id, code=code, name=f"Dealer {code}", tally_ledger_guid=f"g-{code}")
    db.add(d)
    db.flush()
    return d


def test_numbers_go_to_the_matching_dealers(world, tmp_path):
    db = world.db
    co = world.company()
    a = _dealer(db, co, "A1")
    b = _dealer(db, co, "B1")
    a.tally_ledger_name = "Sharma Building Material"
    b.tally_mobile = "011-2345678 / 98100 22222"
    db.add_all([DealerContact(dealer_id=a.id, name="Sharma ji", phone="9810011111", is_primary=True,
                              whatsapp_opt_in=True),
                DealerContact(dealer_id=a.id, name="Munim", phone="9810033333")])
    db.flush()
    f = tmp_path / "m.json"
    export_dealer_contacts(db, str(f))
    rows = json.loads(f.read_text())["contacts"]
    assert len(rows) == 3

    # the other ERP: same dealers (found by Tally ledger name), no saved numbers, Tally mobile not synced yet
    db.execute(delete(DealerContact))
    b.tally_mobile = None
    a.tally_ledger_guid = None
    db.flush()
    import_dealer_contacts(db, str(f), dry_run=False)
    got = {(c.dealer_id, c.phone, c.is_primary) for c in db.execute(select(DealerContact)).scalars()}
    assert got == {(a.id, "9810011111", True), (a.id, "9810033333", False), (b.id, "9810022222", True)}

    # running it again adds nothing
    import_dealer_contacts(db, str(f), dry_run=False)
    assert len(db.execute(select(DealerContact)).scalars().all()) == 3


def test_unknown_dealer_is_reported_not_added(world, tmp_path, capsys):
    db = world.db
    co = world.company()
    _dealer(db, co, "A1")
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"kind": "tayal-erp-dealer-contacts", "contacts": [
        {"dealer": "Nobody Traders", "code": "ZZ", "phone": "9810044444", "name": "x"}]}))
    import_dealer_contacts(db, str(f), dry_run=False)
    assert "Nobody Traders (1)" in capsys.readouterr().out
    assert db.execute(select(DealerContact)).first() is None
    assert db.execute(select(Dealer)).first() is not None
