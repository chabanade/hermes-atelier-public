# Changer le mot de passe du bot vocal (rotation du jeton Telegram)

> **C'est de la PRUDENCE, pas une urgence.** Le bot `@ermes_Voc_bot` ne répond qu'à **toi**
> (il est verrouillé sur ton numéro). Même si quelqu'un voyait l'ancien jeton, il ne pourrait
> pas te lire ni se faire passer pour toi. À faire quand tu as 2 minutes, sans stress.

## Pourquoi
Le « jeton » d'un bot Telegram, c'est sa **clé**. La nôtre a pu être vue à un moment pendant
le développement. Par hygiène, on la **remplace** : l'ancienne devient inutile, la nouvelle
prend le relais. Comme changer la serrure après avoir prêté un double.

## Étape 1 — Obtenir une nouvelle clé (toi, sur ton téléphone, 1 min)
1. Ouvre Telegram → écris à **@BotFather**.
2. Envoie `/mybots` → choisis **@ermes_Voc_bot**.
3. Appuie sur **API Token** → **Revoke current token** → confirme.
4. BotFather affiche une **nouvelle clé** (une longue suite du genre `8123…:AAE…`).
   **Ne me la colle PAS dans la conversation** (règle d'or : un secret ne passe jamais en clair
   dans un chat).

## Étape 2 — L'appliquer en sécurité (avec moi, 30 s)
Dis-moi simplement **« j'ai la nouvelle clé du bot »**. On la transfère sans jamais l'afficher :
- tu la colles dans un petit fichier texte sur ton PC (ex. `Bureau\nouvelle-cle.txt`) ;
- je la dépose sur le serveur dans le fichier prévu (`.new-token`) ;
- je lance le script `roter-cle-bot.sh` qui **vérifie d'abord** la clé auprès de Telegram
  (si elle est mauvaise, **rien n'est touché**), puis remplace, redémarre le bot et **efface**
  la clé en clair ;
- j'efface aussi ton fichier sur le PC.

## Étape 3 — Vérifier (toi, 10 s)
Envoie un petit vocal au bot. S'il te répond, c'est bon : la nouvelle clé fonctionne et
l'ancienne est morte.

---
*Outil : `vps-travaux/hermes-voc-bot/roter-cle-bot.sh` (déjà déposé sur le serveur, prêt à l'emploi).
Sécurités : clé validée avant application, jamais affichée, `.env` sauvegardé, clé en clair effacée après.*
