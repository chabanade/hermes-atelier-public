# DIAGNOSTIC — WebRTC Vocal (Phase 2 : instrumentation + STUN)

Guide pour répondre à **une seule** question, la plus fréquente : *« je clique
Démarrer, la page dit “connecté”, je parle… et rien »*. Avant la Phase 2 on ne
savait pas **où** la chaîne se cassait. Maintenant si : un compteur **`frames_in`**
dit si l'audio arrive vraiment au serveur, et **STUN** corrige la cause n°1 d'un
silence total à distance (la traversée de NAT).

La chaîne complète :

```
micro navigateur ──[A] WebRTC/NAT──▶ serveur reçoit des trames ──[B] VAD/endpointing
   ──[C] STT ──[D] inbox→Hermès→outbox ──[E] TTS ──[F] piste sortante──▶ oreillette
```

`frames_in` isole **[A]** (le plus opaque) du reste : tant qu'il vaut 0, inutile
de soupçonner le STT, Hermès ou Piper — **aucun son n'entre**.

---

## 1. Le réflexe : lire `/diag`

Tout est dans un seul endpoint JSON. Depuis l'hôte du VPS :

```bash
curl -fsS http://127.0.0.1:8686/diag | python3 -m json.tool
```

Exemple (une conversation en cours, saine) :

```json
{
  "global": { "sessions_total": 3, "frames_in_total": 12450,
              "frames_out_total": 18020, "turns_total": 5 },
  "ice_servers": [ { "urls": ["stun:stun.l.google.com:19302"] } ],
  "config": { "vad_backend": "auto", "end_silence_ms": 800,
              "media_heartbeat_s": 5.0, "no_frame_warn_s": 4.0, "log_level": "INFO" },
  "sessions": {
    "a8e5484d": {
      "state": "listening", "connection_state": "connected", "ice_state": "completed",
      "local_candidate_types": ["host", "host", "srflx"],
      "frames_in": 1530, "frames_out": 2050, "samples_in": 489600,
      "vad_frames": 1020, "speech_frames": 240, "recv_timeouts": 2,
      "max_rms": 3120.0, "uptime_s": 31.2, "since_last_frame_s": 0.1
    }
  }
}
```

**Les trois chiffres qui tranchent**, par session :

| Champ | Sain | Ce que ça veut dire si anormal |
|---|---|---|
| `frames_in` | **monte** (≈50/s en parlant) | **0 ⇒ aucun audio n'entre** : voir §2 (NAT/STUN, firewall, micro). |
| `max_rms` | quelques centaines à quelques milliers | **~0 alors que `frames_in` monte** : audio reçu mais **muet** (micro coupé/mauvais ⇒ STT vide). |
| `speech_frames` | > 0 quand on parle | **0 alors que `max_rms` est haut** : le VAD ne déclenche pas (seuil/agressivité — voir §4). |

Raccourci ultra-rapide sans tout l'objet :

```bash
curl -fsS http://127.0.0.1:8686/health    # {"ok":true,"sessions":1,"ice_servers":1,"frames_in_total":1530}
```

`/poll?session=<id>` (déjà consommé par la page) porte aussi `frames_in`,
`frames_out`, `ice_state`, `connection_state` — pratique côté navigateur.

---

## 2. `frames_in` reste à 0 : c'est (presque toujours) la traversée de NAT

C'était LE piège du serveur d'origine. **Symptôme** : `connection_state` finit à
`failed` (ou reste `connecting`), `frames_in` ne décolle jamais, et le log porte :

```
[a8e5484d 1.2.3.4] ⚠ 4s écoulées, connexion=connecting mais 0 trame audio ENTRANTE
   (frames_in=0). Cause probable : traversée NAT échouée … STUN absent/bloqué …
```

### Pourquoi (le fond)

WebRTC essaie des **paires de candidats** (mon adresse → ton adresse) jusqu'à en
trouver une routable. Sans STUN, le navigateur de Mehdi (iPhone en 4G, ou Wi-Fi
derrière la box) n'annonce que son **IP privée** :

```
serveur VOTRE_IP_VPS  ──▶  client 192.168.1.55   ❌ injoignable (derrière NAT)
```

`192.168.1.55` n'existe pas sur Internet : le VPS ne peut pas y envoyer de paquet.
**STUN** fait découvrir au navigateur son **adresse publique** vue d'Internet (un
candidat de type **`srflx`**, *server-reflexive*) :

```
serveur VOTRE_IP_VPS  ──▶  client 88.120.x.y:51234 (srflx)   ✅ routable
```

> **Lecture historique** (logs avant Phase 2) : la connexion ne « marchait » que
> par chance, quand **les deux** pairs avaient une IPv6 publique routable
> (`2a02:… → 2a01:…` SUCCEEDED). En IPv4-seul, l'unique paire était
> `VOTRE_IP_VPS → 192.168.1.55` (privée) + des bridges Docker `172.x` inutiles :
> aucune routable ⇒ silence total. STUN supprime cette dépendance à la chance.

