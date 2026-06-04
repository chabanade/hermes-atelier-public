#!/usr/bin/env python3
"""
Synthèse vocale (TTS) autonome via Piper — appelée en sous-processus par server.py.

Symétrique de transcribe.py : la synthèse onnx est sortie de la boucle asyncio
dans un PROCESSUS isolé. server.py invoque ce script avec l'interpréteur configuré
(cf. TTS_PYTHON ; par défaut le .venv local où Piper est dispo), ce qui évite de
charger onnx dans la boucle et permet de tout tester hors-ligne avec un faux TTS
(cf. tests/synth_fake.py).

DEUX MODES :
  • one-shot (défaut) : un texte (stdin) → un WAV (--out) → un JSON → on sort.
    Invoque Piper en sous-processus (`python -m piper`). Pratique en essai manuel.
  • --serve (utilisé par server.py) : on charge la VOIX .onnx UNE fois (API Python
    PiperVoice) puis on boucle, une requête JSON par ligne sur STDIN → un WAV +
    un résultat JSON par ligne sur STDOUT. La voix reste CHAUDE entre les phrases
    (plus de rechargement ~1,5 s/tour). Cf. RAPPORT-LENTEUR.md (Action 2).

Contrat d'E/S (stable, car server.py en dépend) :
  • one-shot — Entrée : le texte sur STDIN ; --out (WAV) ; --voice (.onnx).
  • --serve  — Entrée : une requête JSON par ligne sur STDIN : {"text": ..., "out": ...}.
  • Action : écrit un WAV à la sortie demandée.
  • Sortie : sur STDOUT, un objet JSON (one-shot : unique ; serve : un par ligne)
               {"out": str, "rate": int, "samples": int, "engine": "piper"}
             ou, en cas d'échec, {"error": str} (one-shot : code de sortie non nul ;
             serve : la ligne d'erreur, puis on continue à servir).
  • Logs   : tout sur STDERR (STDOUT réservé au JSON).

Usage :
  echo "Bonjour" | python synth.py --out /tmp/r.wav --voice models/piper/fr_FR-upmc-medium.onnx
  python synth.py --serve --voice models/piper/fr_FR-upmc-medium.onnx
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import wave


def log(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def run_piper(text: str, out: str, voice: str) -> None:
    """one-shot : invoque Piper en SOUS-PROCESSUS. Essaie `python -m piper`
    (cf. configurer-piper-tts.sh), puis, à défaut, l'exécutable `piper` sur le PATH.
    Recharge la voix à chaque appel — réservé au one-shot ; le mode --serve garde
    la voix chaude via l'API Python (cf. PersistentVoice)."""
    attempts = [
        [sys.executable, "-m", "piper", "--model", voice, "--output_file", out],
        ["piper", "--model", voice, "--output_file", out],
    ]
    last = None
    for cmd in attempts:
        try:
            log(f"[synth] {' '.join(cmd)}")
            proc = subprocess.run(
                cmd, input=text.encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            last = exc
            continue
        if proc.returncode == 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            if err:
                log(f"[synth] piper: {err.splitlines()[-1]}")
            return
        last = RuntimeError(
            f"piper code {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:200]}"
        )
    raise RuntimeError(f"Piper indisponible ({last})")


def synth_to_wav_persistent(voice_obj, text: str, out: str) -> dict:
    """--serve : synthétise avec une VOIX déjà chargée (API Python Piper). Renvoie
    le dict de résultat (ou {"error": ...}). Ne lève pas : une phrase ratée ne doit
    pas tuer le worker."""
    try:
        t0 = time.monotonic()
        with wave.open(out, "wb") as w:
            voice_obj.synthesize_wav(text, w)
        with wave.open(out, "rb") as w:
            rate = w.getframerate()
            samples = w.getnframes()
        log(f"[synth] fait en {time.monotonic() - t0:.2f}s — "
            f"{samples} échantillons @ {rate} Hz ({samples / max(rate, 1):.1f}s)")
        return {"out": out, "rate": rate, "samples": samples, "engine": "piper"}
    except Exception as exc:  # noqa: BLE001
        log(f"[synth] ERREUR : {exc}")
        return {"error": str(exc)}


def serve(voice_path: str) -> int:
    """Boucle persistante : charge la voix UNE fois puis traite les requêtes JSON
    ligne par ligne sur STDIN. EOF ⇒ sortie propre. La voix reste chaude."""
    try:
        from piper import PiperVoice
    except Exception as exc:  # noqa: BLE001
        json.dump({"error": f"piper (API Python) indisponible : {exc}"}, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 3
    log(f"[synth] chargement de la voix '{voice_path}'…")
    t0 = time.monotonic()
    voice_obj = PiperVoice.load(voice_path)
    log(f"[synth] voix chargée en {time.monotonic() - t0:.1f}s — mode --serve prêt.")

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if not isinstance(obj, dict) or not obj.get("text") or not obj.get("out"):
            result = {"error": f"requête invalide (attendu {{text, out}}) : {raw[:120]!r}"}
        else:
            result = synth_to_wav_persistent(voice_obj, obj["text"], obj["out"])
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    log("[synth] STDIN fermé — arrêt du worker.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="TTS Piper -> WAV + JSON sur stdout")
    p.add_argument("--serve", action="store_true",
                   help="mode persistant : voix chaude, requêtes JSON par ligne")
    p.add_argument("--out", help="fichier WAV de sortie (one-shot)")
    p.add_argument("--voice", default=os.environ.get("PIPER_VOICE", ""),
                   help="modèle de voix .onnx")
    args = p.parse_args()

    if not args.voice or not os.path.exists(args.voice):
        json.dump({"error": f"voix introuvable : {args.voice!r}"}, sys.stdout)
        return 2

    if args.serve:
        return serve(args.voice)

    # one-shot
    if not args.out:
        json.dump({"error": "--out requis en one-shot"}, sys.stdout)
        return 2
    text = sys.stdin.read().strip()
    if not text:
        json.dump({"error": "texte vide"}, sys.stdout)
        return 2
    try:
        t0 = time.monotonic()
        run_piper(text, args.out, args.voice)
        with wave.open(args.out, "rb") as w:
            rate = w.getframerate()
            samples = w.getnframes()
        log(f"[synth] fait en {time.monotonic() - t0:.1f}s — "
            f"{samples} échantillons @ {rate} Hz ({samples / max(rate, 1):.1f}s)")
        json.dump({"out": args.out, "rate": rate, "samples": samples, "engine": "piper"},
                  sys.stdout, ensure_ascii=False)
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"[synth] ERREUR : {exc}")
        json.dump({"error": str(exc)}, sys.stdout, ensure_ascii=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
