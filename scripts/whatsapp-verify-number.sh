#!/usr/bin/env bash
# Verifies and registers the WhatsApp business number with Meta when the website gives no "voice call" option.
#   1. asks Meta to CALL the number and read out the 6-digit code (or SMS, if you choose)
#   2. sends the code back to Meta
#   3. registers the number for the Cloud API with a 6-digit two-step PIN you choose
# Needs: the Phone number ID (read from .env) and a temporary access token from
#        developers.facebook.com -> your app -> WhatsApp -> API Setup -> "Generate access token".
# The token is typed in here and never saved.
set -euo pipefail
cd "$(dirname "$0")/.."
VER="v22.0"
PID="$(grep -E '^ERP_WHATSAPP_PHONE_NUMBER_ID=' .env 2>/dev/null | cut -d= -f2- || true)"
[ -n "${PID}" ] || read -rp "Phone number ID: " PID
echo "Phone number ID: ${PID}"
read -rsp "Paste the access token (input hidden), then Enter: " TOKEN; echo
[ -n "${TOKEN}" ] || { echo "No token given."; exit 1; }

call() {  # call <path> <form fields...>  -> prints Meta's reply, fails on error
  local path="$1"; shift
  local out
  out="$(curl -sS -X POST "https://graph.facebook.com/${VER}/${PID}/${path}" -H "Authorization: Bearer ${TOKEN}" "$@")"
  echo "  Meta says: ${out}"
  if echo "${out}" | grep -q '"error"'; then return 1; fi
}

echo
echo "Current status of the number:"
curl -sS "https://graph.facebook.com/${VER}/${PID}?fields=display_phone_number,verified_name,code_verification_status,status,name_status" \
  -H "Authorization: Bearer ${TOKEN}"; echo; echo

read -rp "Send the code by (1) voice call or (2) SMS? [1]: " M
METHOD="VOICE"; [ "${M:-1}" = "2" ] && METHOD="SMS"
echo "Asking Meta to send the code by ${METHOD}..."
if ! call request_code -d "code_method=${METHOD}" -d "language=en_US"; then
  echo
  echo "Meta refused to send a code. Common reasons:"
  echo " - The number is still active in WhatsApp / WhatsApp Business on a phone (delete it there first, or connect it as an existing app number)."
  echo " - Too many attempts: wait a few hours (up to 24) and run this again."
  echo " - The token is not from the app that owns this number, or has expired."
  exit 1
fi

echo
read -rp "Enter the 6-digit code you received: " CODE
call verify_code -d "code=${CODE}" || { echo "Code not accepted. Run the script again for a new code."; exit 1; }
echo "OK: Number verified."

echo
echo "Now registering the number for the API. Choose a 6-digit PIN (two-step verification)."
echo "Write it down - Meta asks for it if the number is ever moved or re-registered."
read -rp "6-digit PIN: " PIN
call register -H "Content-Type: application/json" \
  -d "{\"messaging_product\":\"whatsapp\",\"pin\":\"${PIN}\"}" || {
  echo "Registration failed. If it says the PIN is wrong, a PIN was set earlier - use that one, or reset it in WhatsApp Manager -> Phone numbers -> Two-step verification."
  exit 1; }
echo
echo "OK: Done. The number can now send messages through the WhatsApp API."
echo "Next: put your permanent token in .env as ERP_WHATSAPP_TOKEN=..., restart ./start-mac.sh, then Settings -> Send test message."
