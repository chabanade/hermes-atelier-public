#!/usr/bin/env python3
"""
Hermes Voc Bot — façade VOIX Telegram pour Hermès (l'assistant).

Mehdi parle (note vocale) OU écrit (dictée Siri / clavier) à @ermes_Voc_bot ;
Hermès lui répond en TEXTE **et** en NOTE VOCALE.

Architecture (réutilise ce qui marche déjà, ne touche NI Hermès NI l'aiguilleur) :
  1. Telegram -> on récupère le message (vocal -> OGG, ou texte brut).
  2. Si vocal : transcription locale via Speaches (faster-whisper, FR).
  3. On dépose le texte dans la BOÎTE PARTAGÉE d'Hermès
     (WEBRTC_DATA_DIR/inbox/<id>.json, status=pending) — exactement comme la
     page web. Le pont `webrtc-reply.sh` (déjà actif, scrute toutes les 1 s)
     appelle Hermès et écrit la réponse dans .../outbox/<id>.json.
  4. On attend la réponse (poll de l'outbox), puis on l'envoie à Mehdi :
       • en TEXTE (toujours, fiable, relisible) ;
       • en NOTE VOCALE : Piper (synth.py) -> WAV -> ffmpeg -> OGG/Opus -> sendVoice.
  5. La synthèse échoue ? On a déjà envoyé le texte : on dégrade en douceur.

Sécurité : le bot ne répond qu'au chat autorisé (ALLOWED_CHAT_ID = Mehdi).
Tout est local (RGPD) : STT et TTS tournent sur le VPS, rien ne sort vers un tiers.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------- #
# Configuration (surchargeable via .env / environnement)
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def load_telegram_token() -> str:
    b64 = os.environ.get("BOT_TOKEN_B64", "").strip()
    if b64:
        try:
            return base64.b64decode(b64).decode().strip()
        except Exception:
            logging.getLogger("hermes-voc-bot").error("BOT_TOKEN_B64 indécodable ; repli TELEGRAM_TOKEN.")
    return os.environ.get("TELEGRAM_TOKEN", "").strip()


TELEGRAM_TOKEN = load_telegram_token()

# STT (Speaches). Le bot tourne sur l'HÔTE -> 127.0.0.1:8000 (et non speaches:8000
# qui n'existe que dans le réseau Docker).
SPEACHES_URL = os.environ.get("SPEACHES_URL", "http://127.0.0.1:8000").rstrip("/")
SPEACHES_MODEL = os.environ.get("SPEACHES_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
LANGUAGE = os.environ.get("LANGUAGE", "fr")

# Boîte PARTAGÉE avec le pont Hermès (webrtc-reply.sh scrute inbox/, écrit outbox/).
DATA_DIR = Path(os.environ.get("WEBRTC_DATA_DIR", "/home/ouvrier/travaux/webrtc-vocal/data"))
INBOX_DIR = DATA_DIR / "inbox"
OUTBOX_DIR = DATA_DIR / "outbox"

# TTS (Piper via synth.py one-shot) + conversion ffmpeg pour la note vocale.
PIPER_PYTHON = os.environ.get("PIPER_PYTHON", "/home/ouvrier/travaux/webrtc-vocal/.venv/bin/python")
SYNTH_SCRIPT = os.environ.get("SYNTH_SCRIPT", "/home/ouvrier/travaux/webrtc-vocal/synth.py")
PIPER_VOICE = os.environ.get("PIPER_VOICE", "/home/ouvrier/travaux/webrtc-vocal/models/piper/fr_FR-upmc-medium.onnx")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1200"))  # on ne synthétise pas un roman

# Voix JARVIS « à la demande » : convertisseur RVC dans son propre venv (Python 3.10).
# Lent (~25 s/phrase sur CPU) -> n'est utilisé QUE si le chat est passé en mode jarvis.
RVC_PYTHON = os.environ.get("RVC_PYTHON", "/home/ouvrier/travaux/rvc-jarvis/.venv/bin/python")
RVC_CONVERT = os.environ.get("RVC_CONVERT", "/home/ouvrier/travaux/rvc-jarvis/jarvis_convert.py")
RVC_TIMEOUT = float(os.environ.get("RVC_TIMEOUT", "150"))
# Mode de voix par chat : "tom" (rapide, défaut) ou "jarvis" (timbre JARVIS, lent).
MODE_VOIX = {}

# Combien de temps on attend la réponse d'Hermès (poll de l'outbox).
REPLY_TIMEOUT = float(os.environ.get("REPLY_TIMEOUT", "90"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "0.5"))
STT_TIMEOUT = float(os.environ.get("STT_TIMEOUT", "120"))

# Sécurité : on ne répond QU'à Mehdi. Vide = tout le monde (déconseillé).
ALLOWED_CHAT_ID = os.environ.get("ALLOWED_CHAT_ID", "VOTRE_CHAT_ID_TELEGRAM").strip()
READY_CHAT_ID = os.environ.get("READY_CHAT_ID", "").strip()

TELEGRAM_MSG_LIMIT = 4096

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("hermes-voc-bot")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def autorise(update: Update) -> bool:
    """Vrai si le message vient du chat autorisé (ou si aucun filtre n'est posé)."""
    if not ALLOWED_CHAT_ID:
        return True
    chat = update.effective_chat
    return chat is not None and str(chat.id) == ALLOWED_CHAT_ID


def detecter_mode(texte: str, chat_id: int) -> str | None:
    """Détecte une demande de changement de voix dans le message (vocal ou texte).
    Renvoie un message de confirmation si la voix a changé, sinon None."""
    t = (texte or "").lower()
    if any(k in t for k in ("mode jarvis", "voix jarvis", "en jarvis", "passe en jarvis",
                            "parle comme jarvis", "comme jarvis")):
        MODE_VOIX[chat_id] = "jarvis"
        return ("🎩 Mode JARVIS activé : mes réponses vocales prendront son timbre "
                "(un peu plus lentes). Dis « voix normale » pour revenir.")
    if any(k in t for k in ("voix normale", "mode normal", "voix rapide", "voix tom",
                            "arrête jarvis", "arrete jarvis", "stop jarvis")):
        MODE_VOIX[chat_id] = "tom"
        return "✅ Voix rapide rétablie."
    return None


async def send_chunked(bot, chat_id, text: str) -> None:
    if not text:
        return
    for i in range(0, len(text), TELEGRAM_MSG_LIMIT):
        try:
            await bot.send_message(chat_id=chat_id, text=text[i:i + TELEGRAM_MSG_LIMIT])
        except Exception:
            # Un fragment qui échoue (réseau Telegram) ne doit pas tout interrompre.
            logger.exception("Échec d'envoi d'un fragment de réponse à %s", chat_id)


# --------------------------------------------------------------------------- #
# STT (Speaches)
# --------------------------------------------------------------------------- #
async def transcribe_audio(data: bytes, filename: str = "voice.ogg") -> str:
    url = f"{SPEACHES_URL}/v1/audio/transcriptions"
    files = {"file": (filename, data, "audio/ogg")}
    form = {"model": SPEACHES_MODEL, "language": LANGUAGE, "response_format": "json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(STT_TIMEOUT)) as client:
        resp = await client.post(url, files=files, data=form)
        resp.raise_for_status()
        payload = resp.json()
    return (payload.get("text") or "").strip()


# --------------------------------------------------------------------------- #
# Pont Hermès : écrire l'inbox partagée, attendre la réponse dans l'outbox
# --------------------------------------------------------------------------- #
def _ecrire_inbox(session_id: str, text: str, chat_id: int) -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    record = {"session_id": session_id, "text": text, "status": "pending",
              "source": "telegram", "chat_id": chat_id}
    path = INBOX_DIR / f"{session_id}.json"
    tmp = INBOX_DIR / f".{session_id}.json.tmp"
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # atomique : webrtc-reply ne voit jamais un demi-fichier


async def demander_a_hermes(text: str, chat_id: int) -> str | None:
    """Dépose le texte pour Hermès et attend sa réponse. None si délai dépassé."""
    session_id = f"tg-{chat_id}-{uuid.uuid4().hex[:8]}"
    await asyncio.to_thread(_ecrire_inbox, session_id, text, chat_id)
    outfile = OUTBOX_DIR / f"{session_id}.json"
    deadline = time.monotonic() + REPLY_TIMEOUT
    while time.monotonic() < deadline:
        if outfile.exists():
            try:
                data = json.loads(outfile.read_text(encoding="utf-8"))
            except Exception:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            reply = (data.get("reply") or "").strip()
            try:
                outfile.unlink()  # ménage : on retire l'outbox lu
            except Exception:
                pass
            return reply
        await asyncio.sleep(POLL_INTERVAL)
    return None


# --------------------------------------------------------------------------- #
# TTS : texte -> note vocale (Piper one-shot -> WAV -> ffmpeg -> OGG/Opus)
# --------------------------------------------------------------------------- #
async def _run(cmd: list[str], stdin: bytes | None = None, timeout: float = 60) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(input=stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, b"", b"timeout"
    return proc.returncode, out, err


async def synthetiser_ogg(text: str, jarvis: bool = False) -> Path | None:
    """Renvoie un OGG/Opus prêt pour sendVoice, ou None si la synthèse échoue.
    Si jarvis=True : voix Piper -> conversion RVC vers le timbre JARVIS (lent)."""
    text = text.strip()[:TTS_MAX_CHARS]
    if not text:
        return None
    uid = uuid.uuid4().hex[:10]
    wav = Path(f"/tmp/voc-{uid}.wav")
    jwav = Path(f"/tmp/voc-{uid}.jarvis.wav")
    ogg = Path(f"/tmp/voc-{uid}.ogg")
    src = wav  # le WAV à encoder en OGG : Piper, ou JARVIS si le mode est actif
    try:
        rc, _out, err = await _run(
            [PIPER_PYTHON, SYNTH_SCRIPT, "--out", str(wav), "--voice", PIPER_VOICE],
            stdin=text.encode("utf-8"), timeout=90,
        )
        if rc != 0 or not wav.exists() or wav.stat().st_size == 0:
            logger.warning("Synthèse Piper échouée (rc=%s) : %s", rc, err[:200])
            return None
        if jarvis:
            # Timbre JARVIS (RVC) : lent (~25 s, CPU), donc à la demande seulement.
            rc, _out, err = await _run(
                [RVC_PYTHON, RVC_CONVERT, str(wav), str(jwav)], timeout=RVC_TIMEOUT,
            )
            if rc == 0 and jwav.exists() and jwav.stat().st_size > 0:
                src = jwav
            else:
                logger.warning("Conversion JARVIS échouée (rc=%s) : %s — repli voix normale.", rc, err[:200])
        rc, _out, err = await _run(
            [FFMPEG_BIN, "-y", "-i", str(src), "-c:a", "libopus", "-b:a", "48k",
             "-ar", "48000", str(ogg)], timeout=60,
        )
        if rc != 0 or not ogg.exists() or ogg.stat().st_size == 0:
            logger.warning("Conversion ffmpeg échouée (rc=%s) : %s", rc, err[:200])
            return None
        return ogg
    except Exception:
        logger.exception("Erreur de synthèse vocale")
        return None
    finally:
        for p in (wav, jwav):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def nettoyer(ogg: Path | None) -> None:
    if ogg is not None:
        try:
            ogg.unlink(missing_ok=True)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Cœur : traiter un texte (venant d'un vocal OU d'un message texte)
# --------------------------------------------------------------------------- #
async def traiter_et_repondre(update: Update, context: ContextTypes.DEFAULT_TYPE, texte_entree: str) -> None:
    message = update.message
    chat_id = message.chat_id
    jarvis = MODE_VOIX.get(chat_id) == "jarvis"

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    reply = await demander_a_hermes(texte_entree, chat_id)

    if reply is None:
        await message.reply_text("⏳ Hermès met un peu de temps à répondre — réessaie dans un instant.")
        return
    if not reply:
        await message.reply_text("🤔 Hermès n'a rien renvoyé cette fois. Reformule peut-être ?")
        return

    # 1) Toujours le texte (fiable, relisible).
    await send_chunked(context.bot, chat_id, reply)

    # 2) Puis la note vocale (dégradation douce si la synthèse échoue).
    ogg = await synthetiser_ogg(reply, jarvis=jarvis)
    if ogg is not None:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
            with ogg.open("rb") as f:
                await context.bot.send_voice(chat_id=chat_id, voice=f)
        except Exception:
            logger.exception("Échec de l'envoi de la note vocale (le texte est déjà parti).")
        finally:
            nettoyer(ogg)


# --------------------------------------------------------------------------- #
# Handlers Telegram
# --------------------------------------------------------------------------- #
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update):
        return
    message = update.message
    media = message.voice or message.audio
    if media is None:
        return
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
    try:
        tg_file = await context.bot.get_file(media.file_id)
        audio = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("Échec téléchargement audio")
        await message.reply_text("⚠️ Impossible de récupérer ton audio. Réessaie.")
        return
    try:
        texte = await transcribe_audio(audio, filename="voice.ogg")
    except httpx.HTTPStatusError as exc:
        logger.error("Speaches HTTP %s : %s", exc.response.status_code, exc.response.text[:200])
        await message.reply_text("⚠️ La transcription a échoué (service STT). Réessaie dans un instant.")
        return
    except httpx.RequestError as exc:
        logger.error("Speaches injoignable : %r", exc)
        await message.reply_text("⚠️ Service de transcription injoignable. Réessaie bientôt. 🙏")
        return
    except Exception:
        logger.exception("Erreur transcription")
        await message.reply_text("⚠️ Erreur pendant la transcription.")
        return
    if not texte:
        await message.reply_text("🤔 Je n'ai rien entendu d'intelligible (audio vide ou silencieux).")
        return
    switch = detecter_mode(texte, message.chat_id)
    if switch:
        await message.reply_text(switch)
        return
    logger.info("Vocal transcrit (%d car.), envoi à Hermès.", len(texte))
    await traiter_et_repondre(update, context, texte)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Message texte (dictée Siri / clavier) -> traité comme une question à Hermès."""
    if not autorise(update):
        return
    texte = (update.message.text or "").strip()
    if not texte:
        return
    switch = detecter_mode(texte, update.effective_chat.id)
    if switch:
        await update.message.reply_text(switch)
        return
    logger.info("Texte reçu (%d car.), envoi à Hermès.", len(texte))
    await traiter_et_repondre(update, context, texte)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update):
        return
    await update.message.reply_text(
        "Bot vocal d'Hermès prêt ✅\n\n"
        "Parle-moi (note vocale) ou écris-moi (dictée Siri, clavier) : "
        "Hermès te répond en texte ET en voix. 🎙️🧠"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update):
        return
    await update.message.reply_text(
        "🎙️ Hermes Voc Bot\n\n"
        "• Note vocale → je transcris (français) et Hermès répond en texte + voix.\n"
        "• Message texte (dictée Siri) → Hermès répond en texte + voix.\n"
        "• Tout est local (RGPD) : transcription et voix sur le serveur.\n\n"
        "Voix : /jarvis (timbre JARVIS, plus lent) · /normal (voix rapide).\n"
        "Commandes : /start /help /jarvis /normal"
    )


async def cmd_jarvis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update):
        return
    MODE_VOIX[update.effective_chat.id] = "jarvis"
    await update.message.reply_text(
        "🎩 Mode JARVIS activé. Mes réponses vocales prendront son timbre — c'est un peu "
        "plus lent (~15-20 s). Dis /normal (ou « voix normale ») pour revenir à la voix rapide."
    )


async def cmd_normal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not autorise(update):
        return
    MODE_VOIX[update.effective_chat.id] = "tom"
    await update.message.reply_text("✅ Voix rapide rétablie.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception non gérée", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Démarrage
# --------------------------------------------------------------------------- #
async def on_startup(app: Application) -> None:
    me = await app.bot.get_me()
    logger.info("Bot @%s (id=%s) en ligne | STT=%s | inbox=%s | chat autorisé=%s",
                me.username, me.id, SPEACHES_URL, INBOX_DIR, ALLOWED_CHAT_ID or "tous")
    if READY_CHAT_ID:
        try:
            await app.bot.send_message(chat_id=READY_CHAT_ID, text="Bot vocal d'Hermès prêt ✅")
        except Exception:
            logger.exception("Échec ping de démarrage")


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("Token Telegram manquant (TELEGRAM_TOKEN dans %s/.env).", BASE_DIR)
        sys.exit(1)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("jarvis", cmd_jarvis))
    app.add_handler(CommandHandler(["normal", "tom", "rapide"], cmd_normal))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)

    logger.info("Démarrage du polling Telegram…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
