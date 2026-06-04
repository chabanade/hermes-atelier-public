---
name: garde-fou-auto-reparation
description: "DIABOLUS LIGHT — Garde-fou anti-auto-réparation : ce skill empêche de casser ce qui marche en voulant « réparer » un service temporairement indisponible."
category: research
tags: [garde-fou, diabolus, 10eme-homme, resilience, oauth]
---

# Garde-fou Anti-Auto-Réparation (DIABOLUS LIGHT)

Ce skill est la voix du 10ème homme intégrée à Hermes. Il empêche deux classes d'erreurs partageant la même cause racine : **agir sans vérifier, sans demander**.

Voir `references/cas-concrets.md` pour les incidents documentés.

---

## PARTIE 1 — Services en erreur (retry automatique)

### Déclencheur

Ce skill DOIT être chargé dès qu'un outil externe (MCP, API, service) retourne :
- Un message d'erreur contenant « retry », « wait », « cooldown », « reconnect », « backoff »
- Un code d'erreur temporaire (429, 502, 503, 504)
- « not connected », « unreachable », « auto-retry available in »

### Règle absolue

> **NE JAMAIS tenter de « réparer » un service qui annonce un retry automatique.**

### Procédure

1. **LIRE** le message d'erreur en entier. Chercher spécifiquement :
   - Un délai de retry (« retry in Xs », « available in ~Xs »)
   - Une instruction explicite (« do not retry », « ne retente pas »)
   - Un code de statut temporaire

2. **Si un délai est indiqué** → attendre CE délai + 10 secondes de marge. Ne rien faire.

3. **Passé le délai** → tester avec `hermes mcp test <nom>` (pas login, pas reconnect).

4. **Si toujours down** → informer Mehdi, utiliser les fallbacks.

5. **Si rétabli** → reprendre.

### INTERDIT

- ❌ `hermes mcp login <service>` en headless — OAuth PKCE nécessite un navigateur
- ❌ Relancer un outil MCP qui vient d'échouer 3 fois
- ❌ Supprimer/recréer un token sans preuve d'expiration
- ❌ Ignorer un message « ne retente pas »

### AUTORISÉ

- ✅ `hermes mcp test <service>` — lecture seule
- ✅ Fallbacks : `session_search()`, `search_files()`, `browser_navigate()`
- ✅ Informer Mehdi : « Service X indisponible, retry auto dans Y secondes. »

---

## PARTIE 2 — Actions système (installations, modifications)

### Déclencheur

Dès que l'action envisagée implique de modifier l'environnement :
- Installation de package (npm install -g, pip install, apt install, git clone)
- Modification de config système (.bashrc, .profile, /etc/)
- Création de fichiers hors du scope projet immédiat
- Toute commande avec effet persistant au-delà de la session

### Règle absolue

> **PROPOSER d'abord. Exécuter SEULEMENT après feu vert explicite de Mehdi.**

Même si l'outil est documenté dans un skill. Même si techniquement possible. Même si le skill dit « installer avec npm install -g ».

### Procédure

1. **Identifier** que l'action est une modification système
2. **Formuler** une proposition claire : quoi, pourquoi, impact
3. **Attendre** le « oui » explicite de Mehdi
4. **Exécuter** seulement après approbation

### INTERDIT

- ❌ `npm install -g`, `pip install`, `apt install` sans approbation
- ❌ Modifier `~/.bashrc`, `~/.profile`, variables d'environnement persistantes
- ❌ `git clone` dans des emplacements système
- ❌ Toute commande précédée de `sudo` sans demande explicite

### AUTORISÉ

- ✅ Lister ce qui est disponible : `which`, `--version`, vérifications
- ✅ Proposer un plan d'installation : « Voici ce qu'il faut installer et pourquoi »
- ✅ Installer APRÈS feu vert uniquement

---

## PARTIE 3 — Infrastructure externe (ne pas enquêter)

