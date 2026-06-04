# RUNBOOK VPS — WebRTC Vocal (brique 3/3), déploiement & vérification

Procédure **copier-coller** pour mettre en production les durcissements de la
Phase 2 et vérifier que la chaîne vocale tourne. À exécuter **depuis un shell de
l'hôte du VPS** (PAS depuis le bac à sable de l'agent : le restart doit pouvoir
tuer/rebinder le port 8686, ce que la sandbox ne peut pas faire).

| Repère | Valeur |
|---|---|
| Dossier appli (live) | `~/travaux/webrtc-vocal` |
| Écoute serveur | HTTP clair `127.0.0.1:8686` (TLS terminé par **Caddy**, 443) |
| URL publique | `https://votre-domaine.example` (**sans port**) |
| Data dir (inbox/outbox) | `~/travaux/webrtc-vocal/data` |
| Logs serveur | `~/travaux/webrtc-vocal/logs/server.log` |
| Démon de réponse | `deploy/webrtc-reply.sh` (génère les réponses Hermès) |

Fichiers modifiés en Phase 2 : `server.py`, `restart.sh`, `transcribe.py`,
`synth.py`, `requirements.txt`, `README.md`, + nouveau `tests/test_real_voice.py`.

```bash
# Raccourci utilisé dans tout ce runbook
export APP=~/travaux/webrtc-vocal
cd "$APP"
```

---

## 1. Déployer les fichiers modifiés sur le VPS

> **Cas A — le dossier `~/travaux/webrtc-vocal` du VPS EST déjà l'arbre live**
> (édition en place). Alors il n'y a **rien à copier** : les fichiers modifiés y
> sont déjà. Passez directement à l'étape 2. Vérifiez juste leur fraîcheur :
>
> ```bash
> cd "$APP"
> ls -l server.py restart.sh transcribe.py synth.py requirements.txt README.md tests/test_real_voice.py
> grep -n "preflight()" server.py | head            # le préflight est bien présent
> grep -n "HF_HUB_OFFLINE" restart.sh               # HF_HUB_OFFLINE=1 est bien présent
> ```

> **Cas B — vous éditez sur une machine de dev séparée.** Poussez par rsync
> (le `.venv` reste sur le VPS ; `models/` est conservé car il porte la voix +
> le cache whisper) :
>
> ```bash
> VPS=srvVOTRE_ID_VPS                                    # alias SSH du VPS
> rsync -av \
>   server.py restart.sh transcribe.py synth.py requirements.txt README.md RUNBOOK.md \
>   tests/test_real_voice.py \
>   "$VPS:travaux/webrtc-vocal/"                     # NB: chemin ~/travaux côté VPS
> # si requirements.txt a changé, mettre le venv local à jour (faster-whisper + piper-tts) :
> ssh "$VPS" 'cd ~/travaux/webrtc-vocal && ~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt'
> ```

**Pré-check optionnel mais recommandé — prouver STT/TTS sur le VPS lui-même**
(Piper → Whisper → inbox, hors-ligne, sans navigateur ni micro) :

```bash
cd "$APP"
.venv/bin/python tests/test_real_voice.py
# Attendu en fin de sortie :
#   ✅ E2E VRAIE VOIX VALIDÉE : Piper → Whisper → inbox (chaîne LOCALE réelle, hors-ligne)
```

