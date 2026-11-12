#!/usr/bin/env bash
# Home-server trigger: asks GitHub to run the daily alert workflow now.
# GitHub's own cron is delayed by hours, so a systemd timer calls this at 08:00 KST.
# Token: fine-grained PAT for Sin-Yejun/etf-tracker with "Contents: Read and write".
set -euo pipefail
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/etf-tracker/github-token}"
TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
for attempt in 1 2 3 4 5; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    https://api.github.com/repos/Sin-Yejun/etf-tracker/dispatches \
    -d '{"event_type":"daily-alerts"}' || echo 000)
  if [ "$code" = 204 ]; then echo "dispatched (attempt $attempt)"; exit 0; fi
  echo "dispatch failed: HTTP $code (attempt $attempt)" >&2
  case "$code" in 401|403|404|422) exit 1;; esac
  sleep 60
done
exit 1
