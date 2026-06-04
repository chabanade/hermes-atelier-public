#!/usr/bin/env bash
# =============================================================================
# ouvrier-notify-daemon.sh — Solution B (alternative, style maison).
# =============================================================================
# Même résultat que la Solution A, mais avec un démon bash pur calqué sur
# webrtc-reply.sh (le pont "0 token au repos" déjà adopté ici), pour ceux qui
# préfèrent un script lisible à une unité .path systemd.
#
# `inotifywait -m` bloque dans le noyau tant que rien ne bouge => ZÉRO CPU,
# ZÉRO token au repos. À chaque apparition de .done, il appelle notify-hermes.sh
# (le MÊME relais que la Solution A). Pré-requis : paquet `inotify-tools`.
#
# Lancement : sous systemd via ouvrier-notify.service (recommandé), ou à la main
#   ./ouvrier-notify-daemon.sh
# =============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DONE_FILE="${OUVRIER_DONE_FILE:-/home/ouvrier/travaux/.done}"
WATCH_DIR="$(dirname "$DONE_FILE")"
WATCH_BASE="$(basename "$DONE_FILE")"
RELAY="$HERE/notify-hermes.sh"
LOG_FILE="${NOTIFY_LOG_FILE:-/home/ouvrier/journal/ouvrier-notify.log}"
LOCK_FILE="${NOTIFY_LOCK_FILE:-/tmp/ouvrier-notify.lock}"

log() { printf '%s [notify-daemon] %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE" 2>&1; }

# Une seule instance.
exec 9>"$LOCK_FILE" || { log "verrou inaccessible"; exit 1; }
flock -n 9 || { log "déjà en cours — sortie."; exit 0; }

command -v inotifywait >/dev/null 2>&1 || { log "inotifywait absent (apt install inotify-tools)"; exit 1; }

trap 'log "arrêt."; exit 0' INT TERM
log "démarrage — surveille $DONE_FILE"

# Rattrapage : si un .done attendait déjà avant le lancement.
[[ -e "$DONE_FILE" ]] && "$RELAY"

# -m : monitor continu ; -e create,moved_to,close_write : couvre le mv atomique
# d'ouvrier-done.sh comme une écriture directe.
inotifywait -m -e create -e moved_to -e close_write --format '%f' "$WATCH_DIR" 2>>"$LOG_FILE" \
  | while read -r fname; do
      [[ "$fname" == "$WATCH_BASE" ]] || continue
      "$RELAY"
    done
