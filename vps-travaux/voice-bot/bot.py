#!/usr/bin/env python3
"""Bot Telegram vocal — partie RÉCEPTION uniquement.

Pipeline réalisé par ce module :
    1. Réception d'un message vocal Telegram (type « voice »).
    2. Téléchargement du fichier OGG (Opus) via l'API getFile.
    3. Conversion OGG -> WAV 16 kHz mono (ffmpeg).
    4. Transcription locale via Speaches (API compatible OpenAI).
    5. Écriture de la transcription dans la boîte aiguilleur (in/),
       avec un en-tête (frontmatter) décrivant la source.

La partie RÉPONSE (surveillance de out/ + sendVoice) n'est PAS gérée ici,
c'est volontaire : ce module ne fait que poser le message transcrit dans
la file d'attente.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration (surchargeable par variables d'environnement)
# ---------------------------------------------------------------------------

# Jeton du bot Telegram — OBLIGATOIRE. Récupéré auprès de @BotFather.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Point d'accès Speaches (STT local), compatible API OpenAI.
SPEACHES_URL = os.environ.get(
    "SPEACHES_URL",
    "http://speaches:8000/v1/audio/transcriptions",
)

# Modèle Whisper servi par Speaches.
SPEACHES_MODEL = os.environ.get(
    "SPEACHES_MODEL",
    "deepdml/faster-whisper-large-v3-turbo-ct2",
)

# Langue forcée pour la transcription.
SPEACHES_LANGUAGE = os.environ.get("SPEACHES_LANGUAGE", "fr")

# Boîte aiguilleur : dossier où l'on dépose les messages entrants.
QUEUE_IN_DIR = Path(os.environ.get("QUEUE_IN_DIR", "/opt/data/claude-queue/in"))

# Nom du fichier de sortie dans la boîte aiguilleur.
QUEUE_OUT_FILE = os.environ.get("QUEUE_OUT_FILE", "telegram-msg.txt")

# Délai max (secondes) pour la requête de transcription.
SPEACHES_TIMEOUT = float(os.environ.get("SPEACHES_TIMEOUT", "120"))


# ---------------------------------------------------------------------------
# Journalisation
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
# La bibliothèque httpx est très bavarde en INFO : on la calme.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("voice-bot")


# ---------------------------------------------------------------------------
# Étapes du pipeline
# ---------------------------------------------------------------------------

async def convertir_ogg_en_wav(chemin_ogg: Path, chemin_wav: Path) -> None:
    """Convertit un fichier OGG/Opus en WAV 16 kHz mono via ffmpeg.

    Whisper attend idéalement du PCM 16 bits, mono, 16 kHz : on impose donc
    ces paramètres pour une transcription optimale et un fichier léger.
    """
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",                     # écrase le fichier de sortie sans demander
        "-i", str(chemin_ogg),    # fichier source
        "-ar", "16000",           # rééchantillonnage à 16 kHz
        "-ac", "1",               # mono
        "-c:a", "pcm_s16le",      # PCM 16 bits little-endian
        str(chemin_wav),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        # On remonte la sortie d'erreur ffmpeg pour faciliter le diagnostic.
        message_erreur = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Échec de la conversion ffmpeg : {message_erreur}")


async def transcrire_via_speaches(chemin_wav: Path) -> str:
    """Envoie le WAV à Speaches et renvoie le texte transcrit.

    L'API de Speaches est compatible OpenAI : on poste un multipart/form-data
    avec le fichier audio, le modèle, la langue et le format de réponse.
    """
    with open(chemin_wav, "rb") as f:
        fichiers = {"file": ("audio.wav", f, "audio/wav")}
        donnees = {
            "model": SPEACHES_MODEL,
            "language": SPEACHES_LANGUAGE,
            "response_format": "json",
        }

        async with httpx.AsyncClient(timeout=SPEACHES_TIMEOUT) as client:
            reponse = await client.post(SPEACHES_URL, data=donnees, files=fichiers)

    reponse.raise_for_status()
    # Le format « json » renvoie un objet { "text": "..." }.
    texte = reponse.json().get("text", "").strip()
    return texte


def ecrire_dans_aiguilleur(chat_id: int, texte: str) -> Path:
    """Écrit la transcription dans la boîte aiguilleur, avec frontmatter.

    L'écriture est ATOMIQUE : on écrit d'abord dans un fichier temporaire du
    même dossier, puis on le renomme. Un consommateur ne lira donc jamais un
    fichier à moitié écrit.
    """
    QUEUE_IN_DIR.mkdir(parents=True, exist_ok=True)
    chemin_final = QUEUE_IN_DIR / QUEUE_OUT_FILE

    # En-tête décrivant la source du message + drapeau de réponse vocale.
    contenu = (
        "---\n"
        "source: telegram\n"
        f"chat_id: {chat_id}\n"
        "reply_audio: true\n"
        "---\n"
        f"{texte}\n"
    )

    # Écriture atomique : fichier temporaire puis remplacement.
    fd, chemin_temporaire = tempfile.mkstemp(dir=QUEUE_IN_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenu)
        os.replace(chemin_temporaire, chemin_final)
    except Exception:
        # En cas d'échec, on ne laisse pas de fichier temporaire orphelin.
        if os.path.exists(chemin_temporaire):
            os.remove(chemin_temporaire)
        raise

    return chemin_final


# ---------------------------------------------------------------------------
# Gestionnaire des messages vocaux
# ---------------------------------------------------------------------------

async def gerer_message_vocal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Traite un message vocal entrant de bout en bout."""
    message = update.message
    chat_id = update.effective_chat.id
    voice = message.voice

    logger.info("Message vocal reçu de chat_id=%s (durée=%ss)", chat_id, voice.duration)

    # On travaille dans un dossier temporaire auto-nettoyé.
    with tempfile.TemporaryDirectory(prefix="voice-bot-") as dossier_temp:
        dossier_temp = Path(dossier_temp)
        chemin_ogg = dossier_temp / "message.ogg"
        chemin_wav = dossier_temp / "message.wav"

        try:
            # 1. Téléchargement du fichier OGG (getFile + download).
            fichier = await context.bot.get_file(voice.file_id)
            await fichier.download_to_drive(custom_path=str(chemin_ogg))
            logger.info("Fichier OGG téléchargé : %s", chemin_ogg)

            # 2. Conversion OGG -> WAV.
            await convertir_ogg_en_wav(chemin_ogg, chemin_wav)
            logger.info("Fichier converti en WAV : %s", chemin_wav)

            # 3. Transcription via Speaches.
            texte = await transcrire_via_speaches(chemin_wav)
            if not texte:
                logger.warning("Transcription vide pour chat_id=%s", chat_id)
                await message.reply_text("⚠️ Transcription vide, rien à traiter.")
                return
            logger.info("Transcription obtenue (%d caractères)", len(texte))

            # 4. Dépôt dans la boîte aiguilleur.
            chemin = ecrire_dans_aiguilleur(chat_id, texte)
            logger.info("Message déposé dans l'aiguilleur : %s", chemin)

            # Accusé de réception (texte simple — ce n'est PAS la réponse vocale).
            await message.reply_text("✅ Reçu et transcrit, message mis en file.")

        except Exception:
            # On loggue la trace complète et on prévient l'utilisateur sans planter le bot.
            logger.exception("Erreur lors du traitement du message vocal")
            await message.reply_text("❌ Erreur lors du traitement du message vocal.")


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> None:
    """Démarre le bot en mode long polling."""
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "La variable d'environnement TELEGRAM_BOT_TOKEN n'est pas définie."
        )

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # On ne s'abonne qu'aux messages vocaux (type « voice »).
    application.add_handler(MessageHandler(filters.VOICE, gerer_message_vocal))

    logger.info("Bot vocal démarré — en attente de messages vocaux…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
