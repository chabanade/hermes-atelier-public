#!/usr/bin/env python3
"""
Faux TTS pour les tests hors-ligne (Piper absent du dev).

Respecte le MÊME contrat que synth.py (texte sur stdin, WAV à --out, JSON sur
stdout, et le mode --serve persistant) mais génère un bip sinusoïdal déterministe
au lieu d'appeler Piper. server.py le branche via TTS_SCRIPT lors des tests. La
durée du WAV est proportionnelle à la longueur du texte (pour vérifier que la
lecture dure).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import wave

RATE = 22050  # débit typique d'une voix Piper « medium » (peu importe : server.py rééchantillonne)


def write_beep(text: str, out: str) -> dict:
    # Durée ~ longueur du texte, bornée à [0.4s, 3s] — assez pour une lecture audible.
    dur = max(0.4, min(3.0, 0.06 * len(text)))
    n = int(RATE * dur)
    freq = 330.0
    print(f"[synth_fake] {len(text)} car. -> bip {dur:.1f}s @ {RATE}Hz", file=sys.stderr)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        for i in range(n):
            v = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / RATE))
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    return {"out": out, "rate": RATE, "samples": n, "engine": "fake"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--serve", action="store_true")
    p.add_argument("--out")
    p.add_argument("--voice", default=os.environ.get("PIPER_VOICE", "fake"))
    args = p.parse_args()

    if args.serve:
        print("[synth_fake] mode --serve prêt", file=sys.stderr)
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = None
            if not isinstance(obj, dict) or not obj.get("text") or not obj.get("out"):
                result = {"error": f"requête invalide : {raw[:120]!r}"}
            else:
                result = write_beep(obj["text"], obj["out"])
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return 0

    if not args.out:
        json.dump({"error": "--out requis en one-shot"}, sys.stdout)
        return 2
    text = sys.stdin.read().strip()
    if not text:
        json.dump({"error": "texte vide"}, sys.stdout)
        return 2
    json.dump(write_beep(text, args.out), sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
