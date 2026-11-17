"""Sends approved message templates through Meta's WhatsApp Cloud API.

Business-initiated messages (like a balance reminder) must use a template approved in WhatsApp Manager.
The template's body variables are filled positionally: {{1}}, {{2}}, ...
"""

import httpx

from app.core.config import get_settings


class WhatsAppError(Exception):
    pass


def configured() -> bool:
    s = get_settings()
    return bool(s.whatsapp_token and s.whatsapp_phone_number_id)


def _explain(status: int, payload: dict) -> str:
    err = payload.get("error") or {}
    code = err.get("code")
    details = (err.get("error_data") or {}).get("details") or err.get("message") or f"HTTP {status}"
    hints = {
        190: "The access token is wrong or has expired — create a permanent System User token.",
        100: "Check the Phone number ID and the template's number of variables.",
        132001: "The template name or language does not exist / is not approved yet.",
        132000: "The template expects a different number of variables.",
        131030: "This number is not on the test recipient list (test numbers can only message allowed numbers).",
        131026: "This number is not on WhatsApp or could not receive the message.",
        131047: "More than 24 hours since the dealer replied — only approved templates can be sent.",
        131056: "Too many messages to this number in a short time. Try later.",
        368: "Meta has temporarily restricted this WhatsApp account.",
    }
    hint = hints.get(code)
    return f"WhatsApp refused the message ({code}): {details}" + (f" {hint}" if hint else "")


def send_template(to: str, template: str, language: str, params: list[str]) -> str:
    """Send a template message; returns Meta's message id (wamid…). `to` is 91XXXXXXXXXX."""
    s = get_settings()
    if not configured():
        raise WhatsAppError("WhatsApp API keys are not set. Add ERP_WHATSAPP_TOKEN and ERP_WHATSAPP_PHONE_NUMBER_ID "
                            "to the .env file and restart.")
    url = f"https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}],
        },
    }
    try:
        r = httpx.post(url, json=body, headers={"Authorization": f"Bearer {s.whatsapp_token}"}, timeout=20)
    except httpx.HTTPError as e:
        raise WhatsAppError(f"Could not reach WhatsApp (graph.facebook.com): {e}") from e
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    if r.status_code >= 400 or not payload.get("messages"):
        raise WhatsAppError(_explain(r.status_code, payload))
    return payload["messages"][0]["id"]