### Le verdict en une ligne

Dans les **logs du navigateur** (zone noire en bas de la page) après « Démarrer » :

```
candidats ICE locaux : host×2, srflx×1      ✅ STUN a marché
candidats ICE locaux : host×2               ⚠ aucun srflx → NAT non traversé
```

Côté **serveur**, `/diag` → `local_candidate_types` montre les candidats du VPS
(le `host` public suffit côté serveur — il n'est pas derrière NAT ; c'est le
**navigateur** qui a besoin du `srflx`).

### Les correctifs (par ordre)

1. **STUN activé ?** `curl …/ice` doit renvoyer au moins un serveur :
   ```bash
   curl -fsS http://127.0.0.1:8686/ice   # {"iceServers":[{"urls":["stun:stun.l.google.com:19302"]}]}
   ```
   Si `{"iceServers":[]}` ⇒ `WEBRTC_ICE_SERVERS` est vide. Mettre (défaut conseillé) :
   ```bash
   WEBRTC_ICE_SERVERS="stun:stun.l.google.com:19302" ./restart.sh
   ```
2. **Firewall UDP.** STUN/ICE utilisent l'**UDP**. Le port média est éphémère et
   sortant côté serveur (aiortc), mais un firewall qui bloque l'UDP sortant
   casse tout. Sur le VPS, autoriser l'UDP sortant ; Hostinger hPanel ▸ Firewall.
