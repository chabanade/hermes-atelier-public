#!/usr/bin/env bash
# Installe coturn (relais TURN) en Option A pour webrtc-vocal.
# À LANCER EN ROOT SUR LE VPS (hors sandbox) :  sudo bash deploy/install-coturn.sh
#
# Effet : coturn écoute en TURN-over-TLS sur 5349/TCP UNIQUEMENT (turns:), avec le
# certificat Let's Encrypt de Caddy (resynchronisé tout seul), authentification V1
# statique (un compte webrtc:<secret>). Le web (Caddy 443) et le serveur Python
# (8686) restent INCHANGÉS. L'appli apprend le relais via deploy/turn.env, que ce
# script écrit pour restart.sh — donc « zéro code applicatif touché ».
#
# PRÉREQUIS : Caddy déjà en place et le certificat émis (install-caddy.sh, puis
#   curl -fsS https://<domaine>/health  doit répondre). coturn refuse de démarrer
#   sans certificat valide pour le domaine.
#
# Surcharges (réutiliser sur un autre VPS) :  TURN_DOMAIN=… EXTERNAL_IP=… sudo -E …
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Ce script doit être lancé en root (sudo)." >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"     # …/webrtc-vocal/deploy
REPO="$(cd "$HERE/.." && pwd)"            # …/webrtc-vocal
TURN_DOMAIN="${TURN_DOMAIN:-votre-domaine.example}"
EXTERNAL_IP="${EXTERNAL_IP:-VOTRE_IP_VPS}"
RELAY_RANGE="49160:49200"                 # doit correspondre à min-port/max-port de turnserver.conf

# 1. Paquet coturn.
if ! command -v turnserver >/dev/null 2>&1; then
  apt-get update
  apt-get install -y coturn
fi

# 2. Activer le démon (la gate Debian/Ubuntu dans /etc/default/coturn).
if [[ -f /etc/default/coturn ]]; then
  sed -i 's/^#*[[:space:]]*TURNSERVER_ENABLED=.*/TURNSERVER_ENABLED=1/' /etc/default/coturn
  grep -q '^TURNSERVER_ENABLED=1' /etc/default/coturn || echo 'TURNSERVER_ENABLED=1' >> /etc/default/coturn
else
  echo 'TURNSERVER_ENABLED=1' > /etc/default/coturn
fi

# 3. Secret TURN : RÉUTILISER celui déjà installé (idempotence), sinon en générer un.
TURN_PASS=""
if [[ -f /etc/turnserver.conf ]]; then
  existing="$(sed -n 's/^user=webrtc:\(.*\)$/\1/p' /etc/turnserver.conf | head -n1 || true)"
  if [[ -n "$existing" && "$existing" != "CHANGE_ME_TURN_PASSWORD" ]]; then
    TURN_PASS="$existing"
    echo "Secret TURN existant réutilisé."
  fi
fi
if [[ -z "$TURN_PASS" ]]; then
  TURN_PASS="$(openssl rand -hex 24)"
  echo "Nouveau secret TURN généré."
fi

# 4. Installer la configuration, en injectant secret/domaine/IP.
install -D -m 0640 -o turnserver -g turnserver "$HERE/turnserver.conf" /etc/turnserver.conf
sed -i \
  -e "s|^realm=.*|realm=${TURN_DOMAIN}|" \
  -e "s|^external-ip=.*|external-ip=${EXTERNAL_IP}|" \
  -e "s|^user=webrtc:.*|user=webrtc:${TURN_PASS}|" \
  /etc/turnserver.conf

# 5. Script + units de synchro du certificat.
install -D -m 0755 "$HERE/coturn-cert-sync.sh" /usr/local/sbin/coturn-cert-sync.sh
install -D -m 0644 "$HERE/systemd/coturn-cert-sync.service" /etc/systemd/system/coturn-cert-sync.service
install -D -m 0644 "$HERE/systemd/coturn-cert-sync.path"    /etc/systemd/system/coturn-cert-sync.path
install -D -m 0644 "$HERE/systemd/coturn-cert-sync.timer"   /etc/systemd/system/coturn-cert-sync.timer
# Aligner le domaine surveillé par le .path si on a surchargé TURN_DOMAIN.
sed -i "s|srvVOTRE_ID_VPS\.hstgr\.cloud|${TURN_DOMAIN}|g" /etc/systemd/system/coturn-cert-sync.path
systemctl daemon-reload

# 6. Synchro initiale du certificat (échec = prérequis Caddy non rempli → on stoppe).
if ! TURN_DOMAIN="$TURN_DOMAIN" /usr/local/sbin/coturn-cert-sync.sh; then
  echo >&2
  echo "✗ Impossible de récupérer le certificat de Caddy pour « $TURN_DOMAIN »." >&2
  echo "  Lancez d'abord install-caddy.sh et vérifiez : curl -fsS https://$TURN_DOMAIN/health" >&2
  echo "  Puis relancez ce script." >&2
  exit 1
fi

# 7. Pare-feu : 5349/TCP (TURN/TLS) + plage UDP de relais média.
if command -v ufw >/dev/null 2>&1; then
  ufw allow 5349/tcp || true
  ufw allow "${RELAY_RANGE}/udp" || true        # ufw veut la plage avec « : » → 49160:49200/udp
fi
echo "⚠ Pare-feu Hostinger (hPanel > VPS > Firewall) : ouvrir aussi 5349/TCP et ${RELAY_RANGE/:/-}/UDP."

# 8. Démarrer coturn + armer la resynchro automatique du certificat.
systemctl enable coturn >/dev/null 2>&1 || true
systemctl restart coturn
systemctl enable --now coturn-cert-sync.path  >/dev/null 2>&1 || true
systemctl enable --now coturn-cert-sync.timer >/dev/null 2>&1 || true
systemctl --no-pager --full status coturn | head -n 8 || true

# 9. Écrire deploy/turn.env pour restart.sh (lu automatiquement) — contient le
#    secret : mode 0640, propriété rendue au propriétaire du dépôt (pas root).
TURN_ENV="$HERE/turn.env"
cat > "$TURN_ENV" <<EOF
# Généré par deploy/install-coturn.sh — relais TURN (Option A). NE PAS COMMITTER.
# Lu automatiquement par restart.sh (source). Le serveur l'expose au navigateur via /ice.
WEBRTC_TURN_URL=turns:${TURN_DOMAIN}:5349?transport=tcp
WEBRTC_TURN_USER=webrtc
WEBRTC_TURN_PASS=${TURN_PASS}
EOF
chmod 0640 "$TURN_ENV"
chown "$(stat -c '%U:%G' "$REPO")" "$TURN_ENV" 2>/dev/null || true

echo
echo "OK. coturn écoute en turns:${TURN_DOMAIN}:5349 (TCP/TLS), compte « webrtc »."
echo "Secret écrit dans $TURN_ENV (et /etc/turnserver.conf)."
echo
echo "Activer côté appli :  cd $REPO && ./restart.sh    # charge turn.env, annonce le TURN via /ice"
echo "Vérifier            :  curl -fsS http://127.0.0.1:8686/ice   # doit lister le turns:…:5349"
echo "Test TURN seul      :  voir deploy/TURN.md §« Tester » (turnutils_uclient / page trickle-ICE)."
