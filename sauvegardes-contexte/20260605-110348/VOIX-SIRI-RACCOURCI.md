# 🚗 Parler à Hermès MAINS-LIBRES en voiture (Raccourci Siri) — Mehdi

> Objectif d'origine : parler à Hermès **sans toucher le téléphone**, en conduisant.
> Voici comment, en 5 minutes, avec un **Raccourci iPhone**. Pas besoin de note vocale :
> ton iPhone fait la dictée (voix→texte) et lit la réponse à voix haute. Hermès, lui,
> tourne sur ton serveur. Tout est testé côté serveur ; il ne reste qu'à créer le Raccourci.

**Le jeton secret est sur ton Telegram** (je te l'ai envoyé) — garde-le, il sert ci-dessous.

---

## Créer le Raccourci « Dis à Hermès »

1. Ouvre l'app **Raccourcis** → **+** (nouveau raccourci) → renomme-le **Dis à Hermès**
   (c'est ce nom que tu diras à Siri).

2. Ajoute ces actions, dans l'ordre :

   **① Dicter le texte**
   - Cherche « Dicter le texte », ajoute-la. (Langue : Français.)

   **② Obtenir le contenu de l'URL**
   - URL : `https://srv1720865.hstgr.cloud/siri`
   - Appuie sur « Afficher plus » :
     - **Méthode** : `POST`
     - **En-têtes** : ajoute `X-Siri-Token` = *(le jeton reçu sur Telegram)*
     - **Corps de la requête** : `JSON`
       - Ajoute un champ **Texte** nommé `text`, et mets dedans la variable **Texte dicté** (de l'action ①).

   **③ Obtenir la valeur du dictionnaire**
   - Clé : `reply` — dans le **Contenu de l'URL** (résultat de l'action ②).

   **④ Énoncer le texte**
   - Texte : la **Valeur du dictionnaire** (de l'action ③). (Voix : française.)

3. Enregistre.

## L'utiliser (mains-libres)
- Dis : **« Dis Siri, Dis à Hermès »** → parle (« où en est mon chantier de Grasse ? ») →
  ton iPhone envoie, attend ~8-10 s, et **lit la réponse d'Hermès à voix haute**. 🔊
- En voiture (CarPlay / Bluetooth) : ça marche les mains sur le volant.

## Si ça coince
- « Non autorisé » → le jeton `X-Siri-Token` est mal recopié.
- Rien ne se passe → vérifie l'URL exacte et que le corps est bien en **JSON** avec le champ `text`.
- Dis-le moi, je regarde les logs côté serveur.

---

### Comment ça marche (pour info)
Ton iPhone fait la **dictée** (gratuit, local Apple) → envoie le texte (protégé par jeton, en
HTTPS) au point d'entrée `/siri` du serveur → qui passe par le **même pont qu'Hermès vocal**
(Telegram + web) → Hermès répond → ton iPhone **lit** la réponse. Aucun audio ne transite :
seul du texte chiffré va et vient. C'est le 3ᵉ moyen de parler à Hermès, le plus mains-libres.
