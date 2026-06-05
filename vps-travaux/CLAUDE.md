# Atelier de l'ouvrier — règles

## 🧭 Ta méthode de travail : ARTS (le réflexe AVANT tout le reste)

Avant de te jeter sur une tâche, applique cette boucle en 4 temps. Elle évite de casser ce
qui marche et d'annoncer un succès qui n'en est pas un. C'est **non négociable**.

**A — Audite l'état réel d'abord.** Regarde ce qui EXISTE et ce qui TOURNE vraiment avant de
toucher (le service est-il `active` ? le fichier est-il là ? la valeur est-elle déjà bonne ?).
Ne te fie jamais à une supposition : vérifie d'abord.

**R — Reste réversible.** Avant de modifier un fichier, fais-en une copie horodatée
(`cp -a fichier fichier.bak-$(date +%Y%m%d-%H%M%S)`). Préfère une surcharge (drop-in systemd)
plutôt que réécrire un fichier entier. Garde toujours un chemin de retour.

**T — Teste AVANT de dire que c'est fait.** Ne déclare JAMAIS une tâche réussie sans preuve.
Lance la commande qui le prouve : un service `active`, un `curl` qui répond `200` **sur le
domaine public** (jamais `localhost` : ta cage a son propre localhost, ce n'est pas le vrai
serveur), un fichier réellement créé. Si tu ne l'as pas VU marcher, ce n'est pas fait.

**S — Sois propre en fin.** Pas de fichier temporaire qui traîne, le résultat rangé dans ton
atelier `/home/ouvrier/travaux/<projet>/`.

INTERDIT : Annoncer « c'est corrigé / installé / déployé » sans l'avoir testé toi-même.
À LA PLACE : Montre la preuve (la sortie de ta commande de test) dans ta réponse.

INTERDIT : Réécrire ou supprimer un fichier de config sans copie de sauvegarde d'abord.
À LA PLACE : Copie horodatée AVANT, puis modifie ; en cas de souci, restaure la copie.

## Tu as les droits root, via le « sas root » (ne demande JAMAIS à un humain)

Tu n'as pas `sudo` directement dans ta cage — c'est NORMAL, ne cherche jamais à le
contourner (pas de `--dangerously-skip-permissions`, pas de socket Docker, etc.).

Quand une tâche a besoin de root — installer un paquet (`apt`), (re)démarrer un
service systemd, ouvrir un port avec `ufw`, lancer un script d'installation système :

1. **Écris ton script `.sh`** dans ton atelier (par ex. `deploy/install-X.sh`).
2. **Déclare-le** pour exécution root en ajoutant son chemin absolu dans `.root-request` :
   ```
   echo "/home/ouvrier/travaux/<projet>/deploy/install-X.sh" >> /home/ouvrier/travaux/.root-request
   ```
3. **C'est tout.** Après ta tâche, un **sas root** exécute ton script EN ROOT
   automatiquement et joint le résultat à ta réponse.

Le sas refuse **seulement les catastrophes** (effacement système, désarmement des
protections copilote/cage, backdoor SSH, exfiltration de secret). **Tout le reste passe
tout seul** : `apt install`, `systemctl restart`, `ufw allow`, déploiements, etc.

➡️ Donc tu n'as **JAMAIS** besoin de demander à un humain de lancer un `sudo` à ta place.
Tu prépares le script, tu le déclares dans `.root-request`, le sas s'en occupe.

## Le pare-feu Hostinger, via le « sas Hostinger » (ne demande JAMAIS d'aller dans hPanel)

Le pare-feu EXTERNE du VPS (chez Hostinger) ne se règle pas avec `ufw` : il a sa propre
porte (l'API Hostinger). Tu n'as pas besoin de la clé — un **guichet** (root) la détient.

Pour vérifier ou changer un port côté Hostinger, écris une demande (une par ligne) dans
`.hostinger-request` :
```
echo "diag"           >> /home/ouvrier/travaux/.hostinger-request   # voir l'etat reel (pare-feu attache ou non)
echo "open 5349 udp"  >> /home/ouvrier/travaux/.hostinger-request   # ouvrir un port
echo "close 5349 tcp" >> /home/ouvrier/travaux/.hostinger-request   # fermer un port
```
Après ta tâche, le **sas Hostinger** exécute ta demande via l'API officielle et joint le
résultat à ta réponse.

⚠️ **Commence TOUJOURS par `diag`.** Très souvent un port « bloqué » ne l'est PAS chez
Hostinger (aucun pare-feu attaché) : le souci est alors côté serveur (`ufw` local ou le
service lui-même). Le `diag` te le dit en 2 secondes — règle-le toi-même, n'envoie JAMAIS
un humain cliquer dans hPanel.

(Le guichet refuse de créer/attacher un pare-feu de zéro — ça couperait le SSH — et exige
que le port 22 reste ouvert. Ces cas-là seulement demandent un GO humain.)

## Une seule copie de travail
Travaille toujours dans `/home/ouvrier/travaux/<projet>/` (ton atelier réel, monté sur
l'hôte). N'utilise pas de copie ailleurs : c'est cette copie-là qui est servie en prod.
