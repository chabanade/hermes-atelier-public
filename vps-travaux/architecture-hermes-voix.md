# Hermès mains-libres en voiture — Architecture voix↔IA
**Architecte technique · 3 juin 2026 · pour Mehdi**

Objectif : boucler la boucle **vocal Mehdi → texte → Hermès → texte → vocal Mehdi**, sans toucher le téléphone, évolutif vers WhatsApp et Meta Ray-Ban, zéro maintenance, budget mini, RGPD.

---

## TL;DR (décision recommandée)

| Brique | Choix recommandé | Pourquoi |
|---|---|---|
| **STT (voix→texte)** | **faster-whisper `large-v3-turbo`** auto-hébergé sur le VPS via **Speaches** (Docker) | Français excellent, ~1-2 s par mémo, **100 % local (RGPD)**, **0 €** marginal. Fallback **Groq** ($0,04/h) si VPS chargé. |
| **Boucle** | **Bot Telegram** (aiogram/python-telegram-bot) ↔ STT ↔ **boîte de l'aiguilleur** `/opt/data/claude-queue/in/` ↔ Hermès (+TTS) ↔ `sendVoice` | Réutilise l'aiguilleur existant. Livrable en **1-2 jours**. |
| **Mains-libres en conduisant** | Court terme : **« Hey Google → message Telegram à Hermès »** (Android) ou **Raccourci Siri** (iPhone). Vrai eyes-free : **pont d'appel téléphonique** (semaine 2). | Enregistrer un vocal Telegram n'est PAS mains-libres ; ces deux voies le sont vraiment. |
| **TTS (texte→voix)** | Garder le `text_to_speech` d'Hermès. Si c'est du cloud et que le RGPD prime → **Piper / Kokoro / Voxtral TTS** en local. | Déjà en place ; local possible gratuitement. |
| **Évolutivité** | **Passerelle Voix** avec adaptateurs d'entrée/sortie. Ray-Ban = un nouvel adaptateur (via **Meta Wearables Device Access Toolkit**, dispo en dev preview). | Brancher un canal ≠ tout refaire. |
| **Zéro maintenance** | Docker `restart=always` + **Uptime Kuma** + healthchecks + **alertes Telegram** + auto-test nocturne | Auto-réparation + supervision. |

**Coût d'exploitation réel pour l'usage de Mehdi (~250 min/mois) : ≈ 0 €/mois** en tout-local (hors VPS déjà payé). Fallback cloud STT < 1 $/mois. Seul coût récurrent éventuel : un numéro de téléphone (~1-2 €/mois) si on active le pont d'appel.

---

## PHASE 1 — ÉTAT DE L'ART

### 1.1 APIs de transcription cloud (STT)

