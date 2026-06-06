#!/usr/bin/env bash
# =============================================================================
# webrtc-reply.sh — pont fichiers WebRTC -> Hermès, SANS agent LLM au repos.
# =============================================================================
#
# POURQUOI : l'ancien cron `webrtc-hermes-reply` réveillait l'agent LLM complet
# toutes les minutes (~1440 fois/jour) même quand l'inbox était vide → tokens
# brûlés pour rien. Ce script est un démon bash pur (no_agent=true) : il scrute
# l'inbox en local (find/glob, 0 token) et n'invoque Hermès QUE lorsqu'un vrai
# message « pending » est présent. Inbox vide => silencieux => zéro dépense.
#
# CYCLE pour chaque inbox/<id>.json (status="pending") :
#   1. lire `text`             (jq, local, gratuit)
#   2. demander une réponse à Hermès                       <-- seul coût en token
#   3. écrire outbox/<id>.json {session_id, reply, status} (atomique .tmp+mv)
#   4. supprimer inbox/<id>.json
# Si l'inbox est vide : rien, on dort, on recommence.
#
# Contrat (cf. webrtc-vocal/server.py: write_inbox / wait_for_reply) :
#   inbox/<id>.json  : { "session_id": "<id>", "text": "...", "status": "pending", ... }
#   outbox/<id>.json : { "session_id": "<id>", "reply": "...", "status": "done" }
#   <id> = base du nom de fichier inbox = ce que le serveur attend dans outbox.
#
# DÉPLOIEMENT (VPS) : copier vers /opt/data/scripts/webrtc-reply.sh, chmod +x,
#   puis lancer sous systemd (voir le bloc unit en bas de ce fichier) APRÈS
#   avoir supprimé l'ancien cron : `crontab -l | grep -v webrtc-hermes-reply | crontab -`
#
# Variables d'environnement (toutes optionnelles) :
#   WEBRTC_DATA_DIR        racine inbox/outbox        (défaut /opt/data/webrtc-vocal)
#   WEBRTC_POLL_INTERVAL   secondes entre 2 scans     (défaut 1 — cf. RAPPORT-LENTEUR.md Action 1 ; scan local 0 token)
#   HERMES_BIN             binaire CLI Hermès         (défaut "hermes" ; appel : `hermes ask "<prompt>"`)
#   HERMES_URL             si défini -> mode HTTP (POST {prompt}) au lieu du CLI
#   HERMES_PROMPT_PREFIX   préfixe ajouté au texte    (défaut "réponds en français, ...: ")
#   HERMES_HTTP_TIMEOUT    timeout curl en mode HTTP  (défaut 55)
#   WEBRTC_FALLBACK_REPLY  réponse si Hermès échoue   (défaut message d'excuse)
#   WEBRTC_LOCK_FILE       verrou anti-double-instance(défaut /tmp/webrtc-reply.lock)
# =============================================================================
set -uo pipefail

DATA_DIR="${WEBRTC_DATA_DIR:-/opt/data/webrtc-vocal}"
INBOX_DIR="$DATA_DIR/inbox"
OUTBOX_DIR="$DATA_DIR/outbox"
INTERVAL="${WEBRTC_POLL_INTERVAL:-1}"
HERMES_BIN="${HERMES_BIN:-/opt/hermes/hermes}"
HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"
HERMES_CLI_TIMEOUT="${HERMES_CLI_TIMEOUT:-75}"
HERMES_URL="${HERMES_URL:-}"
PROMPT_PREFIX="${HERMES_PROMPT_PREFIX:-réponds en français, de façon concise et orale, à : }"
HTTP_TIMEOUT="${HERMES_HTTP_TIMEOUT:-55}"
# NB : la valeur par défaut est posée hors de ${...:-} car une apostrophe à
# l'intérieur de ${VAR:-...} casse l'analyse bash, même entre guillemets doubles.
FALLBACK_REPLY="${WEBRTC_FALLBACK_REPLY:-}"
[[ -n "$FALLBACK_REPLY" ]] || FALLBACK_REPLY="Désolé, je n'ai pas pu générer de réponse pour le moment."
LOCK_FILE="${WEBRTC_LOCK_FILE:-/tmp/webrtc-reply.lock}"
# Mémoire de conversation CENTRALE (trans-canal) : tous les canaux passent par ce pont,
# donc UN SEUL fil commun (web, Telegram, Siri, et demain Alexa/Home). Helper + store partagé.
CTX_HELPER="${CONV_HELPER:-/opt/data/scripts/contexte-conversation.py}"
CTX_PY="${CONV_PYTHON:-python3}"

log() { printf '%s [webrtc-reply] %s\n' "$(date '+%F %T')" "$*" >&2; }

# --- Verrou : une seule instance ne traite l'inbox à la fois. -----------------
exec 9>"$LOCK_FILE" || { log "impossible d'ouvrir le verrou $LOCK_FILE"; exit 1; }
if ! flock -n 9; then
  log "une autre instance tourne déjà (verrou $LOCK_FILE) — sortie."
  exit 0
fi

# --- Arrêt propre sur SIGINT/SIGTERM (systemd stop). --------------------------
running=1
trap 'running=0' INT TERM

