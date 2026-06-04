"""Bot Telegram « Hermès voix » — assemble la boucle vocal ↔ IA ↔ vocal.

Pour chaque message **vocal** de Mehdi :
  1. Téléchargement de l'OGG/Opus Telegram.
  2. Transcription locale (STT Speaches / faster-whisper).
  3. Purge immédiate de l'audio reçu (RGPD).
  4. Écriture de l'ordre dans la file in/ de l'aiguilleur.
  5. Attente de la réponse d'Hermès dans out/.
  6. Synthèse vocale de la réponse (ou réutilisation de l'audio d'Hermès).
  7. Renvoi en vocal (sendVoice) + texte.

Les messages **texte** (ex. dictés via « Raccourci Siri ») suivent le même chemin,
en sautant simplement les étapes 1-3.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message

from .aiguilleur import Aiguilleur, Order
from .config import Config
from .health import HealthServer
from .logging_setup import setup_logging
from .stt import SpeechToText, STTError
from .tts import TextToSpeech, TTSError

logger = logging.getLogger("voice_bot")

# Limites de l'API Telegram.
_CAPTION_MAX = 1024
_MESSAGE_MAX = 4096


class VoiceBot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bot = Bot(
            cfg.telegram_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self.stt = SpeechToText(
            cfg.stt_base_url, cfg.stt_model, cfg.stt_api_key,
            cfg.stt_language, cfg.stt_timeout, cfg.stt_vad_filter,
        )
        self.tts = TextToSpeech(cfg)
        self.aig = Aiguilleur(
            cfg.queue_in, cfg.queue_out, cfg.queue_archive, cfg.audio_tmp_dir,
            cfg.out_poll_interval, cfg.out_timeout, cfg.out_quiesce,
        )
        self.health = HealthServer(cfg)
        self._register()

    # --- Enregistrement des handlers -----------------------------------------

    def _register(self) -> None:
        self.dp.message.register(self.on_start, CommandStart())
        self.dp.message.register(self.on_ping, Command("ping"))
        self.dp.message.register(self.on_voice, F.voice)
        self.dp.message.register(self.on_voice, F.audio)   # fichiers audio aussi
        self.dp.message.register(self.on_text, F.text)     # texte (dicté Siri, etc.)
        self.dp.startup.register(self.on_startup)
        self.dp.shutdown.register(self.on_shutdown)

    # --- Cycle de vie --------------------------------------------------------

    async def on_startup(self) -> None:
        self.cfg.audio_tmp_dir.mkdir(parents=True, exist_ok=True)
        self.aig.ensure_dirs()
        await self.health.start()
        me = await self.bot.get_me()
        logger.info(
            "Bot @%s démarré | STT=%s | TTS=%s | in=%s | out=%s",
            me.username, self.cfg.stt_base_url, self.cfg.tts_backend,
            self.cfg.queue_in, self.cfg.queue_out,
        )
        if not self.cfg.allowed_user_ids:
            logger.warning(
                "ALLOWED_USER_IDS vide : le bot répond à TOUT LE MONDE. "
                "Renseigne l'ID Telegram de Mehdi pour le verrouiller."
            )

    async def on_shutdown(self) -> None:
        await self.health.stop()

    # --- Contrôle d'accès ----------------------------------------------------

    def _authorized(self, message: Message) -> bool:
        if not self.cfg.allowed_user_ids:
            return True  # pas de liste blanche = ouvert (à éviter en production)
        return bool(message.from_user) and message.from_user.id in self.cfg.allowed_user_ids

    @staticmethod
    def _who(message: Message) -> str:
        u = message.from_user
        if not u:
            return ""
        return u.full_name or (u.username or str(u.id))

    @staticmethod
    def _rid(message: Message) -> str:
        """Identifiant de requête unique et traçable."""
        return f"tg-{message.chat.id}-{message.message_id}-{uuid.uuid4().hex[:8]}"

    # --- Commandes simples ---------------------------------------------------

    async def on_start(self, message: Message) -> None:
        if not self._authorized(message):
            return
        await message.answer(
            "🎙️ <b>Hermès vocal</b> est prêt.\n\n"
            "Envoie-moi un <b>message vocal</b> (ou un texte) : je le transcris, "
            "je le transmets à Hermès, puis je te réponds <b>en vocal + texte</b>.\n\n"
            f"<i>Ton ID Telegram : <code>{message.from_user.id}</code></i>"
        )

    async def on_ping(self, message: Message) -> None:
        if not self._authorized(message):
            return
        await message.answer("pong ✅")

    # --- Entrée vocale -------------------------------------------------------

    async def on_voice(self, message: Message) -> None:
        if not self._authorized(message):
            logger.warning(
                "Vocal ignoré (utilisateur non autorisé : %s)",
                message.from_user.id if message.from_user else "?",
            )
            return

        request_id = self._rid(message)
        log = logging.getLogger(f"voice_bot.{request_id}")
        ogg_in = self.cfg.audio_tmp_dir / f"{request_id}-in.ogg"
        tmp_files: list[Path] = [ogg_in]

        try:
            await self.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

            # 1) Téléchargement de l'audio Telegram.
            media = message.voice or message.audio
            await self._download(media.file_id, ogg_in)
            log.info("audio reçu (%d octets)", ogg_in.stat().st_size)

            # 2) Transcription locale.
            text = await self.stt.transcribe(ogg_in)

            # 3) Purge immédiate de l'audio reçu (RGPD : rétention 0).
            if not self.cfg.keep_audio:
                self._purge(ogg_in)
                tmp_files.remove(ogg_in)

            if not text:
                await message.answer("🤔 Je n'ai rien compris d'exploitable. Tu peux réessayer ?")
                return
            log.info("transcription : %s", text)
            if self.cfg.send_transcript:
                await message.answer(f"📝 <i>J'ai compris :</i> {html.escape(text)}")

            # 4-7) Routage Hermès + réponse vocale.
            await self._handle_order(message, text, request_id, tmp_files)

        except STTError as exc:
            log.error("STT : %s", exc)
            await self._safe_answer(message, "❌ Transcription impossible (service STT). Réessaie.")
        except Exception as exc:  # garde-fou : un imprévu ne doit pas tuer le bot
            log.exception("erreur inattendue : %s", exc)
            await self._safe_answer(message, "❌ Une erreur est survenue. Réessaie.")
        finally:
            if not self.cfg.keep_audio:
                for f in tmp_files:
                    self._purge(f)

    # --- Entrée texte (ex. dictée Siri) --------------------------------------

    async def on_text(self, message: Message) -> None:
        if not self._authorized(message):
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return  # commandes non gérées : on ignore

        request_id = self._rid(message)
        log = logging.getLogger(f"voice_bot.{request_id}")
        tmp_files: list[Path] = []
        try:
            await self.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
            await self._handle_order(message, text, request_id, tmp_files)
        except Exception as exc:
            log.exception("erreur inattendue : %s", exc)
            await self._safe_answer(message, "❌ Une erreur est survenue. Réessaie.")
        finally:
            if not self.cfg.keep_audio:
                for f in tmp_files:
                    self._purge(f)

    # --- Cœur commun : ordre → attente → réponse vocale ----------------------

    async def _handle_order(
        self, message: Message, text: str, request_id: str, tmp_files: list[Path]
    ) -> None:
        log = logging.getLogger(f"voice_bot.{request_id}")

        # 4) Écriture de l'ordre dans in/.
        order = Order(
            request_id=request_id,
            chat_id=message.chat.id,
            msg_id=message.message_id,
            text=text,
            user_name=self._who(message),
            reply_audio=True,
        )
        self.aig.write_order(order)

        # 5) Attente de la réponse d'Hermès dans out/.
        await self.bot.send_chat_action(message.chat.id, ChatAction.RECORD_VOICE)
        response = await self.aig.wait_for_response(request_id)
        if response is None:
            await message.answer("⏳ Hermès n'a pas répondu à temps. Réessaie dans un instant.")
            return
        if response.audio_path:
            tmp_files.append(response.audio_path)
        log.info("réponse Hermès : %s", (response.text or "[audio]")[:200])

        # 6) Audio de réponse : priorité à l'audio d'Hermès, sinon synthèse TTS.
        ogg_out = self.cfg.audio_tmp_dir / f"{request_id}-out.ogg"
        tmp_files.append(ogg_out)
        voice_path: Path | None = None

        if self.cfg.prefer_out_audio and response.audio_path:
            try:
                voice_path = await self.tts.convert_to_ogg(response.audio_path, ogg_out)
            except TTSError as exc:
                log.warning("conversion de l'audio d'Hermès impossible : %s", exc)

        if voice_path is None and response.text:
            try:
                voice_path = await self.tts.synthesize_to_ogg(response.text, ogg_out)
            except TTSError as exc:
                log.error("TTS impossible : %s", exc)

        # 7) Envoi de la réponse (vocal + texte).
        await self._send_reply(message, response.text, voice_path)

    # --- Envoi de la réponse -------------------------------------------------

    async def _send_reply(self, message: Message, text: str, voice_path: Path | None) -> None:
        if voice_path is not None and voice_path.exists():
            caption = None
            if self.cfg.reply_with_text and text and len(text) <= _CAPTION_MAX:
                caption = text
            # parse_mode=None : le texte d'Hermès est brut, pas du HTML.
            await message.answer_voice(
                FSInputFile(str(voice_path)), caption=caption, parse_mode=None
            )
            # Texte trop long pour la légende → envoyé à part.
            if self.cfg.reply_with_text and text and caption is None:
                await self._send_long_text(message, text)
        elif text:
            await self._send_long_text(message, text)
        else:
            await message.answer("✅ Hermès a répondu, mais sans contenu lisible.")

    async def _send_long_text(self, message: Message, text: str) -> None:
        """Envoie un texte en le découpant si nécessaire (limite Telegram)."""
        for i in range(0, len(text), _MESSAGE_MAX):
            await message.answer(text[i:i + _MESSAGE_MAX], parse_mode=None)

    # --- Utilitaires ---------------------------------------------------------

    async def _download(self, file_id: str, dest: Path) -> None:
        file = await self.bot.get_file(file_id)
        await self.bot.download_file(file.file_path, destination=dest)

    @staticmethod
    def _purge(path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    async def _safe_answer(self, message: Message, text: str) -> None:
        try:
            await message.answer(text)
        except Exception:  # ne jamais propager une erreur d'envoi d'erreur
            pass

    # --- Boucle principale ---------------------------------------------------

    async def run(self) -> None:
        try:
            await self.dp.start_polling(
                self.bot, allowed_updates=self.dp.resolve_used_update_types()
            )
        finally:
            await self.bot.session.close()


def main() -> None:
    cfg = Config.from_env()
    setup_logging(cfg.log_level)
    bot = VoiceBot(cfg)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