3. **STUN ne suffit pas (NAT symétrique / CGNAT strict).** Rare mais réel : même
   avec un `srflx`, la paire échoue. Il faut alors **relayer** le média par un
   serveur **TURN** (le seul qui marche à 100 %, car le trafic transite par lui).
   Un coturn est désormais **fourni dans le dépôt** (Option A : `turns:…:5349` en
   TCP, certificat Let's Encrypt mutualisé avec Caddy, le web reste propre sur 443).
   Déploiement en root sur le VPS :
   ```bash
   sudo bash deploy/install-coturn.sh   # installe coturn + écrit deploy/turn.env
   ./restart.sh                         # charge turn.env → /ice annonce le TURN
   ```
   Détails, credentials, pare-feu, test et dépannage : **`deploy/TURN.md`**.

   **Le signal de succès** : un nouveau type de candidat **`relay`** apparaît côté
   navigateur (`candidats ICE locaux : host×2, srflx×1, relay×1`) et la paire
   retenue passe par lui — `frames_in` décolle enfin. Sans monter de coturn, on
   peut aussi pointer un TURN tiers à la main :
   ```bash
   WEBRTC_TURN_URL="turns:turn.example.net:5349?transport=tcp" \
   WEBRTC_TURN_USER="user" WEBRTC_TURN_PASS="secret" ./restart.sh
   ```

---

## 3. `frames_in` monte mais Hermès reste muet : c'est en aval

L'audio **entre** bien ([A] OK). Le `frames_in=… (+N, …/s)` du heartbeat le
prouve. On descend la chaîne :

| Observation (logs / `/diag`) | Étape | Action |
|---|---|---|
| `max_rms` ~0 malgré `frames_in` qui monte | micro muet | vérifier le micro/la permission ; tester un autre appareil |
| `speech_frames`=0 alors que `max_rms` est haut | VAD ne déclenche pas | §4 (seuils VAD) |
| log `transcription VIDE (…s d'audio capté…)` | STT [C] | micro faible/bruité ; essayer `WHISPER_MODEL=small` |
| `transcription '…'` OK mais **aucune** outbox | pont Hermès [D] | démon non lancé ou **`WEBRTC_DATA_DIR` différent** entre serveur et démon |
| `reply.log` : `hermes: command not found` | cerveau Hermès | corriger `HERMES_BIN` ou passer en `HERMES_URL=http://…` |
| `pas de réponse Hermès (timeout)` | [D] lent | augmenter `HERMES_TIMEOUT_S` ; vérifier le LLM |
| `frames_out` reste à 0 pendant `speaking` | TTS/piste [E][F] | échec Piper (voir log `TTS[stderr]`) ; voix `.onnx` présente ? |

> Ce tableau prolonge celui du **RUNBOOK** (« je parle mais aucune réponse ») : la
> nouveauté Phase 2 est qu'on sait désormais **distinguer** [A] (NAT) du reste,
> au lieu de tout soupçonner.

---

## 4. VAD qui ne déclenche pas (`speech_frames`=0)

Si `max_rms` est franc (centaines/milliers) mais `speech_frames` reste à 0, le
VAD juge tout « silence ». Leviers (puis `./restart.sh`) :

```bash
WEBRTC_VAD=webrtcvad VAD_AGGRESSIVENESS=1   # 0=permissif … 3=strict
# ou, en repli énergie, baisser le seuil RMS :
WEBRTC_VAD=energy VAD_ENERGY_THRESHOLD=300
```

Inversement, si Hermès se déclenche sur du bruit, **monter** l'agressivité / le
seuil. `MIN_SPEECH_MS` rejette les phrases trop courtes (toux) ; `END_SILENCE_MS`
règle le délai de silence qui marque la fin de phrase.

---

## 5. Logs : ce qui est nouveau et comment les lire

Journalisation enrichie (toujours dans `logs/server.log`) :

- **Au démarrage** — la bannière ajoute la ligne `ICE=…` (serveurs STUN/TURN
  effectifs) et `log=… heartbeat_média=…`. Si `ICE=AUCUN`, un **WARNING** prévient
  qu'un client derrière NAT échouera.
- **À l'ouverture d'une session** — `candidats ICE locaux : …` (les IP que le VPS
  annonce) et, transition par transition, `ICE -> checking/connected/completed`
  (`failed` ⇒ WARNING explicite).
- **1ʳᵉ trame** — `1ʳᵉ trame micro reçue (0.42s après l'ouverture)` : le flux
  entrant est établi. Si cette ligne n'apparaît jamais ⇒ §2.
- **Heartbeat média** — toutes les `WEBRTC_MEDIA_HEARTBEAT_S` (défaut 5 s) :
  ```
  média: frames_in=445 (+50, 50/s) voix=69/105 frames_out=449 max_rms=4547 état=listening
  ```
  `+N/…s` = débit instantané (≈50 trames/s en parlant). `0/s` durablement en
  pleine parole ⇒ le flux s'est tari.
- **Alerte 0 trame** — le WARNING `… 0 trame audio ENTRANTE …` après
  `WEBRTC_NO_FRAME_WARN_S` (défaut 4 s) si connecté sans aucune trame.
- **aioice** — les paires de candidats ICE (`CandidatePair(... ) State…SUCCEEDED`,
  `ICE completed`) restent loggées par la lib : **c'est là qu'on voit la paire
  RETENUE** (l'API n'expose pas proprement la paire « gagnante », d'où ce renvoi).
  Trop verbeux ? `WEBRTC_AIOICE_LOG=WARNING`. Besoin de tout voir ?
  `WEBRTC_LOG_LEVEL=DEBUG`.

---

## 6. Variables d'environnement (Phase 2)

| Variable | Défaut | Rôle |
|---|---|---|
| `WEBRTC_ICE_SERVERS` | `stun:stun.l.google.com:19302` | serveurs ICE (liste d'URL séparées par virgules/espaces **ou** JSON `[{urls,username,credential}]`). **Vide ⇒ host-only** (un client derrière NAT échoue). |
| `WEBRTC_TURN_URL` / `_USER` / `_PASS` | _(vide)_ | relais TURN (quand STUN ne suffit pas). Renseigné par `deploy/turn.env` via `deploy/install-coturn.sh` ; cf. `deploy/TURN.md`. |
| `WEBRTC_LOG_LEVEL` | `INFO` | niveau du logger applicatif (`DEBUG` pour tout voir). |
| `WEBRTC_AIOICE_LOG` | `INFO` | niveau du logger ICE (`WARNING` pour réduire le bruit). |
| `WEBRTC_MEDIA_HEARTBEAT_S` | `5` | période du heartbeat média. |
| `WEBRTC_NO_FRAME_WARN_S` | `4` | délai avant l'alerte « 0 trame entrante ». |

Les mêmes serveurs ICE servent **et** au serveur aiortc **et** au navigateur (via
`/ice`) : une seule source de vérité.

---

## 7. Endpoints de diagnostic

| Route | Rôle |
|---|---|
| `GET /diag` | tableau de bord JSON : compteurs cumulés (`global`), config ICE, et **métriques par session** (dont `frames_in`). |
| `GET /ice` | liste des serveurs ICE servie au navigateur (`{"iceServers":[…]}`). |
| `GET /health` | sonde + `sessions`, `ice_servers`, `frames_in_total`. |
| `GET /poll?session=<id>` | état conversation + `frames_in`/`frames_out`/`ice_state` (déjà utilisé par la page). |

---

## 8. Reproduire / valider hors-ligne

Le test d'intégration vérifie la chaîne **et** l'instrumentation (boucle locale
⇒ host-only, donc sans dépendance à un STUN externe) :

```bash
.venv/bin/python tests/test_conversation.py
# … ✅ CONVERSATION VALIDÉE …
# … ✅ INSTRUMENTATION VALIDÉE : /ice + /diag + frames_in (Phase 2)
```

Sur le VPS, après `./restart.sh`, le **test de bout en bout** depuis le téléphone
reste celui du RUNBOOK (§4–5) ; en cas de silence, ouvrir `/diag` **avant** tout :
si `frames_in=0`, c'est §2 (NAT/STUN) — pas la peine de toucher au STT ni à Hermès.
