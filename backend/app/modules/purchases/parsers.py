# ruff: noqa: E741, B023  (ported unchanged from the Purchase Uploader app)
"""Read Ambuja / Shree purchase-invoice Excel exports into a common invoice shape.

Ported unchanged from the Purchase Uploader app (parsers.py), except that files are read from bytes.
"""
import datetime as dt
import io
import re

import openpyxl


def _num(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s[:11].strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognised date: {v!r}")


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _workbook(content: bytes):
    """Open an upload. Portals often save .xlsx data under a .xls name, or send real old .xls files."""
    head = content[:8]
    if head.startswith(b"PK"):
        return openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # real legacy .xls
        try:
            import xlrd
        except ImportError as e:
            raise ValueError("This is an old-format .xls file. Open it in Excel and save it as .xlsx.") from e
        book = xlrd.open_workbook(file_contents=content)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for sh in book.sheets():
            ws = wb.create_sheet(sh.name[:31] or "Sheet")
            for r in range(sh.nrows):
                row = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                    else:
                        row.append(cell.value)
                ws.append(row)
        return wb
    if head[:5].lower() in (b"<html", b"<?xml") or head[:4] == b"<tab":
        raise ValueError("This file is HTML/XML saved with an Excel name. Open it in Excel and save as .xlsx.")
    raise ValueError("This is not an Excel file.")


def _rows(content: bytes):
    wb = _workbook(content)
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        for i, r in enumerate(rows[:30]):
            cells = [_s(c).lower() for c in r]
            yield ws.title, i, cells, rows


def _col(header, *names, startswith=False):
    for n in names:
        n = n.lower()
        for i, h in enumerate(header):
            if (h.startswith(n) if startswith else h == n):
                return i
    return None


def detect_and_parse(content: bytes, cfg):
    for _sheet, hi, header, rows in _rows(content):
        if "gross value" in header and "invoice" in header and "material description" in header:
            return "ambuja", parse_ambuja(header, rows[hi + 1:], cfg)
        if "inv no." in header and "source" in header and any(h.startswith("amount") for h in header):
            return "shree", parse_shree(header, rows[hi + 1:], cfg)
    raise ValueError("Could not recognise this file as an Ambuja 'Invoice details' or a Shree "
                     "'Invoice Summary Report' export.")


def _match(text, rules, key="match"):
    """rules: list of {match: 'A|B', ...}; '|' = any of, '&' = all of (e.g. 'JUNGRODHAK&OPC')."""
    t = text.upper()
    for r in rules:
        for alt in r[key].split("|"):
            parts = [p.strip().upper() for p in alt.split("&") if p.strip()]
            if parts and all(p in t for p in parts):
                return r
    return None


def _line(cfg, supplier, desc, source_key, mt, gross, taxable=None, cgst=0.0, sgst=0.0, igst=0.0):
    """gross = invoice value incl. GST. taxable/cgst/sgst/igst given for Ambuja; derived for Shree."""
    scfg = cfg["suppliers"][supplier]
    bags = round(mt * cfg["bags_per_mt"], 3)
    if abs(bags - round(bags)) < 1e-6:
        bags = int(round(bags))
    prod = _match(desc, scfg["products"])
    if taxable is None:  # Shree: amount includes GST at a flat rate
        g = cfg.get("shree_gst", {})
        rate = float(g.get("rate", 18) or 0)
        taxable = round(gross / (1 + rate / 100), 2)
        if _match(source_key, [{"match": g.get("igst_sources", "")}]) if g.get("igst_sources") else False:
            igst = round(taxable * rate / 100, 2)
        else:
            cgst = sgst = round(taxable * rate / 200, 2)  # equal halves; paise difference goes to round off
    if cfg.get("gst"):
        amount = taxable
    elif supplier == "ambuja" and scfg.get("amount_field") == "net":
        amount = taxable
    else:
        amount = gross
    return {
        "description": desc,
        "item": prod["item"] if prod else "",
        "mt": mt,
        "bags": bags,
        "gross": round(gross, 2),
        "taxable": round(taxable, 2),
        "cgst": round(cgst, 2), "sgst": round(sgst, 2), "igst": round(igst, 2),
        "amount": round(amount, 2),
        "rate": round(amount / bags, 4) if bags else 0,
        "source_key": source_key,
    }


def _finish(inv, cfg, supplier):
    scfg = cfg["suppliers"][supplier]
    rule = None
    if supplier == "ambuja":
        for r in scfg["godowns"]:
            if any(inv["invoice_no"].upper().startswith(p.strip().upper()) for p in r["match"].split("|") if p.strip()):
                rule = r
                break
    else:
        rule = _match(inv["source"], scfg["godowns"])
    inv["godown"] = rule["godown"] if rule else ""
    L = inv["lines"]
    inv["items_total"] = round(sum(l["amount"] for l in L), 2)
    for k in ("gross", "taxable", "cgst", "sgst", "igst"):
        inv[k] = round(sum(l[k] for l in L), 2)
    inv["gst_mode"] = bool(cfg.get("gst"))
    # what the supplier is credited with
    inv["amount"] = inv["gross"] if inv["gst_mode"] else inv["items_total"]
    inv["round_off"] = round(inv["amount"] - inv["items_total"]
                             - ((inv["cgst"] + inv["sgst"] + inv["igst"]) if inv["gst_mode"] else 0), 2)
    inv["mt"] = round(sum(l["mt"] for l in L), 3)
    inv["bags"] = sum(l["bags"] for l in L)
    inv["supplier"] = supplier
    inv["party_ledger"] = scfg["ledger"]
    inv["id"] = f"{supplier}:{inv['invoice_no']}"
    return inv


def parse_ambuja(h, rows, cfg):
    c = {k: _col(h, k) for k in ["invoice", "ref no.", "bill date", "gross value", "net value", "quantity",
                                  "truck no.", "material description", "delivery doc", "bill type", "sold to",
                                  "cgst", "sgst", "igst", "tax amount"]}
    out = {}
    for r in rows:
        inv_no = _s(r[c["invoice"]])
        if not inv_no or inv_no.lower().startswith("total"):
            continue
        g = lambda k: _num(r[c[k]]) if c[k] is not None else 0.0  # noqa: E731
        inv = out.setdefault(inv_no, {
            "invoice_no": inv_no,
            "date": _date(r[c["bill date"]]).isoformat(),
            "truck": _s(r[c["truck no."]]),
            "ref": _s(r[c["ref no."]]),
            "delivery_doc": _s(r[c["delivery doc"]]) if c["delivery doc"] is not None else "",
            "dealer_code": _s(r[c["sold to"]]) if c["sold to"] is not None else "",
            "source": inv_no[:4],
            "lines": [],
        })
        cgst, sgst, igst = g("cgst"), g("sgst"), g("igst")
        if not (cgst or sgst or igst) and g("tax amount"):
            igst = g("tax amount")  # no split given: treat as IGST
        inv["lines"].append(_line(cfg, "ambuja", _s(r[c["material description"]]), inv_no[:4],
                                  g("quantity"), g("gross value"), g("net value"), cgst, sgst, igst))
    return [_finish(i, cfg, "ambuja") for i in out.values()]


def parse_shree(h, rows, cfg):
    c = {
        "date": _col(h, "inv date"),
        "inv": _col(h, "inv no."),
        "doc": _col(h, "doc no."),
        "desc": _col(h, "product description"),
        "qty": _col(h, "qty", startswith=True),
        "amt": _col(h, "amount", startswith=True),
        "src": _col(h, "source"),
        "veh": _col(h, "vehicle no"),
        "loc": _col(h, "consignee location"),
        "code": _col(h, "consignee code"),
    }
    out = {}
    for r in rows:
        if c["inv"] >= len(r):
            continue
        inv_no = _s(r[c["inv"]])
        if not inv_no or inv_no.lower().startswith("total"):
            continue
        src = _s(r[c["src"]])
        inv = out.setdefault(inv_no, {
            "invoice_no": inv_no,
            "date": _date(r[c["date"]]).isoformat(),
            "truck": _s(r[c["veh"]]),
            "ref": _s(r[c["doc"]]),
            "delivery_doc": "",
            "dealer_code": _s(r[c["code"]]) if c["code"] is not None else "",
            "source": src,
            "consignee_location": _s(r[c["loc"]]) if c["loc"] is not None else "",
            "lines": [],
        })
        inv["lines"].append(_line(cfg, "shree", _s(r[c["desc"]]), src, _num(r[c["qty"]]), _num(r[c["amt"]])))
    return [_finish(i, cfg, "shree") for i in out.values()]


def source_label(src):
    """'8002-DL RAJNAGAR PALAM C RD' -> 'DL RAJNAGAR PALAM C RD'"""
    return re.sub(r"^\d+\s*-\s*", "", src or "").strip()
