# Hermès — Atelier d'ouvriers IA pilotables à distance

> Un écosystème qui permet à **Mehdi** de commander des **ouvriers IA** (Claude Code) à distance,
> depuis son téléphone, **en toute sécurité** — façon JARVIS / Miranda.
>
> Ce dépôt est la **stack de référence** : preuve d'avancement, base de travail et cible de critique
> pour un workshop. Tout le monde est invité à auditer, casser, et proposer des évolutions.

---

## 1. L'idée en une image

```
   Mehdi (téléphone)
        │  parle / écrit (Telegram, voix)
        ▼
   ┌─────────────┐     dépose un "ordre"      ┌──────────────────────────────┐
   │   HERMÈS    │ ─────────────────────────▶ │  Boîte aux lettres (queue)   │
   │ (l'agent,   │ ◀───────────────────────── │   in/  out/  done/           │
   │  DeepSeek,  │     lit la réponse + le     └──────────────┬───────────────┘
   │  conteneur) │     SUIVI EN DIRECT (.live)                │ relève (toutes les 15 s)
   └─────────────┘                                            ▼
                                                   ┌────────────────────┐
                                                   │    AIGUILLEUR      │  (root, hôte)
                                                   │  routeur déterministe
                                                   └─────────┬──────────┘
                                                             │ lance dans une CAGE
                                                             ▼
                                            ┌────────────────────────────────┐
                                            │   OUVRIER = Claude Code         │
                                            │   bubblewrap + COPILOTE         │
                                            │   (sans root, sans secrets)     │
                                            └────────────────────────────────┘
```

