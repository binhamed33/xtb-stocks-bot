#!/usr/bin/env bash
# Service entry point: interactive Discord bot when a bot token is configured,
# otherwise the webhook-only scheduler. Keeps the old deployment working unchanged.
set -euo pipefail
cd /opt/xtb-stocks-bot
set -a; . ./.env; set +a
if [ -n "${DISCORD_BOT_TOKEN:-}" ]; then
  exec .venv/bin/python -m stockbot discord
else
  exec .venv/bin/python -m stockbot run
fi
