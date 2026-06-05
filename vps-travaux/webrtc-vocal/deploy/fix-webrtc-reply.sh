#!/usr/bin/env bash
# =============================================================================
# fix-webrtc-reply.sh — DIAGNOSTIC + RÉPARATION du pont webrtc-reply (exécuté ROOT)
# =============================================================================
# Problème : 7 messages "pending" jamais traités. Hypothèse forte : le service
# du pont surveille le MAUVAIS dossier (DATA_DIR sans "/data") alors que server.py
# écrit dans <racine>/data/inbox. Ce script ne CROIT NI la mission NI l'installeur :
# il aligne le pont sur le DATA_DIR RÉEL du serveur en cours d'exécution, puis teste.
# Idempotent : relançable sans dommage.
# =============================================================================
set -uo pipefail
say(){ printf '\n### %s\n' "$*"; }
kv(){ printf '  %-24s %s\n' "$1" "$2"; }

PROJ_PROD=/opt/data/webrtc-vocal
SRC_DEV=/home/ouvrier/travaux/webrtc-vocal/deploy/webrtc-reply.sh
DEST=/opt/data/scripts/webrtc-reply.sh
UNIT=/etc/systemd/system/webrtc-reply.service

say "0. CONTEXTE HÔTE"
kv "date"     "$(date '+%F %T')"
kv "hostname" "$(hostname 2>/dev/null || echo '?')"
command -v jq >/dev/null 2>&1 || { apt-get update -y >/dev/null 2>&1; apt-get install -y jq >/dev/null 2>&1; }
kv "jq"   "$(command -v jq   || echo 'MANQUANT')"
kv "curl" "$(command -v curl || echo 'MANQUANT')"
kv "flock" "$(command -v flock || echo 'MANQUANT')"

say "1. ÉTAT ACTUEL DU SERVICE webrtc-reply"
systemctl status webrtc-reply --no-pager 2>&1 | head -18 || true
echo "--- unit actuelle ($UNIT) ---"
[[ -f "$UNIT" ]] && cat "$UNIT" || echo "(aucun fichier unit)"