### Déclencheur

Dès que l'action envisagée implique de **comprendre ou modifier un système qui tourne hors du conteneur Hermes** :
- Chercher un script, binaire, ou processus qui n'est pas dans `/opt/data/` ou `/opt/hermes/`
- Investiguer des mécanismes de l'hôte (cron système, services, daemons)
- Tenter de « réparer » ou « relancer » un composant d'infrastructure
- Scanner `/proc`, `/etc/cron*`, `systemctl` pour trouver comment quelque chose fonctionne

### Règle absolue

> **Si c'est hors conteneur, c'est voulu. Ne pas enquêter. Ne pas essayer de comprendre comment ça marche.**

Les systèmes externes (aiguilleur, files d'attente PC, services hôte) sont gérés par Mehdi ou l'infrastructure. Mon rôle est d'utiliser l'interface fournie, pas d'en Reverse-Engineer le fonctionnement.

### Procédure

1. **Identifier** que la curiosité porte sur une boîte noire externe
2. **S'arrêter.** Ne pas lancer `find /`, `ps aux | grep`, `crontab -l`, `systemctl`
3. **Utiliser l'interface documentée** : déposer dans `in/`, lire dans `out/`
4. **Si l'interface ne répond pas** → informer Mehdi, ne pas investiguer pourquoi

### INTERDIT

- ❌ `find / -name "*aiguilleur*"` ou toute chasse au trésor système
- ❌ `ps aux | grep` pour trouver des processus hôte
- ❌ `crontab -l`, `systemctl`, investigation cron
- ❌ Théories sur « comment ça devrait marcher » suivies d'actions de réparation
- ❌ Supposer que l'absence de réponse = bug à corriger soi-même

### AUTORISÉ

- ✅ Déposer un ordre dans la file et attendre
- ✅ Signaler à Mehdi : « La file X n'a pas répondu depuis Y minutes »
- ✅ Demander à Mehdi de vérifier côté hôte/PC
- ✅ Consulter `references/claude-queue.md` pour le mode d'emploi des files d'attente

---

## PARTIE 4 — Ne pas court-circuiter l'ouvrier (impatience)

### Déclencheur

Dès qu'un ordre a été déposé dans `/opt/data/claude-queue/in/` et que l'ouvrier (VPS ou @pc) est en train de le traiter.

### Règle absolue

> **Ne JAMAIS reprendre la main sur une tâche déléguée à l'ouvrier. Attendre le `.out`.**

Si l'ouvrier met du temps, c'est qu'il réfléchit — pas qu'il est bloqué. Claude Code peut prendre 5, 10, voire 60 minutes sur une tâche complexe. Le résultat justifie l'attente.

### Procédure

1. **Déposer** l'ordre dans `in/`
2. **Vérifier** le log aiguilleur pour confirmer le routage
3. **Attendre** — ne rien faire en parallèle sur le même sujet
4. **Lire** le `.out` quand il arrive
5. **Si timeout** (status 124) → relancer en plus petites briques, ne pas faire soi-même

### INTERDIT

- ❌ Faire la tâche soi-même pendant que l'ouvrier la traite
- ❌ Modifier des fichiers que l'ouvrier est en train de modifier
- ❌ Considérer l'ouvrier comme « bloqué » après 4-5 minutes
- ❌ Dire à Mehdi « l'ouvrier traîne, je fais moi-même »

### AUTORISÉ

- ✅ Vérifier le log aiguilleur pour confirmer que l'ordre a été pris
- ✅ Signaler à Mehdi : « L'ouvrier bosse dessus, j'attends son retour »
- ✅ Si timeout après 15 min (VPS) ou 8 min (PC), relancer en briques plus petites

---

## Vérification après résolution

Quand le service est rétabli ou l'action terminée :
- Confirmer que les outils fonctionnent
- Noter la durée réelle d'indisponibilité
- Si le comportement a différé du message → sauvegarder dans memory
