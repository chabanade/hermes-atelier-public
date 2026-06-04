#!/usr/bin/env bash
# =============================================================================
# install-webrtc-reply.sh — déploiement ROOT du pont WebRTC -> Hermès.
# =============================================================================
# Exécuté par le « sas root » (chemin déclaré dans ~/travaux/.root-request).
# Idempotent : peut être relancé sans dommage.
#
# Ce que ça fait :
#   1. installe le démon webrtc-reply.sh dans /opt/data/scripts/
#   2. écrit l'unit systemd /etc/systemd/system/webrtc-reply.service
#   3. retire l'ancien cron LLM par-minute « webrtc-hermes-reply » (s'il reste)
#   4. enable --now + vérifie que le service est actif
#
# Le démon scrute /opt/data/webrtc-vocal/inbox/ en local (0 token au repos) et
# n'appelle Hermès QUE lorsqu'un .json « pending » arrive.
# =============================================================================
set -uo pipefail   # pas de -e : on veut continuer sur les checks non bloquants.

log()  { printf '\n=== %s ===\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*" >&2; }
die()  { printf '❌ %s\n' "$*" >&2; exit 1; }

DATA_DIR=/opt/data/webrtc-vocal
SCRIPTS_DIR=/opt/data/scripts
DEST="$SCRIPTS_DIR/webrtc-reply.sh"
UNIT=/etc/systemd/system/webrtc-reply.service
HERMES_BIN=/opt/data/home/.hermes/hermes
SVC_USER=hermes

# --- 1. Source du script (prod-staged en priorité, sinon copie atelier) -------
SRC_PROD=/opt/data/home/.hermes/scripts/webrtc-reply.sh
SRC_DEV=/home/ouvrier/travaux/webrtc-vocal/deploy/webrtc-reply.sh
if [[ -s "$SRC_PROD" ]]; then SRC="$SRC_PROD"; else SRC="$SRC_DEV"; fi
[[ -s "$SRC" ]] || die "aucune source trouvée (ni $SRC_PROD ni $SRC_DEV)"
log "Source retenue : $SRC"
sha256sum "$SRC" 2>/dev/null || true

# --- 2. Utilisateur de service ------------------------------------------------
if id "$SVC_USER" >/dev/null 2>&1; then
  SVC_HOME="$(getent passwd "$SVC_USER" | cut -d: -f6)"
  [[ -n "$SVC_HOME" ]] || SVC_HOME=/opt/data/home
  log "Utilisateur de service : $SVC_USER (HOME=$SVC_HOME)"
else
  warn "utilisateur '$SVC_USER' absent → le service tournera en root"
  SVC_USER=root
  SVC_HOME=/root
fi

# --- 3. Copie du démon --------------------------------------------------------
install -d -m 0755 "$SCRIPTS_DIR"
install -m 0755 "$SRC" "$DEST" || die "copie vers $DEST impossible"
log "Démon installé : $DEST"

# --- 4. Répertoires inbox/outbox ---------------------------------------------
install -d -m 0775 "$DATA_DIR/inbox" "$DATA_DIR/outbox"
if [[ "$SVC_USER" != root ]]; then
  chown "$SVC_USER" "$DATA_DIR/inbox" "$DATA_DIR/outbox" 2>/dev/null \
    || warn "chown inbox/outbox vers $SVC_USER impossible (laissé tel quel)"
fi

# --- 5. Vérif binaire Hermès (non bloquant) ----------------------------------
if [[ -x "$HERMES_BIN" ]]; then
  log "Binaire Hermès OK : $HERMES_BIN"
else
  warn "binaire Hermès introuvable/non exécutable : $HERMES_BIN"
  warn "  → le service démarre quand même ; réponse de repli servie en attendant."
fi

# --- 6. Unit systemd ----------------------------------------------------------
cat > "$UNIT" <<EOF
[Unit]
Description=Pont WebRTC -> Hermès (réponses vocales, sans agent au repos)
After=network.target

[Service]
ExecStart=$DEST
Environment=WEBRTC_DATA_DIR=$DATA_DIR
Environment=HERMES_BIN=$HERMES_BIN
Environment=HOME=$SVC_HOME
# Relève l'inbox toutes les 1 s (au lieu de 5) — cf. RAPPORT-LENTEUR.md Action 1.
# Scan LOCAL (glob), 0 token au repos : seul l'arrivée d'un message coûte.
Environment=WEBRTC_POLL_INTERVAL=1
Restart=always
RestartSec=3
User=$SVC_USER

[Install]
WantedBy=multi-user.target
EOF
log "Unit écrite : $UNIT"
cat "$UNIT"

# --- 7. Retrait de l'ancien cron LLM par-minute (s'il subsiste) ---------------
for u in root "$SVC_USER"; do
  if crontab -l -u "$u" 2>/dev/null | grep -q 'webrtc-hermes-reply'; then
    crontab -l -u "$u" 2>/dev/null | grep -v 'webrtc-hermes-reply' | crontab -u "$u" - \
      && log "Ancien cron 'webrtc-hermes-reply' retiré de la crontab de $u"
  fi
done

# --- 8. Activation + démarrage ------------------------------------------------
systemctl daemon-reload
systemctl enable --now webrtc-reply

# --- 9. Vérification ----------------------------------------------------------
sleep 2
log "Vérification"
printf 'enabled : '; systemctl is-enabled webrtc-reply 2>&1 || true
printf 'active  : '; systemctl is-active  webrtc-reply 2>&1 || true
systemctl status webrtc-reply --no-pager 2>&1 | head -12 || true
log "Derniers logs (journalctl)"
journalctl -u webrtc-reply -n 8 --no-pager 2>&1 || true

if systemctl is-active --quiet webrtc-reply; then
  log "✅ webrtc-reply est ACTIF (running) — silencieux tant que l'inbox est vide."
  exit 0
else
  die "webrtc-reply n'est PAS actif — voir status/journal ci-dessus."
fi