| Fournisseur / modèle | Prix /min | Offert | Français | Latence ~20 s | UE / RGPD | Lien |
|---|---|---|---|---|---|---|
| **Groq** `whisper-large-v3-turbo` | **$0,00067** ($0,04/h) | crédits d'essai | très bon (Whisper) | **< 1 s** (216× temps réel) | ❌ US, pas de région UE | [groq.com](https://groq.com/blog/whisper-large-v3-turbo-now-available-on-groq-combining-speed-quality-for-speech-recognition) |
| Groq `whisper-large-v3` | ~$0,00185 ($0,111/h) | idem | excellent | ~1 s | ❌ US | [console.groq.com](https://console.groq.com/docs/model/whisper-large-v3-turbo) |
| **OpenAI** `gpt-4o-mini-transcribe` | $0,003 | 5 $ crédits | excellent | 1-3 s | ⚠️ US, ZDR/EU sur demande | [openai.com/api/pricing](https://openai.com/api/pricing/) |
| OpenAI `gpt-4o-transcribe` / `whisper-1` | $0,006 | idem | excellent | 1-3 s | ⚠️ US, ZDR/EU sur demande | [costgoat](https://costgoat.com/pricing/openai-transcription) |
| **Deepgram** Nova-3 (batch) | $0,0043 | 200 $ crédits | bon (FR ajouté 2025) | < 1 s | ⚠️ US, option on-prem entreprise | [deepgram.com/pricing](https://deepgram.com/pricing) |
| Deepgram Nova-3 (streaming) | $0,0077 | idem | bon | temps réel | ⚠️ US | [deepgram.com](https://deepgram.com/learn/deepgram-expands-nova-3-with-spanish-french-and-portuguese-support) |
| **AssemblyAI** Universal-2 | $0,0025 ($0,15/h) | 50 $ crédits | bon | 1-3 s | ❌ US | [assemblyai.com/pricing](https://www.assemblyai.com/pricing/) |
| **ElevenLabs** Scribe | $0,0067 ($0,40/h) | quota gratuit | excellent | 1-2 s (v2 realtime 150 ms) | ❌ US | [elevenlabs.io](https://elevenlabs.io/speech-to-text) |
| **Google** STT v2 / Chirp (std) | $0,016 | 60 min/mois | bon | 1-2 s | ✅ **région Belgique + CMEK** | [cloud.google.com](https://cloud.google.com/speech-to-text/pricing) |
| Google STT v2 (batch) | $0,004 | idem | bon | différé | ✅ UE | idem |
| **Azure** AI Speech (std temps réel) | $0,0167 ($1/h) | 5 h/mois | bon | temps réel | ✅ **France Central / West Europe** | [azure.microsoft.com](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/) |
| Azure (batch) | $0,003 ($0,18/h) | idem | bon | différé | ✅ UE | idem |

**À retenir :** pour le **prix/vitesse pur**, **Groq** est imbattable (216× temps réel, $0,04/h) mais **hébergé aux US** → mémos audio bruts qui sortent de l'UE = friction RGPD. Pour rester **dans l'UE en cloud**, **Azure (France Central)** ou **Google (Belgique)** sont les seuls « propres » nativement. Mais le meilleur compromis RGPD reste l'**auto-hébergement** (§1.2).

### 1.2 STT open-source / auto-hébergé (la voie RGPD)

| Solution | Français | Vitesse 20-30 s | RAM / dispo | Licence | Serveur HTTP prêt | Lien |
|---|---|---|---|---|---|---|
| **faster-whisper** `large-v3-turbo` (int8) | **excellent** | **~1-2 s sur CPU** (≈40× temps réel : 13 min audio → **19 s** sur un i7 8 threads) | ~1,5-2 Go RAM, modèle ~1,6 Go | MIT | via Speaches/Docker | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| **Speaches** (faster-whisper + Kokoro/Piper) | excellent | idem | conteneur Docker | MIT | ✅ **API compatible OpenAI** (STT **+ TTS**) | [speaches-ai/speaches](https://github.com/speaches-ai/speaches) |
| **whisper.cpp** (serveur) | excellent | ~quelques s CPU | léger, GGUF | MIT | ✅ serveur HTTP intégré | [ggml-org/whisper.cpp](https://github.com/ggerganov/whisper.cpp) |
| **hwdsl2/docker-whisper** | excellent | CPU/GPU | Docker multi-arch | — | ✅ OpenAI-compat + diarisation + SSE | [hwdsl2/docker-whisper](https://github.com/hwdsl2/docker-whisper) |
| **Mistral Voxtral** Mini (3B) / 24B | **SOTA FR** | très rapide sur GPU ; Mini OK CPU | Mini ~ 8-10 Go (GPU conseillé) | **Apache 2.0** | via vLLM ; variante **realtime** | [HF Voxtral-Mini-Realtime](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) |
| **Vosk** | correct (< Whisper) | temps réel, ultra-léger | ~100-2 Go modèle FR | Apache 2.0 | serveur websocket | [alphacephei.com/vosk](https://alphacephei.com/vosk/) |

> **Fait décisif :** sur un **simple VPS CPU**, `large-v3-turbo` transcrit un mémo de 20-30 s en **1-2 s**. On n'a **pas besoin de GPU** pour une expérience fluide en voiture. → l'auto-hébergement coche **toutes** les cases : français excellent, rapide, gratuit, **0 fuite de données**.

**TTS auto-hébergé (si on veut remplacer un TTS cloud d'Hermès) :** **Piper** (MIT, ultra-rapide, voix FR), **Kokoro** (Apache, 82M, qualité élevée), et nouveauté **Voxtral TTS** (Mistral, mars 2026, open-weights, **bat ElevenLabs Flash v2.5** en test à l'aveugle, FR, tourne en local). [Voxtral](https://mistral.ai/news/voxtral/) · [Piper](https://github.com/rhasspy/piper).

### 1.3 Telegram : réception des vocaux & bots de transcription
- Un vocal Telegram = fichier **OGG/Opus**. Le bot le récupère via `getFile` → `file_path` → téléchargement HTTPS. Réponse audio via **`sendVoice`** (OGG/Opus) ou `sendAudio`. Libs 2026 : **python-telegram-bot v22** ou **aiogram 3.x**.
- Bots open-source qui transcrivent déjà les vocaux : **Voicy** ([backmeupplz/voicy](https://github.com/backmeupplz/voicy), backends Whisper/Google/wit.ai) — bonne base de référence.
- ⚠️ **La transcription native de Telegram Premium n'est PAS accessible aux bots** (c'est une fonction côté client de l'app). → il **faut** notre propre STT. (Confirmé par l'absence dans la Bot API.)

### 1.4 Mains-libres en conduisant (le vrai sujet)
Enregistrer un vocal Telegram demande de tenir/appuyer → **pas vraiment mains-libres**. Réalité 2026 :

| Voie | Mains-libres ? | Détail |
|---|---|---|
| **« Hey Google, envoie un message Telegram à Hermès : … »** | ✅ Oui (Android) | Google Assistant **dicte et envoie un TEXTE** au bot → **aucun STT chez nous**, gratuit, dispo aujourd'hui. **Meilleur quick-win d'entrée.** |
| **Raccourci Siri « Dis à Hermès »** (iPhone) | ✅ Oui | Un Shortcut enregistre l'audio et le POST vers la passerelle. |
| **Telegram sur Android Auto** | ❌ Faible | Non supporté nativement ; **les vocaux ne se lisent pas dans Android Auto** ([bug confirmé](https://bugs.telegram.org/c/50136)). |
| **Pont d'appel téléphonique** (§1.5) | ✅✅ Total, eyes-free | On appelle un numéro (ou bouton volant Bluetooth « appeler Hermès »), on parle, on entend. Marche dans **n'importe quelle voiture**. |

→ **Quick-win** : Telegram + « Hey Google »/Siri (quasi mains-libres). **Vraie conduite eyes-free** : pont d'appel (semaine 2).

### 1.5 Pont d'appel temps réel (conversation naturelle au volant)
- **Twilio Voice + Media Streams** ⇄ **OpenAI Realtime API** : caller parle → STT→LLM→TTS → caller entend, **< 200 ms** possible. Coût combiné **≈ $0,02-0,30/min** ([Twilio](https://www.twilio.com/code-exchange/ai-voice-assistant-openai-realtime-api)). OpenAI Realtime ≈ $0,30/min de conversation (audio in/out).
- **Plateformes managées** : **Vapi / Retell / Bland** — montage rapide ; pertinent < 10 000 min/mois. Au-delà, OpenAI Realtime + LiveKit s'amortit.
- **Variante 100 % RGPD/local** : trunk SIP (OVH/Telnyx) + **LiveKit/Asterisk** + **Voxtral STT** + **Voxtral/Piper TTS** + Hermès → conversation maison sans données qui sortent.

### 1.6 Plaud (déjà utilisé pour les mémos AGEA)
- **Pas d'API publique temps réel** : l'OAuth API Plaud est en **beta privée (liste d'attente)**. ([FAQ Plaud OAuth](https://support.plaud.ai/hc/en-us/articles/56[TELEPHONE]09-FAQs-for-Plaud-OAuth-API))
- Mais **intégration asynchrone dispo via Zapier/Webhook** : déclencheurs **« transcript generated »** et **« summary generated »** → on peut **router automatiquement** une transcription Plaud vers la passerelle / vers AGEA. ([Zapier Plaud](https://zapier.com/apps/plaud/integrations) · [doc](https://support.plaud.ai/hc/en-us/articles/1220[TELEPHONE]-Zapier-integration))
- **Usage** : garder Plaud pour les **mémos longs AGEA** (async), et la passerelle Telegram/appel pour l'**interaction temps réel** avec Hermès. Plaud = un **adaptateur d'entrée différé** de plus.

### 1.7 WhatsApp Business / Cloud API
- **Réception des vocaux** : oui, via **webhook media** (OGG/Opus) — il faut **notre propre STT** (WhatsApp ne transcrit pas).
- **Tarif 2026** : modèle **par message** (depuis juil. 2025) ; **messages de service gratuits dans la fenêtre de 24 h** après un message du client ; tarifs marketing **baissés pour la France** au 1ᵉʳ janv. 2026. ([Meta pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing))
- **Verdict** : utile comme **2ᵉ canal** (Mehdi écrit/parle déjà sur WhatsApp), mais Telegram d'abord (API plus simple, `sendVoice` natif, pas de validation de templates).

### 1.8 Meta Ray-Ban (le futur vision + audio)
- **Mai 2026 : Meta Wearables Device Access Toolkit** (dev preview) — SDK **iOS (Swift) / Android (Kotlin)** + voie **web**. **Accès caméra** + **micro/haut-parleurs via profils Bluetooth**. ([Meta devs](https://developers.meta.com/blog/introducing-meta-wearables-device-access-toolkit/) · [Road to VR](https://roadtovr.com/meta-ray-ban-smart-glasses-third-party-app-sdk-device-access-toolkit/))
- ⚠️ **Le wake-word « Hey Meta » n'est PAS ouvert aux tiers cette année** → on déclenche la capture par **appui (Neural Band/bouton)** ou via l'app, pas encore par mot-clé. Publication ouverte progressivement en 2026 (partenaires sélectionnés d'abord).
- **Verdict** : **faisable dès maintenant en preview** comme **nouvel adaptateur** (capture audio+photo → passerelle → Hermès vision → audio retour BT). Vision = Claude analyse les photos. À mettre en **roadmap**, pas en quick-win.

---

## PHASE 2 — ARCHITECTURE CIBLE

### Principe : une **Passerelle Voix** avec contrat interne stable + **adaptateurs** enfichables
Chaque canal (Telegram, WhatsApp, appel, Ray-Ban, PWA) est un **adaptateur d'entrée/sortie** autour d'un cœur immuable. Ajouter un canal = écrire un adaptateur, **le cœur ne bouge pas**.

```
                 ┌─────────────── ENTRÉES (M1 – adaptateurs) ───────────────┐
  Voix Mehdi →   │ Bot Telegram │ WhatsApp │ Appel tél. │ Ray-Ban(futur) │ PWA │
                 └───────┬──────────┬──────────┬─────────────┬──────────┬───┘
                         │  audio (ogg/wav) normalisé + métadonnées
                         │  { canal, user_id, reply_handle, lieu? , photo? }
                         ▼
                 ┌──────────────── M2 – STT (LOCAL) ─────────────────┐
                 │ Speaches / faster-whisper large-v3-turbo (FR)     │
                 │ HTTP compatible OpenAI · audio PURGÉ après usage   │
                 └───────┬───────────────────────────────────────────┘
                         │ texte FR + métadonnées
                         ▼
                 ┌──────────────── M3 – ROUTAGE HERMÈS ──────────────┐
                 │ écrit l'ordre dans /opt/data/claude-queue/in/      │
                 │ (AIGUILLEUR EXISTANT) → Hermès traite              │
                 └───────┬───────────────────────────────────────────┘
                         │ réponse texte (+ audio) dans …/out/
                         ▼
                 ┌──────────────── M4 – TTS ─────────────────────────┐
                 │ text_to_speech d'Hermès (ou Piper/Kokoro/Voxtral)  │
                 └───────┬───────────────────────────────────────────┘
                         │ voix (ogg/opus) + texte
                         ▼
                 ┌────────────── SORTIES (M5 – mêmes adaptateurs) ──────────┐
  ← Voix Mehdi   │ Telegram sendVoice │ WhatsApp │ flux appel │ BT lunettes │
                 └──────────────────────────────────────────────────────────┘

  Transversal : Orchestrateur (file) · Monitoring Uptime Kuma + healthchecks
                · Docker/systemd restart=always · Alertes Telegram · RGPD (tout local)
```

### Module 1 — Réception (adaptateurs d'entrée)
- **Telegram** (quick-win) : bot **aiogram 3.x** ou **python-telegram-bot v22**, en *long-polling* (zéro config réseau) ou webhook. Sur `message.voice` → `getFile` → download OGG.
- **WhatsApp** : webhook Cloud API (HTTPS + domaine). Plus tard.
- **Appel** : trunk SIP/Twilio → flux audio. Plus tard.
- **Ray-Ban / PWA** : POST audio (+ photo) sur l'endpoint de la passerelle. Roadmap.
- **Contrat de sortie d'adaptateur (normalisé)** : `{ canal, user_id, reply_handle, audio_path, ts, photo_path? }`. C'est tout ce que le cœur connaît → découplage total.

### Module 2 — STT
- **Service local** = conteneur **Speaches** (API compatible OpenAI `/v1/audio/transcriptions`), modèle **`Systran/faster-whisper-large-v3-turbo`**, `language=fr`, quantization **int8**.
- L'adaptateur POST l'audio → reçoit le **texte FR** en ~1-2 s.
- **Bascule de backend** par variable d'env : `STT_BACKEND=local|groq|openai`. Local par défaut (RGPD) ; Groq en secours si le VPS est saturé.
- **Purge** : le fichier audio est **supprimé immédiatement** après transcription (rétention 0).

### Module 3 — Routage vers Hermès (aiguilleur existant)
- On **écrit l'ordre** dans `/opt/data/claude-queue/in/` (la « boîte aux lettres » déjà opérationnelle — cf. `hello-aiguilleur.md`). Format proposé, un fichier `.md` + en-tête de métadonnées pour le **routage retour** :
  ```markdown
  ---
  source: telegram
  chat_id: 123456789
  msg_id: 42
  reply_handle: telegram:123456789
  reply_audio: true
  ts: 2026-06-03T10:15:00Z
  ---
  [Transcription du vocal de Mehdi]
  ```
- Hermès traite et **dépose la réponse dans `…/out/`** (texte + audio TTS), corrélée par `reply_handle`/`msg_id`. ➜ **à confirmer** : le contrat exact de `out/` (nom de fichier, où atterrit l'audio TTS).
- Avantage : **on ne touche pas à Hermès ni à l'aiguilleur** — on s'y branche par fichiers.

### Module 4 — TTS
- **Par défaut** : on réutilise le **`text_to_speech` d'Hermès**. La passerelle récupère l'audio dans `out/`.
- **Si TTS d'Hermès = cloud et RGPD prioritaire** : remplacer par **Piper** (rapide) ou **Voxtral TTS / Kokoro** (qualité) en local — Speaches expose déjà `/v1/audio/speech`.
- Sortie : **OGG/Opus** (format natif `sendVoice`) + le texte (utile pour relecture / Android Auto).

### Module 5 — Sortie + interface mobile optionnelle
- **Sortie** : le même adaptateur renvoie sur le bon canal (`sendVoice` + texte pour Telegram ; audio WhatsApp ; flux pour l'appel ; BT pour les lunettes).
- **PWA légère (option, en parallèle de Telegram)** : une web-app « push-to-talk » (bouton micro géant, lit la réponse audio). Avantages : indépendante de Telegram, installable sur l'écran d'accueil, **données chez nous**. Stack : page statique + WebRTC/`MediaRecorder` → endpoint passerelle. ~1 jour de dev.

### Transversal — Zéro maintenance, monitoring, RGPD
- **Auto-réparation** : tout en **Docker Compose** `restart: always` (ou services **systemd** `Restart=always`). Un **watchdog** ping le STT (`/health`) et le redémarre s'il fige.
- **Supervision** : **Uptime Kuma** (open-source, self-host) surveille bot + STT + aiguilleur ; **alertes Telegram/e-mail** en cas de panne. ([Uptime Kuma](https://github.com/louislam/uptime-kuma))
- **Auto-test nocturne** : un cron envoie un **audio de synthèse** (« test ») → vérifie qu'on récupère bien la transcription attendue → alerte sinon. (Détecte les pannes **avant** Mehdi.)
- **RGPD** : STT/TTS **100 % locaux** sur le VPS UE ; **audio purgé** après transcription (TTL) ; **disque chiffré** ; pas de tiers cloud pour l'audio (ou, si fallback, **Azure France Central / Google Belgique** avec DPA). ⚠️ *Caveat honnête* : les messages **transitent par les serveurs Telegram** (pas de E2E pour les bots) — pour une confidentialité maximale, privilégier **PWA** ou **pont d'appel auto-hébergé**.

---

## PHASE 3 — PLAN DE RÉALISATION

### Étape 0 — Quick win : boucler la boucle Telegram (**1-2 jours**)
**On développe :** un service **bot Telegram** (systemd/Docker) qui : reçoit le vocal → télécharge l'OGG → POST au STT local → écrit l'ordre dans `in/` → surveille `out/` → renvoie **`sendVoice`(audio) + texte**.
**Sur étagère :** **Speaches** (Docker, faster-whisper `large-v3-turbo`) ; **TTS d'Hermès** existant ; **Telegram Bot API** (gratuit) ; **aiogram**.
**Mains-libres :** configurer **« Hey Google → message Telegram à Hermès »** (Android) ou **Raccourci Siri** (iPhone).
**Prérequis :** token bot (@BotFather) ; accès VPS ; **confirmer le contrat `in/` ↔ `out/`** de l'aiguilleur ; lancer le conteneur STT.
**Coût :** ~**0 €/mois** (local) ou **< 1 $/mois** (fallback Groq).
**Latence visée :** download ~0,5 s + STT ~1-2 s + Hermès 2-10 s + TTS ~1-2 s ≈ **5-15 s** (style mémo) — OK au volant.

### Étape 1 — Solution complète : robuste, multi-canal, supervisée (**1-2 semaines**)
**On développe :**
1. Généraliser en **Passerelle Voix** (pattern adaptateurs M1-M5, contrat interne).
2. **Pont d'appel** pour le vrai eyes-free : *option A managée* (Twilio + OpenAI Realtime, rapide à livrer, ~$0,02-0,30/min) ou *option B RGPD* (SIP + LiveKit + Voxtral STT/TTS, tout local). **Reco : démarrer option A** pour valider l'usage, basculer B si besoin RGPD/volume.
3. **WhatsApp** comme 2ᵉ canal (optionnel).
4. **PWA push-to-talk** (canal parallèle, données chez nous).
5. **Zéro-maintenance** : Compose `restart=always` + **Uptime Kuma** + healthchecks + alertes Telegram + auto-test nocturne + rotation des logs.
6. **RGPD** : purge audio TTL, chiffrement disque, documentation des flux.
**Sur étagère :** Speaches, Uptime Kuma, Caddy/nginx (TLS pour webhooks), Twilio/LiveKit, (Voxtral si option B).
**Prérequis :** un **domaine + TLS** (webhooks WhatsApp/PWA) ; compte WhatsApp Business (si activé) ; **numéro/trunk** (si pont d'appel).
**Coût :** **~5-15 €/mois** (numéro + cloud minimal), ou **~0 €** en tout-local hors numéro.

### Étape 2 — Roadmap futur
- **Meta Ray-Ban** : app compagnon via **Wearables Device Access Toolkit** (caméra + micro/HP BT). Capture audio/photo → passerelle (nouvel adaptateur) → **Hermès + vision Claude** → audio retour BT. Déclenchement par **appui** tant que « Hey Meta » n'est pas ouvert. → **vision + audio** mains-libres.
- **App mobile native** (Flutter/React Native) en remplacement de la PWA : **wake-word maison** (« Dis Hermès »), enregistrement en arrière-plan, intégration messagerie Android Auto/CarPlay.
- **Speech-to-speech temps réel** : migrer appel/lunettes vers **OpenAI Realtime** ou **Voxtral-Mini-Realtime + Kyutai/Moshi** auto-hébergé → tours de parole **< 1 s**.
- **Plaud → AGEA automatique** : brancher le trigger Zapier **« transcript generated »** → POST passerelle → classement auto des mémos AGEA. Temps réel quand l'**OAuth API Plaud** passera en GA.

---

## Décisions à confirmer (3 points)
1. **Téléphone de Mehdi : Android ou iPhone ?** → détermine la voie mains-libres d'entrée (« Hey Google » vs Raccourci Siri).
2. **VPS : CPU seul (combien de vCPU/RAM) ou GPU ?** → CPU suffit pour `large-v3-turbo`, mais valide le dimensionnement / l'option Voxtral.
3. **Le `text_to_speech` d'Hermès est-il local ou cloud ?** → s'il est cloud et que le RGPD prime, on bascule sur Piper/Voxtral TTS local.

---

## Sources
**STT cloud :** [Groq turbo](https://groq.com/blog/whisper-large-v3-turbo-now-available-on-groq-combining-speed-quality-for-speech-recognition) · [Groq docs](https://console.groq.com/docs/model/whisper-large-v3-turbo) · [OpenAI pricing](https://openai.com/api/pricing/) · [OpenAI transcribe (costgoat)](https://costgoat.com/pricing/openai-transcription) · [Deepgram pricing](https://deepgram.com/pricing) · [Deepgram FR](https://deepgram.com/learn/deepgram-expands-nova-3-with-spanish-french-and-portuguese-support) · [AssemblyAI](https://www.assemblyai.com/pricing/) · [ElevenLabs Scribe](https://elevenlabs.io/speech-to-text) · [Google STT](https://cloud.google.com/speech-to-text/pricing) · [Azure Speech](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/)
**STT/TTS open-source :** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [Speaches](https://github.com/speaches-ai/speaches) · [whisper.cpp](https://github.com/ggerganov/whisper.cpp) · [docker-whisper](https://github.com/hwdsl2/docker-whisper) · [Voxtral](https://mistral.ai/news/voxtral/) · [Voxtral-Mini-Realtime](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) · [Vosk](https://alphacephei.com/vosk/) · [Piper](https://github.com/rhasspy/piper)
**Telegram / conduite :** [Voicy](https://github.com/backmeupplz/voicy) · [vocaux Android Auto (bug)](https://bugs.telegram.org/c/50136)
**Pont d'appel :** [Twilio + OpenAI Realtime](https://www.twilio.com/code-exchange/ai-voice-assistant-openai-realtime-api)
**Plaud :** [Zapier](https://zapier.com/apps/plaud/integrations) · [OAuth API FAQ](https://support.plaud.ai/hc/en-us/articles/56[TELEPHONE]09-FAQs-for-Plaud-OAuth-API)
**WhatsApp :** [Meta pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
**Ray-Ban :** [Meta Wearables Toolkit](https://developers.meta.com/blog/introducing-meta-wearables-device-access-toolkit/) · [Road to VR](https://roadtovr.com/meta-ray-ban-smart-glasses-third-party-app-sdk-device-access-toolkit/)
**Monitoring :** [Uptime Kuma](https://github.com/louislam/uptime-kuma)
