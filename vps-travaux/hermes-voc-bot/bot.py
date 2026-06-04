#!/usr/bin/env python3
"""
Hermes Voc Bot — pont Telegram ↔ Speaches (STT local, français) ↔ Hermès.

Flux :
  1. Reçoit un message vocal (ou audio) Telegram.
  2. Télécharge le fichier audio (.ogg pour les vocaux Telegram).
  3. L'envoie à Speaches (API OpenAI-compatible /v1/audio/transcriptions).
  4. Dépose la transcription dans INBOX_DIR sous forme de JSON « pending » :
     Hermès (l'assistant) lit ce fichier, réfléchit, puis renvoie sa réponse
     en POSTant sur l'endpoint HTTP /reply de ce bot.
  5. Répond « 🧠 Hermes réfléchit… » à l'utilisateur (accusé de réception).
  6. Archive aussi la transcription brute dans TRANSCRIPTS_DIR.

Endpoint HTTP local (aiohttp, port 8080 par défaut, bind 127.0.0.1) :
  • POST /reply  { "chat_id": ..., "text": "réponse de Hermès" }
                 → envoie le texte au chat via l'API Telegram.
  • GET  /health → sonde de vivacité.

Pas de ffmpeg : Speaches accepte directement l'ogg/opus de Telegram.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from aiohttp import web
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
# Configuration (tout est surchargeable via .env / variables d'environnement)
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def load_telegram_token() -> str:
    """Token Telegram.

    Priorité à BOT_TOKEN_B64 (token encodé en base64, passé par l'environnement
    au lancement) ; à défaut, TELEGRAM_TOKEN en clair (typiquement via .env).
    """
    b64 = os.environ.get("BOT_TOKEN_B64", "").strip()
    if b64:
        try:
            return base64.b64decode(b64).decode().strip()
        except Exception:
            logging.getLogger("hermes-voc-bot").error(
                "BOT_TOKEN_B64 présent mais indécodable ; repli sur TELEGRAM_TOKEN."
            )
    return os.environ.get("TELEGRAM_TOKEN", "").strip()


TELEGRAM_TOKEN = load_telegram_token()
SPEACHES_URL = os.environ.get("SPEACHES_URL", "http://speaches:8000").rstrip("/")
SPEACHES_MODEL = os.environ.get("SPEACHES_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2")
LANGUAGE = os.environ.get("LANGUAGE", "fr")
TRANSCRIPTS_DIR = Path(os.environ.get("TRANSCRIPTS_DIR", BASE_DIR / "transcripts"))
INBOX_DIR = Path(os.environ.get("INBOX_DIR", BASE_DIR / "inbox"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "180"))
READY_CHAT_ID = os.environ.get("READY_CHAT_ID", "").strip()  # ping de démarrage facultatif

# Endpoint HTTP /reply : par défaut on n'écoute QUE sur la loopback, car Hermès
# tourne sur la même machine. Ne PAS exposer 0.0.0.0 sans authentification :
# /reply peut envoyer un message arbitraire à n'importe quel chat.
HTTP_HOST = os.environ.get("HTTP_HOST", "127.0.0.1").strip()
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
# Jeton partagé facultatif : si renseigné, /reply exige l'en-tête X-Auth-Token.
REPLY_AUTH_TOKEN = os.environ.get("REPLY_AUTH_TOKEN", "").strip()

READY_MESSAGE = "Bot vocal prêt ✅"
THINKING_MESSAGE = "🧠 Hermes réfléchit…"
TELEGRAM_MSG_LIMIT = 4096  # limite d'un message Telegram

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
# httpx est bavard au niveau INFO (une ligne par requête) : on le calme.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("hermes-voc-bot")


# --------------------------------------------------------------------------- #
# Speaches : transcription
# --------------------------------------------------------------------------- #
async def transcribe_audio(data: bytes, filename: str = "voice.ogg") -> str:
    """Envoie l'audio à Speaches et renvoie le texte transcrit (peut être vide)."""
    url = f"{SPEACHES_URL}/v1/audio/transcriptions"
    files = {"file": (filename, data, "audio/ogg")}
    form = {
        "model": SPEACHES_MODEL,
        "language": LANGUAGE,
        "response_format": "json",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT)) as client:
        resp = await client.post(url, files=files, data=form)
        resp.raise_for_status()
        payload = resp.json()
    return (payload.get("text") or "").strip()


