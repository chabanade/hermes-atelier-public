# TURN (coturn) — relais média pour les NAT récalcitrants · **Option A**

STUN (déjà en place, cf. `DIAGNOSTIC.md`) règle ~90 % des cas en faisant découvrir
au navigateur son adresse publique (candidat `srflx`). Mais derrière un **NAT
symétrique** ou un **CGNAT strict** (certaines 4G, Wi-Fi d'hôtel/entreprise) même
le `srflx` ne donne pas de paire routable : `frames_in` reste à 0, silence total.
La seule parade fiable à 100 % est de **relayer** tout le média par un serveur
**TURN** — un candidat de type **`relay`**, par lequel transite l'audio.

Ce dossier fournit un **coturn** clé en main.

```
                         turns:…:5349 (TCP/TLS, cert Let's Encrypt)
  navigateur (NAT) ───────────────────────────────────────────▶ coturn ─┐
        ▲                                                                │ relais
        │  tout l'audio encapsulé dans la connexion TLS unique           │ UDP
        └──────────────────────────────  média relayé  ◀────────────────┘  (49160-49200)
                                                          aiortc / server.py (VPS)
```

---

## Pourquoi « Option A »

| | Option A (**ce dépôt**) | Option B (pour mémoire) |
|---|---|---|
| Port TURN | **5349/TCP** (TLS, `turns:`) | 443 partagé avec le web |
| Web (Caddy 443) | **intact, propre** | doit cohabiter (ALPN/partage) |
| Traverse les pare-feu « 443 only » | ✅ (TLS ressemble à du HTTPS) | ✅ |
| Complexité | **faible** (port dédié) | élevée (multiplexage 443) |
| Auth | **V1 statique** (1 compte fixe) | idem possible |
| Code applicatif | **zéro changement** | zéro changement |

On choisit A : un port dédié `5349`, le **certificat Let's Encrypt déjà émis par
Caddy** (mutualisé, pas de second domaine), une authentification **statique**
simple (un compte `webrtc:<secret>`). Le serveur Python n'est pas touché : il sait
déjà annoncer un relais TURN au navigateur via `/ice` dès que `deploy/turn.env`
existe (chargé par `restart.sh`).

> **3478 clair, DTLS, UDP** : **désactivés**. Seul `5349/TCP/TLS` est ouvert.

---

## Prérequis

1. **Caddy en place et certificat émis** — c'est lui qui détient le certificat que
   coturn va réutiliser. Vérifier :
   ```bash
   curl -fsS https://votre-domaine.example/health
   ```
   (Si ça échoue : `sudo bash deploy/install-caddy.sh` d'abord.)
2. Être **root sur le VPS** (hors sandbox de l'agent).

---

## Déploiement (3 commandes)

```bash
sudo bash deploy/install-coturn.sh   # installe + configure coturn, écrit deploy/turn.env
cd ~/travaux/webrtc-vocal
./restart.sh                         # recharge l'appli : /ice annonce désormais le TURN
```

Vérification immédiate :
```bash
curl -fsS http://127.0.0.1:8686/ice | python3 -m json.tool
# doit contenir un objet { "urls": ["turns:votre-domaine.example:5349?transport=tcp"],
#                          "username": "webrtc", "credential": "…" }
```

### Ce que fait `install-coturn.sh`

- installe le paquet **coturn** et l'active (`/etc/default/coturn`) ;
- **génère un secret** aléatoire (réutilisé si déjà présent — idempotent) et
  l'injecte dans `/etc/turnserver.conf` **et** dans `deploy/turn.env` (gardés
  synchrones) ;
- installe `turnserver.conf` (domaine/IP publique adaptés) ;
- installe le **script + units de synchro du certificat** (voir plus bas) et fait
  une **première synchro** du cert depuis Caddy ;
- ouvre le **pare-feu** `ufw` (`5349/tcp` + `49160:49200/udp`) et rappelle de faire
  de même côté **hPanel Hostinger** ;
- démarre `coturn` et arme la resynchro automatique du certificat.

---

## Certificat : mutualisé avec Caddy, resynchronisé tout seul

coturn tourne sous l'utilisateur `turnserver` et **ne peut pas lire** le store
privé de Caddy (`/var/lib/caddy`, clés en `0600`). On en recopie donc une instance
dans `/etc/coturn/certs/` :

| Élément | Rôle |
|---|---|
| `deploy/coturn-cert-sync.sh` → `/usr/local/sbin/` | copie cert+clé de Caddy → `/etc/coturn/certs/`, **restart coturn uniquement si le cert a changé**. |
| `coturn-cert-sync.path` | surveille le `.crt` de Caddy ; déclenche la synchro **au renouvellement** (quasi immédiat). |
| `coturn-cert-sync.timer` | **filet quotidien** : couvre les cas où le `.path` ne se déclenche pas (autre émetteur ACME, événement manqué). |

Let's Encrypt renouvelle ~30 j avant l'expiration ; la synchro est donc passive et
silencieuse en régime établi. Pour forcer une synchro à la main :
```bash
sudo /usr/local/sbin/coturn-cert-sync.sh
```

---

## Pare-feu

Ouvrir **deux** choses (les deux côtés : `ufw` ET hPanel Hostinger) :

| Port / plage | Proto | Rôle |
|---|---|---|
| `5349` | **TCP** | TURN over TLS (signalisation + données encapsulées côté navigateur). |
| `49160-49200` | **UDP** | ports de **relais média** que coturn alloue vers le pair (le serveur aiortc). |

```bash
sudo ufw allow 5349/tcp
sudo ufw allow 49160:49200/udp
```
> La plage UDP doit correspondre à `min-port`/`max-port` de `turnserver.conf`.
> Côté **navigateur**, rien à ouvrir : tout passe par l'unique connexion TCP 5349.

---

## Authentification (V1 statique)

Un seul compte long-terme, défini dans `/etc/turnserver.conf` :
```
lt-cred-mech
realm=votre-domaine.example
user=webrtc:<secret généré>
```
Le même secret est dans `deploy/turn.env` (lu par `restart.sh`), donc côté
navigateur (via `/ice`). **Faire tourner le secret** :
```bash
NEW=$(openssl rand -hex 24)
sudo sed -i "s|^user=webrtc:.*|user=webrtc:$NEW|" /etc/turnserver.conf
sudo sed -i "s|^WEBRTC_TURN_PASS=.*|WEBRTC_TURN_PASS=$NEW|" deploy/turn.env
sudo systemctl restart coturn && ./restart.sh
```

> `deploy/turn.env` contient un **secret** : il est en `0640` et **gitignoré**
> (cf. `deploy/.gitignore`). Ne le committez pas.

---

## Tester

**1. Le certificat présenté sur 5349** (doit être le LE, valide pour le domaine) :
```bash
openssl s_client -connect votre-domaine.example:5349 \
  -servername votre-domaine.example </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

**2. coturn écoute bien** :
```bash
sudo ss -tlnp | grep 5349        # turnserver en LISTEN sur 5349
journalctl -u coturn -n 30 --no-pager
```

**3. Un candidat `relay` est obtenu** (le test qui tranche). Ouvrir la page
**Trickle ICE** de WebRTC :
<https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/>
- supprimer les serveurs par défaut, ajouter **uniquement** :
  - URL `turns:votre-domaine.example:5349?transport=tcp`
  - username `webrtc`, password = le secret
- « Gather candidates » : une ligne de **type `relay`** ⇒ TURN+TLS+auth OK.
  *(Aucun `relay` ⇒ voir Dépannage.)*

**4. En CLI** (alternative) — une allocation réussie prouve TLS+auth :
```bash
turnutils_uclient -S -t -p 5349 -u webrtc -w '<secret>' votre-domaine.example
# « success » sur l'ALLOCATE = OK (les timeouts data sont normaux sans turnutils_peer).
```

**5. Bout en bout** : depuis le téléphone sur le réseau qui posait problème,
ouvrir la page, parler. Côté navigateur, les logs doivent montrer
`candidats ICE locaux : …, relay×1` et `frames_in` décoller (cf. `DIAGNOSTIC.md`).

---

## Dépannage

| Symptôme | Piste |
|---|---|
| `install-coturn.sh` s'arrête sur « Certificat introuvable » | Caddy n'a pas (encore) émis le cert. `curl -fsS https://<domaine>/health`, puis relancer. |
| `coturn` ne démarre pas (`journalctl -u coturn`) | cert/clé absents ou illisibles dans `/etc/coturn/certs/` → `sudo /usr/local/sbin/coturn-cert-sync.sh` ; vérifier `TURNSERVER_ENABLED=1` dans `/etc/default/coturn`. |
| `openssl s_client` échoue/timeout sur 5349 | pare-feu (5349/TCP) — **hPanel Hostinger** en plus de `ufw`. |
| Trickle ICE : aucun `relay` | mauvais user/pass (vérifier `deploy/turn.env` ↔ `/etc/turnserver.conf`), ou `realm` ≠ domaine, ou cert non valide pour le domaine (test 1). |
| `relay` obtenu mais audio toujours muet | plage **UDP 49160-49200** non ouverte (le relais ne peut pas joindre le pair). |
| `/ice` n'annonce pas le TURN | `deploy/turn.env` absent/non chargé → relancer `./restart.sh` ; il doit afficher la ligne `TURN : turns:…`. |
| Le cert a été renouvelé mais coturn sert l'ancien | `systemctl status coturn-cert-sync.path coturn-cert-sync.timer` ; forcer : `sudo /usr/local/sbin/coturn-cert-sync.sh`. |

Logs utiles :
```bash
journalctl -u coturn -f                       # serveur TURN
systemctl list-timers | grep coturn-cert      # prochaine resynchro cert
```

---

## Revenir en arrière (désactiver le TURN)

Sans désinstaller coturn — l'appli repasse en **STUN seul** :
```bash
mv deploy/turn.env deploy/turn.env.off && ./restart.sh   # /ice n'annonce plus le TURN
sudo systemctl disable --now coturn coturn-cert-sync.path coturn-cert-sync.timer
```
Pour retirer complètement : `sudo apt-get purge coturn` puis supprimer
`/etc/coturn/`, `/usr/local/sbin/coturn-cert-sync.sh` et les units
`/etc/systemd/system/coturn-cert-sync.*`.

---

## Aller plus loin (hors périmètre de cette V1)

- **Option B — TURN sur 443 partagé** avec le web : nécessaire si même le `5349`
  est filtré. Plus intrusif (multiplexage du 443) ; à n'envisager que si des
  clients restent injoignables malgré l'Option A.
- **V2 — credentials éphémères** (`use-auth-secret`, REST API) : un secret tournant
  par session au lieu d'un compte statique. Pertinent en multi-utilisateurs ; pour
  un usage perso unique, la V1 statique suffit.
