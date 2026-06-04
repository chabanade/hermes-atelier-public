#!/usr/bin/env python3
"""
Faux transcripteur pour les tests hors-ligne (faster-whisper absent du dev).

Respecte le MÊME contrat que transcribe.py (mêmes arguments, JSON sur stdout, et
le mode --serve persistant) mais renvoie un texte canné au lieu d'appeler le
modèle. server.py le branche via la variable d'env WHISPER_SCRIPT lors des tests.
Le texte peut être imposé par la variable FAKE_TEXT pour des assertions
déterministes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import wave


def measure_duration(audio: str) -> float:
    try:  # mesure réelle de la durée si c'est un WAV — sinon on s'en passe
        with wave.open(audio, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:  # noqa: BLE001
        return 0.0


def result_for(audio: str, language: str) -> dict:
    text = os.environ.get("FAKE_TEXT", "ceci est un test de transcription")
    print(f"[transcribe_fake] {audio} -> {text!r}", file=sys.stderr)
    return {"text": text, "language": language, "duration": measure_duration(audio),
            "model": "fake"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("audio", nargs="?")
    p.add_argument("--serve", action="store_true")
    p.add_argument("--model", default="fake")
    p.add_argument("--language", default="fr")
    p.add_argument("--device", default="cpu")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--beam-size", type=int, default=1)
    args = p.parse_args()

    if args.serve:
        print("[transcribe_fake] mode --serve prêt", file=sys.stderr)
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            audio = raw
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    audio = obj.get("audio") or obj.get("path") or ""
                elif isinstance(obj, str):
                    audio = obj
            except json.JSONDecodeError:
                pass
            sys.stdout.write(json.dumps(result_for(audio, args.language),
                                        ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return 0

    json.dump(result_for(args.audio, args.language), sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
