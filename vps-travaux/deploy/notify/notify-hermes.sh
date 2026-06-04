#!/usr/bin/env bash
# =============================================================================
# notify-hermes.sh — relais "fin de travail ouvrier" -> Hermès, SANS LLM.
# =============================================================================
#
# QUI M'APPELLE : je suis déclenché par le watch inotify de l'hôte (au choix) :
#   - le .path systemd  ouvrier-done.path           (Solution A, recommandée)
#   - le démon bash     ouvrier-notify-daemon.sh     (Solution B, style maison)
# dès que l'ouvrier dépose /home/ouvrier/travaux/.done.
#
# CE QUE JE FAIS (pur bash + curl, ZÉRO token) :
#   1. je prends le .done de façon atomique (mv vers un temp unique) ;
#   2. j'en extrais task / status / summary / report_path (jq) ;
#   3. je POSTe le résumé sur l'endpoint Telegram EXISTANT du bot :
#         POST $HERMES_REPLY_URL  { "chat_id": ..., "text": "..." }
#      (= hermes-voc-bot /reply, port 8080, qui parle déjà à Telegram) ;
#   4. si /reply échoue, je tombe en repli sur l'API Telegram directe ;
#   5. j'archive le .done et je journalise.
#
# POURQUOI ÇA COÛTE 0 TOKEN : aucun agent/LLM n'est réveillé. Au repos, c'est
# le noyau (inotify) qui attend — aucun process, aucune dépense. À la fin de
# tâche, l'ouvrier a DÉJÀ été payé pour son travail ; la notification, elle, ne
# traverse que du shell et le process bot déjà en route. On supprime ainsi la
# boucle « cat .live » d'Hermès (chaque cat = une invocation LLM = des tokens).
#
# CONFIG : voir notify.env (copié depuis notify.env.example par l'installeur).
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Config (toutes les valeurs ont un défaut raisonnable) -------------------
ENV_FILE="${NOTIFY_ENV_FILE:-$HERE/notify.env}"
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"

DONE_FILE="${OUVRIER_DONE_FILE:-/home/ouvrier/travaux/.done}"
REPLY_URL="${HERMES_REPLY_URL:-http://127.0.0.1:8080/reply}"
REPLY_AUTH_TOKEN="${REPLY_AUTH_TOKEN:-}"          # en-tête X-Auth-Token si /reply le réclame
CHAT_ID="${NOTIFY_CHAT_ID:-}"                      # destinataire Telegram par défaut
HERMES_ENV_FALLBACK="${HERMES_ENV_FALLBACK:-/home/ouvrier/travaux/hermes-voc-bot/.env}"
TELEGRAM_TOKEN="${TELEGRAM_TOKEN:-}"              # repli direct si /reply HS
HTTP_TIMEOUT="${NOTIFY_HTTP_TIMEOUT:-15}"
ARCHIVE_DIR="${NOTIFY_ARCHIVE_DIR:-/home/ouvrier/journal/done-archive}"
LOG_FILE="${NOTIFY_LOG_FILE:-/home/ouvrier/journal/ouvrier-notify.log}"

