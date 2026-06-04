#!/usr/bin/env bash
# Redémarrage propre du Hermes Voc Bot.
#
#   • Stoppe l'instance précédente (bot.pid), en vérifiant que le PID est bien
#     CE bot (et pas un PID recyclé), puis attend sa fin.
#   • Relance en arrière-plan et enregistre le nouveau PID dans bot.pid.
#
# Token : si BOT_TOKEN_B64 est exporté, bot.py l'utilise (prioritaire) ; sinon
# il lit TELEGRAM_TOKEN depuis .env. Exemple :
#   BOT_TOKEN_B64="$(printf '%s' "$TELEGRAM_TOKEN" | base64 -w0)" ./restart.sh
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE=bot.pid
PY=.venv/bin/python
MATCH='hermes-voc-bot/bot.py'

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
      echo "PID $old n'est pas ce bot (recyclé) — ignoré."
    fi
  fi
  rm -f "$PIDFILE"
fi

# Filet de sécurité : tout autre poller résiduel de CE bot.
pkill -f "$MATCH" 2>/dev/null || true
sleep 1

# 2. Relancer.
mkdir -p logs
nohup "$PY" bot.py >> logs/bot.log 2>&1 &
echo $! > "$PIDFILE"
echo "Bot relancé (PID $(cat "$PIDFILE")). Logs : logs/bot.log  |  HTTP : http://127.0.0.1:8080/health"
