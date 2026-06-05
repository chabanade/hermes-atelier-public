# Contribuer — critiques & propositions d'évolution

Bienvenue. Ce dépôt est la **stack de référence d'Hermès** (un atelier d'ouvriers IA pilotables à
distance, en sécurité). Il est partagé comme **preuve d'avancement** et comme **base de travail** :
votre regard critique et vos idées d'évolution sont exactement ce qu'on cherche.

> Avant de contribuer, lisez le [README](README.md) — il explique l'architecture en 5 minutes.

---

## Ce qu'on attend de vous

Deux types de contributions, également précieux :

- 🔎 **Une critique** — un risque, une faille, une faiblesse, une mauvaise idée que vous repérez.
- 💡 **Une évolution** — une amélioration concrète, une alternative, une brique manquante.

Pas besoin de tout détailler ni de coder : un constat clair et bien argumenté vaut de l'or.

---

## Comment déposer une contribution

### Le plus simple : une *issue* GitHub
Ouvrez une **issue** sur ce dépôt. Titre clair, et dans le corps : le composant concerné, le constat,
pourquoi ça compte, et votre proposition si vous en avez une. C'est parfait pour lancer une discussion.

### Pour une contribution écrite (fiche dans le repo)
Si vous voulez laisser une fiche durable, déposez un fichier Markdown dans le dossier `critiques/` :

- **Nom du fichier** : `AAAA-MM-JJ_<type>_<prénom>_<sujet-court>.md`
  *(type = `critique`, `evolution` ou `question`)*
  Exemple : `2026-06-10_critique_amine_sas-root-perimetre.md`
- **En-tête** (frontmatter) à mettre en haut du fichier :

```yaml
---
titre: "Le périmètre du sas root est-il assez serré ?"
auteur: amine
type: critique            # critique | evolution | question
composant: sas-root       # cage | copilote | aiguilleur | sas-root | sas-hostinger | garde-de-nuit | suivi-direct | autre
date: 2026-06-10
status: ouvert            # ouvert | traité
---
```

- **Le corps** : le constat, l'impact, la proposition, et une référence (`fichier:ligne`) si possible.

---

## ⚠️ La seule règle dure : zéro donnée personnelle

Ce dépôt est **public**. Comme dans la stack elle-même, **aucun secret, aucune donnée perso** ne doit
y apparaître :

- Pas de vraie clé, token, mot de passe — **jamais**, même en exemple.
- Pas d'IP réelle, de domaine réel, d'identifiant Telegram, de nom de client ou d'entreprise.
- Utilisez les mêmes marqueurs que le reste du repo : `VOTRE_IP_VPS`, `votre-domaine.example`,
  `VOTRE_CHAT_ID_TELEGRAM`, `[SECRET_REDACTED]`, etc.

Si vous utilisez **Claude Code**, le skill `docu-hermes` fait cette anonymisation automatiquement
pour vous (et range votre fiche au bon endroit).

---

## L'esprit

On veut le **fond** (la méthode, la critique, l'idée), pas la forme (vos données ou les nôtres).
Soyez francs : une critique dure et juste est plus utile qu'un compliment. Merci de faire avancer Hermès. 🤝