log() { printf '%s [notify-hermes] %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE" 2>&1; }

# Faute de chat_id explicite, on réutilise READY_CHAT_ID du bot hermes-voc-bot.
if [[ -z "$CHAT_ID" && -f "$HERMES_ENV_FALLBACK" ]]; then
  CHAT_ID="$(grep -E '^READY_CHAT_ID=' "$HERMES_ENV_FALLBACK" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
fi
if [[ -z "$TELEGRAM_TOKEN" && -f "$HERMES_ENV_FALLBACK" ]]; then
  TELEGRAM_TOKEN="$(grep -E '^TELEGRAM_TOKEN=' "$HERMES_ENV_FALLBACK" | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"
fi

# --- 1. Prise atomique du .done (rien à faire s'il a disparu) ----------------
[[ -e "$DONE_FILE" ]] || { log "réveil mais pas de $DONE_FILE (déjà traité) — rien à faire."; exit 0; }
TMP="$(mktemp "${TMPDIR:-/tmp}/ouvrier-done.XXXXXX.json")" || { log "mktemp KO"; exit 1; }
mv -f "$DONE_FILE" "$TMP" 2>/dev/null || { log "impossible de prendre $DONE_FILE"; exit 0; }

# --- 2. Extraction du contenu (jq si dispo, sinon brut) ----------------------
TASK="" STATUS="done" SUMMARY="" REPORT="" OVERRIDE_CHAT=""
if command -v jq >/dev/null 2>&1 && jq -e . "$TMP" >/dev/null 2>&1; then
  TASK="$(jq -r '.task    // ""' "$TMP")"
  STATUS="$(jq -r '.status  // "done"' "$TMP")"
  SUMMARY="$(jq -r '.summary // ""' "$TMP")"
  REPORT="$(jq -r '.report_path // ""' "$TMP")"
  OVERRIDE_CHAT="$(jq -r '.chat_id // ""' "$TMP")"
else
  # Pas un JSON : on prend le fichier tel quel comme résumé.
  SUMMARY="$(cat "$TMP")"
fi
[[ -n "$OVERRIDE_CHAT" ]] && CHAT_ID="$OVERRIDE_CHAT"

# --- 3. Construction du message ----------------------------------------------
case "$STATUS" in
  done|ok|success) ICON="✅" ;;
  error|fail|ko)   ICON="❌" ;;
  need-input|ask)  ICON="❓" ;;
  *)               ICON="ℹ️" ;;
esac
TEXT="$ICON Ouvrier — ${TASK:-tâche} : ${STATUS}"
[[ -n "$SUMMARY" ]] && TEXT="$TEXT"$'\n'"$SUMMARY"
[[ -n "$REPORT"  ]] && TEXT="$TEXT"$'\n'"📄 $REPORT"

if [[ -z "$CHAT_ID" ]]; then
  log "AUCUN chat_id (ni notify.env ni READY_CHAT_ID) — message NON envoyé. Renseigne NOTIFY_CHAT_ID dans notify.env."
  mkdir -p "$ARCHIVE_DIR"; mv -f "$TMP" "$ARCHIVE_DIR/$(date '+%Y%m%d-%H%M%S')-NOCHATID.json"
  exit 1
fi

# --- 4. Envoi : d'abord /reply (canal officiel), puis repli Telegram ----------
sent=0
if curl -fsS --max-time "$HTTP_TIMEOUT" \
        -H 'Content-Type: application/json' \
        ${REPLY_AUTH_TOKEN:+-H "X-Auth-Token: $REPLY_AUTH_TOKEN"} \
        -d "$(jq -nc --arg c "$CHAT_ID" --arg t "$TEXT" '{chat_id:($c|tonumber? // $c), text:$t}')" \
        "$REPLY_URL" >/dev/null 2>>"$LOG_FILE"; then
  sent=1; log "OK via /reply ($REPLY_URL) chat=$CHAT_ID status=$STATUS task=${TASK:-?}"
elif [[ -n "$TELEGRAM_TOKEN" ]] && curl -fsS --max-time "$HTTP_TIMEOUT" \
        -d chat_id="$CHAT_ID" --data-urlencode text="$TEXT" \
        "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" >/dev/null 2>>"$LOG_FILE"; then
  sent=1; log "OK via Telegram direct (repli) chat=$CHAT_ID status=$STATUS task=${TASK:-?}"
else
  log "ÉCHEC envoi (/reply ET Telegram) chat=$CHAT_ID — .done conservé dans l'archive pour rejouer."
fi

# --- 5. Archivage (garde une trace ; permet de rejouer en cas d'échec) --------
mkdir -p "$ARCHIVE_DIR"
suffix=$([[ "$sent" = 1 ]] && echo sent || echo FAILED)
mv -f "$TMP" "$ARCHIVE_DIR/$(date '+%Y%m%d-%H%M%S')-$suffix.json" 2>/dev/null || rm -f "$TMP"
exit $(( sent == 1 ? 0 : 1 ))
