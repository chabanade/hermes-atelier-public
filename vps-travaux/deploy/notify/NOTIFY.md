# Notification « fin de travail ouvrier » — zéro token au repos

## Le problème
Aujourd'hui Hermès fait des `cat .live` répétés pour savoir si l'ouvrier a fini.
Chaque `cat` est une invocation LLM → des tokens brûlés même quand rien n'a
changé. On veut l'inverse : **l'ouvrier POUSSE un signal quand il a fini**, et un
relais **sans LLM** prévient Hermès/Mehdi. Au repos : rien ne tourne, 0 token.

## Le principe (commun aux 2 solutions)
```
  Ouvrier (dans la cage)                Hôte (hors cage)                 Telegram
  ───────────────────────               ──────────────────               ────────
  fin de tâche
  └─ ouvrier-done.sh "résumé"  ─écrit─► /home/ouvrier/travaux/.done
                                              │ (watch inotix, 0 token au repos)
                                              ▼
                                        notify-hermes.sh  ──POST /reply──►  📨 message
                                        (pur bash + curl,    (bot déjà en route)   à Mehdi
                                         AUCUN LLM)
```
Trois faits du système rendent ça gratuit et fiable :
1. **L'ouvrier peut écrire `~/travaux/.done`** (disque hôte persistant) mais **ne
   peut pas** joindre Telegram lui-même (garde-fou secret→réseau) → d'où le relais hôte.
2. **Le watch est de l'inotify** (noyau) : tant qu'aucun `.done` n'apparaît,
   **aucun process, aucun token**. Réaction quasi instantanée à l'écriture.
3. **Le canal de sortie existe déjà** : `POST 127.0.0.1:8080/reply {chat_id,text}`
   du bot `hermes-voc-bot` envoie un message Telegram **sans réveiller de LLM**.
   Donc même la notification coûte 0 token. Le seul coût LLM reste le travail
   lui-même (incompressible), plus jamais le « est-ce fini ? ».

Le `.done` est un petit JSON :
```json
{ "task": "...", "status": "done|error|need-input",
  "summary": "résumé 1 ligne", "report_path": "/chemin/RAPPORT.md", "ts": "..." }
```

---

## ✅ Solution A (RECOMMANDÉE) — unité systemd `.path` (inotify natif)
Le couple `ouvrier-done.path` + `ouvrier-done.service`. Le `.path` arme un watch
inotify géré par systemd ; à l'apparition de `.done`, il lance le `.service`
(oneshot) qui exécute `notify-hermes.sh`. Le relais consomme et supprime le
`.done` → le watch se réarme. `PathExists` (pas `PathModified`) garantit le
**rattrapage** : si le watcher était arrêté, le `.done` en attente est traité au
redémarrage → **aucune notification ratée**.

**Pourquoi celle-ci d'abord** : c'est *exactement* le patron déjà en prod chez
toi (les « sas » root/Hostinger surveillent `.root-request`/`.hostinger-request`).
Rien à superviser : pas de process au repos, systemd gère tout, redémarre seul.

**Installer** (via le sas root) :
```
SOLUTION=A bash /home/ouvrier/travaux/deploy/notify/install-ouvrier-notify.sh
# (déjà câblé : echo ".../install-ouvrier-notify.sh" >> ~/travaux/.root-request)
```
**Configurer** : renseigner le destinataire dans `notify.env`
(`NOTIFY_CHAT_ID=<ton chat_id>`). Si vide, le relais reprend `READY_CHAT_ID` /
`TELEGRAM_TOKEN` de `hermes-voc-bot/.env`.

**Comment ça notifie Hermès** : `notify-hermes.sh` POSTe le résumé sur le
`/reply` du bot → Mehdi reçoit « ✅ Ouvrier — <tâche> : done \n <résumé> \n 📄 <rapport> ».
Repli automatique sur l'API Telegram directe si `/reply` est injoignable.

---

## ⚙️ Solution B (alternative, style maison) — démon bash `inotifywait`
`ouvrier-notify-daemon.sh` sous `ouvrier-notify.service`. Même relais, mais un
démon bash calqué sur `webrtc-reply.sh` (le pont « 0 token au repos » que tu as
déjà adopté). `inotifywait -m` bloque dans le noyau → 0 CPU / 0 token au repos.

**Quand la préférer** : si tu veux un script unique lisible plutôt qu'une unité
`.path`, dans la continuité directe de `webrtc-reply.sh`. Coût : un process
résident (léger) à superviser, et le paquet `inotify-tools`.

**Installer** (via le sas root) :
```
SOLUTION=B bash /home/ouvrier/travaux/deploy/notify/install-ouvrier-notify.sh
```
> ⚠️ N'active **pas** A et B en même temps (double notification). L'installeur
> désactive l'autre automatiquement.

---

## (Repli C, pour mémoire) — cron `no_agent` shell toutes les N min
Un cron *shell* (pas LLM) qui teste l'existence de `.done` et appelle le relais.
0 token aussi, **mais** : pas instantané (latence ≤ N min) et tourne à vide.
À ne retenir que sur une machine sans inotify/systemd. inotify est strictement
meilleur sur les 3 critères (instantané, fiable, 0 token).

---

## Côté ouvrier : une seule ligne à la fin d'une tâche
```bash
/home/ouvrier/travaux/deploy/notify/ouvrier-done.sh \
   -t "webrtc lenteur" -s done -r /home/ouvrier/travaux/webrtc-vocal/RAPPORT.md \
   "TTS 2x plus rapide, tests verts"
```
(ou, pour signaler une question/erreur : `-s need-input` / `-s error`.)
On peut aussi câbler cet appel dans un hook `Stop`/`SubagentStop` du copilote
pour l'automatiser à 100 %.

## Test (sur l'HÔTE, où le bot tourne — pas dans la cage)
```bash
sudo -u ouvrier /home/ouvrier/travaux/deploy/notify/ouvrier-done.sh -t demo "ça marche"
tail -f /home/ouvrier/journal/ouvrier-notify.log
```
Vérifié hors-ligne dans la cage : dépôt atomique, prise atomique du `.done`,
parsing, tentative `/reply` puis repli Telegram, archivage `done-archive/`,
journal. L'envoi réel (secret→réseau) se valide sur l'hôte.

## Fichiers livrés (`deploy/notify/`)
| Fichier | Rôle |
|---|---|
| `ouvrier-done.sh` | l'ouvrier dépose `.done` (atomique) — à appeler en fin de tâche |
| `notify-hermes.sh` | relais hôte : lit `.done`, POST `/reply`, repli Telegram, archive |
| `ouvrier-done.path` / `.service` | **Solution A** : watch systemd + oneshot |
| `ouvrier-notify-daemon.sh` / `ouvrier-notify.service` | **Solution B** : démon bash inotify |
| `notify.env.example` | config (destinataire, URL) — copier en `notify.env` |
| `install-ouvrier-notify.sh` | installeur root (`SOLUTION=A` ou `B`) |
