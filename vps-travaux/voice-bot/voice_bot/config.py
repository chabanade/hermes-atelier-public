"""Configuration du bot, chargée depuis les variables d'environnement.

On centralise ici toute la lecture de l'environnement : le reste du code ne touche
jamais `os.environ` directement. Chaque réglage a un défaut raisonnable, sauf le
token Telegram qui est obligatoire.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# --- Petits utilitaires de lecture typée de l'environnement -------------------

def _get_bool(name: str, default: bool) -> bool:
    """Lit un booléen tolérant : 1/true/yes/oui/on (insensible à la casse)."""
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    return val.strip().lower() in {"1", "true", "yes", "oui", "on"}


def _get_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val and val.strip() else default


def _get_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val and val.strip() else default


def _get_ids(name: str) -> set[int]:
    """Lit une liste d'IDs Telegram séparés par des virgules (ou points-virgules)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return set()
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return ids


@dataclass(frozen=True)
class Config:
    """Réglages immuables du bot (un seul objet passé partout)."""

    # --- Telegram ---
    telegram_token: str
    allowed_user_ids: set[int]   # liste blanche ; vide = ouvert à tous (déconseillé)
    send_transcript: bool        # renvoyer la transcription pour relecture/confirmation
    reply_with_text: bool        # joindre le texte de la réponse d'Hermès

    # --- STT (transcription, API compatible OpenAI : Speaches / Groq / OpenAI) ---
    stt_base_url: str
    stt_model: str
    stt_api_key: str
    stt_language: str
    stt_timeout: float
    stt_vad_filter: bool         # filtre les silences (utile en local Speaches)

    # --- TTS (synthèse vocale) ---
    tts_backend: str             # "speaches" | "piper" | "none"
    tts_base_url: str
    tts_model: str
    tts_voice: str
    tts_api_key: str
    tts_speed: float
    tts_timeout: float
    prefer_out_audio: bool       # si Hermès a déposé un audio dans out/, le réutiliser
    piper_bin: str
    piper_model: str

    # --- Aiguilleur (file de fichiers partagée avec Hermès) ---
    queue_in: Path
    queue_out: Path
    queue_archive: Path | None   # None = supprimer les fichiers out/ traités
    out_poll_interval: float
    out_timeout: float
    out_quiesce: float           # délai d'« immobilité » avant de lire un fichier out/

    # --- Divers / exploitation ---
    audio_tmp_dir: Path
    keep_audio: bool             # RGPD : False = purge des audios après usage
    health_host: str
    health_port: int
    health_require_stt: bool     # si True, /health échoue quand le STT est injoignable
    log_level: str

    @staticmethod
    def from_env() -> "Config":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN manquant. Crée un bot via @BotFather puis "
                "définis TELEGRAM_BOT_TOKEN dans l'environnement (.env)."
            )

        stt_base = os.environ.get("STT_BASE_URL", "http://speaches:8000").rstrip("/")
        tts_base = os.environ.get("TTS_BASE_URL", "").rstrip("/") or stt_base
        archive = os.environ.get("QUEUE_ARCHIVE", "").strip()

        return Config(
            telegram_token=token,
            allowed_user_ids=_get_ids("ALLOWED_USER_IDS"),
            send_transcript=_get_bool("SEND_TRANSCRIPT", True),
            reply_with_text=_get_bool("REPLY_WITH_TEXT", True),
            stt_base_url=stt_base,
            stt_model=os.environ.get("STT_MODEL", "Systran/faster-whisper-large-v3-turbo"),
            stt_api_key=os.environ.get("STT_API_KEY", ""),
            stt_language=os.environ.get("STT_LANGUAGE", "fr"),
            stt_timeout=_get_float("STT_TIMEOUT", 120.0),
            stt_vad_filter=_get_bool("STT_VAD_FILTER", True),
            tts_backend=os.environ.get("TTS_BACKEND", "speaches").strip().lower(),
            tts_base_url=tts_base,
            tts_model=os.environ.get("TTS_MODEL", "speaches-ai/Kokoro-82M-v1.0-ONNX"),
            tts_voice=os.environ.get("TTS_VOICE", "ff_siwis"),
            tts_api_key=os.environ.get("TTS_API_KEY", ""),
            tts_speed=_get_float("TTS_SPEED", 1.0),
            tts_timeout=_get_float("TTS_TIMEOUT", 120.0),
            prefer_out_audio=_get_bool("PREFER_OUT_AUDIO", True),
            piper_bin=os.environ.get("PIPER_BIN", "piper"),
            piper_model=os.environ.get("PIPER_MODEL", ""),
            queue_in=Path(os.environ.get("QUEUE_IN", "/opt/data/claude-queue/in")),
            queue_out=Path(os.environ.get("QUEUE_OUT", "/opt/data/claude-queue/out")),
            queue_archive=Path(archive) if archive else None,
            out_poll_interval=_get_float("OUT_POLL_INTERVAL", 1.0),
            out_timeout=_get_float("OUT_TIMEOUT", 180.0),
            out_quiesce=_get_float("OUT_QUIESCE", 0.5),
            audio_tmp_dir=Path(os.environ.get("AUDIO_TMP_DIR", "/tmp/hermes-voice-bot")),
            keep_audio=_get_bool("KEEP_AUDIO", False),
            health_host=os.environ.get("HEALTH_HOST", "0.0.0.0"),
            health_port=_get_int("HEALTH_PORT", 8080),
            health_require_stt=_get_bool("HEALTH_REQUIRE_STT", False),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )
