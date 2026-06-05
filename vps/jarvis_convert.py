#!/usr/bin/env python3
"""
jarvis_convert.py — convertit un WAV (voix Piper FR) vers le TIMBRE de JARVIS (RVC).

Appelé en sous-processus par le bot vocal quand Mehdi est en « mode JARVIS ».
Lent sur CPU (~15-25 s avec le chargement), donc invoqué A LA DEMANDE seulement.

Usage : <python du venv rvc-jarvis> jarvis_convert.py <entree.wav> <sortie.wav>

Notes techniques (chèrement acquises) :
- Python 3.10 + pip tolérant (rvc-python a des deps anciennes mal formées).
- torch.load patché en weights_only=False : sinon PyTorch >=2.6 casse le chargement
  du modèle hubert (format fairseq, source fiable).
- rvc-python 0.1.5 a un bug d'écriture : on appelle vc_single() et on extrait
  nous-mêmes le tableau audio du tuple renvoyé.
"""
import sys
import numpy as np
import torch

# PyTorch >= 2.6 met weights_only=True par défaut -> casse hubert (fairseq). On force False.
_orig_load = torch.load
torch.load = lambda *a, **k: _orig_load(*a, **{**k, "weights_only": False})

from scipy.io import wavfile  # noqa: E402
from rvc_python.infer import RVCInference  # noqa: E402

D = "/home/ouvrier/travaux/rvc-jarvis"
MODEL = D + "/model/jarvis_test.pth"
INDEX = D + "/model/added_IVF409_Flat_nprobe_1_jarvis_test_v2.index"


def _find_audio(x):
    if isinstance(x, np.ndarray):
        return x
    if isinstance(x, (tuple, list)):
        for e in x:
            a = _find_audio(e)
            if a is not None:
                return a
    return None


def _find_sr(x):
    if isinstance(x, (tuple, list)):
        for e in x:
            if isinstance(e, int) and 8000 <= e <= 48000:
                return e
    return None


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: jarvis_convert.py <in.wav> <out.wav>\n")
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    rvc = RVCInference(models_dir=D + "/rvc_models", device="cpu:0", version="v2")
    try:
        rvc.load_model(MODEL, index_path=INDEX)
    except TypeError:
        rvc.load_model(MODEL)
    idx = rvc.models[rvc.current_model].get("index", "")
    res = rvc.vc.vc_single(
        sid=0, input_audio_path=src,
        f0_up_key=rvc.f0up_key, f0_method=rvc.f0method, file_index=idx,
        index_rate=rvc.index_rate, filter_radius=rvc.filter_radius,
        resample_sr=rvc.resample_sr, rms_mix_rate=rvc.rms_mix_rate,
        protect=rvc.protect, f0_file="", file_index2="",
    )
    audio = _find_audio(res)
    sr = _find_sr(res) or rvc.vc.tgt_sr
    if audio is None:
        sys.stderr.write("RVC: pas d'audio en sortie (%s)\n" % (repr(res)[:200],))
        return 1
    wavfile.write(dst, sr, audio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
