#!/usr/bin/env bash
# Corrige 2 variables d'environnement du service webrtc-reply puis recharge/redémarre.
#   Bug 1 : WEBRTC_DATA_DIR -> /opt/data/webrtc-vocal/data  (inbox = $DATA_DIR/inbox)
#   Bug 2 : HERMES_BIN      -> /opt/hermes/hermes           (vrai binaire)
# Exécuté EN ROOT par le sas. Idempotent.
set -uo pipefail

UNIT=/etc/systemd/system/webrtc-reply.service

echo "===== AVANT ====="
grep -nE 'WEBRTC_DATA_DIR|HERMES_BIN' "$UNIT" || true

# Sauvegarde horodatée (une fois suffit, mais sans écraser une sauvegarde existante du jour)
cp -n "$UNIT" "$UNIT.bak" 2>/dev/null || true

# Bug 1 — racine des données (le démon fait INBOX_DIR="$DATA_DIR/inbox")
sed -i -E 's|^Environment=WEBRTC_DATA_DIR=.*$|Environment=WEBRTC_DATA_DIR=/opt/data/webrtc-vocal/data|' "$UNIT"
# Bug 2 — binaire Hermès
sed -i -E 's|^Environment=HERMES_BIN=.*$|Environment=HERMES_BIN=/opt/hermes/hermes|' "$UNIT"

echo "===== APRÈS ====="
grep -nE 'WEBRTC_DATA_DIR|HERMES_BIN' "$UNIT" || true

echo "===== Vérif des chemins corrigés (info) ====="
for p in /opt/data/webrtc-vocal/data/inbox /opt/hermes/hermes; do
  if [ -e "$p" ]; then echo "OK   présent : $p"; else echo "WARN absent  : $p"; fi
done

echo "===== daemon-reload / restart ====="
systemctl daemon-reload
systemctl restart webrtc-reply
sleep 1

echo "===== status ====="
systemctl status webrtc-reply --no-pager | head -12

echo "===== journal ====="
journalctl -u webrtc-reply -n 5 --no-pager
