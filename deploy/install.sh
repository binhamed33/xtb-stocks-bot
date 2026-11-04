#!/usr/bin/env bash
# Install or update on an Ubuntu / Oracle Linux VM. Run as root:
#   curl -fsSL https://raw.githubusercontent.com/binhamed33/xtb-stocks-bot/main/deploy/install.sh | sudo bash
set -euo pipefail
REPO="https://github.com/binhamed33/xtb-stocks-bot.git"
BRANCH="${BRANCH:-main}"
DEST=/opt/xtb-stocks-bot
SRC=/opt/xtb-stocks-bot-src

if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq git python3 python3-venv python3-pip rsync >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q git python3 python3-pip rsync >/dev/null
fi
id -u stocksbot >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin stocksbot

if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q origin "$BRANCH" && git -C "$SRC" reset -q --hard "origin/$BRANCH"
else
  rm -rf "$SRC"; git clone -q --branch "$BRANCH" --depth 1 "$REPO" "$SRC"
fi

mkdir -p "$DEST/state"
rsync -a --delete "$SRC/stockbot/" "$DEST/stockbot/"
cp "$SRC/requirements.txt" "$DEST/"
cp -r "$SRC/deploy" "$DEST/" && chmod +x "$DEST"/deploy/*.sh
[ -d "$DEST/.venv" ] || python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"
if [ ! -f "$DEST/.env" ]; then
  cp "$SRC/.env.example" "$DEST/.env"
  echo ">>> Edit $DEST/.env: set DISCORD_WEBHOOK_URL, DISCORD_BOT_TOKEN and GEMINI_API_KEY, then: systemctl restart xtb-stocks-bot"
fi
touch "$DEST/bot.log"
chown -R stocksbot:stocksbot "$DEST"
chmod 600 "$DEST/.env"
cp "$DEST/deploy/xtb-stocks-bot.service" /etc/systemd/system/xtb-stocks-bot.service
systemctl daemon-reload
systemctl enable xtb-stocks-bot >/dev/null
systemctl restart xtb-stocks-bot
# web dashboard: only (re)started when it was set up with install-web.sh
if [ -f /etc/systemd/system/xtb-stocks-web.service ]; then
  cp "$DEST/deploy/xtb-stocks-web.service" /etc/systemd/system/xtb-stocks-web.service
  systemctl daemon-reload && systemctl restart xtb-stocks-web
fi
echo "Installed to $DEST  ($(git -C "$SRC" rev-parse --short HEAD))"
echo "Status: systemctl status xtb-stocks-bot --no-pager | head -5"
echo "Logs:   journalctl -u xtb-stocks-bot -f"
