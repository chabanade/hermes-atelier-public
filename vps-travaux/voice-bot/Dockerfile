# Image du bot vocal Hermès.
# ffmpeg est indispensable : conversion audio → OGG/Opus (format sendVoice).
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Utilisateur non-root (sécurité).
RUN useradd --create-home --uid 10001 hermes

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY voice_bot/ ./voice_bot/

ENV PYTHONUNBUFFERED=1 \
    AUDIO_TMP_DIR=/tmp/hermes-voice-bot \
    HEALTH_HOST=0.0.0.0 \
    HEALTH_PORT=8080

USER hermes
EXPOSE 8080

# Healthcheck Docker (complète Uptime Kuma).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${HEALTH_PORT}/health" || exit 1

# tini : gestion correcte des signaux (PID 1) → arrêt/redémarrage propre.
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "voice_bot"]
