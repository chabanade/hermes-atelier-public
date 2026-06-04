# Protocole de délégation aux ouvriers

## Architecture

```
Moi (Hermes) → /opt/data/claude-queue/in/  (UNE seule boîte)
                    │
            🚦 Aiguilleur (hôte VPS) lit le tag :
                    ├─ "@pc …"  → 🖥️ Ouvrier PC (VOTRE_USER, auto)
                    └─ défaut   → ☁️ Ouvrier VPS (cage, auto H24)
                    │
        📤 Toutes les réponses → /opt/data/claude-queue/out/
```

## Règles absolues

### 1. Toujours déléguer le travail technique
- Installation de packages, configuration système, debug, scripting → ouvrier
- Mon rôle : orchestrer, pas exécuter
- Exception : tâches triviales (lire un fichier, lister un dossier)

### 2. Une seule boîte
- Dépôt TOUJOURS dans `/opt/data/claude-queue/in/`
- Lecture TOUJOURS dans `/opt/data/claude-queue/out/`
- La boîte PC (`claude-queue-pc/`) n'existe plus — ne pas chercher

### 3. Routage @pc
- Pour cibler l'ouvrier sur VOTRE_USER : préfixer l'ordre par `@pc ` (sur la première ligne, suivi d'un espace)
- Sans préfixe = ouvrier VPS (cage)
- L'aiguilleur fait le routage automatiquement

### 4. Patience
- L'ouvrier peut prendre 5 à 10 minutes — c'est normal
- Ne PAS reprendre la main en parallèle
- Attendre le `.out` puis lire le résultat
- « Si l'ouvrier traîne, c'est peut-être parce qu'il réfléchit beaucoup pour trouver la meilleure solution »

### 5. Timeouts et découpage
- Ouvrier VPS : timeout **15 minutes**
- Ouvrier PC (VOTRE_USER) : timeout **8 minutes**
- Si une tâche risque de dépasser → la découper en briques de 5-10 minutes
- Une tâche qui a timeout → status 124 dans le log, fichier `.out` vide ou partiel
- Relancer en plus petites étapes, ne pas insister sur le même ordre

### 6. Parsing du releveur PC
- Le releveur PC enlève le tag `@pc` du début de l'ordre
- ⚠️ Éviter de commencer l'ordre par un mot ambigu qui pourrait être confondu avec le tag
- Exemple : ne pas écrire `@pc Tâche unique : ...` car le releveur peut ne garder que `Tâche`
- Écrire plutôt : `@pc Redémarre le gateway...` (verbe d'action en premier)

### 7. Ne pas poser de questions techniques à Mehdi
- Questions SSH, config, accès, commandes → à l'ouvrier (@pc ou VPS)
- Mehdi n'est pas développeur, il est souvent en voiture
- Si l'ouvrier est bloqué/bridé → dire à Mehdi « l'ouvrier est bloqué sur X, à vérifier ce soir »

### 8. Ne jamais montrer de code à Mehdi
- Pas de YAML, pas de JSON, pas de chemins de fichier, pas de commandes shell
- Expliquer le RÉSULTAT, pas la mécanique
- S'il veut les détails techniques, il les demandera à Claude Code sur VOTRE_USER

## Erreurs commises (NE PAS RÉPÉTER)

| Date | Erreur | Correction |
|------|--------|------------|
| 03/06 | Dépôt dans `claude-queue-pc/` au lieu de `claude-queue/` | Une seule boîte, routage @pc |
| 03/06 | Fait l'install Piper moi-même au lieu de déléguer | Déposer l'ordre, laisser l'ouvrier faire |
| 03/06 | Court-circuité l'ouvrier (piper-config) sans attendre le .out | Attendre le `.out` |
| 03/06 | Montré du YAML/code à Mehdi | Résultat uniquement, pas la mécanique |
| 03/06 | Demandé à Mehdi si VOTRE_USER a un accès SSH | Demander à @pc, pas à Mehdi |
| 03/06 | Ordre `@pc Tâche unique : ...` → le releveur n'a lu que `Tâche` | Verbe d'action en premier après @pc |
| 03/06 | Tâche voice-bot-dev trop grosse → timeout 15 min (status 124) | Découper en briques de 5-10 min |
