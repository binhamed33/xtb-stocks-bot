#!/usr/bin/env bash
# Service entry point: interactive Discord bot when a bot token is configured,
# otherwise the webhook-only scheduler. The .env file is parsed by the Python
# app itself (python-dotenv); it is never sourced as shell, so values may
# contain spaces or special characters.
set -euo pipefail
cd /opt/xtb-stocks-bot
if grep -qE '^[[:space:]]*DISCORD_BOT_TOKEN[[:space:]]*=[[:space:]]*["'"'"']?[^"'"'"'[:space:]#]' .env 2>/dev/null; then
  exec .venv/bin/python -m stockbot discord
else
  exec .venv/bin/python -m stockbot run
fi
