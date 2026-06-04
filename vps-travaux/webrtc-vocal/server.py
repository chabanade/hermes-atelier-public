"""
Serveur WebRTC vocal — Brique 3/3 : conversation naturelle (« comme un appel »).

Différence avec la brique 2 (talkie-walkie : connecte → parle → raccroche →
transcrit → réponse texte) : ici la connexion WebRTC reste OUVERTE pendant toute
la conversation. Mehdi parle, Hermès répond EN AUDIO, Mehdi répond, etc. — mains
libres, zéro clic, le silence ne coûte rien.

Boucle d'un tour de parole, entièrement pilotée côté serveur :

    micro navigateur ──WebRTC (piste entrante, continue)──▶ consume_audio()
        │  rééchantillonne 48 kHz → 16 kHz mono
        │  VAD (silero ▸ webrtcvad ▸ énergie) : parle / se tait ?
        │  endpointing : silence > 800 ms après la parole ⇒ fin de phrase
        ▼
    handle_turn(audio)
        1. WAV 16 kHz                                   (/tmp/webrtc_<ex>.wav)
        2. STT faster-whisper (worker persistant)       → texte
        3. inbox/<ex>.json  (status "pending")          ── le cron Hermès lit,
                                                            réfléchit, écrit ──┐
        4. attend outbox/<ex>.json (réponse d'Hermès)   ◀───────────────────┘
        5. TTS Piper (worker persistant)                → WAV
        6. rééchantillonne → piste SORTANTE WebRTC      → joué dans l'oreillette
        7. pendant la lecture : VAD coupé (pas de self-écoute) ; rétabli après.

Découplage (comme la brique 2) : faster-whisper ET Piper vivent dans le venv
LOCAL (`.venv/`, qui les porte). On les appelle dans des PROCESSUS isolés
(transcribe.py / synth.py) avec cet interpréteur, ce qui évite de charger
ctranslate2/onnx dans la boucle asyncio et permet de TOUT tester hors-ligne avec
de faux binaires (cf. tests/). Ces processus sont PERSISTANTS (mode --serve) : le
modèle est chargé UNE fois au démarrage et reste chaud entre les phrases (cf.
PersistentWorker — Action 2 de RAPPORT-LENTEUR.md, ~2,5 s gagnées/tour). Le VAD,
lui, tourne dans cette boucle (temps réel, local, sans GPU) — backend choisi
automatiquement.

Pont fichiers Hermès : chaque TOUR de parole = un « échange » avec son propre
identifiant `<session>-t<NNN>` ⇒ une paire inbox/outbox par tour, sans collision
sur une conversation longue. Le contrat de fichiers reste celui de la brique 2
(un id ↔ un JSON ; status "pending" en entrée, champ `reply` en sortie).

Écoute en clair sur 0.0.0.0:8686 (HTTP — debug local + reverse proxy) et, dès
qu'un certificat est disponible, AUSSI en HTTPS natif sur 0.0.0.0:8443 dans le
même process. C'est le 8443 (HTTPS) que l'iPhone de Mehdi atteint à travers le
firewall : getUserMedia() impose un contexte sécurisé, donc le port public doit
parler TLS. Sans certificat, seul le HTTP 8686 tourne (TLS alors délégué à un
reverse proxy type Caddy).
"""

import argparse
import asyncio
import fractions
import json
import logging
import os
import re
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path

import av
import numpy as np
from av.audio.resampler import AudioResampler
from aiohttp import web
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError

ROOT = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Configuration (tout est surchargé par l'environnement -> dev sans /opt,
# prod sur le VPS, et tests avec de faux binaires STT/TTS).
# --------------------------------------------------------------------------- #
# Cache du modèle faster-whisper : par défaut sous ce dossier (persistant), pour
# ne pas re-télécharger à chaque run et rester self-contained. Posé AVANT le
# lancement des sous-processus STT, qui en héritent.
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hf"))

DATA_DIR = Path(os.environ.get("WEBRTC_DATA_DIR", str(ROOT / "data")))
INBOX_DIR = DATA_DIR / "inbox"
OUTBOX_DIR = DATA_DIR / "outbox"
AUDIO_TMP_DIR = Path(os.environ.get("WEBRTC_TMP_DIR", "/tmp"))

# STT/TTS : par défaut le venv LOCAL (qui porte faster-whisper et piper). Tout
# est self-contained dans ce dossier — aucune dépendance à un venv externe.
WHISPER_PYTHON = os.environ.get("WHISPER_PYTHON", str(ROOT / ".venv" / "bin" / "python"))
WHISPER_SCRIPT = Path(os.environ.get("WHISPER_SCRIPT", str(ROOT / "transcribe.py")))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "fr")
WHISPER_CONCURRENCY = int(os.environ.get("WHISPER_CONCURRENCY", "1"))
# beam_size 1 par défaut : mesuré -0,2 s vs 5, texte identique en français
# (cf. RAPPORT-LENTEUR.md, Action 3).
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "1"))
# Timeout d'une requête au worker STT persistant (s). Garde-fou anti-blocage : si
# le worker pend, on le recycle plutôt que de figer le tour de parole.
WHISPER_TIMEOUT_S = float(os.environ.get("WHISPER_TIMEOUT_S", "120"))

# TTS : interpréteur Piper + script (même venv local que whisper).
TTS_PYTHON = os.environ.get("TTS_PYTHON", str(ROOT / ".venv" / "bin" / "python"))
TTS_SCRIPT = Path(os.environ.get("TTS_SCRIPT", str(ROOT / "synth.py")))
PIPER_VOICE = os.environ.get(
    "PIPER_VOICE", str(ROOT / "models" / "piper" / "fr_FR-upmc-medium.onnx")
)
TTS_CONCURRENCY = int(os.environ.get("TTS_CONCURRENCY", "1"))
# Timeout d'une requête au worker TTS persistant (s). Cf. WHISPER_TIMEOUT_S.
TTS_TIMEOUT_S = float(os.environ.get("TTS_TIMEOUT_S", "60"))

# VAD : backend ("auto" essaie silero -> webrtcvad -> énergie) et réglages.
VAD_BACKEND = os.environ.get("WEBRTC_VAD", "auto").lower()
VAD_AGGRESSIVENESS = int(os.environ.get("VAD_AGGRESSIVENESS", "2"))   # webrtcvad 0..3
VAD_FRAME_MS = int(os.environ.get("VAD_FRAME_MS", "30"))             # 10/20/30 (webrtcvad)
ENERGY_THRESHOLD = float(os.environ.get("VAD_ENERGY_THRESHOLD", "500"))  # RMS int16
SILERO_THRESHOLD = float(os.environ.get("VAD_SILERO_THRESHOLD", "0.5"))

# Endpointing (détection de fin de phrase) et tour de parole.
SAMPLE_RATE = 16000                                                  # le STT veut du 16k
END_SILENCE_MS = int(os.environ.get("END_SILENCE_MS", "600"))        # silence -> fin de phrase (Action 3 : 800->600, conversation plus vive)
MIN_SPEECH_MS = int(os.environ.get("MIN_SPEECH_MS", "250"))          # sinon = bruit, ignoré
PREROLL_MS = int(os.environ.get("PREROLL_MS", "300"))                # audio gardé avant l'attaque
MAX_UTTERANCE_MS = int(os.environ.get("MAX_UTTERANCE_MS", "20000"))  # garde-fou anti-bruit continu
TAIL_SILENCE_MS = int(os.environ.get("TAIL_SILENCE_MS", "250"))      # pause après la voix d'Hermès
HERMES_TIMEOUT_S = float(os.environ.get("HERMES_TIMEOUT_S", "60"))   # attente max de la réponse

