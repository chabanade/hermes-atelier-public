"""Pont avec l'« aiguilleur » Hermès via une file de fichiers partagée.

  - Entrée  (in/)  : on écrit l'ordre transcrit avec un en-tête de métadonnées YAML
                     (source, request_id, chat_id, reply_handle…) pour le routage retour.
  - Sortie  (out/) : on surveille l'arrivée de la réponse d'Hermès, corrélée par
                     `request_id`.

⚠️ Le contrat exact de out/ n'est pas encore figé côté Hermès. Ce module est donc
volontairement tolérant (cf. `_match_text` / `_read_text`) :
    • réponse texte : <request_id>.md  (ou .txt / .json)
    • réponse audio : <request_id>.<ext audio>  (TTS déjà fait par Hermès)
    • à défaut, tout fichier texte récent de out/ contenant le request_id.
Adapte ces fonctions si le format réel diffère — le reste du bot n'y touche pas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("voice_bot.aiguilleur")

# Extensions reconnues pour la réponse d'Hermès.
AUDIO_EXTS = (".ogg", ".opus", ".oga", ".wav", ".mp3", ".m4a")
TEXT_EXTS = (".md", ".txt", ".json")


@dataclass
class Order:
    """Un ordre à transmettre à Hermès."""
    request_id: str
    chat_id: int
    msg_id: int
    text: str
    user_name: str = ""
    reply_audio: bool = True


@dataclass
class Response:
    """La réponse d'Hermès récupérée dans out/."""
    text: str
    audio_path: Path | None = None   # audio déjà prêt (copié dans le dossier tmp du bot)


def _now_iso() -> str:
    """Horodatage UTC ISO-8601 (sans dépendance à la TZ locale)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _yaml_quote(value: str) -> str:
    """Met une valeur scalaire entre guillemets et échappe le minimum nécessaire."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _strip_frontmatter(text: str) -> str:
    """Retire un éventuel bloc de métadonnées YAML (--- … ---) en tête de texte."""
    if text.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*\n?(.*)$", text, flags=re.DOTALL)
        if m:
            return m.group(1)
    return text


