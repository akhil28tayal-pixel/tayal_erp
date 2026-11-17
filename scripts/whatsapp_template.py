"""Lists your WhatsApp message templates and creates the balance reminder template if it is missing.

Run from the tayal_erp folder:   backend/.venv/bin/python scripts/whatsapp_template.py
Add a plainer backup template (reviewed in parallel; use whichever is approved first):
                                 backend/.venv/bin/python scripts/whatsapp_template.py --backup
Reads ERP_WHATSAPP_TOKEN and ERP_WHATSAPP_PHONE_NUMBER_ID from .env. Prints no secrets.
"""

import sys
from pathlib import Path

import httpx

VER = "v22.0"
NAME = "tayal_balance_due"  # must match ERP Settings -> Meta template name
LANG = "en"
BODY = ("*Payment Reminder*\n"
        "Dear {{1}}, your outstanding balance with Tayal Traders is ₹{{2}} as on {{3}}.\n\n"
        "Please arrange payment at the earliest. If already paid, kindly ignore this message.")
# Backup: same three values, plain text (no bold line), different name.
BACKUP_NAME = "tayal_dues_notice"
BACKUP_BODY = ("Dear {{1}}, this is a reminder that your outstanding balance with Tayal Traders is Rs. {{2}} "
               "as on {{3}}. Please arrange payment at the earliest. If already paid, kindly ignore this message.")
EXAMPLE = ["Sharma Building Material", "1,85,000", "10 Nov 2026"]  # {{1}} dealer, {{2}} balance, {{3}} date


def env() -> dict:
    root = Path(__file__).resolve().parent.parent
    vals = {}
    for line in (root / ".env").read_text().splitlines():
        k, _, v = line.partition("=")
        vals[k.strip()] = v.strip().strip("\"'")
    return vals


def get(url: str, token: str, **params) -> dict:
    r = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    data = r.json()
    if "error" in data:
        sys.exit(f"Meta error: {data['error'].get('message')}")
    return data


def find_waba(token: str, phone_id: str, given: str | None) -> str:
    """The WhatsApp Business Account (WABA) that owns the phone number.

    Uses ERP_WHATSAPP_WABA_ID from .env if set; otherwise looks in the token's permissions; otherwise asks."""
    if given:
        return given
    try:
        dbg = get(f"https://graph.facebook.com/{VER}/debug_token", token, input_token=token).get("data", {})
    except SystemExit:
        dbg = {}
    ids = []
    for scope in dbg.get("granular_scopes", []):
        print(f"  token permission: {scope.get('scope')}  accounts: {', '.join(scope.get('target_ids', [])) or 'all / not listed'}")
        if scope.get("scope") in ("whatsapp_business_management", "whatsapp_business_messaging"):
            ids += scope.get("target_ids", [])
    # Also look through the businesses this token can see (their own and client WhatsApp accounts).
    auth = {"Authorization": f"Bearer {token}"}
    try:
        biz = httpx.get(f"https://graph.facebook.com/{VER}/me/businesses", params={"fields": "id,name"},
                        headers=auth, timeout=30).json().get("data", [])
    except Exception:
        biz = []
    for b in biz:
        for edge in ("owned_whatsapp_business_accounts", "client_whatsapp_business_accounts"):
            try:
                accts = httpx.get(f"https://graph.facebook.com/{VER}/{b['id']}/{edge}", params={"fields": "id,name"},
                                  headers=auth, timeout=30).json().get("data", [])
            except Exception:
                accts = []
            ids += [a["id"] for a in accts]
    candidates = [i for i in dict.fromkeys(ids) if i != phone_id]
    for waba in candidates:
        r = httpx.get(f"https://graph.facebook.com/{VER}/{waba}/phone_numbers", params={"fields": "id"},
                      headers=auth, timeout=30).json()
        if any(p.get("id") == phone_id for p in r.get("data", [])):
            return waba
    if len(candidates) == 1:  # only one account visible: it must be this one
        print(f"Using the only WhatsApp account this token can see: {candidates[0]}")
        return candidates[0]
    if candidates:
        print("Accounts this token can see: " + ", ".join(candidates))
    print()
    print("Could not find the WhatsApp Business Account ID automatically.")
    print("Easiest: open WhatsApp Manager (business.facebook.com -> All tools -> WhatsApp Manager) and look at the")
    print("browser address bar: copy the number after 'waba_id=' (NOT business_id, NOT the phone number ID).")
    waba = input("WhatsApp Business Account ID: ").strip()
    if not waba.isdigit():
        sys.exit("That does not look like an account ID (digits only).")
    if waba == phone_id:
        sys.exit("That is the phone number ID. The WhatsApp Business Account ID is a different number.")
    return waba


def create(waba: str, token: str, name: str, body: str) -> None:
    print(f"Creating template '{name}' ({LANG}, Utility) ...")
    r = httpx.post(f"https://graph.facebook.com/{VER}/{waba}/message_templates",
                   headers={"Authorization": f"Bearer {token}"}, timeout=30,
                   json={"name": name, "language": LANG, "category": "UTILITY",
                         "components": [{"type": "BODY", "text": body, "example": {"body_text": [EXAMPLE]}}]})
    data = r.json()
    if "error" in data:
        err = data["error"]
        sys.exit(f"Meta refused: {err.get('error_user_msg') or err.get('message')}")
    print(f"Submitted '{name}'. Status: {data.get('status')}  Category: {data.get('category')}")
    print("Run this script (without --backup) every so often; it tells you which template to use once one is APPROVED.")