# Piste sortante (voix d'Hermès) : 48 kHz mono, trames de 20 ms.
OUT_RATE = 48000
OUT_FRAME_MS = 20

# --------------------------------------------------------------------------- #
# ICE / STUN — traversée de NAT.
# Sans STUN, chaque pair n'annonce que ses IP d'interface (« host ») : l'iPhone
# de Mehdi en 4G/Wi-Fi derrière une box n'offre qu'une IP PRIVÉE (192.168.x.x),
# que le VPS ne peut JOINDRE. STUN fait découvrir au navigateur son adresse
# PUBLIQUE vue d'Internet (candidat « srflx », server-reflexive) → la paire
# VPS↔iPhone devient routable. On configure les DEUX côtés (le serveur reçoit
# la même liste via /ice) : côté client c'est vital (il est derrière NAT), côté
# serveur c'est une ceinture-bretelles (utile si le VPS passe un jour en NAT/
# conteneur).  Réglable par l'env :
#   • WEBRTC_ICE_SERVERS : soit une liste d'URL séparées par des virgules/espaces
#     (« stun:host:port turn:host:port »), soit un JSON [{"urls":…,"username":…,
#     "credential":…}, …] pour porter des identifiants TURN. Vide = AUCUN
#     serveur ICE (host-only, comportement d'avant la Phase 2).
#   • WEBRTC_TURN_URL / _USER / _PASS : raccourci pour ajouter UN relais TURN
#     (utile quand STUN ne suffit pas — NAT symétrique, CGNAT strict).
DEFAULT_STUN = "stun:stun.l.google.com:19302"
ICE_SERVERS_ENV = os.environ.get("WEBRTC_ICE_SERVERS", DEFAULT_STUN)
TURN_URL = os.environ.get("WEBRTC_TURN_URL", "").strip()
TURN_USER = os.environ.get("WEBRTC_TURN_USER", "").strip()
TURN_PASS = os.environ.get("WEBRTC_TURN_PASS", "").strip()

