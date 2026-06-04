"""Bot Telegram vocal « Hermès » — Étape 0 (quick-win) de l'architecture voix↔IA.

Boucle complète : message vocal de Mehdi → transcription locale (faster-whisper) →
ordre déposé dans la file de l'aiguilleur → réponse d'Hermès → synthèse vocale →
renvoi en vocal + texte sur Telegram.

100 % local (STT/TTS sur le VPS), audio purgé après usage (RGPD).
"""

__version__ = "0.1.0"
