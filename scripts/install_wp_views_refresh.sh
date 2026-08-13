#!/usr/bin/env bash
# One-time setup of a weekly launchd job (macOS) that refreshes WealthTechToday
# view counts and pushes data/wp_views.json, so the dashboard's numbers stay current
# without anyone lifting a finger.
#
# WHY a Mac (not the cloud): SiteGround's Anti-Bot AI blocks Streamlit Cloud and
# GitHub Actions from the stats endpoint. A normal Mac connection isn't blocked, so
# the refresh has to run from an always-on machine like the Mac mini.
#
# PREREQS on this machine:
#   - this repo is cloned
#   - .streamlit/secrets.toml has WORDPRESS_USER and WORDPRESS_APP_PASSWORD
#   - a Python with `requests` (the repo venv, or `pip3 install requests`)
#   - `git push` works here (PAT stored in the keychain)
#
# RUN ONCE:
#   bash scripts/install_wp_views_refresh.sh
#
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.ezragroup.wp-views"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$REPO/scripts/wp_views.log"

# Prefer the repo venv's Python (has requests); fall back to system python3.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# The command the job runs each week: fetch, then commit + push only if changed.
CMD="cd '$REPO' && '$PY' scripts/fetch_wp_views.py && { [ -z \"\$(git status --porcelain data/wp_views.json)\" ] || { git add data/wp_views.json && git commit -m 'chore: refresh WordPress view counts' && git push; }; }"

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>${CMD}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>1</integer>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict></plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed launchd job '$LABEL' — runs every Monday at 08:00."
echo "Log file: $LOG"
echo ""
echo "Running a test now to confirm fetch + push work on this machine..."
if bash -lc "$CMD"; then
  echo ""
  echo "SUCCESS — view snapshot refreshed and pushed (if it changed). You're set."
  echo "To remove later:  launchctl unload '$PLIST' && rm '$PLIST'"
else
  echo ""
  echo "TEST FAILED — check $LOG. Common causes: requests not installed"
  echo "(pip3 install requests), or git push needs auth here."
  exit 1
fi