**Principe clé :** Hermès est *exposé* (Telegram, mail → risque d'injection) donc il ne touche jamais
le moteur : il ne fait que **déposer des ordres** et **lire des réponses**. L'ouvrier, lui, est
*enfermé* dans une cage : il a de la puissance, mais ne peut rien casser ni exfiltrer.

---

## 2. La sécurité : "F1 surveillée", pas "Twingo bridée"

Choix de conception de Mehdi : la sécurité = **un pilote + des capteurs**, pas des barrages qui
empêchent de travailler. « Une F1 qu'on bride trop, une Twingo la double. »

| Couche | Rôle |
|---|---|
| **Cage** (`cage.sh`, bubblewrap) | l'ouvrier ne voit qu'un bac à sable ; pas de `/`, pas de secrets, jamais root |
| **Copilote** (`copilote.js`) | laisse passer + journalise + lève des drapeaux ; **bloque seulement** l'exfiltration (réseau **+** secret) ou la tentative de se désarmer. *Fail-open.* |
| **Sas root** (`root-gateway.sh`) | l'ouvrier prépare un script, un guichet **root** le valide (refuse les catastrophes) et l'exécute. Plus besoin d'humain pour un `sudo`. |
| **Sas Hostinger** (`hostinger-gateway.sh`) | l'ouvrier gère le pare-feu **cloud** (API Hostinger) sans jamais voir la clé ; anti-coupure SSH intégré |

Les secrets ne sont **jamais** dans le code : ils sont lus à l'exécution depuis des fichiers
hors-dépôt (`/root/.hermes/.env`, `/root/.secrets/`, le « casier »). **Loi Zéro :** un secret ne
transite jamais en clair dans une conversation.

---

## 3. Le "facteur" : comment Hermès suit le travail

Au-delà du simple aller-retour de texte, l'ouvrier est suivi **comme Mehdi suit Claude Code** :

- **Suivi EN DIRECT** (`format-live.py`) — un fichier `.live` se remplit action par action en temps
  réel (💬 ce qu'il dit, 💻 ses commandes, 📖 lectures, ✏️ écritures). Hermès le lit « par-dessus
  l'épaule » et corrige sans attendre la fin.
- **Casier d'artefacts** — les **fichiers** produits par l'ouvrier sont livrés pour de vrai
  (`out/<ordre>.colis/` + archive), pas juste décrits en texte.
- **Tableau de bord** (`etat-global.sh`) — un état du système rafraîchi en continu, lu d'un coup
  d'œil au lieu de tâtonner avec 15 commandes.
- **Garde de nuit** (`garde-nuit.sh`) — veille **proactive** : prévient Mehdi sur Telegram tout seul
  (service tombé, échéance, saturation, ouvrier coincé) + briefing matin/soir.

---

## 4. Les fichiers

### `vps/` — tout ce qui tourne sur le serveur
| Fichier | Rôle |
|---|---|
| `aiguilleur.sh` | le routeur : relève la queue, lance l'ouvrier en cage, branche les 3 sas + artefacts |
| `cage.sh` / `cage-pc.sh` | la cage bubblewrap (VPS / PC) |
| `copilote.js` / `videur.js` | le surveillant fail-open (copilote) / l'ancien bloqueur (videur, en réserve) |
| `root-gateway.sh` | sas root (actions root validées) |
| `hostinger-gateway.sh` | sas Hostinger (pare-feu cloud via API) |
| `format-live.py` | le suivi en direct (.live) |
| `format-drapeaux.py` | met en forme les drapeaux du copilote pour Hermès |
| `etat-global.sh` | le tableau de bord permanent |
| `garde-nuit.sh` + `.service` + `.timer` | la veille proactive + son timer systemd |
| `ouvrier-CLAUDE.md` | les règles données à l'ouvrier dans son atelier |
| `banc-*.sh` | les **bancs d'essai** (tests à blanc de chaque mécanisme) |

### racine — le côté PC
| Fichier | Rôle |
|---|---|
| `poller.ps1` / `poller-loop.ps1` | le « releveur » : le PC va chercher les ordres `@pc` sur le VPS |
| `ouvrier-settings.json` | la config des hooks de l'ouvrier (gardien actif) |

### `_hermes-audit/` — l'âme d'Hermès
`SOUL.md` = le prompt système d'Hermès (caractère, règles, comment il orchestre l'ouvrier). C'est
ici que vivent les leçons (ne jamais contourner la sécurité, tester le domaine pas localhost, etc.).

---

## 5. Refaire un doublon sur un autre VPS

La stack est conçue pour être répliquée. Sur un nouveau serveur, il faut :

1. Installer les dépendances : `bubblewrap`, `jq`, `curl`, `openssl`, Node, Python 3, le CLI `claude`.
2. Créer l'utilisateur `ouvrier` (sans sudo) et déposer les scripts de `vps/` dans `/home/ouvrier/`.
3. Adapter les **identifiants d'infra** (ils sont en clair, ce ne sont PAS des secrets) :
   - l'IP / le domaine du VPS (`poller.ps1`, `etat-global.sh`, `garde-nuit.sh`) ;
   - l'`id` de discussion Telegram (`garde-nuit.sh`, variable `GN_CHAT`).
4. Déposer les **secrets** dans leurs fichiers hors-dépôt (jamais dans le git) :
   - `/root/.hermes/.env` → `TELEGRAM_BOT_TOKEN`, etc. ;
   - `/root/.secrets/hostinger-api.token` → la clé API Hostinger (droits 600, root).
5. Installer les services systemd (`aiguilleur.service`, `garde-nuit.timer`, services vocaux) + la
   boucle de l'aiguilleur.
6. Vérifier avec les bancs d'essai (`banc-*.sh`) avant d'ouvrir les vraies vannes.

> ⚠️ **Sécurité avant tout partage** : ce dépôt ne doit JAMAIS contenir de vrai secret. Le
> `.gitignore` exclut déjà `.env`, clés, jetons et états locaux. Vérifie-le avant chaque commit.

---

## 6. État d'avancement (juin 2026)

| Brique | État |
|---|---|
| Cage + copilote (VPS & PC) | ✅ en service |
| Boîte aux lettres + aiguilleur (systemd, increvable) | ✅ |
| Suivi en direct (.live) + casier artefacts | ✅ |
| Sas root + sas Hostinger | ✅ |
| Tableau de bord permanent | ✅ |
| Garde de nuit (alertes + briefing Telegram) | ✅ |
| La voix (STT/TTS, WebRTC, TURN) | 🟠 plomberie posée, à finir + tester |
| Délégation autonome (Hermès découpe seul) | 🟡 déjà bien avancé, à affiner |

---

*Stack développée par Mehdi (une PME) avec l'aide de Claude. Partagée avec
la cohorte de pairs comme base de travail et de critique.*
