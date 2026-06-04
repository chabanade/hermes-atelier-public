"""Client de transcription (Speech-To-Text) compatible API OpenAI.

Par défaut on parle à **Speaches** (faster-whisper `large-v3-turbo`), 100 % local.
Le même client fonctionne avec Groq ou OpenAI en changeant simplement
STT_BASE_URL / STT_API_KEY / STT_MODEL (utile comme secours si le VPS sature).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger("voice_bot.stt")


class STTError(RuntimeError):
    """Échec de transcription : service injoignable, modèle non chargé, etc."""


class SpeechToText:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        language: str = "fr",
        timeout: float = 120.0,
        vad_filter: bool = True,
    ) -> None:
        self._url = f"{base_url}/v1/audio/transcriptions"
        self._model = model
        self._language = language
        self._timeout = timeout
        self._vad_filter = vad_filter
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def transcribe(self, audio_path: Path) -> str:
        """Transcrit un fichier audio (OGG/Opus) et renvoie le texte (FR)."""
        data = {
            "model": self._model,
            "language": self._language,
            "response_format": "json",
        }
        # Le VAD coupe les silences : transcription plus propre et plus rapide.
        # (Spécifique à Speaches/faster-whisper — désactive-le pour Groq/OpenAI.)
        if self._vad_filter:
            data["vad_filter"] = "true"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                with audio_path.open("rb") as fh:
                    files = {"file": (audio_path.name, fh, "audio/ogg")}
                    resp = await client.post(
                        self._url, data=data, files=files, headers=self._headers
                    )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise STTError(f"STT injoignable ou en erreur : {exc}") from exc

        # response_format=json → {"text": "..."} ; on tolère aussi du texte brut.
        try:
            payload = resp.json()
            text = payload.get("text", "") if isinstance(payload, dict) else str(payload)
        except ValueError:
            text = resp.text
        return (text or "").strip()