# Journalisation : niveau réglable (WEBRTC_LOG_LEVEL=DEBUG pour tout voir).
# aioice loggue déjà les paires de candidats ICE en INFO — précieux pour le
# diagnostic NAT — mais c'est verbeux ; WEBRTC_AIOICE_LOG le borne séparément.
LOG_LEVEL = os.environ.get("WEBRTC_LOG_LEVEL", "INFO").upper()
AIOICE_LOG_LEVEL = os.environ.get("WEBRTC_AIOICE_LOG", "INFO").upper()
# Heartbeat média : période (s) du log « N trames reçues » et seuil (s) au-delà
# duquel on ALERTE « connecté mais 0 trame entrante » (signature d'un échec NAT).
MEDIA_HEARTBEAT_S = float(os.environ.get("WEBRTC_MEDIA_HEARTBEAT_S", "5"))
NO_FRAME_WARN_S = float(os.environ.get("WEBRTC_NO_FRAME_WARN_S", "4"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("webrtc-vocal")
logging.getLogger("aioice").setLevel(getattr(logging, AIOICE_LOG_LEVEL, logging.INFO))


def build_ice_servers() -> list:
    """Construit la liste d'RTCIceServer pour aiortc, depuis l'environnement.

    Accepte WEBRTC_ICE_SERVERS en JSON (objets {urls,username,credential}) OU en
    liste d'URL séparées par virgules/espaces, et ajoute le relais TURN optionnel
    (WEBRTC_TURN_URL/_USER/_PASS). Renvoie [] si tout est vide (mode host-only).
    """
    servers: list = []
    raw = ICE_SERVERS_ENV.strip()
    if raw:
        parsed = None
        if raw[0] in "[{":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                log.warning("WEBRTC_ICE_SERVERS : JSON illisible (%s) — "
                            "interprété comme liste d'URL", exc)
        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    servers.append(RTCIceServer(urls=item))
                elif isinstance(item, dict) and item.get("urls"):
                    servers.append(RTCIceServer(
                        urls=item["urls"],
                        username=item.get("username"),
                        credential=item.get("credential"),
                    ))
        else:  # liste d'URL « a, b c »
            urls = [u for u in re.split(r"[\s,]+", raw) if u]
            servers.extend(RTCIceServer(urls=u) for u in urls)
    if TURN_URL:
        servers.append(RTCIceServer(
            urls=TURN_URL,
            username=TURN_USER or None,
            credential=TURN_PASS or None,
        ))
    return servers


def ice_servers_public(servers: list) -> list:
    """Version JSON-sérialisable de la liste ICE, pour le navigateur (/ice).

    Renvoie des objets {urls, [username], [credential]} consommables tels quels
    par `new RTCPeerConnection({iceServers: …})`.
    """
    out: list = []
    for s in servers:
        urls = s.urls if isinstance(s.urls, list) else [s.urls]
        entry = {"urls": urls}
        if s.username:
            entry["username"] = s.username
        if s.credential:
            entry["credential"] = s.credential
        out.append(entry)
    return out


# Liste ICE construite une fois (mêmes serveurs pour le serveur aiortc et, via
# /ice, pour le navigateur). Recalculée par les tests qui modifient l'env.
ICE_SERVERS = build_ice_servers()

# Connexions actives — pour une fermeture propre à l'arrêt du serveur.
pcs: set[RTCPeerConnection] = set()

# État par session (= par connexion / conversation). Lu par /poll, écrit par la
# boucle audio et l'orchestration. Champs : state, speaking, turn, turns[], ...
sessions: dict[str, dict] = {}

# Compteurs CUMULÉS, à l'échelle du process (survivent à la fin des sessions).
# Exposés par /diag et /health : permettent de répondre, sans toucher au client,
# à « est-ce que de l'audio arrive vraiment au serveur ? » (frames_in_total).
GLOBAL: dict[str, float] = {
    "sessions_total": 0,     # conversations ouvertes depuis le démarrage
    "frames_in_total": 0,    # trames micro reçues, toutes sessions confondues
    "frames_out_total": 0,   # trames de voix d'Hermès émises vers les clients
    "turns_total": 0,        # tours de parole traités (un par phrase détectée)
}


def new_metrics(now: float) -> dict:
    """Compteurs d'UNE session. `frames_in` est LA mesure clé du diagnostic :
    s'il reste à 0 alors que la connexion est « connected », l'audio n'arrive
    pas (NAT/ICE, firewall UDP, ou micro coupé) — c'est tout l'objet du /diag."""
    return {
        "frames_in": 0,          # trames audio brutes reçues du micro (track.recv)
        "frames_in_drained": 0,  # trames reçues pendant qu'Hermès parlait (ignorées)
        "samples_in": 0,         # échantillons 16k produits après rééchantillonnage
        "frames_out": 0,         # trames 20 ms @48k émises (voix d'Hermès)
        "samples_out": 0,
        "vad_frames": 0,         # trames d'analyse soumises au VAD
        "speech_frames": 0,      # ... jugées « voix »
        "recv_timeouts": 0,      # track.recv() a expiré (flux entrant tari)
        "max_rms": 0.0,          # plus fort niveau RMS vu (micro muet ⇒ ~0)
        "started_at": now,
        "first_frame_at": None,  # horloge monotone de la 1ʳᵉ trame entrante
        "last_frame_at": None,   # ... de la dernière (fraîcheur du flux)
        "ice_state": "new",
        "ice_gathering": "new",
        "connection_state": "new",
        "local_candidate_types": [],  # host/srflx/relay vus dans le SDP local
    }

# Bornent l'usage CPU (inférence) sur tout le serveur. Avec les workers PERSISTANTS
# (un process chaud par moteur, qui sérialise déjà ses requêtes via un verrou
# interne), ces sémaphores ne servent plus qu'à plafonner le nombre d'appelants
# simultanés — défaut 1, comportement inchangé.
_whisper_sem = asyncio.Semaphore(WHISPER_CONCURRENCY)
_tts_sem = asyncio.Semaphore(TTS_CONCURRENCY)

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_session(value: object) -> str | None:
    """N'accepte qu'un identifiant simple (anti path-traversal sur inbox/outbox)."""
    if isinstance(value, str) and _SESSION_RE.match(value):
        return value
    return None


def _supervise(task: asyncio.Task, session: str) -> asyncio.Task:
    """Suit une tâche de fond (consume_audio, handle_turn).

    Deux rôles, via add_done_callback :
      • la rattacher à la session pour qu'un teardown puisse l'annuler ;
      • SURTOUT, journaliser FORT toute exception non rattrapée. Sans ça, une
        coroutine de fond qui lève voit son exception disparaître en silence
        (asyncio ne la signale qu'au ramasse-miettes) → on croit le tour de
        parole « en cours » alors qu'il est mort. Ici, ça part en log.error.
    """
    st = sessions.get(session)
    if st is not None:
        st.setdefault("tasks", set()).add(task)

    def _done(t: asyncio.Task) -> None:
        s = sessions.get(session)
        if s is not None:
            s.get("tasks", set()).discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("tâche de fond (session %s) terminée sur exception non "
                      "rattrapée : %r", session[:8], exc, exc_info=exc)

    task.add_done_callback(_done)
    return task


# --------------------------------------------------------------------------- #
# VAD — détection d'activité vocale. Interface commune ; 3 implémentations.
#   • is_speech(frame) : frame = ndarray int16 de longueur frame_samples @16kHz
#   • frame_samples    : taille d'analyse imposée par le backend
# Choix auto : silero (neuronal, meilleur) ▸ webrtcvad (léger) ▸ énergie (toujours
# dispo, déterministe — sert aussi de secours et aux tests hors-ligne).
# --------------------------------------------------------------------------- #
class EnergyVAD:
    """VAD par énergie (RMS). Aucune dépendance hors numpy ; déterministe."""

    name = "energy"

    def __init__(self, threshold: float = ENERGY_THRESHOLD, frame_ms: int = VAD_FRAME_MS):
        self.threshold = threshold
        self.frame_samples = int(SAMPLE_RATE * frame_ms / 1000)

    def is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        return rms >= self.threshold

    def reset(self) -> None:
        pass


class WebRtcVAD:
    """VAD WebRTC (libwebrtc) — classique, robuste, ~0 CPU. Trames 10/20/30 ms."""

    name = "webrtcvad"

    def __init__(self, aggressiveness: int = VAD_AGGRESSIVENESS, frame_ms: int = VAD_FRAME_MS):
        import webrtcvad  # tardif : ne casse pas le serveur si absent

        if frame_ms not in (10, 20, 30):
            frame_ms = 30
        self._vad = webrtcvad.Vad(aggressiveness)
        self.frame_samples = int(SAMPLE_RATE * frame_ms / 1000)

    def is_speech(self, frame: np.ndarray) -> bool:
        return self._vad.is_speech(frame.astype(np.int16).tobytes(), SAMPLE_RATE)

    def reset(self) -> None:
        pass


class SileroVAD:
    """VAD silero (neuronal, ONNX/torch). Fenêtre fixe de 512 échantillons @16k."""

    name = "silero"

    def __init__(self, threshold: float = SILERO_THRESHOLD):
        from silero_vad import load_silero_vad  # tardif : import lourd (torch)
        import torch  # noqa: F401  (utilisé via le modèle)

        self._torch = __import__("torch")
        self._model = load_silero_vad()
        self.threshold = threshold
        self.frame_samples = 512  # imposé par le modèle 16 kHz

    def is_speech(self, frame: np.ndarray) -> bool:
        x = self._torch.from_numpy((frame.astype(np.float32) / 32768.0))
        prob = float(self._model(x, SAMPLE_RATE).item())
        return prob >= self.threshold

    def reset(self) -> None:
        try:
            self._model.reset_states()
        except Exception:  # noqa: BLE001
            pass


def make_vad():
    """Instancie le VAD selon WEBRTC_VAD ; "auto" descend la liste des backends."""
    order = {
        "silero": [SileroVAD],
        "webrtcvad": [WebRtcVAD],
        "energy": [EnergyVAD],
        "auto": [SileroVAD, WebRtcVAD, EnergyVAD],
    }.get(VAD_BACKEND, [SileroVAD, WebRtcVAD, EnergyVAD])

    last_err = None
    for cls in order:
        try:
            vad = cls()
            log.info("VAD: backend «%s» (trame %d échantillons / %d ms)",
                     vad.name, vad.frame_samples, int(1000 * vad.frame_samples / SAMPLE_RATE))
            return vad
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.info("VAD: backend «%s» indisponible (%s)", cls.name, exc)
    # En dernier recours, l'énergie ne dépend que de numpy et ne lève jamais.
    log.warning("VAD: repli forcé sur l'énergie (%s)", last_err)
    return EnergyVAD()


# --------------------------------------------------------------------------- #
# Endpointing — machine à états qui découpe le flux en phrases (« utterances »).
# Alimentée trame par trame (frame_samples @16k). Renvoie l'audio d'une phrase
# quand le silence dépasse END_SILENCE_MS après une parole assez longue.
# --------------------------------------------------------------------------- #
class Endpointer:
    def __init__(self, vad):
        self.vad = vad
        self.frame_samples = vad.frame_samples
        self.frame_ms = 1000.0 * self.frame_samples / SAMPLE_RATE
        self.preroll_max = max(1, int(PREROLL_MS / self.frame_ms))
        self.reset()

    def reset(self) -> None:
        self._preroll: deque[np.ndarray] = deque(maxlen=self.preroll_max)
        self._utt: list[np.ndarray] = []
        self._speaking = False
        self._voiced_ms = 0.0
        self._last_voice_at = 0.0
        try:
            self.vad.reset()
        except Exception:  # noqa: BLE001
            pass

    @property
    def speaking(self) -> bool:
        return self._speaking

    def feed(self, frame: np.ndarray, now: float) -> bool:
        """Ingère une trame d'analyse (longueur frame_samples).

        Renvoie la décision du VAD pour CETTE trame (parole = True), ce qui
        permet à l'appelant de comptabiliser les trames « voix » sans relancer
        le VAD (silero est coûteux).
        """
        sp = self.vad.is_speech(frame)
        if not self._speaking:
            self._preroll.append(frame)
            if sp:  # attaque : on démarre la phrase, préfixée du pré-roll.
                self._speaking = True
                self._utt = list(self._preroll)
                self._preroll.clear()
                self._voiced_ms = self.frame_ms
                self._last_voice_at = now
        else:
            self._utt.append(frame)
            if sp:
                self._voiced_ms += self.frame_ms
                self._last_voice_at = now
        return sp

    def poll(self, now: float) -> np.ndarray | None:
        """Renvoie l'audio de la phrase si elle vient de se terminer, sinon None.

        Fin de phrase = silence ≥ END_SILENCE_MS après le dernier mot, OU phrase
        trop longue (garde-fou). Une phrase trop courte (< MIN_SPEECH_MS) est
        jetée (toux, bruit) : on renvoie None et on se remet à l'écoute.
        """
        if not self._speaking:
            return None
        silence_ms = (now - self._last_voice_at) * 1000.0
        too_long = len(self._utt) * self.frame_ms >= MAX_UTTERANCE_MS
        if silence_ms < END_SILENCE_MS and not too_long:
            return None
        voiced = self._voiced_ms
        utt = self._utt
        self._speaking = False
        self._utt = []
        self._voiced_ms = 0.0
        if voiced < MIN_SPEECH_MS or not utt:
            return None  # bruit transitoire : on ignore
        return np.concatenate(utt).astype(np.int16)


# --------------------------------------------------------------------------- #
# Helpers audio : ndarray int16 16k -> WAV ; n'importe quel WAV -> 48k mono int16
# --------------------------------------------------------------------------- #
def write_wav16k(path: Path, samples: np.ndarray) -> None:
    """Écrit `samples` (int16 mono @16k) en WAV PCM 16-bit — format du STT."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.ascontiguousarray(samples, dtype=np.int16).tobytes())


def wav_to_out_rate(path: Path) -> np.ndarray:
    """Décode un WAV (tout débit/format lisible par PyAV) -> int16 mono @OUT_RATE.

    Fonction bloquante (PyAV) : à lancer via run_in_executor.
    """
    resampler = AudioResampler(format="s16", layout="mono", rate=OUT_RATE)
    chunks: list[np.ndarray] = []
    with av.open(str(path)) as inc:
        for frame in inc.decode(audio=0):
            frame.pts = None
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().reshape(-1))
        for rf in resampler.resample(None):  # flush
            chunks.append(rf.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(chunks).astype(np.int16)


# --------------------------------------------------------------------------- #
# Piste SORTANTE : la voix d'Hermès renvoyée dans l'oreillette.
# Produit en continu des trames de 20 ms @48k : silence quand rien à dire, les
# échantillons TTS quand on en a mis en file. wait_drained() attend la fin de la
# lecture (pour rétablir le VAD ensuite). Cadencée sur l'horloge réelle.
# --------------------------------------------------------------------------- #
class TTSPlaybackTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, metrics: dict | None = None):
        super().__init__()
        self.rate = OUT_RATE
        self.frame_samples = int(OUT_RATE * OUT_FRAME_MS / 1000)  # 960
        self._buf = np.zeros(0, dtype=np.int16)
        self._lock = asyncio.Lock()
        self._pts = 0
        self._start: float | None = None
        self._playing = False
        self._drained = asyncio.Event()
        self._drained.set()
        # Métriques de la session : recv() y compte chaque trame ÉMISE (le pair
        # tire la piste ⇒ le chemin média SORTANT est vivant). None hors session.
        self._metrics = metrics

    async def enqueue(self, samples: np.ndarray) -> None:
        """Met des échantillons (int16 mono @48k) en file de lecture."""
        async with self._lock:
            self._buf = np.concatenate([self._buf, samples.astype(np.int16)])
            self._playing = True
            self._drained.clear()

    async def wait_drained(self) -> None:
        """Bloque jusqu'à ce que toute la file soit jouée."""
        await self._drained.wait()

    async def recv(self) -> av.AudioFrame:
        if self._start is None:
            self._start = time.time()
        # Cadence temps réel : ne pas produire plus vite que l'horloge.
        target = self._start + self._pts / self.rate
        delay = target - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

        async with self._lock:
            n = self.frame_samples
            if len(self._buf) >= n:
                chunk = self._buf[:n]
                self._buf = self._buf[n:]
            else:  # pas assez : on complète en silence
                chunk = np.zeros(n, dtype=np.int16)
                if len(self._buf):
                    chunk[: len(self._buf)] = self._buf
                    self._buf = self._buf[:0]
                if self._playing:  # on vient de finir de jouer la file
                    self._playing = False
                    self._drained.set()

        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(chunk, dtype=np.int16).reshape(1, -1),
            format="s16", layout="mono",
        )
        frame.sample_rate = self.rate
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, self.rate)
        self._pts += n
        if self._metrics is not None:
            self._metrics["frames_out"] += 1
            self._metrics["samples_out"] += n
            GLOBAL["frames_out_total"] += 1
        return frame