def main() -> None:
    e = env()
    token, phone_id = e.get("ERP_WHATSAPP_TOKEN"), e.get("ERP_WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        sys.exit("ERP_WHATSAPP_TOKEN and ERP_WHATSAPP_PHONE_NUMBER_ID must be in .env")
    waba = find_waba(token, phone_id, e.get("ERP_WHATSAPP_WABA_ID"))
    print(f"WhatsApp Business Account: {waba}\n")
    if not e.get("ERP_WHATSAPP_WABA_ID"):
        print("Tip: add this line to .env so you are not asked again:  ERP_WHATSAPP_WABA_ID=" + waba + "\n")

    tpls = get(f"https://graph.facebook.com/{VER}/{waba}/message_templates", token,
               fields="name,language,status,category,rejected_reason,components", limit=200).get("data", [])
    print("Templates on your account:" if tpls else "No templates on your account yet.")
    for t in sorted(tpls, key=lambda t: t["name"]):
        extra = f"  rejected: {t['rejected_reason']}" if t.get("rejected_reason") not in (None, "NONE") else ""
        print(f"  {t['name']:<30} {t['language']:<7} {t['status']:<10} {t.get('category', '')}{extra}")
    print()

    approved = [t for t in tpls if t["status"] == "APPROVED" and t["name"] in (NAME, BACKUP_NAME)
                or t["status"] == "APPROVED" and t["name"].startswith(NAME + "_v")]
    if approved:
        t = approved[0]
        print(f"READY: '{t['name']}' ({t['language']}) is APPROVED.")
        print(f">>> In the ERP: Settings -> 'Meta template name' = {t['name']}  and  'Meta template language code' = {t['language']}")
        print(">>> Then Settings -> Send test message, and switch to Automatic (Meta API).")
        return
    if "--backup" in sys.argv:
        if any(t["name"] == BACKUP_NAME for t in tpls):
            print(f"Backup '{BACKUP_NAME}' already submitted - status shown above. Run again later.")
        else:
            create(waba, token, BACKUP_NAME, BACKUP_BODY)
        return

    same = [t for t in tpls if t["name"] == NAME]
    exact = [t for t in same if t["language"] == LANG]
    if exact:
        text = next((c.get("text", "") for c in exact[0].get("components", []) if c.get("type") == "BODY"), "")
        if text.strip() != BODY.strip():
            print(f"'{NAME}' ({LANG}) already exists with DIFFERENT wording:\n---\n{text}\n---")
            print("To use the new wording: delete it in WhatsApp Manager -> Message templates, then run this again.")
            print("(Meta may block re-using the same name for a while after deleting; if so, set NAME in this")
            print(" script and 'Meta template name' in ERP Settings to e.g. payment_reminder.)")
            return
        st = exact[0]["status"]
        if st == "APPROVED":
            print(f"'{NAME}' ({LANG}) is APPROVED. Send the test message again from Settings.")
        elif st in ("PENDING", "IN_APPEAL"):
            print(f"'{NAME}' ({LANG}) is {st} — Meta is still reviewing it (usually minutes, up to 24 h). Try later.")
        else:
            print(f"'{NAME}' ({LANG}) is {st}. Delete it in WhatsApp Manager → Message templates and run this again.")
        return
    if same:
        langs = ", ".join(t["language"] for t in same)
        print(f"'{NAME}' exists in language: {langs} (not '{LANG}').")
        print(f"Easiest fix: in the ERP go to Settings → 'Meta template language code' and set it to: {same[0]['language']}")
        return

    # Meta blocks a name for 4 weeks after it is deleted, so if a name is refused, try the next free one.
    taken = {t["name"] for t in tpls}
    names = [NAME] + [f"{NAME}_v{i}" for i in range(2, 10)]
    for name in names:
        if name in taken:
            continue
        print(f"Creating template '{name}' ({LANG}, Utility) ...")
        r = httpx.post(f"https://graph.facebook.com/{VER}/{waba}/message_templates",
                       headers={"Authorization": f"Bearer {token}"}, timeout=30,
                       json={"name": name, "language": LANG, "category": "UTILITY",
                             "components": [{"type": "BODY", "text": BODY, "example": {"body_text": [EXAMPLE]}}]})
        data = r.json()
        if "error" in data:
            err = data["error"]
            msg = err.get("error_user_msg") or err.get("message") or ""
            if any(w in msg.lower() for w in ("being deleted", "already exists", "4 weeks", "new message template", "name")):
                print(f"  Meta refused '{name}': {msg}\n  Trying another name ...")
                continue
            sys.exit(f"Meta refused: {msg}")
        print(f"Submitted '{name}'. Status: {data.get('status')}  Category: {data.get('category')}")
        if name != NAME:
            print()
            print(f">>> In the ERP: Settings -> 'Meta template name (automatic sending)' -> set it to: {name}  and Save.")
            print(f">>> Also change NAME at the top of this script to \"{name}\" so later runs check the right one.")
        if data.get("status") == "APPROVED":
            print("Approved already - send the test message again from Settings.")
        else:
            print("Meta reviews it (usually a few minutes). Run this script again to check, then send the test message.")
        if data.get("category") and data["category"] != "UTILITY":
            print(f"Note: Meta classed it as {data['category']}, which is billed at a higher rate.")
        return
    sys.exit("Meta refused every name tried. Send these lines to support.")

if __name__ == "__main__":
    main()
