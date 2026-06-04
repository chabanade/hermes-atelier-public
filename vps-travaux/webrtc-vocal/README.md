# WebRTC Vocal — serveur auto-hébergé (3 briques)

Serveur vocal **gratuit / auto-hébergé** en Python (aiohttp + aiortc). Le
navigateur capte le micro, l'envoie en WebRTC, le serveur transcrit, fait
répondre l'assistant **Hermès**, et **rejoue sa voix dans l'oreillette**.

- **Brique 1/3 — faite.** Signalisation + streaming audio + écho (full-duplex).
- **Brique 2/3 — faite.** STT (faster-whisper) + pont Hermès par fichiers
  (inbox/outbox) + `/poll`. Talkie-walkie : parler → raccrocher → réponse texte.
- **Brique 3/3 — ce dépôt.** **Conversation naturelle, comme un appel.** La
  connexion reste ouverte ; un VAD détecte quand vous parlez, l'endpointing
  marque la fin de phrase, Hermès répond **en voix** (Piper TTS), et vous
  enchaînez — mains libres, zéro clic. Le silence ne coûte rien (ni CPU ni token).

> ## ⚙️ État réel sur CE VPS (réparé le 2026-06-04)
> Historique de la panne : un ancien layout supposait des venv/data externes qui
> **n'ont jamais été provisionnés ici** — d'où le « je parle mais aucune réponse »
> (le STT plantait sur un interpréteur introuvable, à chaque tour, en silence).
> Tout est désormais **self-contained dans ce dossier** (et un **préflight** au
> démarrage refuse de booter si STT/TTS ne sont pas réellement installés) :
> - STT/TTS dans le **venv local** `.venv/` (`faster-whisper` + `piper-tts` installés) ;
> - voix Piper : `models/piper/fr_FR-upmc-medium.onnx` ;
> - modèle whisper `base` en cache : `models/hf/` (`HF_HOME`) ;
> - inbox/outbox : `./data/` (`WEBRTC_DATA_DIR`).
>
> `server.py` (defaults) et `restart.sh` pointent maintenant sur ces chemins locaux.
> **Redémarrer le serveur (depuis un shell de l'hôte, hors bac à sable) :**
> ```bash
> cd /home/ouvrier/travaux/webrtc-vocal && ./restart.sh
> # HTTP clair sur 127.0.0.1:8686 ; TLS terminé par Caddy (443). curl http://127.0.0.1:8686/health
> ```
> **Démon de réponse** (génère les réponses ; même `WEBRTC_DATA_DIR` que le serveur) :
> ```bash
> WEBRTC_DATA_DIR=/home/ouvrier/travaux/webrtc-vocal/data \
>   HERMES_BIN=hermes  nohup bash deploy/webrtc-reply.sh >> logs/reply.log 2>&1 &
> # ou, si Hermès est une API HTTP : remplacer HERMES_BIN=… par HERMES_URL=http://…/…
> ```
> NB : un serveur **Speaches** (STT/TTS OpenAI-compatible) tourne déjà sur `:8000` ;
> il n'est PAS utilisé par ce flux (on garde les sous-processus locaux), mais reste
> une alternative possible. Le **cerveau Hermès** (LLM) doit être joignable par le
> démon (`hermes ask` ou `HERMES_URL`) — seul point à vérifier côté hôte.

## Flux d'un tour de parole (entièrement piloté côté serveur)

```
micro navigateur ──WebRTC (piste entrante, CONTINUE)──▶ server.py
   │  rééchantillonne 48 kHz → 16 kHz mono
   │  VAD (silero ▸ webrtcvad ▸ énergie) : parle / se tait ?
   │  endpointing : silence > 800 ms après la parole ⇒ fin de phrase
   ▼
   1. WAV 16 kHz                                  (/tmp/webrtc_<ex>_in.wav)
   2. STT  -> transcribe.py (faster-whisper, sous-processus)
   3. inbox/<ex>.json (status "pending")  ── cron Hermès lit, réfléchit, écrit ─┐
   4. attend outbox/<ex>.json  ◀────────────────────────────────────────────────┘
   5. TTS  -> synth.py (Piper, sous-processus) -> WAV
   6. rééchantillonne -> piste SORTANTE WebRTC -> joué dans l'oreillette
   7. pendant la lecture : VAD coupé (pas de self-écoute) ; réarmé juste après.

page web ──GET /poll?session=<id> (toutes les 800 ms)──▶ {status, speaking, turns[]}
        (sous-titres live + indicateur d'état ; l'AUDIO, lui, passe par WebRTC)
```

**Découplage STT/TTS.** faster-whisper et Piper vivent dans le **venv local**
(`.venv/`). `server.py` lance `transcribe.py` et `synth.py` en **sous-processus**
avec cet interpréteur (`WHISPER_PYTHON`, `TTS_PYTHON`), ce qui évite de charger
ctranslate2/onnx dans la boucle asyncio et permet de **tout tester hors-ligne**
avec de faux binaires. Le **VAD**, lui, tourne dans la boucle (temps réel, local,
sans GPU).

## Contrat de fichiers (pour le cron Hermès)

Tout est sous `WEBRTC_DATA_DIR` (défaut `./data`, soit `inbox/` + `outbox/` dans
ce dossier). **Un tour de parole = un « échange »** d'identifiant
`<session>-t<NNN>` ⇒ une paire
inbox/outbox par tour, sans collision sur une conversation longue. Le contrat est
celui de la brique 2 (un id ↔ un JSON).

`inbox/<ex>.json` — **écrit par le serveur**, à lire par Hermès :

```json
{
  "ts": "2026-06-04T07:30:00",
  "session_id": "1f3c…-t001",
  "conversation_id": "1f3c…",
  "turn": 1,
  "text": "ce que Mehdi a dit",
  "status": "pending",
  "source": "webrtc",
  "stt": { "model": "base", "language": "fr", "duration": 2.4 }
}
```

`outbox/<ex>.json` — **à écrire par le cron Hermès** ; le serveur lit en priorité
le champ `reply` (puis, par tolérance, `text` ou `response`) :

```json
{ "session_id": "1f3c…-t001", "reply": "réponse d'Hermès", "status": "done" }
```

> Le cron continue de traiter `inbox/*.json` (status `pending`) → `outbox/<id>.json`
> **exactement comme en brique 2** : seul le nom d'`id` encode désormais le tour.
> Écrire l'outbox de façon **atomique** (`.tmp` puis `os.replace`).

## Endpoints

| Méthode | Route                | Rôle                                                            |
|---------|----------------------|-----------------------------------------------------------------|
| `GET`   | `/`                  | page web (conversation continue)                                |
| `POST`  | `/offer`             | signalisation WebRTC ; corps inclut `session` ; pistes micro↑ + voix↓ |
| `GET`   | `/poll?session=<id>` | état conversation : `{status, speaking, turn, turns[], frames_in, …}` |
| `GET`   | `/ice`               | serveurs ICE (STUN/TURN) servis au navigateur : `{iceServers:[…]}` |
| `GET`   | `/diag`              | diagnostic JSON : compteurs cumulés + métriques par session (`frames_in`…) |
| `GET`   | `/health`            | sonde de vivacité (+ `sessions`, `ice_servers`, `frames_in_total`) |

`status ∈ listening | speech | transcribing | thinking | speaking | ended | unknown`

> **Diagnostiquer un silence** (« je parle, c'est connecté, mais rien »). Le
> compteur **`frames_in`** (via `/diag` ou `/poll`) dit si l'audio arrive
> réellement au serveur ; **STUN** (activé par défaut, cf. `/ice`) corrige la
> cause n°1 d'un échec à distance — la traversée de NAT. Démarche complète,
> arbre de décision et escalade TURN dans **`DIAGNOSTIC.md`**.

## Configuration (variables d'environnement)

| Variable               | Défaut                                              | Rôle                                   |
|------------------------|-----------------------------------------------------|----------------------------------------|
| `WEBRTC_DATA_DIR`      | `./data`                                            | racine `inbox/` + `outbox/`            |
| `WEBRTC_TMP_DIR`       | `/tmp`                                              | WAV temporaires par tour               |
| `WHISPER_PYTHON`       | `./.venv/bin/python`                                | interpréteur faster-whisper            |
| `WHISPER_SCRIPT`       | `./transcribe.py`                                   | script STT                             |
| `WHISPER_MODEL`        | `base`                                              | modèle whisper                         |
| `WHISPER_LANGUAGE`     | `fr`                                                | langue forcée                          |
| `TTS_PYTHON`           | `./.venv/bin/python`                                | interpréteur Piper                     |
| `TTS_SCRIPT`           | `./synth.py`                                        | script TTS                             |
| `PIPER_VOICE`          | `./models/piper/fr_FR-upmc-medium.onnx`             | voix Piper (.onnx)                     |
| `HF_HOME`              | `./models/hf`                                       | cache du modèle faster-whisper         |
| `HF_HUB_OFFLINE`       | `1` (via `restart.sh`)                              | coupe l'accès réseau HF (modèle en cache) |
| `WEBRTC_VAD`           | `auto`                                              | `auto`/`silero`/`webrtcvad`/`energy`   |
| `VAD_AGGRESSIVENESS`   | `2`                                                 | webrtcvad, 0 (permissif) … 3 (strict)  |
| `END_SILENCE_MS`       | `800`                                               | silence ⇒ fin de phrase                |
| `MIN_SPEECH_MS`        | `250`                                               | en-deçà = bruit, ignoré                |
| `PREROLL_MS`           | `300`                                               | audio gardé avant l'attaque            |
| `HERMES_TIMEOUT_S`     | `60`                                                | attente max de la réponse Hermès       |
| `VAD_ENERGY_THRESHOLD` | `500`                                               | seuil RMS du VAD par énergie           |
| `WEBRTC_TLS_CERT`      | _(vide)_                                            | certificat PEM ⇒ active le HTTPS natif |
| `WEBRTC_TLS_KEY`       | _(vide)_                                            | clé privée PEM (avec `WEBRTC_TLS_CERT`)|
| `WEBRTC_HTTPS_PORT`    | `8443`                                              | port HTTPS natif (si certificat fourni)|
| `WEBRTC_ICE_SERVERS`   | `stun:stun.l.google.com:19302`                      | serveurs ICE (liste d'URL, ou JSON `[{urls,username,credential}]`) ; **vide = host-only** |
| `WEBRTC_TURN_URL`      | _(vide)_                                            | relais TURN optionnel (avec `_USER`/`_PASS`) si STUN ne suffit pas |
| `WEBRTC_TURN_USER` / `_PASS` | _(vide)_                                      | identifiants du relais TURN            |
| `WEBRTC_LOG_LEVEL`     | `INFO`                                              | niveau du logger applicatif (`DEBUG`…) |
| `WEBRTC_AIOICE_LOG`    | `INFO`                                              | niveau du logger ICE (`WARNING` = moins de bruit) |
| `WEBRTC_MEDIA_HEARTBEAT_S` | `5`                                             | période du heartbeat média (logs)      |
| `WEBRTC_NO_FRAME_WARN_S`   | `4`                                             | délai avant l'alerte « 0 trame entrante » |

## Lancer en local (dev)

`getUserMedia` exige un contexte sécurisé : passer par **localhost** (tunnel SSH
`ssh -L 8686:localhost:8686 …`) ou du HTTPS.

```bash
.venv/bin/python server.py            # écoute 0.0.0.0:8686
```

faster-whisper et Piper sont installés dans `.venv` (cycle voix réel possible) ;
le cycle conversationnel se teste aussi **hors-ligne** avec de faux binaires
(cf. tests/). Le VAD tombe automatiquement sur `webrtcvad` puis l'énergie (silero
seulement si `torch` est présent).

## Tester le cycle conversationnel (hors-ligne, sans micro/whisper/piper)

```bash
.venv/bin/python tests/test_conversation.py
```

Pilote le vrai serveur via un client aiortc : salves de parole/silence
synthétiques → endpointing → (faux) STT → `inbox` → (cron Hermès simulé) →
(faux) Piper → **piste audio sortante** (l'énergie reçue côté client est
vérifiée) → `/poll`. Deux tours, pour prouver que le VAD se réarme.

## Dépendances pip

Dans **ce** venv (couche temps réel) : `aiohttp`, `aiortc` (tire `av`,
`cryptography`), `numpy`, et un backend VAD — `webrtcvad-wheels` (roues
précompilées, recommandé) ou `webrtcvad` (compile, nécessite `python3-dev`).
Optionnel pour une meilleure qualité : `silero-vad` + `torch` (auto-détecté).

Dans **ce même** venv local (`.venv/`, couche STT/TTS) : `faster-whisper` et
`piper-tts` — installés ici, plus aucun venv externe.

## HTTPS (obligatoire pour `getUserMedia` hors localhost)

Le micro n'est accessible qu'en **contexte sécurisé** : `https://…` ou
`http://localhost`. En accès distant (téléphone, autre PC), il **faut** du HTTPS.
Deux options, le serveur Python restant **toujours en HTTP sur 8686**.

### Option A — Caddy + Let's Encrypt  ✅ recommandé

Certificat **valide** (reconnu par les navigateurs, zéro avertissement),
obtenu et renouvelé automatiquement. Caddy écoute 443 (+ redirige 80) et
reverse-proxy vers `127.0.0.1:8686`. **Aucune modification de `server.py`.**

Bonne nouvelle : ce VPS a déjà un domaine public, son FQDN Hostinger par défaut
**`votre-domaine.example`** (résout vers `VOTRE_IP_VPS` — vérifié). Pas besoin
d'acheter de domaine.

```bash
# SUR LE VPS, en root (hors sandbox). Config : deploy/Caddyfile
sudo bash deploy/install-caddy.sh
# Test (le 1er appel attend ~10 s l'émission du certificat) :
curl -fsS https://votre-domaine.example/health      # -> {"ok": true, ...}
# Recharger après une modif du Caddyfile :
sudo systemctl reload caddy
```

> **Pare-feu** : ouvrir **80 et 443** (Hostinger hPanel ▸ VPS ▸ Firewall, et
> `ufw` si actif). Let's Encrypt valide le domaine via le port 80.
>
> **Domaine perso** (`voix.exemple.fr`) : créer un enregistrement
> `A voix.exemple.fr → VOTRE_IP_VPS` chez le registrar, remplacer le nom dans
> `deploy/Caddyfile`, puis `sudo systemctl reload caddy`. Caddy gère le certificat.

### Option B — TLS natif (auto-signé), sans reverse proxy

Repli pour un test rapide, ou si l'on ne veut pas de Caddy. Le serveur sert
**HTTP sur 8686 ET HTTPS sur 8443** dans le même process (`server.py` accepte
désormais `--tls-cert/--tls-key`, ou `WEBRTC_TLS_CERT/WEBRTC_TLS_KEY`).

```bash
bash deploy/make-selfsigned-cert.sh                  # -> deploy/certs/{cert,key}.pem
WEBRTC_TLS_CERT=$PWD/deploy/certs/cert.pem \
WEBRTC_TLS_KEY=$PWD/deploy/certs/key.pem \
  .venv/bin/python server.py                         # HTTP :8686 + HTTPS :8443
curl -k https://localhost:8443/health                # -> {"ok": true, ...}
```

> ⚠ Un cert **auto-signé** déclenche « connexion non privée » : il faut
> l'approuver/l'importer sur **chaque** appareil. Pour un usage réel, préférez
> l'option A (certificat valide). Le HTTPS natif peut aussi servir un **vrai**
> certificat Let's Encrypt (`WEBRTC_TLS_CERT=/etc/letsencrypt/live/<domaine>/fullchain.pem`,
> `WEBRTC_TLS_KEY=…/privkey.pem`) si l'on veut éviter le reverse proxy.

## Déploiement sur le VPS Hermès

**Layout réel.** L'instance live tourne dans **`~/travaux/webrtc-vocal`** sur le
VPS (HTTP clair `127.0.0.1:8686`, TLS terminé par Caddy/443). Tout est
self-contained dans ce dossier : `.venv/` (faster-whisper + piper), voix sous
`models/piper/`, cache whisper sous `models/hf/`, échanges sous `data/`.

Le pas-à-pas copier-coller (déploiement, restart, et les trois vérifications —
bannière STT, inbox après parole, outbox/réponse Hermès) est dans **`RUNBOOK.md`**.

En résumé, depuis un **shell de l'hôte** (hors bac à sable, le restart a besoin de
tuer/rebinder le port 8686) :

```bash
cd ~/travaux/webrtc-vocal
# si le venv doit être (re)créé : il devient self-contained via requirements.txt
test -d .venv || ~/.local/bin/uv venv .venv
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
./restart.sh                       # exporte les chemins locaux + HF_HUB_OFFLINE=1
curl -fsS http://127.0.0.1:8686/health
```

> Le démarrage exécute un **préflight** : si faster-whisper, Piper, ou la voix
> `.onnx` manquent, le serveur **refuse de démarrer** (message d'erreur explicite)
> au lieu de planter silencieusement à chaque tour. La bannière de démarrage doit
> montrer `STT=… via …/.venv/bin/python` (le venv **local**). Le démon de réponse
> (`webrtc-reply.sh`) doit partager le **même** `WEBRTC_DATA_DIR` que le serveur
> (`~/travaux/webrtc-vocal/data`), sinon les réponses d'Hermès n'arrivent jamais.
