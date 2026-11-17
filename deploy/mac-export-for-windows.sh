#!/usr/bin/env bash
# Tayal ERP — pack everything the Windows PC needs, on the Mac:
#   ~/Desktop/TayalERP-for-windows.zip  =  the code + your data (database copy) + your settings (.env with secrets)
# Copy it to the Windows PC (USB stick or Google Drive), unzip it to C:\ so you get C:\TayalERP, and follow
# deploy/windows/README.md. Delete the zip from the USB / Drive afterwards: it contains your passwords and data.
#
#   ./deploy/mac-export-for-windows.sh          (run from the tayal_erp folder)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB=tayal_erp
OUT="$HOME/Desktop/TayalERP-for-windows.zip"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export PATH="$(brew --prefix postgresql@16 2>/dev/null)/bin:$PATH"
command -v pg_dump >/dev/null || { echo "pg_dump not found (brew install postgresql@16)"; exit 1; }

echo "==> Copying the database"
mkdir -p "$WORK/TayalERP/data"
pg_dump -h localhost -Fc -f "$WORK/TayalERP/data/tayal_erp.dump" "$DB"
echo "    $(du -h "$WORK/TayalERP/data/tayal_erp.dump" | cut -f1)"

echo "==> Copying the code"
rsync -a "$ROOT/" "$WORK/TayalERP/" \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude 'web/.next' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.ruff_cache' --exclude 'logs' --exclude 'mobile' --exclude '*.pyc' \
  --exclude 'backend/.env' --exclude '.DS_Store'
[ -f "$ROOT/.env" ] && echo "    settings (.env) included" || echo "    WARNING: no .env found — WhatsApp / AI settings will have to be added on the PC"

echo "==> Making the zip"
rm -f "$OUT"
(cd "$WORK" && zip -qr "$OUT" TayalERP)
echo ""
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "It holds your data and passwords — move it to the PC and delete other copies."
echo "When the PC is running the ERP, stop the ERP on this Mac so the two don't both sync with Tally."
