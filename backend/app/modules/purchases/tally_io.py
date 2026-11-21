# ruff: noqa: E741, B023  (ported unchanged from the Purchase Uploader app)
"""Talking to TallyPrime for purchases and stock.

The purchase parts (XML cleaning, duplicate check, the Purchase voucher) are ported unchanged from the Purchase
Uploader app's tally.py, so vouchers look exactly as before. Godown-wise stock is new: Tally keeps no ready
"stock per godown" list, so it is worked out the way Tally itself does it — opening stock per godown from the
stock items, plus every inward/outward batch line of every stock voucher since the books began.
"""

import datetime as dt
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

_BAD_REF = re.compile(r"&#(x[0-9a-fA-F]+|\d+);")
_BAD_RAW = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")



class TallyError(Exception):
    pass


def _clean(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

    def ok(m):
        v = m.group(1)
        n = int(v[1:], 16) if v[0] in "xX" else int(v)
        return m.group(0) if n in (9, 10, 13) or n >= 32 else ""

    text = _BAD_RAW.sub("", _BAD_REF.sub(ok, text))
    # Tally emits namespaced tags like <UDF:FOO.LIST> without declaring the namespace -> strip prefixes
    text = re.sub(r"\sxmlns:\w+=\"[^\"]*\"", "", text)
    text = re.sub(r"<(/?)([A-Za-z_][\w.]*):", r"<\1\2_", text)
    text = re.sub(r"(\s)([A-Za-z_][\w.]*):([\w.]+=)", r"\1\2_\3", text)
    # undefined named entities (anything other than the 5 XML ones)
    text = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
    return text



def _e(s):
    return escape(str(s or ""))


MAIN_LOCATION = "Main Location"
# Voucher types that carry items but do not move stock.
NON_STOCK_BASES = {"sales order", "purchase order", "job work in order", "job work out order", "memorandum",
                   "reversing journal", "attendance", "payroll"}


class Tally:
    def __init__(self, url: str, company: str = "", timeout: int = 60):
        self.url = url.rstrip("/")
        self.company = company
        self.timeout = timeout
        self.save_dir = None  # a Path: keep every request and reply (diagnosis)
        self._n = 0

    def post(self, xml: str, timeout: int | None = None) -> ET.Element:
        req = urllib.request.Request(self.url, data=xml.encode("utf-8"),
                                     headers={"Content-Type": "text/xml; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = r.read()
        except Exception as e:  # noqa: BLE001
            raise TallyError(f"Cannot reach Tally at {self.url} ({e}). Is Tally open, and is Tailscale on "
                             f"on both machines?") from e
        if self.save_dir is not None:
            self._n += 1
            (self.save_dir / f"{self._n:02d}_reply.xml").write_bytes(body)
            (self.save_dir / f"{self._n:02d}_request.xml").write_text(xml)
        try:
            return ET.fromstring(_clean(body))
        except ET.ParseError as e:
            raise TallyError(f"Tally sent a reply that could not be read ({e}).") from e

    # ---------- exports ----------
    def _collection(self, ctype, fetch, filt=None, from_date=None, to_date=None, company=True, timeout=None,
                    formulae: dict[str, str] | None = None):
        sv = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
        if company and self.company:
            sv += f"<SVCURRENTCOMPANY>{_e(self.company)}</SVCURRENTCOMPANY>"
        if from_date:
            sv += f"<SVFROMDATE>{from_date:%Y%m%d}</SVFROMDATE><SVTODATE>{to_date:%Y%m%d}</SVTODATE>"
        filt_xml = sysf = ""
        if filt:
            filt_xml = "<FILTER>PUFilt</FILTER>"
            sysf = f'<SYSTEM TYPE="Formulae" NAME="PUFilt">{_e(filt)}</SYSTEM>'
        for name, f in (formulae or {}).items():  # helper formulae the filter refers to
            sysf += f'<SYSTEM TYPE="Formulae" NAME="{name}">{_e(f)}</SYSTEM>'
        xml = f"""<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE>
<ID>PUColl</ID></HEADER><BODY><DESC><STATICVARIABLES>{sv}</STATICVARIABLES><TDL><TDLMESSAGE>
<COLLECTION NAME="PUColl" ISMODIFY="No"><TYPE>{ctype}</TYPE><FETCH>{fetch}</FETCH>{filt_xml}</COLLECTION>{sysf}
</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"""
        root = self.post(xml, timeout)
        err = root.find(".//LINEERROR")
        if err is not None and (err.text or "").strip():
            raise TallyError(f"Tally error: {err.text.strip()}")
        return root

    @staticmethod
    def _names(root, tag):
        out = []
        for el in root.iter(tag):
            n = el.get("NAME") or (el.findtext("NAME") or "")
            if n:
                out.append((n.strip(), el))
        return out

    def masters(self):
        """What the purchase check needs: ledgers, stock items with units, godowns."""
        led = self._names(self._collection("Ledger", "Name,Parent"), "LEDGER")
        items = self._names(self._collection("StockItem", "Name,BaseUnits"), "STOCKITEM")
        gds = self._names(self._collection("Godown", "Name"), "GODOWN")
        return {
            "ledgers": sorted([{"name": n, "parent": (e.findtext("PARENT") or "").strip()} for n, e in led],
                              key=lambda x: x["name"].lower()),
            "items": sorted([{"name": n, "unit": (e.findtext("BASEUNITS") or "").strip()} for n, e in items],
                            key=lambda x: x["name"].lower()),
            "godowns": sorted({n for n, _ in gds}, key=str.lower),
        }

    def existing_purchases(self, d_from: dt.date, d_to: dt.date, kind="Purchase"):
        """Return list of {ref, party, number, date} for purchase vouchers in the range."""
        root = self._collection("Voucher", "Date,VoucherNumber,Reference,PartyLedgerName,VoucherTypeName",
                                filt=f"$$Is{kind}:$VoucherTypeName AND NOT $IsCancelled",
                                from_date=d_from, to_date=d_to)
        out = []
        for v in root.iter("VOUCHER"):
            out.append({
                "ref": (v.findtext("REFERENCE") or "").strip(),
                "number": (v.findtext("VOUCHERNUMBER") or "").strip(),
                "party": (v.findtext("PARTYLEDGERNAME") or "").strip(),
                "date": (v.findtext("DATE") or "").strip(),
            })
        return out

    # ---------- import ----------
    def import_voucher(self, voucher_xml: str):
        xml = f"""<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>
<REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES>
<SVCURRENTCOMPANY>{_e(self.company)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>
<REQUESTDATA><TALLYMESSAGE xmlns:UDF="TallyUDF">{voucher_xml}</TALLYMESSAGE></REQUESTDATA>
</IMPORTDATA></BODY></ENVELOPE>"""
        root = self.post(xml)
        created = int(root.findtext(".//CREATED") or 0)
        errors = int(root.findtext(".//ERRORS") or 0)
        exc = int(root.findtext(".//EXCEPTIONS") or 0)
        msgs = [e.text.strip() for e in root.iter("LINEERROR") if e.text]
        if created >= 1 and not errors and not exc:
            return {"ok": True, "vchid": root.findtext(".//LASTVCHID") or ""}
        if created >= 1:  # it IS in Tally (sending again would make a second one), but Tally flagged a problem
            return {"ok": True, "vchid": root.findtext(".//LASTVCHID") or "", "warning": "; ".join(msgs) or
                    f"Tally made the voucher but put it under Exceptions (errors={errors}, exceptions={exc})"}
        return {"ok": False, "error": "; ".join(msgs) or
                f"Tally did not create the voucher (created={created}, errors={errors}, exceptions={exc})"}

    def create_ledger(self, name: str, parent: str = "Sundry Debtors", mobile: str | None = None,
                      address: list[str] | None = None, state: str | None = None, pincode: str | None = None,
                      gstin: str | None = None, contact: str | None = None) -> dict:
        """Make a party ledger (a new dealer). {"ok": True} if made, or if Tally already has one by that name."""
        lines = [x.strip() for x in (address or []) if x and x.strip()][:4]
        addr = "".join(f"<ADDRESS>{_e(x)}</ADDRESS>" for x in lines)
        reg = "Regular" if gstin else "Unregistered/Consumer"
        today = dt.date.today().strftime("%Y%m%d")
        # Both the older tags and TallyPrime 4's newer ones: Tally ignores the ones it doesn't know.
        led = (f'<LEDGER NAME="{_e(name)}" ACTION="Create"><NAME.LIST><NAME>{_e(name)}</NAME></NAME.LIST>'
               f"<PARENT>{_e(parent)}</PARENT><ISBILLWISEON>Yes</ISBILLWISEON><AFFECTSSTOCK>No</AFFECTSSTOCK>"
               + (f"<LEDGERMOBILE>{_e(mobile)}</LEDGERMOBILE>" if mobile else "")
               + (f"<LEDGERCONTACT>{_e(contact)}</LEDGERCONTACT>" if contact else "")
               + (f'<ADDRESS.LIST TYPE="String">{addr}</ADDRESS.LIST>' if addr else "")
               + (f"<LEDSTATENAME>{_e(state)}</LEDSTATENAME>" if state else "")
               + (f"<PINCODE>{_e(pincode)}</PINCODE>" if pincode else "")
               + "<COUNTRYNAME>India</COUNTRYNAME><COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE>"
               + f"<GSTREGISTRATIONTYPE>{reg}</GSTREGISTRATIONTYPE>"
               + (f"<PARTYGSTIN>{_e(gstin)}</PARTYGSTIN>" if gstin else "")
               + f'<LEDMAILINGDETAILS.LIST><APPLICABLEFROM>{today}</APPLICABLEFROM><MAILINGNAME>{_e(name)}</MAILINGNAME>'
               + (f'<ADDRESS.LIST TYPE="String">{addr}</ADDRESS.LIST>' if addr else "")
               + (f"<STATE>{_e(state)}</STATE>" if state else "") + (f"<PINCODE>{_e(pincode)}</PINCODE>" if pincode else "")
               + "<COUNTRY>India</COUNTRY></LEDMAILINGDETAILS.LIST>"
               + f"<LEDGSTREGDETAILS.LIST><APPLICABLEFROM>{today}</APPLICABLEFROM><GSTREGISTRATIONTYPE>{reg}</GSTREGISTRATIONTYPE>"
               + (f"<STATE>{_e(state)}</STATE>" if state else "") + (f"<GSTIN>{_e(gstin)}</GSTIN>" if gstin else "")
               + "</LEDGSTREGDETAILS.LIST></LEDGER>")
        xml = f"""<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>
<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME><STATICVARIABLES>
<SVCURRENTCOMPANY>{_e(self.company)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>
<REQUESTDATA><TALLYMESSAGE xmlns:UDF="TallyUDF">{led}</TALLYMESSAGE></REQUESTDATA>
</IMPORTDATA></BODY></ENVELOPE>"""
        root = self.post(xml)
        created = int(root.findtext(".//CREATED") or 0) + int(root.findtext(".//ALTERED") or 0)
        errors = int(root.findtext(".//ERRORS") or 0)
        msgs = [e.text.strip() for e in root.iter("LINEERROR") if e.text]
        if created >= 1 and not errors:
            return {"ok": True}
        if any("already exists" in m.lower() for m in msgs):
            return {"ok": True, "existed": True}
        return {"ok": False, "error": "; ".join(msgs) or f"Tally did not make the ledger (errors={errors})"}

    # ---------- godown-wise stock ----------
    def books_from(self) -> dt.date | None:
        root = self._collection("Company", "Name,BooksFrom,StartingFrom", company=False)
        for name, el in self._names(root, "COMPANY"):
            if name.lower() == (self.company or "").lower():
                return parse_tally_date(el.findtext("BOOKSFROM") or el.findtext("STARTINGFROM"))
        return None

    def godown_stock(self, to_date: dt.date | None = None) -> dict:
        """Stock per godown and item exactly as Tally's Godown Summary shows it (detailed view).

        The report comes back as a flat list of name / closing-quantity pairs: each godown followed by its
        items. Godown and item names are told apart with Tally's own godown and stock-item lists. Quick for Tally
        (it is the same report as Display > Godown Summary), unlike adding up every voucher since the books began.
        """
        to_date = to_date or dt.date.today()
        fy = dt.date(to_date.year - (1 if to_date.month < 4 else 0), 4, 1)
        godowns = {n.lower(): n for n, _ in self._names(self._collection("Godown", "Name"), "GODOWN")}
        items_root = self._collection("StockItem", "Name,BaseUnits")
        units = {n: (e.findtext("BASEUNITS") or "").strip() for n, e in self._names(items_root, "STOCKITEM")}
        items = {n.lower() for n in units}
        xml = f"""<ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER><BODY><EXPORTDATA><REQUESTDESC>
<REPORTNAME>Godown Summary</REPORTNAME><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
<SVCURRENTCOMPANY>{_e(self.company)}</SVCURRENTCOMPANY><SVFROMDATE>{fy:%Y%m%d}</SVFROMDATE>
<SVTODATE>{to_date:%Y%m%d}</SVTODATE><EXPLODEFLAG>Yes</EXPLODEFLAG></STATICVARIABLES></REQUESTDESC>
</EXPORTDATA></BODY></ENVELOPE>"""
        root = self.post(xml, timeout=300)
        err = root.find(".//LINEERROR")
        if err is not None and (err.text or "").strip():
            raise TallyError(f"Tally error: {err.text.strip()}")
        return parse_godown_summary(root, godowns, items, units, fy, to_date)


def parse_godown_summary(root: ET.Element, godowns: dict, items: set, units: dict, fy: dt.date,
                         to_date: dt.date) -> dict:
    stock, totals, unknown = defaultdict(Decimal), {}, set()
    current, name = None, None
    for el in root:
        if el.tag == "DSPACCNAME":
            name = (el.findtext("DSPDISPNAME") or "").strip()
            continue
        if el.tag != "DSPSTKINFO" or not name:
            continue
        qty_text = (el.findtext("DSPSTKCL/DSPCLQTY") or "").strip()
        key = name.lower()
        if key in godowns and (key not in items or current is None):
            current = godowns[key]  # a godown heading; its items follow
            if qty_text:
                totals[current] = parse_qty(qty_text)
        elif current is not None:
            if key not in items:
                unknown.add(name)
            if qty_text:
                stock[(current, name)] += parse_qty(qty_text)
        name = None
    # check: a godown's own items should add up to the total Tally prints for it (same unit only)
    mismatch = {}
    for g, total in totals.items():
        own = sum((q for (gd, _), q in stock.items() if gd == g), Decimal(0))
        if own != total:
            mismatch[g] = {"tally_total": str(total), "items_sum": str(own)}
    return {"stock": dict(stock), "units": units, "vouchers": 0,
            "skipped": {"names_not_items": sorted(unknown)[:20], "total_mismatch": mismatch} if (unknown or mismatch) else {},
            "from": fy.isoformat(), "to": to_date.isoformat()}


_QTY = re.compile(r"(\(-\)|-)?\s*([\d,]*\.?\d+)")


def parse_qty(text: str | None) -> Decimal:
    """' 600 Bags' → 600; '-12.5 MT' / '(-)12.5 MT' → -12.5; '600 Bags = 30 MT' → 600 (the main unit)."""
    if not text:
        return Decimal(0)
    m = _QTY.search(text)
    if not m:
        return Decimal(0)
    try:
        q = Decimal(m.group(2).replace(",", ""))
    except InvalidOperation:
        return Decimal(0)
    return -q if m.group(1) else q


def parse_tally_date(text: str | None) -> dt.date | None:
    t = (text or "").strip()
    for fmt in ("%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def _amt(x):
    return f"{x:.2f}"


def _qty(q, unit):
    q = int(q) if float(q).is_integer() else round(q, 3)
    return f" {q} {unit}".rstrip()


def purchase_voucher_xml(inv, cfg, units):
    """Build a Purchase voucher in item-invoice mode. units: {item_name: base_unit}."""
    d = dt.date.fromisoformat(inv["date"])
    party = inv["party_ledger"]
    total = inv["amount"]
    narr = f"{inv['supplier'].title()} Inv {inv['invoice_no']}"
    if inv.get("truck"):
        narr += f" | Truck {inv['truck']}"
    if inv.get("ref"):
        narr += f" | Ref {inv['ref']}"
    if inv["supplier"] == "shree" and inv.get("source"):
        narr += f" | Source {inv['source']}"
    vt = cfg.get("voucher_type") or "Purchase"

    bill = ""
    if cfg.get("use_bill_allocation", True):
        bill = (f"<BILLALLOCATIONS.LIST><NAME>{_e(inv['invoice_no'])}</NAME><BILLTYPE>New Ref</BILLTYPE>"
                f"<AMOUNT>{_amt(total)}</AMOUNT></BILLALLOCATIONS.LIST>")

    inv_xml = ""
    for l in inv["lines"]:
        unit = units.get(l["item"], "") or ""
        q = _qty(l["bags"], unit)
        rate = f"{l['rate']:.4f}".rstrip("0").rstrip(".")
        rate = f"{rate}/{unit}" if unit else rate
        inv_xml += f"""<ALLINVENTORYENTRIES.LIST>
<STOCKITEMNAME>{_e(l['item'])}</STOCKITEMNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<RATE>{rate}</RATE><AMOUNT>-{_amt(l['amount'])}</AMOUNT><ACTUALQTY>{q}</ACTUALQTY><BILLEDQTY>{q}</BILLEDQTY>
<BATCHALLOCATIONS.LIST><GODOWNNAME>{_e(inv['godown'])}</GODOWNNAME><BATCHNAME>Primary Batch</BATCHNAME>
<AMOUNT>-{_amt(l['amount'])}</AMOUNT><ACTUALQTY>{q}</ACTUALQTY><BILLEDQTY>{q}</BILLEDQTY></BATCHALLOCATIONS.LIST>
<ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>{_e(inv.get('purchase_ledger') or cfg['purchase_ledger'])}</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
<AMOUNT>-{_amt(l['amount'])}</AMOUNT></ACCOUNTINGALLOCATIONS.LIST>
</ALLINVENTORYENTRIES.LIST>"""

    tax_xml = ""
    if inv.get("gst_mode"):
        tl = inv.get("tax_ledgers") or {}
        for k in ("cgst", "sgst", "igst"):
            if abs(inv.get(k, 0)) >= 0.005:
                tax_xml += (f"<LEDGERENTRIES.LIST><LEDGERNAME>{_e(tl.get(k, ''))}</LEDGERNAME>"
                            f"<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{_amt(inv[k])}</AMOUNT></LEDGERENTRIES.LIST>\n")
    ro = inv.get("round_off", 0)
    if abs(ro) >= 0.005:
        tl = inv.get("tax_ledgers") or {}
        sign = "Yes" if ro > 0 else "No"
        tax_xml += (f"<LEDGERENTRIES.LIST><LEDGERNAME>{_e(tl.get('round_off', ''))}</LEDGERNAME>"
                    f"<ISDEEMEDPOSITIVE>{sign}</ISDEEMEDPOSITIVE><AMOUNT>{_amt(-ro)}</AMOUNT></LEDGERENTRIES.LIST>\n")

    return f"""<VOUCHER VCHTYPE="{_e(vt)}" ACTION="Create" OBJVIEW="Invoice Voucher View">
<DATE>{d:%Y%m%d}</DATE><EFFECTIVEDATE>{d:%Y%m%d}</EFFECTIVEDATE><VOUCHERTYPENAME>{_e(vt)}</VOUCHERTYPENAME>
<VOUCHERNUMBER>{_e(inv['invoice_no'])}</VOUCHERNUMBER><REFERENCE>{_e(inv['invoice_no'])}</REFERENCE>
<REFERENCEDATE>{d:%Y%m%d}</REFERENCEDATE>
<PARTYLEDGERNAME>{_e(party)}</PARTYLEDGERNAME><PARTYNAME>{_e(party)}</PARTYNAME>
<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW><ISINVOICE>Yes</ISINVOICE>
<NARRATION>{_e(narr)}</NARRATION>
<LEDGERENTRIES.LIST><LEDGERNAME>{_e(party)}</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
<ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>{_amt(total)}</AMOUNT>{bill}</LEDGERENTRIES.LIST>
{inv_xml}{tax_xml}</VOUCHER>"""
