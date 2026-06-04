# Hermes Voc Bot

Pont **Telegram ↔ Speaches (STT local, français) ↔ Hermès**. Reçoit un message
vocal, le transcrit via Speaches, **dépose la transcription dans une _inbox_ que
l'assistant Hermès traite**, puis renvoie la réponse d'Hermès dans le chat.

```
Telegram (vocal .ogg) → bot.py → Speaches /v1/audio/transcriptions → texte
                                   ├→ inbox/AAAA-MM-JJ_HH-MM-SS_chat…_msg….json  (status: pending)
                                   ├→ transcripts/AAAA-MM-JJ_HH-MM-SS_chat…_msg….txt  (archive)
                                   └→ réponse à l'utilisateur : « 🧠 Hermes réfléchit… »

Hermès (lit l'inbox) ──HTTP POST /reply {chat_id,text}──→ bot.py → Telegram (réponse au chat)
```

Hermès n'a pas d'API HTTP propre : il **lit les fichiers JSON de l'inbox**,
réfléchit, puis **POSTe sa réponse** sur l'endpoint `/reply` du bot (voir plus bas).

## Fonctionnement

1. Réception d'un message vocal (ou audio) Telegram.
2. Téléchargement du fichier (.ogg/opus — **aucun ffmpeg requis**, Speaches l'accepte tel quel).
3. POST multipart vers `/v1/audio/transcriptions` (`model`, `language=fr`).
4. Archivage d'un `.txt` horodaté dans `TRANSCRIPTS_DIR`.
5. **Dépôt d'un `.json` « pending » dans `INBOX_DIR`** (écriture atomique).
6. Réponse **« 🧠 Hermes réfléchit… »** à l'utilisateur (accusé de réception).
7. Hermès traite l'inbox puis appelle `POST /reply` → le bot envoie sa réponse au chat.

Gestion d'erreurs : service injoignable, erreur HTTP STT, audio vide, échec
d'écriture inbox/disque — chacun renvoie un message clair et n'interrompt pas le bot.

### Format d'un fichier inbox

```json
{
  "ts": "2026-06-04T01:15:00",
  "chat_id": VOTRE_CHAT_ID_TELEGRAM,
  "user_id": 123456,
  "text": "texte transcrit",
  "status": "pending",
  "message_id": 42,
  "user": "Prénom Nom @handle (id=123456)",
  "source": "voice"
}
```

Les quatre derniers champs sont du contexte additionnel pour Hermès ; les cinq
premiers (`ts`, `chat_id`, `user_id`, `text`, `status`) sont le contrat minimal.

### Endpoint HTTP (aiohttp, dans la boucle du bot)

| Méthode + route | Corps / réponse |
|-----------------|-----------------|
| `POST /reply`   | `{"chat_id": <int|str>, "text": "<réponse>"}` → envoie au chat. Réponse : `{"ok": true, "sent_chars": N}`. |
| `GET /health`   | `{"ok": true, "service": "hermes-voc-bot"}` |

Écoute par défaut sur `127.0.0.1:8080` (Hermès est sur la même machine). Messages
> 4096 caractères automatiquement découpés. Si `REPLY_AUTH_TOKEN` est renseigné,
`/reply` exige l'en-tête `X-Auth-Token`.

```bash
# Exemple : Hermès renvoie une réponse
curl -s -X POST http://127.0.0.1:8080/reply \
  -H 'Content-Type: application/json' \
  -d '{"chat_id": VOTRE_CHAT_ID_TELEGRAM, "text": "Voici ma réponse."}'
```

## Configuration (`.env`)

| Variable           | Rôle                                       | Défaut                                       |
|--------------------|--------------------------------------------|----------------------------------------------|
| `TELEGRAM_TOKEN`   | Token BotFather (en clair)                 | —                                            |
| `BOT_TOKEN_B64`    | Token BotFather en base64 (prioritaire)    | *(vide)*                                      |
| `SPEACHES_URL`     | URL de Speaches                            | `http://speaches:8000`                       |
| `SPEACHES_MODEL`   | Modèle whisper chargé                      | `deepdml/faster-whisper-large-v3-turbo-ct2`  |
| `LANGUAGE`         | Langue forcée                              | `fr`                                         |
| `TRANSCRIPTS_DIR`  | Dossier d'archivage des `.txt`             | `./transcripts`                              |
| `INBOX_DIR`        | Dossier d'inbox Hermès (`.json` pending)   | `./inbox`                                    |
| `HTTP_HOST`        | Bind de l'endpoint `/reply`                | `127.0.0.1`                                  |
| `HTTP_PORT`        | Port de l'endpoint `/reply`                | `8080`                                       |
| `REPLY_AUTH_TOKEN` | Si renseigné, `/reply` exige `X-Auth-Token`| *(vide)*                                      |
| `REQUEST_TIMEOUT`  | Timeout transcription (s)                  | `180`                                        |
| `READY_CHAT_ID`    | Ping « Bot vocal prêt ✅ » au démarrage    | *(vide)*                                      |

> ⚠️ Le modèle n'est **pas** `speaches` (= nom du serveur) mais l'identifiant whisper
> ci-dessus. Vérifier avec `curl $SPEACHES_URL/v1/models`.

> 🔑 Le token peut être fourni encodé : `BOT_TOKEN_B64` est prioritaire sur
> `TELEGRAM_TOKEN`. Décodage interne : `base64.b64decode(os.getenv("BOT_TOKEN_B64")).decode()`.

## Installation & lancement

`pip`/`ensurepip` sont absents du système → on bootstrappe pip dans un venv :

```bash
cd /home/ouvrier/travaux/hermes-voc-bot
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip.py
.venv/bin/python get-pip.py
.venv/bin/pip install -r requirements.txt   # python-telegram-bot httpx python-dotenv aiohttp
```

Lancement en arrière-plan :

```bash
nohup .venv/bin/python bot.py >> logs/bot.log 2>&1 &
echo $! > bot.pid

# OU avec le token passé en base64 (au lieu du .env) :
BOT_TOKEN_B64="$(printf '%s' '<token>' | base64 -w0)" \
  nohup .venv/bin/python bot.py >> logs/bot.log 2>&1 &
```

Arrêt propre : `kill "$(cat bot.pid)" 2>/dev/null` (ou `pkill -f 'hermes-voc-bot/bot.py'`).

> ⚠️ Un seul poller par token : Telegram refuse deux `getUpdates` simultanés
> (`telegram.error.Conflict`). Bien tuer l'ancienne instance avant d'en lancer une.

## Déploiement sur le VPS Hermès

Sur le VPS, `/opt/data/` existe et le bot tourne dans le réseau Docker :

```bash
SPEACHES_URL=http://speaches:8000
TRANSCRIPTS_DIR=/opt/data/hermes-voc-bot/transcripts
INBOX_DIR=/opt/data/hermes-voc-bot/inbox
```

(Sur cette machine de dev, `/opt` n'existe pas et le nom `speaches` ne résout pas →
le `.env` actif utilise `localhost:8000` et des dossiers sous `$HOME`.)
