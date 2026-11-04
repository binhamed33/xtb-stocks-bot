#!/usr/bin/env bash
# Publish the dashboard on a subdomain with HTTPS. Run as root AFTER install.sh and after the
# DNS A record for the domain points at this server:
#   sudo DOMAIN=tdawl.riyami.om EMAIL=you@example.com bash /opt/xtb-stocks-bot/deploy/install-web.sh
set -euo pipefail
DOMAIN="${DOMAIN:-tdawl.riyami.om}"
EMAIL="${EMAIL:-}"
PORT="${PORT:-8787}"
DEST=/opt/xtb-stocks-bot

if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q nginx certbot python3-certbot-nginx >/dev/null
fi

# web service (same code, same .env, same database as the bot)
cp "$DEST/deploy/xtb-stocks-web.service" /etc/systemd/system/xtb-stocks-web.service
grep -q '^WEB_PUBLIC_URL=' "$DEST/.env" || echo "WEB_PUBLIC_URL=https://$DOMAIN" >> "$DEST/.env"
grep -q '^WEB_PORT=' "$DEST/.env" || echo "WEB_PORT=$PORT" >> "$DEST/.env"
systemctl daemon-reload
systemctl enable xtb-stocks-web >/dev/null
systemctl restart xtb-stocks-web

# nginx site
SITE=/etc/nginx/sites-available/$DOMAIN
if [ -d /etc/nginx/sites-available ]; then
  sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__PORT__/$PORT/g" "$DEST/deploy/nginx-tdawl.conf" > "$SITE"
  ln -sf "$SITE" /etc/nginx/sites-enabled/$DOMAIN
else
  sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__PORT__/$PORT/g" "$DEST/deploy/nginx-tdawl.conf" > /etc/nginx/conf.d/$DOMAIN.conf
fi
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx

# firewall (Ubuntu ufw if present; Oracle Cloud also needs ports 80/443 opened in the VCN security list)
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; fi
if command -v iptables >/dev/null; then
  iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
  iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
  command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
fi

# HTTPS
if [ -n "$EMAIL" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect || echo "!! certbot failed: is the DNS A record for $DOMAIN pointing here yet? Re-run this script once it is."
else
  echo ">>> HTTPS skipped: run again with EMAIL=you@example.com to get a free Let's Encrypt certificate."
fi

echo "Dashboard: https://$DOMAIN  (service: systemctl status xtb-stocks-web; logs: journalctl -u xtb-stocks-web -f)"
