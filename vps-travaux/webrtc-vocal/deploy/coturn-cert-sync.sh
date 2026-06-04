#!/usr/bin/env bash
# Synchronise le certificat TLS de coturn avec celui de Caddy (Let's Encrypt).
# À LANCER EN ROOT SUR LE VPS.  Usage :  sudo bash deploy/coturn-cert-sync.sh
#
# POURQUOI : coturn (turns:…:5349) doit présenter un certificat VALIDE pour le
# domaine — exactement celui que Caddy obtient et RENOUVELLE déjà tout seul. Mais
# coturn tourne en utilisateur « turnserver » et n'a pas le droit de lire le store
# privé de Caddy (/var/lib/caddy, clés en 0600). Ce script recopie le couple
# cert+clé dans /etc/coturn/certs/ (lisible par turnserver) et REDÉMARRE coturn
# UNIQUEMENT si le certificat a changé. Il est :
#   • lancé une fois par install-coturn.sh (synchro initiale) ;
#   • rejoué automatiquement à chaque renouvellement par l'unit
#     coturn-cert-sync.path (qui surveille le .crt de Caddy) + un timer quotidien.
#
# Surcharges d'env (toutes optionnelles) :
#   TURN_DOMAIN   domaine du certificat            (défaut votre-domaine.example)
#   CADDY_DATA    racine des données Caddy         (défaut /var/lib/caddy/.local/share/caddy)
#   COTURN_CERT_SRC / COTURN_KEY_SRC   forcer la source (p.ex. cert auto-signé)
#                 — court-circuite la découverte automatique sous Caddy.
#   DEST_DIR      destination lue par turnserver.conf (défaut /etc/coturn/certs)
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Ce script doit être lancé en root (sudo)." >&2
  exit 1
fi

TURN_DOMAIN="${TURN_DOMAIN:-votre-domaine.example}"
CADDY_DATA="${CADDY_DATA:-/var/lib/caddy/.local/share/caddy}"
DEST_DIR="${DEST_DIR:-/etc/coturn/certs}"
# coturn tourne sous cet utilisateur (paquet Debian/Ubuntu) — la copie lui appartient.
TURN_USER="${TURN_USER:-turnserver}"

# 1. Localiser la source (cert + clé).
src_cert="${COTURN_CERT_SRC:-}"
src_key="${COTURN_KEY_SRC:-}"
if [[ -z "$src_cert" || -z "$src_key" ]]; then
  # Découverte sous le store ACME de Caddy : certificates/<issuer>/<domaine>/<domaine>.{crt,key}
  # (l'émetteur peut être Let's Encrypt OU ZeroSSL selon ce que Caddy a choisi).
  base="$CADDY_DATA/certificates"
  src_cert="$(find "$base" -type f -name "${TURN_DOMAIN}.crt" 2>/dev/null | head -n1 || true)"
  src_key="$(find "$base"  -type f -name "${TURN_DOMAIN}.key" 2>/dev/null | head -n1 || true)"
fi

if [[ -z "$src_cert" || -z "$src_key" || ! -r "$src_cert" || ! -r "$src_key" ]]; then
  echo "✗ Certificat introuvable pour « $TURN_DOMAIN »." >&2
  echo "  Cherché sous : $CADDY_DATA/certificates" >&2
  echo "  Caddy a-t-il bien émis le certificat ? (curl -fsS https://$TURN_DOMAIN/health)" >&2
  echo "  Sinon, forcer une source : COTURN_CERT_SRC=… COTURN_KEY_SRC=… $0" >&2
  exit 1
fi

# 2. Copier seulement si ça a changé (évite un restart inutile à chaque tick du timer).
#    Le dossier appartient à turnserver (sinon il ne peut pas le traverser/lire).
mkdir -p "$DEST_DIR"
chown "$TURN_USER":"$TURN_USER" "$DEST_DIR"
chmod 0750 "$DEST_DIR"
changed=0
if ! cmp -s "$src_cert" "$DEST_DIR/cert.pem" || ! cmp -s "$src_key" "$DEST_DIR/key.pem"; then
  install -m 0644 -o "$TURN_USER" -g "$TURN_USER" "$src_cert" "$DEST_DIR/cert.pem"
  install -m 0600 -o "$TURN_USER" -g "$TURN_USER" "$src_key"  "$DEST_DIR/key.pem"
  changed=1
fi

# 3. Recharger coturn si le certificat a bougé (et seulement si le service existe).
if [[ "$changed" -eq 1 ]]; then
  echo "Certificat mis à jour ($src_cert → $DEST_DIR/cert.pem)."
  if systemctl list-unit-files coturn.service >/dev/null 2>&1; then
    # Restart franc : coturn charge le certificat au démarrage ; un renouvellement
    # n'arrive que quelques fois par an, une micro-coupure TURN est acceptable.
    systemctl restart coturn && echo "coturn redémarré."
  fi
else
  echo "Certificat déjà à jour — rien à faire."
fi
