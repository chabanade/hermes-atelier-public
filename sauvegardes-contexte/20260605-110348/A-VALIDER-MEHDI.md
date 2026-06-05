# À valider / à décider — Mehdi (session du 2026-06-05)

> Pendant ton déplacement, j'ai avancé en autonomie sur la fluidité de l'atelier.
> Voici **ce qui est fait** (déjà actif, rien à faire) et **ce qui attend ta décision**.

---

## ✅ Fait en autonomie (déjà actif, testé)

1. **Retour vocal rallumé** : `webrtc-reply` était arrêté depuis la veille (ta voix
   ne te répondait plus) → rallumé + verrouillé pour qu'il tienne au reboot.
2. **Salle de contrôle web (3ᵉ canal)** : `https://srv1720865.hstgr.cloud/atelier/`
   (mot de passe envoyé sur ton Telegram + dans `ACCES.txt` root-only). Tu y vois
   les ouvriers en direct, les résultats, l'état des services ; tu peux **déposer
   un ordre**, **poser une consigne permanente**, **mettre en pause** d'un bouton.
3. **caddy se relève tout seul** s'il tombe (`Restart=always` ajouté — avant, il
   serait resté mort et aurait emporté ton HTTPS + la voix avec lui).
4. **Hermès plus sûr et plus clair** (SOUL.md) : sait qu'un secret en clair dans
   un résultat = incident à signaler ; sait quoi faire quand un ordre dépasse le
   temps ; connaît la nouvelle salle de contrôle.
5. **Robustesse de l'atelier** : un ordre vide renvoie un message clair (au lieu du
   silence) ; un ouvrier coupé renvoie quand même son dernier message (au lieu d'un
   « réponse vide ») ; nettoyage des fichiers temporaires garanti.
6. **Ménage automatique** : la file ne gonfle plus sans fin (rétention 10/21 jours,
   minuteur quotidien) + rotation des journaux (plus de risque de saturer le disque).
7. **Outils plus solides** : hook git réparé (ne bloque plus à tort), poller PC plus
   robuste (livraison en un seul envoi, échec réseau visible et retenté).

Tout est sauvegardé dans **GitHub privé + Gitea souverain** (et la version publique,
nettoyée, pour la cohorte).

---

## 🟠 Ce qui attend TA décision (je n'ai pas tranché à ta place)

### 1. La voix : choisir UNE seule version (important)
Il existe **trois** « bricolages » vocaux en parallèle sur le serveur :
- **webrtc-vocal** : tu parles dans une page web (navigateur) ;
- **voice-bot** et **hermes-voc-bot** : deux bots vocaux Telegram.

C'est trois fois le même but, entretenu trois fois — source de confusion et de bugs.
**Question simple : par quel moyen veux-tu parler à Hermès au quotidien ?** (la page
web, ou un vocal Telegram ?) Une fois que tu me le dis, j'**archive les deux autres**,
je consolide celui qui reste, et je documente le choix.

### 2. La voix : la rendre plus rapide (prête à appliquer, mais à tester par toi)
La latence (~7 s) peut descendre à ~1,5 s : relève du courrier toutes les 1 s au lieu
de 5, et modèles voix gardés « chauds » en mémoire. **Mais ça touche le canal choisi
en (1)**, et il faut **toi, ton téléphone**, pour valider que ça marche en vrai (je ne
peux pas tester une vraie conversation vocale à ta place). On le fait ensemble à ton retour.

### 3. Rotation du token Telegram (par prudence, pas urgent)
Un **ancien** token (inactif) traînait dans un fichier d'exemple → nettoyé. Le bot qui
tourne **n'était pas concerné**. Si ce vieux token a un jour servi à un vrai bot,
change-le via BotFather par précaution. Sinon, rien à faire.

### 4. Relance des notifications de fin d'ouvrier (petit chantier, à ton feu vert)
Si l'envoi Telegram d'une fin de tâche échoue, la notification est aujourd'hui perdue
en silence. Je peux ajouter une **relance automatique**. Dis-moi si tu veux que je le fasse.

### 5. Mot de passe de la salle de contrôle
Généré au hasard (solide). Si tu en veux un **à toi** (plus facile à retenir), dis-le moi.

---

*Tout le détail technique est dans le rapport d'audit (36 trouvailles) et dans git.*
