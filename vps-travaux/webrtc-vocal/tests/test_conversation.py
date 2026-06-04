#!/usr/bin/env python3
"""
Test d'intégration du cycle CONVERSATIONNEL (brique 3/3), hors-ligne, sans micro.

Pilote le VRAI server.py via un client aiortc qui simule un appel téléphonique :

  piste micro synthétique (salves parole/silence) ──▶ serveur
      VAD (énergie) + endpointing : fin de phrase sur silence
      ──▶ STT (FAUX whisper)  ──▶ inbox/<ex>.json (pending)        [assert]
      ──▶ (on simule le cron Hermès : on écrit outbox/<ex>.json)
      ──▶ TTS (FAUX piper) ──▶ piste SORTANTE WebRTC                [assert audio reçu]
      ──▶ /poll : le tour passe à done, transcript + reply          [assert]
  puis on RECOMMENCE un 2ᵉ tour pour prouver que le VAD se réarme.

Phase 2 (diag + STUN) : on vérifie aussi /ice (config ICE servie au navigateur,
host-only en boucle locale) et /diag (compteurs), dont `frames_in` qui prouve que
l'audio est bien REÇU côté serveur — la mesure clé du diagnostic « 0 trame ».

Aucune dépendance à faster-whisper ni à Piper : STT et TTS sont remplacés par
tests/transcribe_fake.py et tests/synth_fake.py (mêmes contrats), branchés via
WHISPER_SCRIPT / TTS_SCRIPT. VAD forcé sur l'énergie (déterministe).

Lancer :  .venv/bin/python tests/test_conversation.py
"""
from __future__ import annotations

import asyncio
import fractions
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

FAKE_TEXT = "bonjour hermès on se parle pour de vrai"
REPLIES = {
    1: "Salut Mehdi ! Oui je t'entends parfaitement, on discute en direct.",
    2: "Avec plaisir, continue, je t'écoute.",
}

OUT_RATE = 48000
FRAME = int(OUT_RATE * 0.02)  # 960 échantillons = 20 ms


def tone(dur: float, freq: float = 440.0, amp: int = 9000) -> np.ndarray:
    n = int(OUT_RATE * dur)
    t = np.arange(n)
    return (amp * np.sin(2 * np.pi * freq * t / OUT_RATE)).astype(np.int16)


