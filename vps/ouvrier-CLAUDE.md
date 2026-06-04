# Atelier de l'ouvrier — règles

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