# --- Génère une réponse via Hermès. Renvoie le texte sur stdout, code != 0 si échec.
generate_reply() {
  local text="$1" prompt
  prompt="${PROMPT_PREFIX}${text}"
  if [[ -n "$HERMES_URL" ]]; then
    # Mode API locale : POST {"prompt": "..."} ; on tolère plusieurs noms de champ.
    curl -fsS --max-time "$HTTP_TIMEOUT" \
         -H 'Content-Type: application/json' \
         -d "$(jq -nc --arg q "$prompt" '{prompt:$q}')" \
         "$HERMES_URL" \
      | jq -r '.reply // .text // .response // .content // empty'
  else
    # Mode CLI : Hermès tourne dans le conteneur Docker « $HERMES_CONTAINER ».
    # On l'invoque en one-shot via -z (réponse « soul-aware » sur stdout). Ce démon
    # tourne en root -> `docker exec` est permis. (L'ancien `hermes ask` n'était PAS
    # une sous-commande valide -> toutes les réponses tombaient en repli : bug corrigé.)
    timeout "$HERMES_CLI_TIMEOUT" docker exec "$HERMES_CONTAINER" "$HERMES_BIN" -z "$prompt" 2>/dev/null
  fi
}

# --- Traite un fichier inbox (génère, écrit l'outbox, supprime l'inbox). -------
process_one() {
  local infile="$1" id status text reply outfile tmp
  id="$(basename "$infile" .json)"

  # JSON illisible (ne devrait pas arriver : le serveur écrit en atomique) → on ignore.
  if ! status="$(jq -er '.status // "pending"' "$infile" 2>/dev/null)"; then
    log "JSON illisible, ignoré : $infile"
    return 0
  fi
  # Déjà traité (status != pending) → on n'y touche pas.
  [[ "$status" == "pending" ]] || return 0

  text="$(jq -r '.text // ""' "$infile" 2>/dev/null)"
  if [[ -z "${text//[[:space:]]/}" ]]; then
    log "tour $id : champ text vide → suppression sans appel Hermès."
    rm -f -- "$infile"
    return 0
  fi

  log "tour $id : message reçu, appel Hermès…"
  # Mémoire trans-canal : on enrichit le message du fil récent (tous canaux confondus).
  local prompt_text
  prompt_text="$(printf '%s' "$text" | "$CTX_PY" "$CTX_HELPER" inject 2>/dev/null)"
  [[ -n "${prompt_text//[[:space:]]/}" ]] || prompt_text="$text"
  # La substitution de commande retire le \n final ; en cas d'échec → reply vide.
  if ! reply="$(generate_reply "$prompt_text")"; then
    reply=""
  fi
  if [[ -z "${reply//[[:space:]]/}" ]]; then
    log "tour $id : Hermès n'a rien renvoyé → réponse de repli."
    reply="$FALLBACK_REPLY"
  else
    # Réponse réelle (pas un repli) → on garde l'échange ORIGINAL dans le fil commun.
    "$CTX_PY" "$CTX_HELPER" save "$text" "$reply" 2>/dev/null || true
  fi

  # Écriture atomique de l'outbox (le serveur peut lire à tout moment).
  mkdir -p "$OUTBOX_DIR"
  outfile="$OUTBOX_DIR/$id.json"
  tmp="$(mktemp "$OUTBOX_DIR/.${id##*/}.XXXXXX")" || { log "tour $id : mktemp a échoué"; return 1; }
  jq -nc --arg sid "$id" --arg reply "$reply" \
     '{session_id:$sid, reply:$reply, status:"done"}' > "$tmp"
  mv -f -- "$tmp" "$outfile"
  # Le consommateur (serveur web + bot Telegram) tourne en « ouvrier » : la réponse
  # doit LUI APPARTENIR (lecture + suppression), tout en restant privée (pas
  # world-readable). On la donne à ouvrier en 600. (Avant : 644 root = soit
  # illisible par ouvrier, soit lisible par tous — les deux étaient mauvais.)
  chown ouvrier:ouvrier -- "$outfile" 2>/dev/null
  chmod 600 -- "$outfile" 2>/dev/null
  log "tour $id : réponse écrite (${#reply} car.) → $outfile"

  # Inbox traité → supprimé (l'outbox étant déjà posé, rien n'est perdu).
  rm -f -- "$infile"
}

# --- Boucle principale. -------------------------------------------------------
mkdir -p "$INBOX_DIR" "$OUTBOX_DIR"
log "démarrage — surveillance $INBOX_DIR toutes les ${INTERVAL}s (scan local, 0 token au repos)."

while (( running )); do
  shopt -s nullglob
  # *.json uniquement → ignore les *.json.tmp (écritures atomiques en cours).
  for infile in "$INBOX_DIR"/*.json; do
    (( running )) || break
    process_one "$infile"
  done
  shopt -u nullglob

  (( running )) || break
  # sleep en arrière-plan + wait : un signal interrompt l'attente immédiatement.
  # 9>&- ferme le FD du verrou pour ce process fils : sinon le sleep hériterait
  # du flock et le garderait ouvert après la mort du démon (faux « déjà en cours »).
  sleep "$INTERVAL" 9>&- &
  wait "$!" 2>/dev/null || true
done

log "arrêt propre."
exit 0

# =============================================================================
# Unit systemd suggérée — /etc/systemd/system/webrtc-reply.service :
#
#   [Unit]
#   Description=Pont WebRTC -> Hermès (réponses vocales, sans agent au repos)
#   After=network.target
#
#   [Service]
#   ExecStart=/opt/data/scripts/webrtc-reply.sh
#   Environment=WEBRTC_DATA_DIR=/opt/data/webrtc-vocal
#   Environment=HERMES_BIN=hermes
#   Environment=WEBRTC_POLL_INTERVAL=1   # relève l'inbox chaque seconde (Action 1)
#   Restart=always
#   RestartSec=3
#   User=hermes
#
#   [Install]
#   WantedBy=multi-user.target
#
#   sudo systemctl daemon-reload && sudo systemctl enable --now webrtc-reply
#   journalctl -u webrtc-reply -f          # voir les logs (rien tant que l'inbox est vide)
# =============================================================================
