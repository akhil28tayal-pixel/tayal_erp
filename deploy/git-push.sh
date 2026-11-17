#!/usr/bin/env bash
# Send the Mac's changes to GitHub, so the Windows PC can take them with deploy\windows\update.ps1.
#
#   First time:   ./deploy/git-push.sh https://github.com/<you>/tayal_erp.git
#   Every time:   ./deploy/git-push.sh            (or ./deploy/git-push.sh "" "what changed")
#
# Never sends .env (passwords, WhatsApp token), the database, backups or logs — see .gitignore.
set -euo pipefail
cd "$(dirname "$0")/.."
URL="${1:-}"
MSG="${2:-Update $(date '+%Y-%m-%d %H:%M')}"

if [ ! -d .git ]; then
  [ -n "$URL" ] || { echo "First time: give the GitHub address, e.g. ./deploy/git-push.sh https://github.com/you/tayal_erp.git"; exit 1; }
  git init -q -b main
  git remote add origin "$URL"
  echo "==> New git folder, sending to $URL"
elif [ -n "$URL" ]; then
  git remote set-url origin "$URL"
fi

git add -A
# Files that are now in .gitignore but were sent before: stop tracking them (they stay on this Mac).
git ls-files -ci --exclude-standard | while IFS= read -r f; do git rm -q --cached -- "$f"; done
# Safety: stop if a secret or data file slipped in.
if git diff --cached --name-only | grep -E '(^|/)\.env$|\.dump$|(^|/)backups/' ; then
  echo "STOPPED: the files above must not go to GitHub. Check .gitignore."; git reset -q; exit 1
fi
if git diff --cached --quiet; then
  echo "Nothing changed since the last push."
else
  git commit -q -m "$MSG"
  echo "==> Saved: $MSG"
fi
git push -u origin main
echo "==> Done. On the Windows PC run deploy\\windows\\update.ps1 as administrator."
