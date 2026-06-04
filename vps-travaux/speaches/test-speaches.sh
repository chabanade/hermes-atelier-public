#!/usr/bin/env bash
# Test Speaches (STT local) — large-v3-turbo, langue fr
# Usage : bash test-speaches.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="http://localhost:8000"
MODEL="deepdml/faster-whisper-large-v3-turbo-ct2"
AUDIO="$DIR/test_fr.wav"

echo "==> 1. Démarrage du conteneur Speaches"
docker compose -f "$DIR/docker-compose.yml" up -d

echo "==> 2. Attente que l'API réponde (max ~90s)"
for i in $(seq 1 45); do
  if curl -sf "$BASE_URL/v1/models" >/dev/null 2>&1; then
    echo "    API up."
    break
  fi
  sleep 2
done

echo "==> 3. Pré-téléchargement du modèle turbo (peut être long la 1re fois)"
docker exec speaches uvx speaches-cli model download "$MODEL" || \
  echo "    (le modèle sera téléchargé automatiquement au 1er appel)"

echo "==> 4. Génération d'un audio de test français"
if [ ! -f "$AUDIO" ]; then
  if command -v espeak-ng >/dev/null 2>&1; then
    espeak-ng -v fr -w "$AUDIO" "Bonjour, ceci est un test de transcription en français."
  elif command -v ffmpeg >/dev/null 2>&1; then
    # Pas de synthèse vocale dispo : on génère un bip 1s (le test valide l'endpoint,
    # la transcription sera vide/du bruit mais la réponse HTTP doit être 200).
    ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=1" "$AUDIO"
  else
    echo "    Ni espeak-ng ni ffmpeg : place un fichier $AUDIO toi-même puis relance." >&2
    exit 1
  fi
fi

echo "==> 5. Appel /v1/audio/transcriptions"
curl -s "$BASE_URL/v1/audio/transcriptions" \
  -F "file=@$AUDIO" \
  -F "model=$MODEL" \
  -F "language=fr" \
  -F "response_format=json"
echo
echo "==> Terminé. Une réponse JSON avec un champ \"text\" = ça marche."
