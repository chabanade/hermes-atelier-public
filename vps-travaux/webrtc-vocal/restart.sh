#!/usr/bin/env bash
# Redémarrage propre du serveur WebRTC vocal (brique 3/3, conversation) — SUR le VPS.
#
#   • Stoppe l'instance précédente (server.pid), en vérifiant que le PID est bien
#     CE serveur (et pas un PID recyclé), puis attend sa fin.
#   • Relance en arrière-plan, PID dans server.pid, logs dans logs/server.log.
#
# Le serveur Python reste en HTTP CLAIR sur 127.0.0.1:8686 ; c'est Caddy (443,
# Let's Encrypt) qui termine le TLS et reverse-proxy vers ce localhost. Pas de
# HTTPS natif ici (--https-port 0) : le 8443 n'est plus utilisé.
#
# Variables d'env utiles (toutes surchargeables ; défauts = self-contained ici) :
#   PORT             port HTTP clair (défaut 8686, cible du reverse_proxy Caddy)
#   HOST             interface d'écoute (défaut 127.0.0.1)
#   WHISPER_PYTHON   interpréteur faster-whisper (défaut .venv local)
#   WHISPER_MODEL    modèle whisper (défaut base)
#   TTS_PYTHON       interpréteur Piper (défaut .venv local)
#   PIPER_VOICE      voix .onnx (défaut models/piper/fr_FR-upmc-medium.onnx)
#   WEBRTC_DATA_DIR  racine inbox/outbox (défaut ./data ; À PARTAGER avec webrtc-reply.sh)
#   HF_HOME          cache du modèle whisper (défaut ./models/hf)
#   WEBRTC_VAD       backend VAD : auto|silero|webrtcvad|energy (défaut auto)
#   WEBRTC_ICE_SERVERS  serveurs STUN/TURN (défaut STUN Google ; vide = host-only —
#                       un client derrière NAT échouera). cf. /ice et DIAGNOSTIC.md
#   WEBRTC_TURN_URL/_USER/_PASS  relais TURN (Option A : turns:…:5349 TCP). Vide =
#                       STUN seul. Renseigné par deploy/turn.env (cf. deploy/TURN.md).
#   WEBRTC_LOG_LEVEL    niveau de log applicatif (défaut INFO ; DEBUG pour tout voir)
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE=server.pid
PY=.venv/bin/python
MATCH='webrtc-vocal/server.py'
PORT="${PORT:-8686}"
HOST="${HOST:-127.0.0.1}"        # Caddy (443) reverse-proxy vers ce localhost:8686

# Tout est self-contained dans CE dossier : le venv local porte faster-whisper et
# piper, la voix .onnx et le cache du modèle whisper vivent sous models/. Aucune
# dépendance externe — pas de venv ni de data partagés hors de ce répertoire.
export WHISPER_PYTHON="${WHISPER_PYTHON:-$PWD/.venv/bin/python}"
export TTS_PYTHON="${TTS_PYTHON:-$PWD/.venv/bin/python}"
export WHISPER_MODEL="${WHISPER_MODEL:-base}"
export PIPER_VOICE="${PIPER_VOICE:-$PWD/models/piper/fr_FR-upmc-medium.onnx}"
export WEBRTC_DATA_DIR="${WEBRTC_DATA_DIR:-$PWD/data}"   # inbox/ outbox/ (partagé avec webrtc-reply.sh)
export HF_HOME="${HF_HOME:-$PWD/models/hf}"              # cache du modèle faster-whisper
# Le modèle whisper est DÉJÀ en cache sous models/hf : on coupe tout accès réseau
# à Hugging Face (sinon faster-whisper tente un appel HF à chaque démarrage à
# froid → latence/échec si le réseau est coupé ou HF indisponible). Si un jour on
# change de WHISPER_MODEL non encore téléchargé, désactiver temporairement cette
# ligne le temps du premier download, puis la remettre.
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

# Traversée de NAT : STUN activé par défaut (indispensable pour un client distant
# derrière une box/4G — sinon il n'annonce qu'une IP privée injoignable). Mêmes
# serveurs côté serveur aiortc ET navigateur (via /ice). Vider pour du host-only.
export WEBRTC_ICE_SERVERS="${WEBRTC_ICE_SERVERS:-stun:stun.l.google.com:19302}"

# Relais TURN (Option A : turns:…:5349 en TCP, voir deploy/TURN.md). Si STUN ne
# suffit pas (NAT symétrique/CGNAT), deploy/install-coturn.sh dépose un fichier
# deploy/turn.env avec WEBRTC_TURN_URL/_USER/_PASS : on le charge ici s'il existe.
# Absent ⇒ STUN seul (défaut). Le serveur annonce ce TURN au navigateur via /ice.
if [[ -f deploy/turn.env ]]; then
  set -a; . deploy/turn.env; set +a
fi
export WEBRTC_TURN_URL="${WEBRTC_TURN_URL:-}"
export WEBRTC_TURN_USER="${WEBRTC_TURN_USER:-}"
export WEBRTC_TURN_PASS="${WEBRTC_TURN_PASS:-}"

export WEBRTC_LOG_LEVEL="${WEBRTC_LOG_LEVEL:-INFO}"

# 1. Stopper l'ancienne instance si elle tourne encore.
if [[ -f "$PIDFILE" ]]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$old/cmdline" 2>/dev/null | grep -q "$MATCH"; then
      echo "Arrêt de l'instance $old…"
      kill "$old" 2>/dev/null || true
      for _ in $(seq 1 50); do kill -0 "$old" 2>/dev/null || break; sleep 0.1; done
      kill -9 "$old" 2>/dev/null || true
    else
      echo "PID $old n'est pas ce serveur (recyclé) — ignoré."
    fi
  fi
  rm -f "$PIDFILE"
fi

# Filet de sécurité : tout autre processus résiduel de CE serveur.
pkill -f "$MATCH" 2>/dev/null || true
sleep 1

# 2. Relancer — HTTP clair seul (--https-port 0), TLS délégué à Caddy.
mkdir -p logs data models/piper models/hf
nohup "$PY" server.py --host "$HOST" --port "$PORT" --https-port 0 >> logs/server.log 2>&1 &
echo $! > "$PIDFILE"
echo "Serveur relancé (PID $(cat "$PIDFILE")). Logs : logs/server.log"
echo "  HTTP   : http://$HOST:$PORT/health        (cible du reverse_proxy Caddy)"
echo "  PUBLIC : https://votre-domaine.example/health  (via Caddy 443)"
if [[ -n "$WEBRTC_TURN_URL" ]]; then
  echo "  TURN   : $WEBRTC_TURN_URL (relais actif, annoncé via /ice)"
else
  echo "  TURN   : aucun (STUN seul). Pour activer : deploy/install-coturn.sh — cf. deploy/TURN.md"
fi
