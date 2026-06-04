#!/usr/bin/env bash
# =============================================================================
# install-ouvrier-notify.sh — installe la notification fin-de-tâche (root).
# =============================================================================
# À déclarer dans .root-request pour exécution par le sas root.
#
#   SOLUTION=A  (défaut) -> couple systemd .path + .service (inotify natif)
#   SOLUTION=B           -> démon bash inotifywait (+ paquet inotify-tools)
#
# Idempotent : relançable sans dégât. N'active jamais A et B en même temps.
# =============================================================================
set -euo pipefail

SRC="/home/ouvrier/travaux/deploy/notify"
SOLUTION="${SOLUTION:-A}"
echo "== Installation notification ouvrier — Solution $SOLUTION =="

# 0. Pré-requis communs : jq, et notify.env.
command -v jq >/dev/null 2>&1 || { echo "-> apt install jq"; apt-get install -y jq || true; }
if [[ ! -f "$SRC/notify.env" ]]; then
  cp "$SRC/notify.env.example" "$SRC/notify.env"
  chown ouvrier:ouvrier "$SRC/notify.env" 2>/dev/null || true
  echo "-> notify.env créé depuis l'exemple (pense à renseigner NOTIFY_CHAT_ID si besoin)."
fi
chmod +x "$SRC"/*.sh
install -d -o ouvrier -g ouvrier /home/ouvrier/journal/done-archive

# Sécurité : on désactive systématiquement l'autre solution avant d'installer.
disable_unit() { systemctl disable --now "$1" 2>/dev/null || true; rm -f "/etc/systemd/system/$1"; }

if [[ "$SOLUTION" == "A" ]]; then
  disable_unit ouvrier-notify.service
  cp "$SRC/ouvrier-done.path"    /etc/systemd/system/ouvrier-done.path
  cp "$SRC/ouvrier-done.service" /etc/systemd/system/ouvrier-done.service
  systemctl daemon-reload
  systemctl enable --now ouvrier-done.path
  echo "-> Solution A active :"; systemctl --no-pager --plain status ouvrier-done.path | head -5 || true

elif [[ "$SOLUTION" == "B" ]]; then
  disable_unit ouvrier-done.path
  disable_unit ouvrier-done.service
  command -v inotifywait >/dev/null 2>&1 || { echo "-> apt install inotify-tools"; apt-get install -y inotify-tools; }
  cp "$SRC/ouvrier-notify.service" /etc/systemd/system/ouvrier-notify.service
  systemctl daemon-reload
  systemctl enable --now ouvrier-notify.service
  echo "-> Solution B active :"; systemctl --no-pager --plain status ouvrier-notify.service | head -5 || true
else
  echo "SOLUTION inconnue : '$SOLUTION' (attendu A ou B)"; exit 2
fi

echo "== OK. Test : sudo -u ouvrier $SRC/ouvrier-done.sh -t demo 'ça marche' =="
