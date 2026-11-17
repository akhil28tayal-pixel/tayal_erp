"""TallyPrime XML-over-HTTP: request builders and tolerant parsers for masters (ledgers, godowns).

Status: written against TallyPrime's documented collection-export format and tested with hand-made fixtures.
It has NOT yet been run against the real Ishika Traders Daily company. The first real run is the discovery
step; response quirks found there get added to the fixtures and parsers.
"""

import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

import httpx

from app.modules.masters.schemas import TallyGodownIn, TallyLedgerIn, TallyStockItemIn


class TallyError(Exception):
    pass


def _envelope(collection_id: str, company: str, collection_tdl: str) -> str:
    return f"""<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>{collection_id}</ID></HEADER>
 <BODY><DESC>
  <STATICVARIABLES>
   <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
   <SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>
  </STATICVARIABLES>
  <TDL><TDLMESSAGE>{collection_tdl}</TDLMESSAGE></TDL>
 </DESC></BODY>
</ENVELOPE>"""


def ledgers_request(company: str) -> str:
    """All ledgers under Sundry Debtors, including sub-groups."""
    tdl = """<COLLECTION NAME="ERPDebtors" ISMODIFY="No">
   <TYPE>Ledger</TYPE>
   <CHILDOF>$$GroupSundryDebtors</CHILDOF>
   <BELONGSTO>Yes</BELONGSTO>
   <FETCH>GUID, MASTERID, ALTERID, NAME, PARENT, PARTYGSTIN, CREDITLIMIT, BILLCREDITPERIOD, CLOSINGBALANCE, LEDGERMOBILE, LEDGERPHONE, LEDGERCONTACT, ADDRESS, LEDSTATENAME, PINCODE, LEDMAILINGDETAILS</FETCH>
  </COLLECTION>"""
    return _envelope("ERPDebtors", company, tdl)


def godowns_request(company: str) -> str:
    tdl = """<COLLECTION NAME="ERPGodowns" ISMODIFY="No">
   <TYPE>Godown</TYPE>
   <FETCH>GUID, MASTERID, ALTERID, NAME, PARENT</FETCH>
  </COLLECTION>"""
    return _envelope("ERPGodowns", company, tdl)


def stock_items_request(company: str) -> str:
    tdl = """<COLLECTION NAME="ERPStockItems" ISMODIFY="No">
   <TYPE>StockItem</TYPE>
   <FETCH>GUID, MASTERID, ALTERID, NAME, PARENT, CATEGORY, BASEUNITS</FETCH>
  </COLLECTION>"""
    return _envelope("ERPStockItems", company, tdl)


def companies_request() -> str:
    tdl = """<COLLECTION NAME="ERPCompanies" ISMODIFY="No"><TYPE>Company</TYPE><FETCH>NAME, GUID</FETCH></COLLECTION>"""
    return f"""<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE>
<ID>ERPCompanies</ID></HEADER><BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
</STATICVARIABLES><TDL><TDLMESSAGE>{tdl}</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"""


# ------------------------------------------------------------------ parsing
_BAD_ENTITY = re.compile(r"&#(?:x0*[0-8bBcCeEfF]|x0*1[0-9a-fA-F]|0*[0-8]|0*1[1-2]|0*1[4-9]|0*2[0-9]|0*3[01]);")
_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize(xml_text: str) -> str:
    """Tally sometimes emits control characters (e.g. &#4;) that make the XML invalid."""
    return _BAD_CHARS.sub("", _BAD_ENTITY.sub("", xml_text))


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _name(el: ET.Element) -> str | None:
    return (el.get("NAME") or _text(el, "NAME") or _text(el, "NAME.LIST/NAME") or "").strip() or None


def parse_amount(value: str | None) -> Decimal | None:
    """Tally amounts: '-50000.00', '50,000.00 Dr', '₹ 1,00,000.00'. Returns the absolute value."""
    if not value:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    try:
        return abs(Decimal(cleaned)) if cleaned not in {"", "-", "."} else None
    except InvalidOperation:
        return None


def parse_balance(value: str | None) -> Decimal | None:
    """Ledger balance as 'dealer owes us' (positive) / 'we owe the dealer' (negative).

    Tally writes debit balances as negative numbers in XML ('-15000.00'); some renderings append 'Dr'/'Cr'
    instead ('15,000.00 Dr'). An empty value means a nil balance."""
    if value is None:
        return None
    text_ = value.strip()
    if not text_:
        return Decimal("0")
    cleaned = re.sub(r"[^\d.\-]", "", text_.replace(",", ""))
    try:
        amount = Decimal(cleaned) if cleaned not in {"", "-", "."} else Decimal("0")
    except InvalidOperation:
        return None
    upper = text_.upper()
    if upper.endswith("DR"):
        return abs(amount)
    if upper.endswith("CR"):
        return -abs(amount)
    return -amount


