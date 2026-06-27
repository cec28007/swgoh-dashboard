#!/usr/bin/env bash
# Pull the latest roster and commit swgoh_data.js only if it changed.
# Used by swgoh-fetch.service. Requires the VM to have push access (a GitHub
# deploy key with write access) and git user.name/email configured.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only origin main || true
python3 fetch_swgoh.py swgoh

if ! git diff --quiet -- swgoh_data.js; then
  git add swgoh_data.js
  git commit -m "Update roster $(date -u +%Y-%m-%d)"
  git push origin main
  echo "Pushed updated roster."
else
  echo "No roster change."
fi