# --------------------------------------------------------------------------- #
# Inbox Hermès : dépôt de la transcription pour traitement par l'assistant
# --------------------------------------------------------------------------- #
def write_inbox(
    text: str,
    *,
    chat_id: int,
    user_id: int | None,
    message_id: int,
    user: str,
    source: str,
) -> Path:
    """Dépose la transcription dans INBOX_DIR sous forme de JSON « pending ».

    Écriture atomique (fichier temporaire + os.replace) pour qu'Hermès ne lise
    jamais un fichier partiellement écrit.
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    record = {
        "ts": now.isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "user_id": user_id,
        "text": text,
        "status": "pending",
        # --- contexte additionnel, utile à Hermès pour répondre ---
        "message_id": message_id,
        "user": user,
        "source": source,
    }
    name = f"{now:%Y-%m-%d_%H-%M-%S}_chat{chat_id}_msg{message_id}.json"
    path = INBOX_DIR / name
    tmp = INBOX_DIR / (name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomique sur le même système de fichiers
    return path


# --------------------------------------------------------------------------- #
# Transcripts : archivage horodaté (canal secondaire, séparé de l'inbox)
# --------------------------------------------------------------------------- #
def save_transcript(
    text: str,
    *,
    user: str,
    chat_id: int,
    message_id: int,
    duration: int | None,
    source: str,
) -> Path:
    """Écrit la transcription dans un fichier .txt horodaté et renvoie son chemin."""
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    name = f"{now:%Y-%m-%d_%H-%M-%S}_chat{chat_id}_msg{message_id}.txt"
    path = TRANSCRIPTS_DIR / name
    header = (
        f"date       : {now.isoformat(timespec='seconds')}\n"
        f"user       : {user}\n"
        f"chat_id    : {chat_id}\n"
        f"message_id : {message_id}\n"
        f"source     : {source}\n"
        f"duration_s : {duration if duration is not None else '?'}\n"
        f"model      : {SPEACHES_MODEL}\n"
        f"language   : {LANGUAGE}\n"
        f"{'-' * 50}\n\n"
    )
    path.write_text(header + text + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def send_chunked(bot, chat_id, text: str) -> None:
    """Envoie `text` à `chat_id`, découpé pour respecter la limite Telegram."""
    if not text:
        return
    for i in range(0, len(text), TELEGRAM_MSG_LIMIT):
        await bot.send_message(chat_id=chat_id, text=text[i : i + TELEGRAM_MSG_LIMIT])


def describe_user(update: Update) -> str:
    u = update.effective_user
    if u is None:
        return "inconnu"
    handle = f"@{u.username}" if u.username else ""
    return f"{u.full_name} {handle} (id={u.id})".strip()


# --------------------------------------------------------------------------- #
# Endpoint HTTP local (aiohttp) : Hermès POSTe ses réponses ici
# --------------------------------------------------------------------------- #
async def handle_reply(request: web.Request) -> web.Response:
    """POST /reply { chat_id, text } → envoie le texte au chat via Telegram."""
    if REPLY_AUTH_TOKEN and request.headers.get("X-Auth-Token") != REPLY_AUTH_TOKEN:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "corps JSON invalide"}, status=400)

    chat_id = payload.get("chat_id")
    text = payload.get("text")
    # chat_id : entier (id) ou chaîne (@canal) ; text : non vide.
    if chat_id is None or not isinstance(chat_id, (int, str)):
        return web.json_response({"ok": False, "error": "chat_id requis (int ou str)"}, status=400)
    if not isinstance(text, str) or not text.strip():
        return web.json_response({"ok": False, "error": "text requis (chaîne non vide)"}, status=400)

    bot = request.app["tg_app"].bot
    try:
        await send_chunked(bot, chat_id, text)
    except Exception as exc:
        logger.exception("Échec de l'envoi de la réponse Hermès à chat_id=%s", chat_id)
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    logger.info("Réponse Hermès envoyée à chat_id=%s (%d car.)", chat_id, len(text))
    return web.json_response({"ok": True, "sent_chars": len(text)})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "hermes-voc-bot"})


async def start_http_server(application: Application) -> None:
    """Démarre le serveur aiohttp dans la boucle asyncio de PTB."""
    http_app = web.Application()
    http_app["tg_app"] = application
    http_app.add_routes(
        [
            web.post("/reply", handle_reply),
            web.get("/health", handle_health),
        ]
    )
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
    await site.start()
    application.bot_data["http_runner"] = runner
    logger.info(
        "Endpoint HTTP en écoute sur http://%s:%s  (POST /reply, GET /health)",
        HTTP_HOST, HTTP_PORT,
    )


async def stop_http_server(application: Application) -> None:
    runner = application.bot_data.pop("http_runner", None)
    if runner is not None:
        await runner.cleanup()
        logger.info("Endpoint HTTP arrêté.")


# --------------------------------------------------------------------------- #
# Handlers Telegram
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"{READY_MESSAGE}\n\n"
        "Envoie-moi un *message vocal* : je le transcris, puis Hermès te répond. 🎙️🧠",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎙️ *Hermes Voc Bot*\n\n"
        "• Envoie un message vocal → je le transcris (français) et le transmets à Hermès.\n"
        "• Hermès réfléchit, puis sa réponse t'est renvoyée ici.\n"
        "• Les transcriptions sont aussi archivées côté serveur.\n\n"
        "Commandes : /start /help",
        parse_mode="Markdown",
    )


async def cmd_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toute réponse à un message texte : confirme que le bot est en ligne."""
    await update.message.reply_text(
        f"{READY_MESSAGE} — envoie-moi un message vocal 🎙️"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    media = message.voice or message.audio
    if media is None:
        return

    source = "voice" if message.voice else "audio"
    user = describe_user(update)
    logger.info("Vocal reçu de %s (chat=%s, %ss)", user, message.chat_id, getattr(media, "duration", "?"))

    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)

    # 1-2. Téléchargement du fichier audio Telegram
    try:
        tg_file = await context.bot.get_file(media.file_id)
        audio = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("Échec du téléchargement de l'audio")
        await message.reply_text("⚠️ Impossible de récupérer ton audio. Réessaie, s'il te plaît.")
        return

    # 3. Transcription via Speaches
    try:
        text = await transcribe_audio(audio, filename=f"{source}.ogg")
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300]
        logger.error("Speaches a renvoyé HTTP %s : %s", exc.response.status_code, body)
        await message.reply_text(
            "⚠️ La transcription a échoué (erreur du service STT). Réessaie dans un instant."
        )
        return
    except httpx.RequestError as exc:
        logger.error("Speaches injoignable (%s) : %r", SPEACHES_URL, exc)
        await message.reply_text(
            "⚠️ Service de transcription injoignable pour le moment. Réessaie bientôt. 🙏"
        )
        return
    except Exception:
        logger.exception("Erreur inattendue pendant la transcription")
        await message.reply_text("⚠️ Une erreur est survenue pendant la transcription.")
        return

    # Audio vide / silencieux
    if not text:
        await message.reply_text("🤔 Je n'ai rien entendu d'intelligible (audio vide ou silencieux).")
        return

    # Archivage de la transcription brute (ne doit jamais bloquer la suite)
    try:
        path = save_transcript(
            text,
            user=user,
            chat_id=message.chat_id,
            message_id=message.message_id,
            duration=getattr(media, "duration", None),
            source=source,
        )
        logger.info("Transcription archivée : %s", path)
    except Exception:
        logger.exception("Impossible d'écrire la transcription sur disque")

    # 4. Dépôt dans l'inbox Hermès (l'assistant lira ce JSON et répondra via /reply).
    #    Si ce dépôt échoue, l'utilisateur n'aurait aucune réponse : on le prévient.
    try:
        inbox_path = write_inbox(
            text,
            chat_id=message.chat_id,
            user_id=update.effective_user.id if update.effective_user else None,
            message_id=message.message_id,
            user=user,
            source=source,
        )
        logger.info("Vocal déposé dans l'inbox Hermès : %s", inbox_path)
    except Exception:
        logger.exception("Impossible d'écrire le message dans l'inbox Hermès")
        await message.reply_text(
            "⚠️ Transcription faite mais impossible de la transmettre à Hermès. Réessaie."
        )
        return

    # 5. Accusé de réception : Hermès prend le relais.
    await message.reply_text(THINKING_MESSAGE)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception non gérée dans un handler", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Démarrage / arrêt
# --------------------------------------------------------------------------- #
async def on_startup(app: Application) -> None:
    me = await app.bot.get_me()
    logger.info(
        "Bot @%s (id=%s) en ligne | Speaches=%s | model=%s | langue=%s | inbox=%s | transcripts=%s",
        me.username, me.id, SPEACHES_URL, SPEACHES_MODEL, LANGUAGE, INBOX_DIR, TRANSCRIPTS_DIR,
    )
    await start_http_server(app)
    if READY_CHAT_ID:
        try:
            await app.bot.send_message(chat_id=READY_CHAT_ID, text=READY_MESSAGE)
            logger.info("Ping de démarrage « %s » envoyé à %s", READY_MESSAGE, READY_CHAT_ID)
        except Exception:
            logger.exception("Échec du ping de démarrage vers %s", READY_CHAT_ID)


async def on_shutdown(app: Application) -> None:
    await stop_http_server(app)


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error(
            "Token Telegram manquant : renseigne TELEGRAM_TOKEN dans %s/.env "
            "ou exporte BOT_TOKEN_B64 (token en base64).",
            BASE_DIR,
        )
        sys.exit(1)

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_text))
    app.add_error_handler(on_error)

    logger.info("Démarrage du polling Telegram…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