def parse_days(value: str | None) -> int | None:
    """'15 Days' / '15' → 15. Date-style credit periods are ignored (None)."""
    if not value:
        return None
    m = re.match(r"\s*(\d+)\s*(day|days)?\s*$", value, re.I)
    return int(m.group(1)) if m else None


def _root(xml_text: str) -> ET.Element:
    try:
        root = ET.fromstring(sanitize(xml_text))
    except ET.ParseError as e:
        raise TallyError(f"Tally returned invalid XML: {e}") from e
    err = root.find(".//LINEERROR")
    if err is not None and err.text:
        raise TallyError(f"Tally error: {err.text.strip()}")
    return root


def parse_ledgers(xml_text: str) -> list[TallyLedgerIn]:
    out = []
    for el in _root(xml_text).iter("LEDGER"):
        name, guid = _name(el), _text(el, "GUID")
        if not name or not guid:
            continue
        gstin = _text(el, "PARTYGSTIN") or _text(el, "LEDGSTREGDETAILS.LIST/GSTIN")
        out.append(TallyLedgerIn(
            guid=guid, name=name, parent=_text(el, "PARENT"), gstin=gstin,
            credit_limit=parse_amount(_text(el, "CREDITLIMIT")),
            credit_period_days=parse_days(_text(el, "BILLCREDITPERIOD")),
            balance=parse_balance(_text(el, "CLOSINGBALANCE")),
            mobile=(_text(el, "LEDGERMOBILE") or "").strip() or None,
            phone=(_text(el, "LEDGERPHONE") or "").strip() or None,
            contact_name=(_text(el, "LEDGERCONTACT") or "").strip() or None,
            address=parse_address(el),
        ))
    return out


def parse_address(el: ET.Element) -> str | None:
    """Ledger address as one line. Older Tally puts ADDRESS.LIST / LEDSTATENAME / PINCODE on the ledger;
    TallyPrime 3+ also keeps them under LEDMAILINGDETAILS.LIST. The first non-empty set wins."""
    for scope in [el, *el.findall("LEDMAILINGDETAILS.LIST")]:
        lines = [a.text.strip() for a in scope.findall("ADDRESS.LIST/ADDRESS") if a.text and a.text.strip()]
        state = _text(scope, "LEDSTATENAME") or _text(scope, "STATE")
        pin = _text(scope, "PINCODE")
        tail = " ".join(x for x in [state, pin] if x)
        parts = lines + ([tail] if tail else [])
        if lines:
            return ", ".join(parts)[:500]
    return None


def parse_godowns(xml_text: str) -> list[TallyGodownIn]:
    out = []
    for el in _root(xml_text).iter("GODOWN"):
        name = _name(el)
        if name:
            out.append(TallyGodownIn(guid=_text(el, "GUID"), name=name, parent=_text(el, "PARENT")))
    return out


def parse_stock_items(xml_text: str) -> list[TallyStockItemIn]:
    out = []
    for el in _root(xml_text).iter("STOCKITEM"):
        name = _name(el)
        if name:
            out.append(TallyStockItemIn(guid=_text(el, "GUID"), name=name, parent=_text(el, "PARENT"),
                                        category=_text(el, "CATEGORY"), base_unit=_text(el, "BASEUNITS")))
    return out


def parse_companies(xml_text: str) -> list[str]:
    return [n for el in _root(xml_text).iter("COMPANY") if (n := _name(el))]


class TallyClient:
    def __init__(self, url: str, timeout: float = 60.0):
        self.url, self.timeout = url.rstrip("/"), timeout

    def post(self, body: str) -> str:
        try:
            r = httpx.post(self.url, content=body.encode("utf-8"),
                           headers={"Content-Type": "text/xml; charset=utf-8"}, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise TallyError(f"Cannot reach Tally at {self.url}: {e}") from e
        if r.status_code != 200:
            raise TallyError(f"Tally HTTP {r.status_code}")
        # Tally answers UTF-8 or UTF-16 depending on configuration.
        raw = r.content
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16")
        return raw.decode("utf-8", errors="replace")

    def companies(self) -> list[str]:
        return parse_companies(self.post(companies_request()))

    def ledgers(self, company: str) -> list[TallyLedgerIn]:
        return parse_ledgers(self.post(ledgers_request(company)))

    def stock_items(self, company: str) -> list[TallyStockItemIn]:
        return parse_stock_items(self.post(stock_items_request(company)))

    def godowns(self, company: str) -> list[TallyGodownIn]:
        return parse_godowns(self.post(godowns_request(company)))
