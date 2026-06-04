"""Serveur HTTP de healthcheck, compatible Uptime Kuma.

  GET /health  → 200 {"status":"ok", "checks":{…}}  si tout va bien
               → 503 {"status":"degraded", …}        sinon
  GET /        → alias de /health

Critères durs : file in/ accessible en écriture, dossier out/ présent.
Critère STT : reporté toujours ; ne fait échouer /health que si HEALTH_REQUIRE_STT=1
(évite les fausses alertes pendant le préchargement du modèle Whisper).
"""

from __future__ import annotations

import logging
import os

import httpx
from aiohttp import web

logger = logging.getLogger("voice_bot.health")


class HealthServer:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._runner: web.AppRunner | None = None

    async def _handle(self, request: web.Request) -> web.Response:
        checks = {
            "queue_in_writable": self._dir_writable(self.cfg.queue_in),
            "queue_out_present": self.cfg.queue_out.is_dir(),
            "stt_reachable": await self._stt_ok(),
        }
        ok = (
            checks["queue_in_writable"]
            and checks["queue_out_present"]
            and (checks["stt_reachable"] or not self.cfg.health_require_stt)
        )
        return web.json_response(
            {"status": "ok" if ok else "degraded", "checks": checks},
            status=200 if ok else 503,
        )

    @staticmethod
    def _dir_writable(path) -> bool:
        return os.access(path, os.W_OK) if path.exists() else False

    async def _stt_ok(self) -> bool:
        """Ping best-effort du service STT (deux endpoints possibles)."""
        for suffix in ("/health", "/v1/models"):
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    r = await client.get(f"{self.cfg.stt_base_url}{suffix}")
                if r.status_code < 500:
                    return True
            except httpx.HTTPError:
                continue
        return False

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._handle)
        app.router.add_get("/", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.cfg.health_host, self.cfg.health_port)
        await site.start()
        logger.info(
            "Healthcheck en écoute sur http://%s:%d/health",
            self.cfg.health_host, self.cfg.health_port,
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
