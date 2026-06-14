# Claude Queue — Mode d'emploi

## Architecture

Deux files d'attente distinctes, relevées par deux ouvriers différents :

| File | Chemin | Relevée par | Environnement |
|------|--------|-------------|---------------|
| Ouvrier local | `/opt/data/claude-queue/` | Aiguilleur hôte (hors conteneur) | Claude Code serveur — 14 skills intégrés, connecteurs MCP (Rexel, AGEA, Drive, Notion, Slack, Gmail) |
| Ouvrier PC | `/opt/data/claude-queue-pc/` | PC de Mehdi (VOTRE_USER) | Claude Code sur le PC de Mehdi — a accès aux skills personnalisés (mehdi-twin, skills métier) |

## Protocole

1. **Déposer un ordre** : écrire la consigne dans `/opt/data/claude-queue{,-pc}/in/<nom>.txt`
   - Pour une mission complexe, commencer par `@max ` (effort maximal)
2. **Attendre** ~20-60 secondes pour la file locale, variable pour la file PC
3. **Lire la réponse** dans `/opt/data/claude-queue{,-pc}/out/<nom>.txt.out`
4. **Itérer** si nécessaire : déposer un nouvel ordre avec ajustements

## Règles strictes

- ❌ Ne JAMAIS investiguer pourquoi une file ne répond pas
- ❌ Ne JAMAIS chercher le script aiguilleur
- ❌ Ne JAMAIS lancer `claude` directement
- ✅ Si pas de réponse après 60s : informer Mehdi
- ✅ Pour la file PC : demander si le PC est connecté

## Différences entre les deux ouvriers

**Ouvrier local** (serveur) :
- 14 skills intégrés Claude Code
- Connecteurs : Rexel, AGEA, Google Drive, Notion, Slack, Gmail, Audible
- Idéal pour : recherches web, revue de code, automatisation

**Ouvrier PC** (VOTRE_USER) :
- Skills personnalisés de Mehdi (mehdi-twin, LEXIA, skills métier)
- Accès aux fichiers locaux de Mehdi
- Idéal pour : tâches nécessitant le jumeau numérique, contexte métier avancé
