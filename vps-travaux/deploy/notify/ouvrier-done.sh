#!/usr/bin/env bash
# =============================================================================
# ouvrier-done.sh — à appeler par l'ouvrier comme TOUT DERNIER geste d'une tâche.
# =============================================================================
# Écrit /home/ouvrier/travaux/.done de façon ATOMIQUE (tmp + mv) pour que le
# watch hôte ne lise jamais un fichier à moitié écrit. C'est ce dépôt qui
# déclenche la notification Hermès — l'ouvrier, lui, n'a RIEN d'autre à faire
# (il ne peut de toute façon pas joindre Telegram depuis la cage).
#
# USAGE :
#   ouvrier-done.sh "résumé en une ligne"
#   ouvrier-done.sh -t "titre tâche" -s done -r /chemin/RAPPORT.md "résumé..."
#
# Options :
#   -t TITRE     nom court de la tâche        (défaut : vide)
#   -s STATUS    done | error | need-input    (défaut : done)
#   -r CHEMIN    chemin d'un rapport complet  (défaut : vide)
#   -c CHAT_ID   forcer un destinataire       (défaut : celui de notify.env)
# =============================================================================
set -euo pipefail

DONE_FILE="${OUVRIER_DONE_FILE:-/home/ouvrier/travaux/.done}"
TITLE="" STATUS="done" REPORT="" CHAT=""
while getopts "t:s:r:c:" opt; do
  case "$opt" in
    t) TITLE="$OPTARG" ;;
    s) STATUS="$OPTARG" ;;
    r) REPORT="$OPTARG" ;;
    c) CHAT="$OPTARG" ;;
    *) echo "option inconnue" >&2; exit 2 ;;
  esac
done
shift $((OPTIND-1))
SUMMARY="${*:-}"

TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
TMP="${DONE_FILE}.tmp.$$"

if command -v jq >/dev/null 2>&1; then
  jq -nc \
     --arg task "$TITLE" --arg status "$STATUS" --arg summary "$SUMMARY" \
     --arg report "$REPORT" --arg chat "$CHAT" --arg ts "$TS" \
     '{task:$task, status:$status, summary:$summary, report_path:$report, ts:$ts}
      + (if $chat == "" then {} else {chat_id:$chat} end)' > "$TMP"
else
  # Repli sans jq : JSON minimal échappé sommairement.
  esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  printf '{"task":"%s","status":"%s","summary":"%s","report_path":"%s","ts":"%s"}\n' \
    "$(esc "$TITLE")" "$(esc "$STATUS")" "$(esc "$SUMMARY")" "$(esc "$REPORT")" "$TS" > "$TMP"
fi

mv -f "$TMP" "$DONE_FILE"
echo "[ouvrier-done] signal déposé -> $DONE_FILE (status=$STATUS)"