# --------------------------------------------------------------------------- #
# Workers PERSISTANTS STT/TTS — le cœur de l'Action 2 (RAPPORT-LENTEUR.md).
#
# AVANT : chaque tour de parole lançait transcribe.py PUIS synth.py en
# sous-processus jetable. Chacun RECHARGEAIT son modèle depuis le disque
# (~1 s faster-whisper, ~1,5 s Piper) avant de s'éteindre — « couper le moteur à
# chaque feu rouge ». ~2,5 s gaspillées par échange.
#
# MAINTENANT : on garde UN process chaud par moteur (transcribe.py --serve /
# synth.py --serve). Le modèle est chargé UNE fois (au démarrage) puis reste en
# mémoire ; chaque tour ne fait plus que l'inférence. On conserve l'isolation
# (ctranslate2/onnx hors de la boucle asyncio) et l'indirection par interpréteur
# (WHISPER_PYTHON / TTS_PYTHON) et par script (⇒ les faux STT/TTS des tests
# marchent toujours, en --serve eux aussi).
#
# Protocole : une requête JSON par ligne sur stdin du worker → une réponse JSON
# par ligne sur sa stdout. Tout le reste (logs, chargement) part sur stderr, drainé
# en continu par une tâche de fond (sinon le tube stderr se remplit et bloque le
# worker). Si le worker meurt, la prochaine requête le relance et réessaie 1 fois.
# --------------------------------------------------------------------------- #
class PersistentWorker:
    """Pilote un sous-processus modèle-chaud via un protocole JSON-par-ligne.

    Un seul worker par moteur ; les requêtes sont sérialisées par un verrou
    (un worker = une inférence à la fois, ce qui colle à CONCURRENCY=1). Relance
    automatique si le worker a planté.
    """

    def __init__(self, name: str, argv: list[str], log_prefix: str) -> None:
        self.name = name
        self._argv = argv
        self._log_prefix = log_prefix
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _spawn_locked(self) -> None:
        if self._alive():
            return
        log.info("%s démarrage du worker persistant : %s",
                 self._log_prefix, " ".join(self._argv))
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc))

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Recopie en continu la stderr du worker dans les logs (et évite le blocage
        par tube plein)."""
        assert proc.stderr is not None
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                txt = line.decode("utf-8", "replace").rstrip()
                if txt:
                    log.info("%s %s", self._log_prefix, txt)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass

    async def _kill_locked(self) -> None:
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._proc is not None:
            if self._proc.returncode is None:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await self._proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None

    async def start(self) -> None:
        """Préchauffe : lance le worker (et donc charge le modèle) sans requête."""
        async with self._lock:
            try:
                await self._spawn_locked()
            except Exception as exc:  # noqa: BLE001
                log.warning("%s préchauffe impossible (%s) — relance à la 1ʳᵉ requête",
                            self._log_prefix, exc)

    async def stop(self) -> None:
        async with self._lock:
            # Fermer stdin laisse le worker sortir proprement ; sinon on le tue.
            if self._alive() and self._proc.stdin is not None:
                try:
                    self._proc.stdin.close()
                    await asyncio.wait_for(self._proc.wait(), timeout=3)
                except Exception:  # noqa: BLE001
                    pass
            await self._kill_locked()

    async def request(self, payload: dict, timeout: float) -> dict:
        """Envoie une requête, renvoie la réponse JSON. Relance le worker et
        réessaie 1 fois s'il a planté. Lève RuntimeError si tout échoue."""
        async with self._lock:
            last_exc: Exception | None = None
            for attempt in (1, 2):
                try:
                    await self._spawn_locked()
                    proc = self._proc
                    assert proc is not None and proc.stdin is not None
                    assert proc.stdout is not None
                    proc.stdin.write(
                        (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                    if not line:
                        raise RuntimeError("worker a fermé sa sortie (EOF)")
                    return json.loads(line.decode("utf-8", "replace"))
                except (BrokenPipeError, ConnectionResetError, RuntimeError,
                        json.JSONDecodeError, asyncio.TimeoutError,
                        AssertionError) as exc:
                    last_exc = exc
                    log.warning("%s requête échouée (essai %d/2 : %s) — relance du worker",
                                self._log_prefix, attempt, exc)
                    await self._kill_locked()
            raise RuntimeError(f"{self.name} indisponible : {last_exc}")


# Instances uniques (chaude). argv = interpréteur + script --serve + réglages.
_stt_worker = PersistentWorker(
    "STT",
    [WHISPER_PYTHON, str(WHISPER_SCRIPT), "--serve",
     "--model", WHISPER_MODEL, "--language", WHISPER_LANGUAGE,
     "--beam-size", str(WHISPER_BEAM_SIZE)],
    "STT[worker]:",
)
_tts_worker = PersistentWorker(
    "TTS",
    [TTS_PYTHON, str(TTS_SCRIPT), "--serve", "--voice", PIPER_VOICE],
    "TTS[worker]:",
)


async def transcribe_async(wav_path: Path) -> dict:
    """Renvoie {text, language, duration, model} ; lève RuntimeError en cas d'échec.
    Passe par le worker STT persistant (modèle chaud)."""
    async with _whisper_sem:
        data = await _stt_worker.request({"audio": str(wav_path)}, WHISPER_TIMEOUT_S)
    if "error" in data:
        raise RuntimeError(f"STT: {data['error']}")
    return data


async def synth_async(text: str, out_path: Path) -> dict:
    """Synthétise `text` -> WAV à `out_path` ; lève RuntimeError en cas d'échec.
    Passe par le worker TTS persistant (voix chaude)."""
    async with _tts_sem:
        data = await _tts_worker.request(
            {"text": text, "out": str(out_path)}, TTS_TIMEOUT_S)
    if "error" in data:
        raise RuntimeError(f"TTS: {data['error']}")
    return data


# --------------------------------------------------------------------------- #
# Pont Hermès (fichiers) : dépôt atomique de l'inbox, attente de l'outbox.
# Un id d'échange par TOUR de parole -> pas de collision sur une conversation.
# --------------------------------------------------------------------------- #
def write_inbox(ex_id: str, text: str, meta: dict, conversation: str, turn: int) -> Path:
    """Écrit inbox/<ex>.json en « pending », de façon atomique (.tmp + replace)."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": ex_id,
        "conversation_id": conversation,
        "turn": turn,
        "text": text,
        "status": "pending",
        "source": "webrtc",
        "stt": {
            "model": meta.get("model"),
            "language": meta.get("language"),
            "duration": meta.get("duration"),
        },
    }
    path = INBOX_DIR / f"{ex_id}.json"
    tmp = INBOX_DIR / f"{ex_id}.json.tmp"
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


async def wait_for_reply(ex_id: str, timeout: float) -> str | None:
    """Attend outbox/<ex>.json puis renvoie le texte de la réponse (ou None)."""
    out_path = OUTBOX_DIR / f"{ex_id}.json"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if out_path.exists():
            try:
                data = json.loads(out_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                await asyncio.sleep(0.2)
                continue
            return (data.get("reply") or data.get("text")
                    or data.get("response") or "").strip()
        await asyncio.sleep(0.2)
    return None


# --------------------------------------------------------------------------- #
# Orchestration d'un tour de parole.
# --------------------------------------------------------------------------- #
async def handle_turn(session: str, audio16k: np.ndarray) -> None:
    """Phrase détectée -> STT -> inbox -> (Hermès) -> TTS -> joué dans l'oreillette.

    `sessions[session]["busy"]` reste True le temps du tour : la boucle d'entrée
    ignore le micro (pas de self-écoute), et on rétablit le VAD à la fin.
    """
    st = sessions.get(session)
    if st is None:
        return
    tts: TTSPlaybackTrack = st["tts_track"]
    tag = st["tag"]
    turn = st["turn"] = st.get("turn", 0) + 1
    GLOBAL["turns_total"] += 1
    ex_id = f"{session[:50]}-t{turn:03d}"  # reste sous 64 car. et anti-collision
    wav_in = AUDIO_TMP_DIR / f"webrtc_{ex_id}_in.wav"
    wav_out = AUDIO_TMP_DIR / f"webrtc_{ex_id}_out.wav"
    loop = asyncio.get_running_loop()
    rec = {"turn": turn, "ex_id": ex_id, "transcript": None, "reply": None,
           "state": "transcribing"}
    st["turns"].append(rec)

    try:
        # 1+2. WAV puis transcription.
        st["state"] = "transcribing"
        dur = len(audio16k) / SAMPLE_RATE
        log.info("%s tour %d : phrase de %.1fs -> STT", tag, turn, dur)
        await loop.run_in_executor(None, write_wav16k, wav_in, audio16k)
        result = await transcribe_async(wav_in)
        text = (result.get("text") or "").strip()
        rec["transcript"] = text
        log.info("%s tour %d : transcription %r", tag, turn, text)
        if not text:  # rien de compréhensible : on se remet à l'écoute
            log.warning("%s tour %d : transcription VIDE (%.1fs d'audio capté, "
                        "modèle=%s langue=%s) — rien envoyé à Hermès, retour à "
                        "l'écoute. Cause probable : audio trop faible/bruité, "
                        "mauvais micro, ou modèle STT inadapté.",
                        tag, turn, dur, result.get("model"), result.get("language"))
            rec["state"] = "empty"
            return

        # 3. Inbox Hermès.
        st["state"] = "thinking"
        rec["state"] = "thinking"
        write_inbox(ex_id, text, result, conversation=session, turn=turn)

        # 4. Attente de la réponse d'Hermès.
        reply = await wait_for_reply(ex_id, HERMES_TIMEOUT_S)
        if not reply:
            log.warning("%s tour %d : pas de réponse Hermès (timeout)", tag, turn)
            rec["state"] = "timeout"
            return
        rec["reply"] = reply
        log.info("%s tour %d : réponse Hermès %r", tag, turn, reply)

        # 5+6. TTS -> piste sortante. VAD coupé pendant la lecture.
        st["state"] = "speaking"
        rec["state"] = "speaking"
        st["speaking"] = True
        await synth_async(reply, wav_out)
        samples = await loop.run_in_executor(None, wav_to_out_rate, wav_out)
        if len(samples):
            await tts.enqueue(samples)
            await tts.wait_drained()
        await asyncio.sleep(TAIL_SILENCE_MS / 1000.0)  # petite pause de fin de tour
        rec["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        log.exception("%s tour %d : échec", tag, turn)
        rec["state"] = "error"
        rec["error"] = str(exc)
        st["last_error"] = str(exc)
    finally:
        # Nettoyage des WAV jetables + réarmement du VAD pour le tour suivant.
        for p in (wav_in, wav_out):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        st["speaking"] = False
        st["state"] = "listening"
        ep: Endpointer = st["endpointer"]
        ep.reset()
        st["busy"] = False


async def media_watchdog(session: str) -> None:
    """Surveillant du flux ENTRANT : heartbeat périodique + alerte « 0 trame ».

    Toutes les MEDIA_HEARTBEAT_S, journalise un résumé du flux (frames_in,
    débit, voix, RMS) tant que la session vit. Surtout : si la connexion est
    établie mais qu'AUCUNE trame n'arrive après NO_FRAME_WARN_S, il ALERTE —
    c'est la signature d'un échec de traversée NAT (candidats non routables,
    faute de STUN) ou d'un firewall UDP, pas d'un bug applicatif. Tâche de fond
    supervisée, annulée au teardown.
    """
    st = sessions.get(session)
    if st is None:
        return
    tag = st["tag"]
    m: dict = st["metrics"]
    loop = asyncio.get_running_loop()
    warned_no_frame = False
    prev_in = 0
    live = {"connected", "completed", "checking", "connecting"}
    while not st.get("ended"):
        await asyncio.sleep(MEDIA_HEARTBEAT_S)
        st = sessions.get(session)
        if st is None or st.get("ended"):
            break
        elapsed = loop.time() - m["started_at"]
        delta = m["frames_in"] - prev_in
        prev_in = m["frames_in"]
        if m["frames_in"] == 0:
            if (not warned_no_frame and elapsed >= NO_FRAME_WARN_S
                    and m["connection_state"] in live):
                warned_no_frame = True
                log.warning("%s ⚠ %.0fs écoulées, connexion=%s mais 0 trame "
                            "audio ENTRANTE (frames_in=0). Cause probable : "
                            "traversée NAT échouée (aucun candidat routable — "
                            "STUN absent/bloqué) ou firewall UDP. Vérifie /ice "
                            "et les candidats « srflx » côté navigateur.",
                            tag, elapsed, m["connection_state"])
        else:
            warned_no_frame = False
            log.info("%s média: frames_in=%d (+%d, %.0f/s) voix=%d/%d "
                     "frames_out=%d max_rms=%.0f état=%s",
                     tag, m["frames_in"], delta, delta / MEDIA_HEARTBEAT_S,
                     m["speech_frames"], m["vad_frames"], m["frames_out"],
                     m["max_rms"], st.get("state"))


async def consume_audio(session: str, track: MediaStreamTrack) -> None:
    """Boucle d'entrée : rééchantillonne le micro en 16k mono, alimente le VAD et
    déclenche un tour de parole à chaque fin de phrase. Tourne jusqu'à la fin de
    la piste (raccrochage)."""
    st = sessions.get(session)
    if st is None:
        return
    tag = st["tag"]
    ep: Endpointer = st["endpointer"]
    m: dict = st["metrics"]
    resampler = AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    carry = np.zeros(0, dtype=np.int16)   # report : reste < une trame d'analyse
    frame_n = ep.frame_samples
    loop = asyncio.get_running_loop()
    log.info("%s écoute du micro (VAD %s)", tag, ep.vad.name)

    while True:
        try:  # timeout court : permet de finaliser même si le flux se tarit
            frame = await asyncio.wait_for(track.recv(), timeout=0.3)
        except asyncio.TimeoutError:
            frame = None
            m["recv_timeouts"] += 1
        except MediaStreamError:
            break  # piste terminée (raccrochage)

        now = loop.time()

        # Instrumentation : toute trame REÇUE compte (preuve que l'audio arrive
        # vraiment — c'est frames_in qui tranche un éventuel échec NAT/ICE).
        if frame is not None:
            m["frames_in"] += 1
            GLOBAL["frames_in_total"] += 1
            if m["first_frame_at"] is None:
                m["first_frame_at"] = now
                log.info("%s 1ʳᵉ trame micro reçue (%.2fs après l'ouverture) — "
                         "le flux audio ENTRANT est établi",
                         tag, now - m["started_at"])
            m["last_frame_at"] = now

        # Pendant qu'Hermès parle/réfléchit : on draine le micro sans l'écouter.
        if st.get("busy"):
            if frame is not None:
                m["frames_in_drained"] += 1
            continue

        if frame is not None:
            for rf in resampler.resample(frame):
                carry = np.concatenate([carry, rf.to_ndarray().reshape(-1)])
            while len(carry) >= frame_n:
                chunk = carry[:frame_n].astype(np.int16)
                rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                if rms > m["max_rms"]:
                    m["max_rms"] = rms
                sp = ep.feed(chunk, now)
                m["vad_frames"] += 1
                m["samples_in"] += frame_n
                if sp:
                    m["speech_frames"] += 1
                carry = carry[frame_n:]
            st["state"] = "speech" if ep.speaking else "listening"

        utt = ep.poll(now)
        if utt is not None and not st.get("busy"):
            st["busy"] = True   # verrou synchrone avant de lancer le tour
            carry = np.zeros(0, dtype=np.int16)
            _supervise(asyncio.create_task(handle_turn(session, utt)), session)

    log.info("%s fin d'écoute du micro (frames_in=%d, voix=%d trames, max_rms=%.0f)",
             tag, m["frames_in"], m["speech_frames"], m["max_rms"])


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
async def index(request: web.Request) -> web.Response:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    return web.Response(content_type="text/html", text=html)


def _candidate_summary(sdp: str) -> tuple[list[str], list[str]]:
    """Extrait du SDP les candidats ICE : (types, libellés lisibles).

    types = ['host', 'srflx', …] (un par candidat). Un « srflx » (server-
    reflexive) prouve que STUN a répondu : l'adresse PUBLIQUE a été découverte —
    c'est exactement ce qui manque, faute de STUN, au navigateur derrière NAT.
    """
    types: list[str] = []
    labels: list[str] = []
    for line in sdp.splitlines():
        line = line.strip()
        if "candidate:" not in line or " typ " not in line:
            continue
        parts = line.split()
        try:
            ip, port = parts[4], parts[5]
            typ = parts[parts.index("typ") + 1]
        except (ValueError, IndexError):
            continue
        types.append(typ)
        labels.append(f"{typ} {ip}:{port}")
    return types, labels


async def offer(request: web.Request) -> web.Response:
    """Signalisation : reçoit l'offre SDP + session_id, met en place les pistes
    entrante (micro) et sortante (voix d'Hermès), renvoie l'answer SDP."""
    params = await request.json()
    session = safe_session(params.get("session"))
    if session is None:
        return web.json_response({"error": "session_id invalide ou manquant"}, status=400)

    offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    # STUN/TURN : mêmes serveurs ICE que ceux annoncés au navigateur via /ice.
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
    pcs.add(pc)
    tag = f"[{session[:8]} {request.remote}]"
    now = asyncio.get_running_loop().time()
    GLOBAL["sessions_total"] += 1
    log.info("%s nouvelle conversation (ICE: %d serveur(s))", tag, len(ICE_SERVERS))

    metrics = new_metrics(now)
    tts_track = TTSPlaybackTrack(metrics)
    pc.addTrack(tts_track)  # piste sortante : la voix d'Hermès
    sessions[session] = {
        "tag": tag,
        "state": "connecting",
        "speaking": False,
        "busy": False,
        "turn": 0,
        "turns": [],
        "tts_track": tts_track,
        "endpointer": Endpointer(make_vad()),
        "tasks": set(),
        "metrics": metrics,
        "remote": request.remote,
    }
    st = sessions[session]

    @pc.on("connectionstatechange")
    async def on_connectionstatechange() -> None:
        metrics["connection_state"] = pc.connectionState
        log.info("%s état -> %s", tag, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await teardown(session, pc)

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange() -> None:
        metrics["ice_state"] = pc.iceConnectionState
        log.info("%s ICE -> %s", tag, pc.iceConnectionState)
        if pc.iceConnectionState == "failed":
            log.warning("%s ⚠ ICE a ÉCHOUÉ : aucune paire de candidats routable. "
                        "Sans STUN, le navigateur n'offre qu'une IP privée que le "
                        "VPS ne peut joindre — active STUN (/ice) ou un relais TURN.",
                        tag)

    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange() -> None:
        metrics["ice_gathering"] = pc.iceGatheringState
        log.debug("%s ICE gathering -> %s", tag, pc.iceGatheringState)

    @pc.on("track")
    def on_track(track) -> None:
        log.info("%s piste reçue : %s", tag, track.kind)
        if track.kind == "audio":
            _supervise(asyncio.ensure_future(consume_audio(session, track)), session)
            _supervise(asyncio.ensure_future(media_watchdog(session)), session)

        @track.on("ended")
        async def on_ended() -> None:
            log.info("%s piste micro terminée", tag)

    await pc.setRemoteDescription(offer_sdp)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Candidats ICE LOCAUX (côté serveur), pour le diagnostic NAT : on voit ici
    # les IP que le VPS annonce (publique = bon ; bridges Docker 172.x = bruit).
    ctypes, clabels = _candidate_summary(pc.localDescription.sdp)
    metrics["local_candidate_types"] = ctypes
    log.info("%s candidats ICE locaux : %s", tag, ", ".join(clabels) or "aucun")

    st["state"] = "listening"
    log.info("%s answer envoyée — conversation ouverte", tag)
    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "session": session}
    )


async def teardown(session: str, pc: RTCPeerConnection) -> None:
    """Ferme la connexion et annule les tâches d'une session (idempotent)."""
    st = sessions.get(session)
    if st is not None:
        if st.get("ended"):
            return
        st["ended"] = True
        st["state"] = "ended"
        for t in list(st.get("tasks", ())):
            t.cancel()
    try:
        await pc.close()
    except Exception:  # noqa: BLE001
        pass
    pcs.discard(pc)


async def poll(request: web.Request) -> web.Response:
    """État de la conversation pour la page web (sous-titres + indicateur).

    Renvoie : {status, speaking, turn, turns:[{turn,transcript,reply,state}], ...}
      status ∈ listening | speech | transcribing | thinking | speaking | ended | unknown
    """
    session = safe_session(request.query.get("session"))
    if session is None:
        return web.json_response({"status": "error", "error": "session invalide"}, status=400)
    st = sessions.get(session)
    if not st:
        return web.json_response({"status": "unknown"})
    turns = [
        {"turn": t["turn"], "transcript": t.get("transcript"),
         "reply": t.get("reply"), "state": t.get("state")}
        for t in st.get("turns", [])
    ]
    m = st.get("metrics", {})
    return web.json_response({
        "status": st.get("state", "listening"),
        "speaking": bool(st.get("speaking")),
        "turn": st.get("turn", 0),
        "turns": turns,
        "error": st.get("last_error"),
        # Instrumentation légère pour le client (le détail complet est sur /diag).
        "frames_in": m.get("frames_in", 0),
        "frames_out": m.get("frames_out", 0),
        "ice_state": m.get("ice_state"),
        "connection_state": m.get("connection_state"),
    })


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "webrtc-vocal", "mode": "conversation",
                              "sessions": len(sessions),
                              "ice_servers": len(ICE_SERVERS),
                              "frames_in_total": GLOBAL["frames_in_total"]})


async def ice_config(request: web.Request) -> web.Response:
    """Serveurs ICE (STUN/TURN) à passer à RTCPeerConnection côté navigateur.

    Le client récupère cette liste AVANT de créer la connexion : la config STUN
    a ainsi une SEULE source de vérité (le serveur, via WEBRTC_ICE_SERVERS), et
    s'aligne sur celle du serveur aiortc.
    """
    return web.json_response({"iceServers": ice_servers_public(ICE_SERVERS)})


async def diag(request: web.Request) -> web.Response:
    """Vue DIAGNOSTIC complète (JSON) : compteurs cumulés du process, config ICE
    effective, et l'état média de chaque session — au premier rang `frames_in`
    (« l'audio arrive-t-il vraiment ? »). Sert de tableau de bord à DIAGNOSTIC.md.
    """
    now = asyncio.get_running_loop().time()
    sess: dict[str, dict] = {}
    for sid, st in sessions.items():
        m = st.get("metrics", {})
        last = m.get("last_frame_at")
        started = m.get("started_at")
        sess[sid[:8]] = {
            "state": st.get("state"),
            "speaking": bool(st.get("speaking")),
            "busy": bool(st.get("busy")),
            "turn": st.get("turn", 0),
            "remote": st.get("remote"),
            "connection_state": m.get("connection_state"),
            "ice_state": m.get("ice_state"),
            "ice_gathering": m.get("ice_gathering"),
            "local_candidate_types": m.get("local_candidate_types"),
            "frames_in": m.get("frames_in", 0),
            "frames_in_drained": m.get("frames_in_drained", 0),
            "frames_out": m.get("frames_out", 0),
            "samples_in": m.get("samples_in", 0),
            "samples_out": m.get("samples_out", 0),
            "vad_frames": m.get("vad_frames", 0),
            "speech_frames": m.get("speech_frames", 0),
            "recv_timeouts": m.get("recv_timeouts", 0),
            "max_rms": round(m.get("max_rms", 0.0), 1),
            "uptime_s": round(now - started, 1) if started is not None else None,
            "since_last_frame_s": round(now - last, 1) if last is not None else None,
        }
    return web.json_response({
        "service": "webrtc-vocal",
        "global": GLOBAL,
        "ice_servers": ice_servers_public(ICE_SERVERS),
        "config": {
            "vad_backend": VAD_BACKEND,
            "end_silence_ms": END_SILENCE_MS,
            "min_speech_ms": MIN_SPEECH_MS,
            "media_heartbeat_s": MEDIA_HEARTBEAT_S,
            "no_frame_warn_s": NO_FRAME_WARN_S,
            "log_level": LOG_LEVEL,
        },
        "sessions": sess,
    })


async def on_startup(app: web.Application) -> None:
    """Préchauffe les moteurs STT/TTS : on lance les workers persistants DÈS le
    démarrage pour que leurs modèles soient déjà chargés au 1ᵉʳ tour de parole
    (sinon le tout premier échange paie le chargement à froid). Non bloquant en
    cas d'échec : la 1ʳᵉ requête relancera le worker."""
    log.info("préchauffe des moteurs STT/TTS (workers persistants)…")
    await asyncio.gather(_stt_worker.start(), _tts_worker.start(),
                         return_exceptions=True)


async def on_shutdown(app: web.Application) -> None:
    log.info("arrêt : fermeture de %d connexion(s) + workers STT/TTS", len(pcs))
    await asyncio.gather(*(pc.close() for pc in list(pcs)), return_exceptions=True)
    pcs.clear()
    await asyncio.gather(_stt_worker.stop(), _tts_worker.stop(),
                         return_exceptions=True)


def create_app() -> web.Application:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/poll", poll)
    app.router.add_get("/health", health)
    app.router.add_get("/ice", ice_config)
    app.router.add_get("/diag", diag)
    return app


# --------------------------------------------------------------------------- #
# Écouteurs HTTP / HTTPS — rôles FIXES, les deux dans le même process :
#   • --port      (défaut 8686) : HTTP EN CLAIR, toujours servi. Debug local sur
#     l'hôte (curl http://127.0.0.1:8686/health) et reverse-proxy éventuel
#     (Caddy → 127.0.0.1:8686, cf. deploy/Caddyfile).
#   • --https-port (défaut 8443) : HTTPS natif, servi DÈS qu'un certificat est
#     disponible. getUserMedia() exige un « contexte sécurisé » (HTTPS) : c'est
#     donc CE port — ouvert au firewall — que l'iPhone de Mehdi atteint sur le
#     chantier. Mettre --https-port 0 (ou aucun certificat) désactive le HTTPS.
#
# Origine du certificat, par ordre de priorité :
#   1. --tls-cert / --tls-key
#   2. WEBRTC_TLS_CERT / WEBRTC_TLS_KEY
#   3. auto-détection de deploy/certs/{cert,key}.pem (cert auto-signé via
#      make-selfsigned-cert.sh, ou cert Let's Encrypt recopié là).
# Sans aucun certificat : seul le HTTP 8686 tourne (TLS alors délégué à un
# reverse proxy type Caddy — cf. deploy/Caddyfile).
# --------------------------------------------------------------------------- #
def _default_cert(name: str) -> str | None:
    """Chemin de deploy/certs/<name> s'il existe, sinon None (auto-détection cert)."""
    p = ROOT / "deploy" / "certs" / name
    return str(p) if p.exists() else None


def _make_ssl_context(cert: str, key: str):
    """Contexte TLS serveur à partir d'un certificat + clé au format PEM."""
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    return ctx


async def _run_dual(host: str, port: int, https_port: int, ssl_ctx) -> None:
    """Sert l'app en HTTP clair sur `port` et, si `ssl_ctx` est fourni, AUSSI en
    HTTPS natif sur `https_port` — dans le même process, derrière une seule app.

    Une seule instance d'app derrière les deux écouteurs : on_shutdown est joué
    une fois via runner.cleanup(). SIGINT/SIGTERM (systemd) arrêtent proprement.
    """
    import signal
    # access_log actif : sans lui, AUCUNE requête entrante (GET /, POST /offer,
    # GET /poll, GET /health) n'apparaît dans les logs — impossible de savoir si
    # une requête externe atteint le serveur. Derrière Caddy, %a vaut 127.0.0.1,
    # donc on logge aussi X-Forwarded-For (IP réelle du client) et le Host visé.
    runner = web.AppRunner(
        create_app(),
        access_log=log,
        access_log_format='%a XFF=%{X-Forwarded-For}i host=%{Host}i "%r" %s %bo %Tfs',
    )
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("HTTP  : http://%s:%d  (clair — debug local + reverse proxy)", host, port)
    if ssl_ctx is not None and https_port and https_port != port:
        await web.TCPSite(runner, host, https_port, ssl_context=ssl_ctx).start()
        log.info("HTTPS : https://%s:%d  (TLS natif — contexte sécurisé getUserMedia)",
                 host, https_port)
    else:
        log.info("HTTPS : désactivé (pas de certificat) — TLS à déléguer à un reverse proxy")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover (plateformes sans add_signal_handler)
            pass
    try:
        await stop.wait()
    finally:
        await runner.cleanup()


def preflight() -> None:
    """Vérifie AU DÉMARRAGE que STT et TTS sont réellement opérationnels, et
    ÉCHOUE FORT (SystemExit) sinon — plutôt que de laisser CHAQUE tour de parole
    planter en silence (le bug d'origine : l'interpréteur configuré n'existait
    pas → FileNotFoundError à chaque phrase, sans que le démarrage ne bronche).

    Deux niveaux de contrôle :
      1. présence des fichiers (interpréteurs, scripts, voix .onnx) ;
      2. sonde d'import réelle : un venv peut exister sans porter faster-whisper
         ou piper — on lance `python -c "import …"` pour le prouver.
    """
    import subprocess

    problems: list[str] = []
    for label, p in (
        ("interpréteur faster-whisper (WHISPER_PYTHON)", Path(WHISPER_PYTHON)),
        ("script STT (WHISPER_SCRIPT)", WHISPER_SCRIPT),
        ("interpréteur piper (TTS_PYTHON)", Path(TTS_PYTHON)),
        ("script TTS (TTS_SCRIPT)", TTS_SCRIPT),
        ("voix Piper (PIPER_VOICE)", Path(PIPER_VOICE)),
    ):
        if not p.exists():
            problems.append(f"{label} introuvable : {p}")

    for label, py, module in (
        ("faster-whisper", WHISPER_PYTHON, "faster_whisper"),
        ("piper", TTS_PYTHON, "piper"),
    ):
        if not Path(py).exists():
            continue  # absence déjà signalée ci-dessus
        try:
            r = subprocess.run(
                [py, "-c", f"import {module}"],
                capture_output=True, timeout=120,
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"sonde d'import {label} impossible via {py} ({exc})")
            continue
        if r.returncode != 0:
            tail = r.stderr.decode("utf-8", "replace").strip().splitlines()
            why = tail[-1] if tail else f"code {r.returncode}"
            problems.append(f"{label} non importable par {py} : {why}")

    if problems:
        log.error("✗ PRÉFLIGHT STT/TTS ÉCHOUÉ — le serveur NE démarre PAS :")
        for pb in problems:
            log.error("    • %s", pb)
        log.error("Corrige l'installation puis relance. Attendu (self-contained) : "
                  "venv local avec `faster-whisper` + `piper-tts`, et la voix "
                  "%s sous models/piper/.", Path(PIPER_VOICE).name)
        raise SystemExit(2)
    log.info("✓ préflight STT/TTS OK (interpréteurs, scripts, voix, imports vérifiés)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serveur WebRTC vocal — conversation (brique 3/3)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get(
                            "PORT", os.environ.get("WEBRTC_HTTP_PORT", "8686"))),
                        help="port HTTP EN CLAIR : debug local + reverse proxy "
                             "Caddy (défaut 8686)")
    parser.add_argument("--https-port", type=int,
                        default=int(os.environ.get("WEBRTC_HTTPS_PORT", "8443")),
                        help="port HTTPS natif, servi dès qu'un certificat est "
                             "dispo ; c'est le port ouvert au firewall que vise "
                             "l'iPhone (0 = aucun ; défaut 8443)")
    parser.add_argument("--tls-cert",
                        default=os.environ.get("WEBRTC_TLS_CERT") or _default_cert("cert.pem"),
                        help="certificat PEM ; sert le HTTPS natif sur --https-port "
                             "(défaut : deploy/certs/cert.pem s'il existe)")
    parser.add_argument("--tls-key",
                        default=os.environ.get("WEBRTC_TLS_KEY") or _default_cert("key.pem"),
                        help="clé privée PEM associée à --tls-cert "
                             "(défaut : deploy/certs/key.pem s'il existe)")
    args = parser.parse_args()

    _tls_on = bool(args.tls_cert and args.tls_key and args.https_port)
    ssl_ctx = _make_ssl_context(args.tls_cert, args.tls_key) if _tls_on else None

    log.info("démarrage (mode conversation) — HTTP :%d%s", args.port,
             f" + HTTPS :{args.https_port}" if _tls_on else " (HTTPS natif désactivé)")
    log.info("  data=%s  (inbox/ outbox/)", DATA_DIR)
    log.info("  STT=%s %s (beam=%d, worker persistant) via %s",
             WHISPER_SCRIPT.name, WHISPER_MODEL, WHISPER_BEAM_SIZE, WHISPER_PYTHON)
    log.info("  TTS=%s voix=%s via %s", TTS_SCRIPT.name, Path(PIPER_VOICE).name, TTS_PYTHON)
    log.info("  VAD=%s  fin_de_phrase=%dms  min_parole=%dms",
             VAD_BACKEND, END_SILENCE_MS, MIN_SPEECH_MS)
    if ICE_SERVERS:
        log.info("  ICE=%s  (STUN/TURN — traversée NAT ; mêmes serveurs via /ice)",
                 ", ".join(", ".join(s.urls) if isinstance(s.urls, list) else s.urls
                           for s in ICE_SERVERS))
    else:
        log.warning("  ICE=AUCUN (host-only) — un client derrière NAT échouera ; "
                    "définis WEBRTC_ICE_SERVERS (défaut conseillé : %s)", DEFAULT_STUN)
    log.info("  log=%s  heartbeat_média=%.0fs  alerte_0_trame=%.0fs",
             LOG_LEVEL, MEDIA_HEARTBEAT_S, NO_FRAME_WARN_S)
    # Garde-fou : STT/TTS doivent être opérationnels AVANT d'ouvrir le port.
    preflight()
    if _tls_on:
        log.info("  TLS natif sur le port %d : cert=%s", args.https_port, args.tls_cert)
    else:
        log.info("  pas de certificat : HTTP clair seul (TLS à déléguer à un reverse proxy)")

    try:
        asyncio.run(_run_dual(args.host, args.port, args.https_port, ssl_ctx))
    except KeyboardInterrupt:
        pass