Si ce test échoue, **inutile de redémarrer** : corrigez d'abord l'installation
(le préflight de l'étape 2 refuserait de toute façon de démarrer).

---

## 2. Redémarrer le serveur

`restart.sh` stoppe l'ancienne instance, exporte les chemins **locaux** + 
`HF_HUB_OFFLINE=1`, exécute le **préflight**, puis relance en HTTP clair sur
`127.0.0.1:8686` (TLS délégué à Caddy).

```bash
cd "$APP"
./restart.sh
```

Sortie attendue (extrait) :

```
Serveur relancé (PID 12345). Logs : logs/server.log
  HTTP   : http://127.0.0.1:8686/health        (cible du reverse_proxy Caddy)
  PUBLIC : https://votre-domaine.example/health  (via Caddy 443)
```

> **Si le serveur refuse de démarrer** (préflight), `logs/server.log` contiendra
> `✗ PRÉFLIGHT STT/TTS ÉCHOUÉ …` avec la cause exacte (interpréteur, script ou
> voix manquants). C'est **voulu** : on échoue au démarrage plutôt qu'à chaque
> tour de parole. Corrigez puis relancez.

Démarrer (ou redémarrer) le **démon de réponse** — ⚠️ il DOIT partager le **même**
`WEBRTC_DATA_DIR` que le serveur, sinon les réponses d'Hermès n'arrivent jamais :

```bash
cd "$APP"
# Stopper un éventuel ancien démon, puis relancer :
pkill -f 'deploy/webrtc-reply.sh' 2>/dev/null || true
WEBRTC_DATA_DIR="$APP/data" HERMES_BIN=hermes \
  nohup bash deploy/webrtc-reply.sh >> logs/reply.log 2>&1 &
echo "démon de réponse relancé (PID $!)"
# Variante API HTTP : remplacer HERMES_BIN=hermes par HERMES_URL=http://…/…
```

---

## 3. Vérifier la bannière STT (doit pointer le venv LOCAL)

```bash
cd "$APP"
grep -E 'préflight|STT=|TTS=|data=|VAD=' logs/server.log | tail -n 8
```

Attendu (les chemins STT/TTS pointent le **`.venv` local**, plus aucun `/opt`) :

```
… INFO  webrtc-vocal:   data=/home/ouvrier/travaux/webrtc-vocal/data  (inbox/ outbox/)
… INFO  webrtc-vocal:   STT=transcribe.py base via /home/ouvrier/travaux/webrtc-vocal/.venv/bin/python
… INFO  webrtc-vocal:   TTS=synth.py voix=fr_FR-upmc-medium.onnx via /home/ouvrier/travaux/webrtc-vocal/.venv/bin/python
… INFO  webrtc-vocal:   VAD=auto  fin_de_phrase=800ms  min_parole=250ms
… INFO  webrtc-vocal: ✓ préflight STT/TTS OK (interpréteurs, scripts, voix, imports vérifiés)
```

🚩 Si vous voyez encore `/opt/hermes/.venv/bin/python` : l'ancien `restart.sh`/
`server.py` tourne encore → reprenez l'étape 1 (fichiers non déployés) puis 2.

Santé HTTP, en local puis via Caddy (public) :

```bash
curl -fsS http://127.0.0.1:8686/health            # -> {"ok": true, "service": "webrtc-vocal", …}
curl -fsS https://votre-domaine.example/health   # idem, via Caddy/443 (cert Let's Encrypt)
```

---

## 4. Vérifier l'inbox après parole

Ouvrez **`https://votre-domaine.example`** sur le téléphone (contexte sécurisé
exigé par `getUserMedia`), autorisez le micro, et **dites une phrase** (p. ex.
« Bonjour Hermès, est-ce que tu m'entends ? »), puis taisez-vous ~1 s.

Observez le serveur en direct (la transcription doit apparaître) :

```bash
cd "$APP"
tail -n 20 -f logs/server.log
# Attendu, à chaque phrase :
#   … tour 1 : phrase de 2.3s -> STT
#   … tour 1 : transcription 'bonjour hermès est-ce que tu m'entends'
#   … tour 1 : réponse Hermès '…'        (après que le démon a répondu — étape 5)
# Si la phrase n'est pas comprise, un WARNING explicite le dit :
#   … tour 1 : transcription VIDE (…s d'audio capté…) — rien envoyé à Hermès…
```

Et côté fichiers (le fichier inbox est **éphémère** : le démon le consomme puis
le supprime dès qu'il a répondu) :

```bash
ls -lt "$APP/data/inbox/" "$APP/data/outbox/"
```

> Pour **capturer** l'inbox `pending` avant qu'elle ne soit consommée, stoppez
> temporairement le démon de réponse, parlez, puis inspectez :
> ```bash
> pkill -f 'deploy/webrtc-reply.sh'                 # met Hermès en pause
> #   … parlez dans le navigateur …
> cat "$APP"/data/inbox/*.json                      # status:"pending", text:"…", turn, conversation_id
> ```
> Pensez à **relancer le démon** ensuite (commande de l'étape 2).

---

## 5. Vérifier l'outbox / réponse Hermès

Démon relancé (étape 2), reparlez : Hermès doit écrire l'outbox, le serveur la
lit, **synthétise la voix (Piper)** et la **rejoue dans l'oreillette**.

```bash
cd "$APP"
# a) le démon de réponse a bien tourné :
tail -n 15 logs/reply.log
# b) l'outbox a été produite (puis l'inbox correspondante supprimée) :
ls -lt "$APP/data/outbox/" | head
cat "$APP"/data/outbox/*.json 2>/dev/null | head    # { "session_id": "...-tNNN", "reply": "...", "status": "done" }
# c) côté serveur, le tour passe à 'speaking' puis 'done' :
grep -E 'réponse Hermès|tour [0-9]+ :' logs/server.log | tail -n 6
```

Côté navigateur, vous **entendez** la réponse et l'indicateur `/poll` passe par
`thinking → speaking → listening` (le VAD se réarme pour le tour suivant).

**Diagnostic « je parle mais aucune réponse » :**

| Symptôme dans les logs | Cause probable | Action |
|---|---|---|
| `transcription '…'` OK mais **aucune** outbox | démon non lancé, ou **`WEBRTC_DATA_DIR` différent** entre serveur et démon | relancer le démon avec `WEBRTC_DATA_DIR="$APP/data"` (étape 2) |
| `reply.log` : `hermes …: command not found` | binaire Hermès injoignable | corriger `HERMES_BIN` ou passer en `HERMES_URL=http://…` |
| serveur ne démarre pas, `PRÉFLIGHT … ÉCHOUÉ` | STT/TTS pas installés localement | `uv pip install -r requirements.txt`, vérifier `models/piper/*.onnx` |
| `transcription VIDE` à répétition | micro faible / bruité, ou modèle inadapté | vérifier le micro ; éventuellement `WHISPER_MODEL=small` |

---

## Rollback rapide

Le précédent process est remplacé par `restart.sh`. Pour revenir à un état
antérieur des sources, restaurez-les (git/backup) puis relancez :

```bash
cd "$APP" && ./restart.sh            # redémarre sur les sources en place
```

> Tant que `./restart.sh` n'a pas été lancé **depuis l'hôte**, l'ancienne
> instance (et son ancien code) continue de tourner.
