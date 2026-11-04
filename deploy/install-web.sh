#!/usr/bin/env bash
# Publish the dashboard on a subdomain with HTTPS. Run as root AFTER install.sh and after the
# DNS record for the domain points at this server (directly or through Cloudflare):
#   sudo DOMAIN=tdawl.riyami.om EMAIL=you@example.com bash /opt/xtb-stocks-bot/deploy/install-web.sh
#
# Safe for servers that already host other sites: the only nginx file written is
# sites-enabled/$DOMAIN (or conf.d/$DOMAIN.conf) and certbot runs in "certonly" mode,
# so it never edits any other server block.
set -euo pipefail
DOMAIN="${DOMAIN:-tdawl.riyami.om}"
EMAIL="${EMAIL:-}"
PORT="${PORT:-8787}"
DEST=/opt/xtb-stocks-bot

if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq nginx certbot >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q nginx certbot >/dev/null
fi
mkdir -p /var/www/html

# ---- web service (same code, same .env, same database as the bot) ------------------------
cp "$DEST/deploy/xtb-stocks-web.service" /etc/systemd/system/xtb-stocks-web.service
grep -q '^WEB_PUBLIC_URL=' "$DEST/.env" || echo "WEB_PUBLIC_URL=https://$DOMAIN" >> "$DEST/.env"
grep -q '^WEB_PORT=' "$DEST/.env" || echo "WEB_PORT=$PORT" >> "$DEST/.env"
systemctl daemon-reload
systemctl enable xtb-stocks-web >/dev/null
systemctl restart xtb-stocks-web

# ---- nginx site file (ours only) ------------------------------------------------------------
if [ -d /etc/nginx/sites-enabled ]; then
  SITE=/etc/nginx/sites-available/$DOMAIN
  mkdir -p /etc/nginx/sites-available
  LINK=/etc/nginx/sites-enabled/$DOMAIN
else
  SITE=/etc/nginx/conf.d/$DOMAIN.conf
  LINK=""
fi
PROXY="proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 15s;"

write_site() {  # $1 = "http" (proxy over plain http) or "https" (redirect + TLS block)
  if [ "$1" = "https" ]; then ACTION="return 301 https://\$host\$request_uri;"; else ACTION="$PROXY"; fi
  sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__PORT__/$PORT/g" "$DEST/deploy/nginx-tdawl.conf" \
    | awk -v action="$ACTION" '{ if ($0 ~ /__HTTP_ACTION__/) print action; else print }' > "$SITE"
  if [ "$1" = "https" ]; then
    cat >> "$SITE" <<EOF

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:tdawl:5m;

    client_max_body_size 2m;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript image/svg+xml;

    location / {
        $PROXY
    }
}
EOF
  fi
  [ -n "$LINK" ] && ln -sf "$SITE" "$LINK"
  if nginx -t 2>&1 | grep -qE "\[emerg\]"; then
    nginx -t 2>&1 | grep -E "\[emerg\]"
    echo "!! nginx config test failed; nothing reloaded"; exit 1
  fi
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl reload nginx || systemctl start nginx
}

# ---- firewall (Oracle Cloud also needs 80/443 opened in the VCN security list) --------------
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; fi
if command -v iptables >/dev/null; then
  iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
  iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
  command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
fi

# ---- certificate: certonly (webroot) never touches other server blocks ---------------------
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  echo ">>> certificate for $DOMAIN already exists; enabling HTTPS"
  write_site https
elif [ -n "$EMAIL" ]; then
  write_site http
  if certbot certonly --webroot -w /var/www/html -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --keep-until-expiring; then
    write_site https
  else
    echo "!! certbot failed: is $DOMAIN resolving to this server (or proxied by Cloudflare to it)? Site stays on HTTP; re-run once DNS is right."
  fi
else
  write_site http
  echo ">>> HTTPS skipped: run again with EMAIL=you@example.com to get a free Let's Encrypt certificate."
fi

echo "Dashboard: https://$DOMAIN  (service: systemctl status xtb-stocks-web; logs: journalctl -u xtb-stocks-web -f)"
