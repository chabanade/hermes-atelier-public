#!/usr/bin/env python3
"""
Test E2E « vraie voix » (hors WebRTC) : Piper RÉEL → Whisper RÉEL → inbox.

But : prouver, sans micro ni navigateur, que la chaîne STT/TTS **locale**
(faster-whisper + Piper dans `.venv`, voix `.onnx`, modèle whisper en cache)
fonctionne réellement de bout en bout — ce que les FAUX binaires du test
hors-ligne (`test_conversation.py`) ne couvrent pas.

Étapes, en appelant les VRAIES fonctions de server.py :
  1. synth_async(phrase)   → WAV via Piper (synth.py réel)
  2. transcribe_async(WAV) → texte via faster-whisper (transcribe.py réel)
  3. comparaison texte transcrit ↔ phrase d'origine (similarité tolérante)
  4. write_inbox(...)      → inbox/<ex>.json (status "pending")     [assert]

Tourne HORS-LIGNE (HF_HUB_OFFLINE=1) : prouve que le modèle whisper en cache
(models/hf) suffit, sans aucun accès réseau à Hugging Face.

Lancer :  .venv/bin/python tests/test_real_voice.py
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent


def normalize(s: str) -> str:
    """Minuscule + sans accents/ponctuation : compare le SENS, pas la forme."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="webrtc-realvoice-"))
    data_dir = tmp / "data"
    # Config RÉELLE : on ne surcharge NI les scripts (transcribe.py/synth.py réels),
    # NI les interpréteurs/voix (défauts = venv local + models/). On force juste le
    # data dir temporaire et le mode hors-ligne HF.
    os.environ["HF_HOME"] = str(PROJECT / "models" / "hf")
    os.environ["HF_HUB_OFFLINE"] = "1"          # cache seul, zéro réseau HF
    os.environ["WEBRTC_DATA_DIR"] = str(data_dir)
    os.environ["WEBRTC_TMP_DIR"] = str(tmp)

    sys.path.insert(0, str(PROJECT))
    import server  # noqa: E402  (après config env, lue au niveau module)

    phrase = "Bonjour, je teste la reconnaissance vocale en français."
    wav = tmp / "voice.wav"

    print(f"• phrase d'origine : {phrase!r}")
    print(f"• Piper   : voix={Path(server.PIPER_VOICE).name}  via {server.TTS_PYTHON}")
    print(f"• Whisper : modèle={server.WHISPER_MODEL}  via {server.WHISPER_PYTHON}"
          f"  (HF_HUB_OFFLINE={os.environ['HF_HUB_OFFLINE']})")

    rc = 1
    try:
        # 1. TTS RÉEL : Piper synthétise la phrase en WAV.
        synth = await server.synth_async(phrase, wav)
        assert wav.exists() and wav.stat().st_size > 0, "Piper n'a produit aucun WAV"
        print(f"  ✓ Piper → {wav.name} : {synth['samples']} échantillons @ {synth['rate']} Hz "
              f"({synth['samples'] / max(synth['rate'], 1):.1f}s)")

        # 2. STT RÉEL : faster-whisper transcrit ce WAV.
        result = await server.transcribe_async(wav)
        text = (result.get("text") or "").strip()
        print(f"  ✓ Whisper → {text!r}  (langue={result.get('language')}, "
              f"durée={result.get('duration')}s)")
        assert text, "transcription VIDE (la chaîne TTS→STT n'a rien rendu)"

        # 3. Comparaison tolérante : TTS+STT n'est jamais exact au caractère près.
        ratio = difflib.SequenceMatcher(None, normalize(phrase), normalize(text)).ratio()
        print(f"  ✓ similarité phrase ↔ transcription = {ratio:.2f}")
        assert ratio >= 0.5, f"transcription trop éloignée ({ratio:.2f}) : {text!r}"

        # 4. inbox : EXACTEMENT le dépôt que fait server.py pour Hermès.
        ex_id = "realvoice-t001"
        server.write_inbox(ex_id, text, result, conversation="realvoice", turn=1)
        inbox = data_dir / "inbox" / f"{ex_id}.json"
        assert inbox.exists(), f"inbox manquante : {inbox}"
        rec = json.loads(inbox.read_text(encoding="utf-8"))
        assert rec["status"] == "pending" and rec["text"] == text, f"inbox incohérente : {rec}"
        print(f"  ✓ inbox → {inbox.name}  status={rec['status']}  text={rec['text']!r}")

        print("\n✅ E2E VRAIE VOIX VALIDÉE : Piper → Whisper → inbox (chaîne LOCALE réelle, hors-ligne)")
        rc = 0
    except AssertionError as exc:
        print(f"\n❌ ÉCHEC : {exc}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"\n❌ ERREUR : {exc}")
    finally:
        # Arrête les workers persistants PENDANT que la boucle tourne encore
        # (sinon leurs finaliseurs s'exécutent après asyncio.run → "Event loop is
        # closed", bruit inoffensif mais trompeur).
        await asyncio.gather(server._stt_worker.stop(), server._tts_worker.stop(),
                             return_exceptions=True)
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