class Aiguilleur:
    def __init__(
        self,
        queue_in: Path,
        queue_out: Path,
        archive: Path | None,
        stash_dir: Path,
        poll_interval: float = 1.0,
        timeout: float = 180.0,
        quiesce: float = 0.5,
    ) -> None:
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.archive = archive
        self.stash_dir = stash_dir          # où copier l'audio d'Hermès avant archivage
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.quiesce = quiesce

    def ensure_dirs(self) -> None:
        """Crée les dossiers nécessaires (idempotent)."""
        self.queue_in.mkdir(parents=True, exist_ok=True)
        self.queue_out.mkdir(parents=True, exist_ok=True)
        self.stash_dir.mkdir(parents=True, exist_ok=True)
        if self.archive:
            self.archive.mkdir(parents=True, exist_ok=True)

    # --- Écriture de l'ordre (in/) -------------------------------------------

    def write_order(self, order: Order) -> Path:
        """Écrit l'ordre dans in/ de façon **atomique** (fichier temporaire + rename).

        Le rename garantit qu'Hermès ne lira jamais un fichier à moitié écrit.
        """
        header = "\n".join([
            "---",
            "source: telegram",
            f"request_id: {order.request_id}",
            f"chat_id: {order.chat_id}",
            f"msg_id: {order.msg_id}",
            f"user: {_yaml_quote(order.user_name)}",
            f"reply_handle: telegram:{order.chat_id}",
            f"reply_audio: {'true' if order.reply_audio else 'false'}",
            f"ts: {_now_iso()}",
            "---",
            "",
        ])
        content = header + order.text.strip() + "\n"

        final = self.queue_in / f"{order.request_id}.md"
        tmp = self.queue_in / f".{order.request_id}.md.tmp"
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, final)   # atomique sur un même système de fichiers
        logger.info("[%s] ordre déposé : %s", order.request_id, final)
        return final

    # --- Attente de la réponse (out/) ----------------------------------------

    async def wait_for_response(self, request_id: str) -> Response | None:
        """Attend la réponse d'Hermès dans out/ (polling) jusqu'au timeout."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            resp = self._find_response(request_id)
            if resp is not None:
                return resp
            await asyncio.sleep(self.poll_interval)
        logger.warning("[%s] aucune réponse dans out/ après %.0fs", request_id, self.timeout)
        return None

    def _find_response(self, request_id: str) -> Response | None:
        """Cherche (et consomme) la réponse corrélée à `request_id`."""
        text_file = self._match_text(request_id)
        if text_file is None:
            return None  # le fichier texte/JSON est notre signal de complétude
        if not self._is_quiescent(text_file):
            return None  # encore en cours d'écriture : on retentera au prochain cycle

        text = self._read_text(text_file)

        audio_copy = None
        audio_file = self._match_audio(request_id)
        if audio_file is not None and self._is_quiescent(audio_file):
            audio_copy = self._stash_audio(audio_file, request_id)

        # Consommation : on retire les fichiers de out/ (archive ou suppression).
        self._dispose(text_file)
        if audio_file is not None:
            self._dispose(audio_file)

        logger.info("[%s] réponse récupérée (%d car., audio=%s)",
                    request_id, len(text), bool(audio_copy))
        return Response(text=text, audio_path=audio_copy)

    # --- Recherche des fichiers ----------------------------------------------

    def _match_text(self, request_id: str) -> Path | None:
        # 1) Correspondance directe par nom : <request_id>.{md,txt,json}
        for ext in TEXT_EXTS:
            cand = self.queue_out / f"{request_id}{ext}"
            if cand.is_file():
                return cand
        # 2) Repli : un fichier texte récent dont le contenu mentionne le request_id.
        now = time.time()
        try:
            entries = list(self.queue_out.iterdir())
        except FileNotFoundError:
            return None
        for p in entries:
            if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in TEXT_EXTS:
                continue
            try:
                if now - p.stat().st_mtime > self.timeout + 5:
                    continue  # trop ancien pour concerner cette requête
                if request_id in p.read_text(encoding="utf-8", errors="replace"):
                    return p
            except OSError:
                continue
        return None

    def _match_audio(self, request_id: str) -> Path | None:
        for ext in AUDIO_EXTS:
            cand = self.queue_out / f"{request_id}{ext}"
            if cand.is_file():
                return cand
        return None

    def _is_quiescent(self, path: Path) -> bool:
        """Vrai si le fichier n'a plus été modifié depuis `quiesce` secondes.

        Garde-fou simple contre la lecture d'un fichier encore en cours d'écriture.
        """
        try:
            return (time.time() - path.stat().st_mtime) >= self.quiesce
        except OSError:
            return False

    # --- Lecture / extraction du texte ---------------------------------------

    def _read_text(self, path: Path) -> str:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(raw)
            except ValueError:
                return raw.strip()
            if isinstance(data, dict):
                for key in ("text", "response", "reply", "message", "content", "answer"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                return json.dumps(data, ensure_ascii=False)
            return str(data).strip()
        # .md / .txt : on retire un éventuel en-tête de métadonnées.
        return _strip_frontmatter(raw).strip()

    # --- Consommation des fichiers out/ --------------------------------------

    def _stash_audio(self, audio_file: Path, request_id: str) -> Path:
        """Copie l'audio d'Hermès dans le dossier tmp du bot (survit à l'archivage)."""
        dest = self.stash_dir / f"{request_id}-hermes{audio_file.suffix.lower()}"
        shutil.copy2(audio_file, dest)
        return dest

    def _dispose(self, path: Path) -> None:
        """Archive (déplace) ou supprime un fichier de out/ une fois traité."""
        try:
            if self.archive:
                target = self.archive / path.name
                os.replace(path, target)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Impossible de retirer %s de out/ : %s", path, exc)
