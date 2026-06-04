#!/usr/bin/env python3
"""
Transcription STT autonome via faster-whisper — appelé en sous-processus.

Pourquoi un script séparé plutôt qu'un import dans server.py ?
  Le calcul STT est lourd et bloquant ; on le sort de la boucle asyncio en le
  lançant dans un PROCESSUS isolé. server.py invoque ce script avec l'interpréteur
  configuré (cf. WHISPER_PYTHON ; par défaut le .venv local qui porte
  faster-whisper), ce qui évite de charger ctranslate2 dans la boucle et permet
  de tout tester hors-ligne avec un faux STT (cf. tests/transcribe_fake.py).

DEUX MODES :
  • one-shot (défaut) : un fichier → un JSON → on sort. Pratique pour le préflight
    et les essais manuels.
  • --serve (utilisé par server.py) : on charge le modèle UNE fois puis on boucle,
    une requête JSON par ligne sur STDIN → un résultat JSON par ligne sur STDOUT.
    Le modèle reste CHAUD entre les phrases (plus de rechargement ~1 s/tour). Cf.
    « moteurs persistants » dans RAPPORT-LENTEUR.md (Action 2).

Contrat d'E/S (stable, car server.py en dépend) :
  • one-shot — Entrée : un chemin de fichier audio (idéalement WAV 16 kHz mono,
              mais faster-whisper décode aussi d'autres formats via PyAV).
  • --serve  — Entrée : une requête JSON par ligne sur STDIN, soit {"audio": chemin}
              soit un simple chemin nu ; champs optionnels {"language","beam_size"}.
  • Sortie  : sur STDOUT, un objet JSON (one-shot : unique ; serve : un par ligne)
                {"text": str, "language": str, "duration": float, "model": str}
              ou, en cas d'échec, {"error": str} (one-shot : code de sortie non nul ;
              serve : la ligne d'erreur, puis on continue à servir).
  • Logs    : tout va sur STDERR pour ne jamais polluer le JSON de STDOUT.

Usage :
  python transcribe.py <audio> [--model base] [--language fr] [--beam-size 1]
                       [--device cpu] [--compute-type int8]
  python transcribe.py --serve [--model base] [--language fr] [--beam-size 1]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def log(*a: object) -> None:
    """Trace sur stderr (stdout est réservé au JSON de résultat)."""
    print(*a, file=sys.stderr, flush=True)


def transcribe_one(model, audio: str, language: str | None, beam_size: int,
                   model_name: str) -> dict:
    """Transcrit un fichier avec un modèle DÉJÀ chargé. Renvoie le dict de résultat
    (ou {"error": ...}). Ne lève pas : en mode serve, une phrase ratée ne doit pas
    tuer le worker."""
    if not os.path.exists(audio):
        return {"error": f"fichier introuvable : {audio}"}
    try:
        t0 = time.monotonic()
        # language=None laisserait whisper détecter ; on force la langue demandée.
        segments, info = model.transcribe(audio, language=language, beam_size=beam_size)
        # `segments` est un générateur paresseux : la transcription se fait ici.
        text = "".join(seg.text for seg in segments).strip()
        dt = time.monotonic() - t0
        log(f"[transcribe] fait en {dt:.1f}s — langue={info.language} "
            f"durée_audio={getattr(info, 'duration', 0):.1f}s — {len(text)} car. "
            f"(beam={beam_size})")
        return {
            "text": text,
            "language": info.language,
            "duration": float(getattr(info, "duration", 0.0) or 0.0),
            "model": model_name,
        }
    except Exception as exc:  # noqa: BLE001
        log(f"[transcribe] ERREUR : {exc}")
        return {"error": str(exc)}


def serve(model, language: str | None, beam_size: int, model_name: str) -> int:
    """Boucle persistante : une requête JSON par ligne sur STDIN → un résultat JSON
    par ligne sur STDOUT. EOF (stdin fermé) ⇒ sortie propre. Le modèle reste chaud."""
    log("[transcribe] mode --serve prêt — modèle chaud, en attente de requêtes.")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        # Requête : un objet JSON {"audio": ...[, "language", "beam_size"]} OU un
        # chemin nu (tolérance). Champs optionnels surchargent les défauts du run.
        audio = None
        req_lang, req_beam = language, beam_size
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                audio = obj.get("audio") or obj.get("path")
                if obj.get("language") is not None:
                    req_lang = obj["language"] or None
                if obj.get("beam_size") is not None:
                    req_beam = int(obj["beam_size"])
            elif isinstance(obj, str):
                audio = obj
        except json.JSONDecodeError:
            audio = raw  # chemin nu non-JSON
        if not audio:
            result = {"error": "requête sans champ 'audio'"}
        else:
            result = transcribe_one(model, audio, req_lang, req_beam, model_name)
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    log("[transcribe] STDIN fermé — arrêt du worker.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="STT faster-whisper -> JSON sur stdout")
    parser.add_argument("audio", nargs="?", help="fichier audio à transcrire (one-shot)")
    parser.add_argument("--serve", action="store_true",
                        help="mode persistant : modèle chaud, requêtes JSON par ligne")
    parser.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "base"))
    parser.add_argument("--language", default=os.environ.get("WHISPER_LANGUAGE", "fr"))
    parser.add_argument("--device", default=os.environ.get("WHISPER_DEVICE", "cpu"))
    parser.add_argument(
        "--compute-type", default=os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
    )
    # beam_size 1 par défaut : mesuré -0,2 s vs 5, texte identique en français
    # (cf. RAPPORT-LENTEUR.md, Action 3).
    parser.add_argument(
        "--beam-size", type=int, default=int(os.environ.get("WHISPER_BEAM_SIZE", "1"))
    )
    args = parser.parse_args()

    if not args.serve and not args.audio:
        json.dump({"error": "aucun fichier audio (et --serve absent)"}, sys.stdout)
        return 2
    if not args.serve and not os.path.exists(args.audio):
        json.dump({"error": f"fichier introuvable : {args.audio}"}, sys.stdout)
        return 2

    try:
        # Import tardif : ne coûte rien tant qu'on n'a pas validé les arguments,
        # et permet à --help de fonctionner même sans faster-whisper installé.
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        json.dump({"error": f"faster-whisper indisponible : {exc}"}, sys.stdout)
        return 3

    log(f"[transcribe] chargement du modèle '{args.model}' "
        f"({args.device}/{args.compute_type})…")
    t0 = time.monotonic()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    log(f"[transcribe] modèle chargé en {time.monotonic() - t0:.1f}s")

    lang = args.language or None

    if args.serve:
        return serve(model, lang, args.beam_size, args.model)

    # one-shot
    result = transcribe_one(model, args.audio, lang, args.beam_size, args.model)
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
