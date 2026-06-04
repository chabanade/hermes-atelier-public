"""Synthèse vocale (Text-To-Speech) + mise au format Telegram (OGG/Opus).

Backends disponibles :
  - "speaches" : POST /v1/audio/speech (compatible OpenAI) → modèle Kokoro/Piper
                 hébergé par Speaches. C'est le défaut (100 % local).
  - "piper"    : binaire Piper local (utile si Hermès a déjà ses voix Piper).
  - "none"     : pas de synthèse (le bot répond en texte, ou réutilise l'audio
                 éventuellement déposé par Hermès dans out/).

Quel que soit le backend, la sortie est **toujours** convertie en OGG/Opus mono
48 kHz via ffmpeg : c'est le seul format accepté par l'API `sendVoice` de Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import httpx

logger = logging.getLogger("voice_bot.tts")

# Paramètres d'encodage Opus adaptés à la voix (faible débit, mono, large bande).
_OPUS_ARGS = [
    "-c:a", "libopus",
    "-b:a", "32k",
    "-ar", "48000",
    "-ac", "1",
    "-application", "voip",
]


class TTSError(RuntimeError):
    """Échec de synthèse vocale ou de conversion audio."""


class TextToSpeech:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.backend = cfg.tts_backend

    # --- API publique ---------------------------------------------------------

    async def synthesize_to_ogg(self, text: str, out_path: Path) -> Path | None:
        """Synthétise `text` et écrit un OGG/Opus dans `out_path`.

        Renvoie le chemin, ou None si le backend est "none" / le texte est vide.
        """
        if self.backend == "none" or not text.strip():
            return None
        if self.backend == "speaches":
            wav = await self._speaches_wav(text)
        elif self.backend == "piper":
            wav = await self._piper_wav(text)
        else:
            raise TTSError(f"TTS_BACKEND inconnu : {self.backend!r}")
        await self._run_ffmpeg(["-i", "pipe:0"], out_path, stdin_bytes=wav)
        return out_path

    async def convert_to_ogg(self, src_path: Path, out_path: Path) -> Path:
        """Convertit un audio quelconque (wav/mp3/ogg…) en OGG/Opus Telegram.

        Sert à réutiliser l'audio déposé par Hermès dans out/ en garantissant
        sa compatibilité avec sendVoice.
        """
        await self._run_ffmpeg(["-i", str(src_path)], out_path)
        return out_path

    # --- Backends de synthèse -------------------------------------------------

    async def _speaches_wav(self, text: str) -> bytes:
        url = f"{self.cfg.tts_base_url}/v1/audio/speech"
        headers = (
            {"Authorization": f"Bearer {self.cfg.tts_api_key}"}
            if self.cfg.tts_api_key else {}
        )
        body = {
            "model": self.cfg.tts_model,
            "input": text,
            "voice": self.cfg.tts_voice,
            "response_format": "wav",  # WAV : décodable partout, on convertit ensuite
            "speed": self.cfg.tts_speed,
        }
        try:
            async with httpx.AsyncClient(timeout=self.cfg.tts_timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TTSError(f"TTS Speaches en erreur : {exc}") from exc
        return resp.content

    async def _piper_wav(self, text: str) -> bytes:
        if not self.cfg.piper_model:
            raise TTSError("PIPER_MODEL non défini (requis pour TTS_BACKEND=piper).")
        # Piper lit le texte sur stdin et écrit un WAV sur stdout (--output_file -).
        proc = await asyncio.create_subprocess_exec(
            self.cfg.piper_bin, "--model", self.cfg.piper_model, "--output_file", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(text.encode("utf-8"))
        if proc.returncode != 0:
            raise TTSError(f"piper a échoué : {err.decode('utf-8', 'replace')[:300]}")
        return out

    # --- Conversion audio (ffmpeg) -------------------------------------------

    async def _run_ffmpeg(self, input_args, out_path: Path, stdin_bytes: bytes | None = None) -> None:
        if shutil.which("ffmpeg") is None:
            raise TTSError("ffmpeg introuvable : impossible de produire l'OGG/Opus.")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *input_args, *_OPUS_ARGS, "-f", "ogg", str(out_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate(stdin_bytes)
        if proc.returncode != 0:
            raise TTSError(f"ffmpeg a échoué : {err.decode('utf-8', 'replace')[:300]}")
