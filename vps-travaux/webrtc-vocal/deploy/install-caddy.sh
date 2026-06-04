#!/usr/bin/env bash
# Installe Caddy (dépôt officiel) et met en place le HTTPS pour webrtc-vocal.
# À LANCER EN ROOT SUR LE VPS (hors sandbox) :  sudo bash deploy/install-caddy.sh
#
# Effet : Caddy écoute 80/443, obtient un certificat Let's Encrypt valide pour le
# domaine du Caddyfile, et reverse-proxy vers le serveur Python (HTTP 8686, qui
# reste inchangé et toujours accessible localement).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Ce script doit être lancé en root (sudo)." >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. Dépôt officiel Caddy (Debian/Ubuntu) + installation.
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

# 2. Pare-feu : Let's Encrypt valide via le 80 ; le service est servi sur le 443.
if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp  || true
  ufw allow 443/tcp || true
fi
echo "⚠ Vérifiez aussi le pare-feu Hostinger (hPanel > VPS > Firewall) : 80 + 443 ouverts."

# 3. Config + (re)chargement.
install -D -m 0644 "$HERE/Caddyfile" /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy
systemctl --no-pager --full status caddy | head -n 8 || true

DOMAIN="$(grep -oE '^[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$HERE/Caddyfile" | head -n1)"
echo
echo "OK. Test (le 1er appel peut prendre ~10 s, le temps d'émettre le certificat) :"
echo "    curl -fsS https://${DOMAIN}/health"
