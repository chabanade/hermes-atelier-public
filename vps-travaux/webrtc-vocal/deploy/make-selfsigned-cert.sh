#!/usr/bin/env bash
# Génère un certificat AUTO-SIGNÉ pour servir le HTTPS sans Caddy/domaine.
#
#   Repli / test uniquement. Les navigateurs afficheront « connexion non privée »
#   et il faut approuver le certificat (ou l'importer) sur CHAQUE appareil. Pour un
#   usage normal, préférez Caddy + Let's Encrypt (cert valide) : install-caddy.sh.
#
# Usage :  bash deploy/make-selfsigned-cert.sh [dossier_sortie]
set -euo pipefail
cd "$(dirname "$0")/.."          # racine du projet webrtc-vocal

OUT="${1:-deploy/certs}"
CN="${CN:-votre-domaine.example}"   # FQDN par défaut du VPS
IP="${IP:-VOTRE_IP_VPS}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"            # chemin absolu (échos corrects, abs ou relatif)

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$OUT/key.pem" -out "$OUT/cert.pem" \
  -days 825 -subj "/CN=$CN" \
  -addext "subjectAltName=DNS:${CN},DNS:localhost,IP:${IP},IP:127.0.0.1"
chmod 600 "$OUT/key.pem"

echo "Certificat auto-signé créé :"
echo "  cert : $OUT/cert.pem"
echo "  clé  : $OUT/key.pem"
echo
echo "Lancer le serveur en HTTP (8686) + HTTPS (8443) :"
echo "  WEBRTC_TLS_CERT=$OUT/cert.pem \\"
echo "  WEBRTC_TLS_KEY=$OUT/key.pem \\"
echo "  .venv/bin/python server.py"
echo
echo "Tester :  curl -k https://localhost:8443/health"
