#!/usr/bin/env bash
# =============================================================================
# apply-lenteur-fixes.sh — applique EN LIVE les correctifs de lenteur (ROOT).
# =============================================================================
# Exécuté par le « sas root » (chemin déclaré dans ~/travaux/.root-request).
# Idempotent : peut être relancé sans dommage.
#
# Ce que ça fait :
#   ACTION 1 — ajoute Environment=WEBRTC_POLL_INTERVAL=1 à
#              /etc/systemd/system/webrtc-reply.service (relève l'inbox chaque
#              seconde au lieu de toutes les 5 s), avec sauvegarde.
#   daemon-reload + restart webrtc-reply.
#   ACTIONS 2 & 3 (code) sont déjà dans l'atelier (server.py / transcribe.py) :
#              on RESTART webrtc-vocal pour charger les moteurs persistants
#              (STT/TTS chauds), beam=1 et fin-de-phrase 600 ms.
#   VÉRIF    — services actifs + /health et /diag répondent.
# =============================================================================
set -uo pipefail   # pas de -e : on veut continuer et tout rapporter.

REPLY_UNIT=/etc/systemd/system/webrtc-reply.service
VOCAL_UNIT=webrtc-vocal.service
HEALTH_URL=http://127.0.0.1:8686/health
DIAG_URL=http://127.0.0.1:8686/diag

log()  { printf '\n=== %s ===\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*" >&2; }

# --- ACTION 1 : poll interval 5s -> 1s dans le service webrtc-reply -----------
log "ACTION 1 — WEBRTC_POLL_INTERVAL=1 dans $REPLY_UNIT"
if [[ ! -f "$REPLY_UNIT" ]]; then
  warn "unité absente : $REPLY_UNIT — Action 1 ignorée (le serveur vocal sera quand même redémarré)."
elif grep -q '^Environment=WEBRTC_POLL_INTERVAL=' "$REPLY_UNIT"; then
  # Déjà présent : on force la valeur à 1 (idempotent).
  sed -i 's#^Environment=WEBRTC_POLL_INTERVAL=.*#Environment=WEBRTC_POLL_INTERVAL=1#' "$REPLY_UNIT"
  echo "déjà présent → valeur forcée à 1."
else
  cp -a "$REPLY_UNIT" "${REPLY_UNIT}.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
  # Insère la ligne juste avant User= (toujours présent dans [Service]) ; à défaut,
  # après ExecStart=.
  if grep -q '^User=' "$REPLY_UNIT"; then
    sed -i '/^User=/i Environment=WEBRTC_POLL_INTERVAL=1' "$REPLY_UNIT"
  else
    sed -i '/^ExecStart=/a Environment=WEBRTC_POLL_INTERVAL=1' "$REPLY_UNIT"
  fi
  echo "ligne ajoutée."
fi
echo "--- $REPLY_UNIT (extrait [Service]) ---"
grep -E '^(ExecStart|Environment|User)=' "$REPLY_UNIT" 2>/dev/null || true

# --- daemon-reload + redémarrages --------------------------------------------
log "Rechargement systemd + redémarrages"
systemctl daemon-reload

for svc in webrtc-reply "$VOCAL_UNIT"; do
  echo "restart $svc…"
  systemctl restart "$svc" 2>&1 || warn "restart $svc a renvoyé une erreur"
done

# Le serveur vocal précharge les modèles STT/TTS au démarrage (~3 s) : on laisse
# le temps qu'il ouvre son port avant de sonder /health.
sleep 6

# --- VÉRIFICATION ------------------------------------------------------------
log "Vérification des services"
for svc in webrtc-reply "$VOCAL_UNIT"; do
  printf '%-18s enabled=%s active=%s\n' "$svc" \
    "$(systemctl is-enabled "$svc" 2>&1)" "$(systemctl is-active "$svc" 2>&1)"
done

log "Sonde /health"
if curl -fsS --max-time 8 "$HEALTH_URL"; then echo; echo "✅ /health OK"; else
  warn "/health NE répond PAS — derniers logs serveur :"
  journalctl -u "$VOCAL_UNIT" -n 25 --no-pager 2>&1 | tail -25 || true
fi

log "Sonde /diag (compteurs)"
if command -v jq >/dev/null 2>&1; then
  curl -fsS --max-time 8 "$DIAG_URL" | jq '{global, nb_sessions: (.sessions|length)}' 2>&1 \
    || { curl -fsS --max-time 8 "$DIAG_URL" 2>&1 | head -c 800; echo; }
else
  curl -fsS --max-time 8 "$DIAG_URL" 2>&1 | head -c 800; echo
fi

log "Confirmation moteurs persistants + réglages (logs serveur)"
journalctl -u "$VOCAL_UNIT" -n 40 --no-pager 2>&1 \
  | grep -iE 'préchauffe|worker persistant|beam=|fin_de_phrase|modèle chargé|voix chargée|prêt' \
  | tail -15 || true

log "Confirmation poll 1 s (logs webrtc-reply)"
journalctl -u webrtc-reply -n 10 --no-pager 2>&1 | grep -iE 'toutes les|surveillance' | tail -3 || true

log "Terminé."