def silence(dur: float) -> np.ndarray:
    return np.zeros(int(OUT_RATE * dur), dtype=np.int16)


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="webrtc-conv-"))
    data_dir = tmp / "data"
    # --- Config AVANT l'import de server (lecture au niveau module) ---
    os.environ.update(
        WEBRTC_DATA_DIR=str(data_dir),
        WEBRTC_TMP_DIR=str(tmp),
        WHISPER_PYTHON=sys.executable,
        WHISPER_SCRIPT=str(HERE / "transcribe_fake.py"),
        FAKE_TEXT=FAKE_TEXT,
        TTS_PYTHON=sys.executable,
        TTS_SCRIPT=str(HERE / "synth_fake.py"),
        PIPER_VOICE="fake",
        WEBRTC_VAD="energy",
        VAD_ENERGY_THRESHOLD="500",
        VAD_FRAME_MS="30",
        END_SILENCE_MS="500",
        MIN_SPEECH_MS="200",
        PREROLL_MS="150",
        TAIL_SILENCE_MS="100",
        HERMES_TIMEOUT_S="15",
        # Phase 2 : boucle locale (127.0.0.1) ⇒ host-only, AUCUN serveur ICE
        # (déterministe + pas d'attente STUN vers Internet). Heartbeat média
        # raccourci pour exercer le surveillant pendant le test.
        WEBRTC_ICE_SERVERS="",
        WEBRTC_MEDIA_HEARTBEAT_S="1",
    )

    sys.path.insert(0, str(PROJECT))
    import server  # noqa: E402  (import retardé volontairement)
    import av  # noqa: E402
    from aiohttp import web, ClientSession  # noqa: E402
    from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription  # noqa: E402
    from aiortc.mediastreams import MediaStreamError  # noqa: E402

    # --- Piste micro pilotable : émet ce qu'on y pousse, sinon du silence -----
    class ScriptedMic(MediaStreamTrack):
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self._buf = np.zeros(0, dtype=np.int16)
            self._lock = asyncio.Lock()
            self._pts = 0
            self._start: float | None = None

        async def push(self, samples: np.ndarray) -> None:
            async with self._lock:
                self._buf = np.concatenate([self._buf, samples])

        async def recv(self) -> "av.AudioFrame":
            if self._start is None:
                self._start = time.time()
            target = self._start + self._pts / OUT_RATE
            d = target - time.time()
            if d > 0:
                await asyncio.sleep(d)
            async with self._lock:
                if len(self._buf) >= FRAME:
                    chunk = self._buf[:FRAME]
                    self._buf = self._buf[FRAME:]
                else:
                    chunk = np.zeros(FRAME, dtype=np.int16)
                    if len(self._buf):
                        chunk[: len(self._buf)] = self._buf
                        self._buf = self._buf[:0]
            f = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(chunk).reshape(1, -1), format="s16", layout="mono")
            f.sample_rate = OUT_RATE
            f.pts = self._pts
            f.time_base = fractions.Fraction(1, OUT_RATE)
            self._pts += FRAME
            return f

    # --- Compteur d'énergie sur la piste reçue (voix d'Hermès rejouée) --------
    class Meter:
        def __init__(self) -> None:
            self.loud = 0
            self.total = 0

        async def run(self, track) -> None:
            try:
                while True:
                    f = await track.recv()
                    nd = f.to_ndarray().reshape(-1).astype(np.float32)
                    self.total += 1
                    if np.sqrt(np.mean(nd ** 2)) > 1500:
                        self.loud += 1
            except MediaStreamError:
                pass

    session_id = "convtest1234"
    runner = web.AppRunner(server.create_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    base = f"http://127.0.0.1:{port}"
    print(f"• serveur conversation lancé sur {base}")

    rc = 1
    pc = RTCPeerConnection()
    mic = ScriptedMic()
    meter = Meter()
    meter_task = None
    try:
        @pc.on("track")
        def on_track(track) -> None:
            nonlocal meter_task
            print(f"• piste reçue du serveur : {track.kind}")
            if track.kind == "audio":
                meter_task = asyncio.ensure_future(meter.run(track))

        async with ClientSession() as http:
            # 1. Connexion WebRTC bidirectionnelle (micro + voix d'Hermès).
            pc.addTrack(mic)
            await pc.setLocalDescription(await pc.createOffer())
            await wait_ice_complete(pc)
            async with http.post(f"{base}/offer", json={
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "session": session_id,
            }) as r:
                assert r.status == 200, f"/offer HTTP {r.status}"
                ans = await r.json()
            assert ans.get("session") == session_id
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
            print("• handshake WebRTC OK — conversation ouverte")

            for turn in (1, 2):
                print(f"\n=== TOUR {turn} ===")
                # 2. Mehdi parle ~1s, puis se tait ~1s (> END_SILENCE_MS).
                await mic.push(tone(1.0))
                await mic.push(silence(1.0))
                print("• parole + silence poussés")

                # 3. Le serveur doit transcrire et écrire l'inbox (Hermès réfléchit).
                ex_id = f"{session_id}-t{turn:03d}"
                d = await poll_until(
                    http, base, session_id,
                    lambda d: bool(turn_of(d, turn) and turn_of(d, turn)["transcript"]),
                    timeout=20)
                t = turn_of(d, turn)
                assert t["transcript"] == FAKE_TEXT, f"transcript inattendu : {t}"
                inbox = data_dir / "inbox" / f"{ex_id}.json"
                assert inbox.exists(), f"inbox manquante : {inbox}"
                rec = json.loads(inbox.read_text())
                assert rec["status"] == "pending" and rec["text"] == FAKE_TEXT
                assert rec["conversation_id"] == session_id and rec["turn"] == turn
                print(f"  ✓ inbox écrite : {inbox.name}  text={rec['text']!r}")

                # 4. Simuler le cron Hermès : déposer la réponse dans l'outbox.
                loud_before = meter.loud
                outbox = data_dir / "outbox" / f"{ex_id}.json"
                outbox.write_text(json.dumps(
                    {"session_id": ex_id, "reply": REPLIES[turn], "status": "done"},
                    ensure_ascii=False), encoding="utf-8")
                print(f"  ✓ outbox simulée (cron Hermès) : {outbox.name}")

                # 5. Le serveur synthétise et REJOUE la voix : le tour passe à done
                #    ET le client doit recevoir de l'audio (énergie non nulle).
                d = await poll_until(
                    http, base, session_id,
                    lambda d: (turn_of(d, turn) or {}).get("state") == "done",
                    timeout=25)
                t = turn_of(d, turn)
                assert t["reply"] == REPLIES[turn], f"reply inattendu : {t}"
                played = meter.loud - loud_before
                assert played >= 10, f"voix d'Hermès non rejouée (frames audibles={played})"
                print(f"  ✓ tour done — reply={t['reply']!r}")
                print(f"  ✓ voix rejouée côté client : {played} trames audibles")

                # 6. Le serveur doit revenir à l'écoute (VAD réarmé) avant le tour suivant.
                await poll_until(
                    http, base, session_id,
                    lambda d: d.get("status") in ("listening", "speech")
                    and not d.get("speaking"),
                    timeout=10)
                print("  ✓ retour à l'écoute (VAD réarmé)")

            # 7. Instrumentation Phase 2 : /ice expose la config (host-only ici),
            #    /diag les compteurs, et frames_in PROUVE que l'audio est arrivé.
            async with http.get(f"{base}/ice") as r:
                ice = await r.json()
            assert ice.get("iceServers") == [], f"host-only attendu, vu {ice}"
            async with http.get(f"{base}/diag") as r:
                dg = await r.json()
            sk = session_id[:8]
            assert sk in dg["sessions"], f"/diag sans la session : {list(dg['sessions'])}"
            sd = dg["sessions"][sk]
            assert sd["frames_in"] > 0, f"frames_in devrait être > 0, vu {sd['frames_in']}"
            assert sd["frames_out"] > 0, f"frames_out devrait être > 0, vu {sd['frames_out']}"
            assert dg["global"]["frames_in_total"] >= sd["frames_in"]
            assert dg["global"]["turns_total"] >= 2
            async with http.get(f"{base}/poll", params={"session": session_id}) as r:
                pj = await r.json()
            assert "frames_in" in pj, "/poll devrait exposer frames_in"
            print(f"  ✓ /ice host-only ; /diag frames_in={sd['frames_in']} "
                  f"frames_out={sd['frames_out']} voix={sd['speech_frames']} "
                  f"max_rms={sd['max_rms']} ; /poll.frames_in={pj['frames_in']}")

            print("\n✅ CONVERSATION VALIDÉE : 2 tours parole→STT→inbox→Hermès→TTS→audio")
            print("✅ INSTRUMENTATION VALIDÉE : /ice + /diag + frames_in (Phase 2)")
            rc = 0
    except AssertionError as exc:
        print(f"\n❌ ÉCHEC : {exc}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"\n❌ ERREUR : {exc}")
    finally:
        if meter_task:
            meter_task.cancel()
        # Fermeture bornée : aiortc laisse parfois traîner des tâches/threads qui
        # bloquent l'arrêt de la boucle — on ne veut pas pendre dessus.
        for coro in (pc.close(), runner.cleanup()):
            try:
                await asyncio.wait_for(coro, timeout=5)
            except Exception:  # noqa: BLE001
                pass
    return rc


def turn_of(d: dict, n: int) -> dict | None:
    for t in d.get("turns", []):
        if t["turn"] == n:
            return t
    return None


async def wait_ice_complete(pc) -> None:
    if pc.iceGatheringState == "complete":
        return
    done = asyncio.get_event_loop().create_future()

    @pc.on("icegatheringstatechange")
    def _on_change() -> None:
        if pc.iceGatheringState == "complete" and not done.done():
            done.set_result(None)

    if pc.iceGatheringState == "complete":
        return
    await done


async def poll_until(http, base, session, predicate, timeout=20.0):
    """Interroge /poll jusqu'à ce que predicate(json) soit vrai (ou timeout)."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    last = None
    while loop.time() - t0 < timeout:
        async with http.get(f"{base}/poll", params={"session": session}) as r:
            last = await r.json()
        if predicate(last):
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(f"timeout /poll (dernier état : {last})")


if __name__ == "__main__":
    # Boucle gérée à la main + os._exit : garantit une sortie immédiate même si
    # aiortc laisse des tâches/threads en fond après la fermeture des connexions.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    code = 1
    try:
        code = loop.run_until_complete(main())
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(code)