say "2. SERVEUR webrtc (port 8686) ET SON DATA_DIR RÉEL"
SRV_PID="$(ss -ltnp 2>/dev/null | awk '/:8686 /{print}' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)"
[[ -z "${SRV_PID:-}" ]] && SRV_PID="$(pgrep -f 'server\.py' | head -1 || true)"
echo "--- tous les server.py ---"; pgrep -af 'server\.py' 2>&1 | head -6 || echo "(aucun)"
SERVER_DATA_DIR=""
if [[ -n "${SRV_PID:-}" ]]; then
  kv "server pid (8686)" "$SRV_PID"
  SRV_CWD="$(readlink -f /proc/$SRV_PID/cwd 2>/dev/null || true)"; kv "cwd" "${SRV_CWD:-?}"
  ENV_DD="$(tr '\0' '\n' < /proc/$SRV_PID/environ 2>/dev/null | sed -n 's/^WEBRTC_DATA_DIR=//p' | head -1 || true)"
  if [[ -n "$ENV_DD" ]]; then
    SERVER_DATA_DIR="$ENV_DD"; kv "WEBRTC_DATA_DIR(env)" "$ENV_DD  (explicite)"
  else
    SRV_PY="$(tr '\0' '\n' < /proc/$SRV_PID/cmdline 2>/dev/null | grep -m1 'server\.py' || true)"
    case "$SRV_PY" in /*) A="$SRV_PY";; *) A="${SRV_CWD:-.}/$SRV_PY";; esac
    SRV_DIR="$(dirname "$(readlink -f "$A" 2>/dev/null || echo "$A")")"
    SERVER_DATA_DIR="$SRV_DIR/data"
    kv "WEBRTC_DATA_DIR(env)" "(absent -> défaut ROOT/data)"
    kv "server.py dir" "${SRV_DIR:-?}"
  fi
else
  kv "server pid (8686)" "(AUCUN serveur en écoute — DATA_DIR déduit autrement)"
fi
kv "=> SERVER_DATA_DIR" "${SERVER_DATA_DIR:-INCONNU}"

say "3. INBOX CANDIDATES (où sont les pending ?)"
for d in "${SERVER_DATA_DIR:-}" "$PROJ_PROD/data" "$PROJ_PROD" /home/ouvrier/travaux/webrtc-vocal/data; do
  [[ -n "$d" ]] || continue
  n=$(ls -1 "$d"/inbox/*.json 2>/dev/null | wc -l)
  printf '  %-50s %s pending .json\n' "$d/inbox" "$n"
done

say "4. DÉCISION DATA_DIR"
DATA_DIR=""; REASON=""
if [[ -n "$SERVER_DATA_DIR" && -d "$SERVER_DATA_DIR/inbox" ]]; then
  DATA_DIR="$SERVER_DATA_DIR"; REASON="aligné sur le serveur EN COURS (source de vérité)"
elif [[ -n "$SERVER_DATA_DIR" ]]; then
  DATA_DIR="$SERVER_DATA_DIR"; REASON="DATA_DIR du serveur (inbox sera créée)"
else
  for d in "$PROJ_PROD/data" /home/ouvrier/travaux/webrtc-vocal/data "$PROJ_PROD"; do
    if ls -1 "$d"/inbox/*.json >/dev/null 2>&1; then DATA_DIR="$d"; REASON="contient le backlog pending"; break; fi
  done
  [[ -z "$DATA_DIR" ]] && { DATA_DIR="$PROJ_PROD/data"; REASON="défaut mission (ROOT/data)"; }
fi
kv "DATA_DIR retenu" "$DATA_DIR"
kv "raison"          "$REASON"
INBOX="$DATA_DIR/inbox"; OUTBOX="$DATA_DIR/outbox"

say "5. INTÉGRATION HERMÈS — quel mode RÉPOND vraiment ?"
HERMES_HOME="$(getent passwd hermes 2>/dev/null | cut -d: -f6)"; [[ -n "$HERMES_HOME" ]] || HERMES_HOME=/opt/data/home
# 5a. CLI ?
HERMES_BIN=""
for c in /opt/hermes/hermes /opt/data/home/.hermes/hermes /opt/data/home/.hermes/scripts/hermes; do
  [[ -x "$c" ]] && { HERMES_BIN="$c"; break; }
done
[[ -z "$HERMES_BIN" ]] && HERMES_BIN="$(command -v hermes 2>/dev/null || true)"
kv "hermes CLI" "${HERMES_BIN:-introuvable}"
CLI_OK=0
if [[ -n "$HERMES_BIN" && -x "$HERMES_BIN" ]]; then
  echo "--- test CLI: '$HERMES_BIN ask ...' (timeout 30s, HOME=$HERMES_HOME) ---"
  CLI_OUT="$(HOME="$HERMES_HOME" timeout 30 "$HERMES_BIN" ask "réponds juste: pong" 2>&1)"; rc=$?
  printf '%s\n' "$CLI_OUT" | head -8
  [[ $rc -eq 0 && -n "${CLI_OUT//[[:space:]]/}" ]] && CLI_OK=1
  kv "CLI rc" "$rc  (ok=$CLI_OK)"
fi
# 5b. HTTP ? (port 8080, route /reply documentée pour Hermès)
echo "--- ports en écoute (8080/8686/11434/8443/443) ---"
ss -ltnp 2>/dev/null | grep -E ':(8080|8686|11434|8443|443)\b' || echo "(aucun)"
HERMES_URL=""
for u in http://127.0.0.1:8080/reply http://127.0.0.1:8080/ask http://127.0.0.1:8080/v1/reply; do
  R="$(curl -fsS -m 8 -H 'Content-Type: application/json' -d '{"prompt":"réponds juste: pong"}' "$u" 2>/dev/null || true)"
  RT="$(printf '%s' "$R" | jq -r '.reply // .text // .response // .content // empty' 2>/dev/null || true)"
  if [[ -n "$RT" ]]; then HERMES_URL="$u"; kv "HTTP OK" "$u -> ${RT:0:60}"; break; fi
done
[[ -z "$HERMES_URL" ]] && kv "HTTP" "aucune route /reply fonctionnelle sur :8080"
# 5c. choix
if   [[ $CLI_OK -eq 1 ]];        then MODE=cli
elif [[ -n "$HERMES_URL" ]];     then MODE=http
elif [[ -n "$HERMES_BIN" ]];     then MODE=cli
else MODE=cli; HERMES_BIN=/opt/hermes/hermes; fi
kv "=> MODE retenu" "$MODE"

say "6. UTILISATEUR DE SERVICE"
if id hermes >/dev/null 2>&1; then SVC_USER=hermes; SVC_HOME="$HERMES_HOME"; else SVC_USER=root; SVC_HOME=/root; fi
kv "SVC_USER" "$SVC_USER"; kv "SVC_HOME" "$SVC_HOME"

say "7. INSTALLATION DU DÉMON + UNIT (alignés sur le serveur)"
[[ -s "$SRC_DEV" ]] || { echo "❌ source démon introuvable: $SRC_DEV"; exit 1; }
install -d -m 0755 /opt/data/scripts
install -m 0755 "$SRC_DEV" "$DEST" && kv "démon copié" "$SRC_DEV -> $DEST"
sha256sum "$DEST" 2>/dev/null || true
install -d -m 0777 "$INBOX" "$OUTBOX"; chmod 0777 "$INBOX" "$OUTBOX" 2>/dev/null || true
kv "inbox/outbox" "$INBOX (0777), $OUTBOX (0777)"
if [[ "$MODE" == http ]]; then HERMES_ENV="Environment=HERMES_URL=$HERMES_URL"
else                          HERMES_ENV="Environment=HERMES_BIN=$HERMES_BIN"; fi
cat > "$UNIT" <<EOF
[Unit]
Description=Pont WebRTC -> Hermès (réponses vocales, sans agent au repos)
After=network.target

[Service]
ExecStart=$DEST
Environment=WEBRTC_DATA_DIR=$DATA_DIR
$HERMES_ENV
Environment=HOME=$SVC_HOME
Environment=WEBRTC_POLL_INTERVAL=1
Restart=always
RestartSec=3
User=$SVC_USER

[Install]
WantedBy=multi-user.target
EOF
echo "--- nouvelle unit ---"; cat "$UNIT"

say "8. REDÉMARRAGE PROPRE (tue les instances orphelines tenant le verrou)"
systemctl daemon-reload
systemctl stop webrtc-reply 2>/dev/null || true
pkill -f 'webrtc-reply\.sh' 2>/dev/null || true
sleep 1
systemctl enable webrtc-reply >/dev/null 2>&1 || true
systemctl start webrtc-reply
sleep 2
kv "is-enabled" "$(systemctl is-enabled webrtc-reply 2>&1)"
kv "is-active"  "$(systemctl is-active  webrtc-reply 2>&1)"

say "9. BACKLOG STALE — archivage hors test (réversible)"
ARCH="$DATA_DIR/inbox.archive-$(date +%Y%m%d-%H%M%S)"
shopt -s nullglob; PEND=( "$INBOX"/*.json ); shopt -u nullglob
moved=0
for f in "${PEND[@]}"; do
  [[ "$(basename "$f")" == "final.json" ]] && continue
  install -d -m 0777 "$ARCH"; mv -f "$f" "$ARCH"/ && moved=$((moved+1))
done
if (( moved )); then kv "archivés" "$moved fichiers -> $ARCH";
  echo "--- aperçu des messages archivés (id : text) ---"
  for f in "$ARCH"/*.json; do printf '  %s : %s\n' "$(basename "$f" .json)" "$(jq -r '.text // ""' "$f" 2>/dev/null | head -c 70)"; done
else kv "backlog" "vide (rien à archiver)"; fi

say "10. TEST E2E : inbox -> pont -> outbox"
rm -f "$OUTBOX/final.json" "$INBOX/final.json"
TMP="$INBOX/.final.json.tmp"
printf '%s\n' '{"session_id": "final", "text": "Bonjour, présente-toi brièvement", "status": "pending", "source": "e2e-test"}' > "$TMP"
chmod 0666 "$TMP"; mv -f "$TMP" "$INBOX/final.json"
kv "injecté" "$INBOX/final.json"
echo "--- attente de outbox/final.json (max 60s) ---"
for i in $(seq 1 60); do
  [[ -f "$OUTBOX/final.json" ]] && { kv "outbox apparu après" "${i}s"; break; }
  sleep 1
done
VERDICT="❌ ÉCHEC"
if [[ -f "$OUTBOX/final.json" ]]; then
  echo "=== CONTENU outbox/final.json ==="; cat "$OUTBOX/final.json"; echo
  RT="$(jq -r '.reply // empty' "$OUTBOX/final.json" 2>/dev/null)"
  FB="Désolé, je n'ai pas pu générer"
  if [[ -n "$RT" && "$RT" != *"$FB"* ]]; then VERDICT="✅ SUCCÈS — vraie réponse Hermès (${#RT} car.)"
  elif [[ -n "$RT" ]]; then VERDICT="⚠️ PONT OK mais Hermès a renvoyé le REPLI (intégration à revoir)"
  else VERDICT="⚠️ outbox présent, champ reply vide"; fi
fi
kv "VERDICT E2E" "$VERDICT"

say "11. ÉTAT FINAL + LOGS"
systemctl status webrtc-reply --no-pager 2>&1 | head -12 || true
echo "--- journalctl -u webrtc-reply -n 30 ---"
journalctl -u webrtc-reply -n 30 --no-pager 2>&1 || true

say "RÉSUMÉ"
kv "DATA_DIR pont"  "$DATA_DIR   ($REASON)"
kv "mode Hermès"    "$MODE   $( [[ $MODE == http ]] && echo "$HERMES_URL" || echo "$HERMES_BIN" )"
kv "service actif"  "$(systemctl is-active webrtc-reply 2>&1)"
kv "verdict e2e"    "$VERDICT"
exit 0
