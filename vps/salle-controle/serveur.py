#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SALLE DE CONTROLE de l'atelier Hermes  --  le 3e canal (apres Telegram et la voix).

Une page web SIMPLE, pensee pour le telephone, qui permet a Mehdi (artisan, pas
developpeur), meme en deplacement, de :
  - VOIR ses ouvriers en direct, SEPARES par machine (VPS / PC), qui travaille,
    sur quoi, ou ca en est (deux fenetres distinctes) ;
  - VOIR quel CERVEAU anime chaque agent (Hermes + secours, ouvriers) ;
  - VOIR l'etat des services + la surveillance (watchdog) + un bandeau de sante ;
  - DONNER un ordre, en choisissant l'EFFORT (faible -> max), le MODELE
    (Opus / Sonnet / Fable / Haiku), le mode ULTRACODE (multi-agents) et la machine ;
  - LIRE un resultat EN ENTIER (et pas juste un apercu) ;
  - COUPER un ouvrier precis, ou METTRE EN PAUSE tout l'atelier ;
  - POSER une consigne permanente (le "canal patron").

Conception :
  - Python 3 STANDARD LIBRARY uniquement (zero dependance a installer).
  - Ecoute en local (127.0.0.1:8787) : jamais expose directement. C'est Caddy
    (HTTPS + mot de passe) qui le publie sous https://<domaine>/atelier/.
  - Tourne en root (systemd) : lit la file et ecrit consignes/ordres de maniere
    ATOMIQUE (temp + rename) pour ne jamais entrer en collision avec l'aiguilleur.
  - Les appels couteux (systemctl/docker) sont MIS EN CACHE quelques secondes :
    la page se rafraichit toutes les 3 s sans relancer 8 sous-processus a chaque fois.

Echelle artisan : la barriere = HTTPS + mot de passe Caddy + ecoute localhost.
Robuste et lisible avant tout.
"""
import json
import os
import re
import time
import glob
import subprocess
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Emplacements (surchargeable par variables d'env pour les tests) ---------
QUEUE = os.environ.get("SC_QUEUE", "/root/.hermes/claude-queue")
PCQUEUE = os.environ.get("SC_PCQUEUE", "/root/claude-queue-pc")
CONSIGNES = os.environ.get("SC_CONSIGNES", "/root/.hermes/consignes-patron.txt")
HERMES_UID = int(os.environ.get("SC_HERMES_UID", "10000"))
HOST = os.environ.get("SC_HOST", "127.0.0.1")
PORT = int(os.environ.get("SC_PORT", "8787"))
PREFIX = os.environ.get("SC_PREFIX", "/atelier/")  # chemin public (pour <base>)

# Sources d'info "cerveaux" (lecture seule, tolerante).
CONFIG_HERMES = os.environ.get("SC_CONFIG_HERMES", "/root/.hermes/config.yaml")
OUVRIER_CLAUDE_JSON = os.environ.get("SC_OUVRIER_JSON", "/home/ouvrier/.claude.json")
OUVRIER_SETTINGS = os.environ.get("SC_OUVRIER_SETTINGS", "/home/ouvrier/hooks/ouvrier-settings.json")
# Effort par defaut par machine (ecrit ici par la salle, lu par l'aiguilleur /
# le releveur). Vide/absent = comportement historique (xhigh).
EFFORT_VPS = os.environ.get("SC_EFFORT_VPS", "/root/.hermes/effort-defaut-vps")
EFFORT_PC = os.environ.get("SC_EFFORT_PC", "/root/.hermes/effort-defaut-pc")
# Modele par defaut par machine (meme principe que l'effort). Vide/absent =
# l'ouvrier garde le modele de sa config (Opus 4.8).
MODELE_VPS = os.environ.get("SC_MODELE_VPS", "/root/.hermes/modele-defaut-vps")
MODELE_PC = os.environ.get("SC_MODELE_PC", "/root/.hermes/modele-defaut-pc")
# Ultracode par defaut par machine (presence du fichier = actif). Si actif, le
# mot-cle "ultracode" est injecte en tete de chaque ordre de cet ouvrier (par
# l'aiguilleur pour le VPS, par le releveur/run-order pour le PC).
ULTRA_VPS = os.environ.get("SC_ULTRA_VPS", "/root/.hermes/ultracode-defaut-vps")
ULTRA_PC = os.environ.get("SC_ULTRA_PC", "/root/.hermes/ultracode-defaut-pc")
# Ordres-types reutilisables (les "boutons a toi" de Mehdi), liste JSON editable
# depuis la salle. Chaque entree : {titre, texte}. Vide/absent = 3 modeles d'usine.
ORDRES_TYPES = os.environ.get("SC_ORDRES_TYPES", "/root/.hermes/ordres-types.json")
# Notifications Telegram (fonction #4) : interrupteur OFF par defaut (fichier
# present = ON). Un fil de fond previent Mehdi quand un ordre se termine. Le
# token + le chat_id sont lus dans le .env d'Hermes -- JAMAIS exposes ni loggues.
NOTIF_FLAG = os.environ.get("SC_NOTIF_FLAG", "/root/.hermes/notif-telegram")
NOTIF_STATE = os.environ.get("SC_NOTIF_STATE", "/root/.hermes/salle-notif-state.json")
NOTIF_ENV = os.environ.get("SC_NOTIF_ENV", "/root/.hermes/.env")

IN = os.path.join(QUEUE, "in")
OUT = os.path.join(QUEUE, "out")
DONE = os.path.join(QUEUE, "done")
STOP = os.path.join(QUEUE, "STOP")
# Demande de coupure de l'ouvrier PC (le releveur la verra a son prochain passage).
KILL_PC = os.path.join(PCQUEUE, "KILL")

# Niveaux d'effort proposes (Opus : tous dispo). 'defaut' = on ne force rien.
EFFORTS = ["defaut", "low", "medium", "high", "xhigh", "max"]

# Modeles proposes pour les ouvriers. 'defaut' = on ne force rien (l'ouvrier
# garde le modele de sa config). ATTENTION : xhigh/max ne marchent QUE sur Opus ;
# si un modele non-Opus est choisi avec xhigh/max, l'aiguilleur plafonne a high.
MODELES = ["defaut", "claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5", "claude-haiku-4-5"]
# Libelles courts pour les menus deroulants (l'ordre suit MODELES).
MODELES_MENU = {
    "defaut": "par defaut (Opus 4.8)",
    "claude-opus-4-8": "Opus 4.8 (le plus fort)",
    "claude-sonnet-4-6": "Sonnet 4.6 (rapide)",
    "claude-fable-5": "Fable 5",
    "claude-haiku-4-5": "Haiku 4.5 (eco)",
}

# Statut du watchdog : le cron de surveillance ecrit ce petit JSON toutes les
# 30 min (effet de bord). On le lit ici pour afficher la sante du serveur
# (disque/RAM/CPU/services) dans la salle de controle. Surchargeable pour les tests.
WATCHDOG_STATUS = os.environ.get("SC_WATCHDOG", "/opt/scripts/watchdog-status.json")

SERVICES = [
    ("aiguilleur", "Routeur (aiguilleur)"),
    ("webrtc-vocal", "Voix : ecoute"),
    ("webrtc-reply", "Voix : reponse"),
    ("coturn", "Voix : relais reseau"),
    ("caddy", "Site web (HTTPS)"),
    ("garde-nuit.timer", "Garde de nuit"),
]
CONTENEURS = [("hermes", "Hermes (le chef)"), ("speaches", "Transcription voix")]

# Noms "amicaux" des modeles (pour la carte Cerveaux).
MODELES_AMI = {
    "gpt-5.5": "GPT-5.5 (Codex)", "gpt-5.4": "GPT-5.4 (Codex)",
    "deepseek-v4-pro": "DeepSeek V4 Pro", "deepseek-chat": "DeepSeek",
    "claude-opus-4-8": "Opus 4.8", "claude-opus-4-7": "Opus 4.7",
    "claude-sonnet-4-6": "Sonnet 4.6", "claude-fable-5": "Fable 5",
    "claude-haiku-4-5": "Haiku 4.5",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
}


def joli_modele(mid):
    mid = (mid or "").strip()
    if not mid:
        return "?"
    return MODELES_AMI.get(mid, mid)


def _run(args, timeout=5):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr or "").strip()
    except Exception:
        return "?"


def _age(secondes):
    s = int(secondes)
    if s < 60:
        return f"il y a {s}s"
    if s < 3600:
        return f"il y a {s // 60} min"
    if s < 86400:
        return f"il y a {s // 3600} h"
    return f"il y a {s // 86400} j"


def _compter(d):
    try:
        return len([x for x in os.listdir(d) if not x.startswith(".")])
    except Exception:
        return 0


def _lire_fin(chemin, n=12, maxc=1400):
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            lignes = f.read().splitlines()
        bout = "\n".join(lignes[-n:])
        return bout[-maxc:]
    except Exception:
        return ""


def _apercu(chemin, maxc=240):
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            t = f.read(maxc + 1).strip().replace("\n", " ")
        return t[:maxc] + ("..." if len(t) > maxc else "")
    except Exception:
        return ""


# --- CACHE des appels couteux (services) -------------------------------------
#  systemctl/docker sont lents et leur reponse change rarement. On garde le
#  resultat ~25 s : la page reste fluide a 3 s sans relancer 8 sous-processus.
_cache = {"services": None, "services_t": 0.0}
CACHE_SERVICES_TTL = float(os.environ.get("SC_CACHE_TTL", "25"))


def services_caches():
    now = time.time()
    if _cache["services"] is not None and (now - _cache["services_t"]) < CACHE_SERVICES_TTL:
        return _cache["services"]
    services = []
    for unit, libelle in SERVICES:
        actif = _run(["systemctl", "is-active", unit]) == "active"
        services.append({"nom": libelle, "ok": actif})
    for nom, libelle in CONTENEURS:
        run = _run(["docker", "inspect", "-f", "{{.State.Running}}", nom]) == "true"
        services.append({"nom": libelle, "ok": run})
    _cache["services"] = services
    _cache["services_t"] = now
    return services


def _heartbeat(texte):
    """Extrait le numero de battement d'un .status du releveur PC."""
    m = re.search(r"BATTEMENT DE COEUR\s*:\s*(\d+)", texte or "")
    return int(m.group(1)) if m else None


def _ouvriers_live():
    """Deux sources, fusionnees mais ETIQUETEES par machine :
       - VPS : fichiers .live (detail action par action, ecrit par format-live) ;
       - PC  : fichiers .status (battement de coeur ecrit par le releveur),
               et .live-pc si le releveur streame le detail (Temps 2).
    """
    maintenant = time.time()
    vps, pc = [], []
    for live in sorted(glob.glob(os.path.join(OUT, "*.live"))):
        nom = os.path.basename(live)[:-5]
        try:
            age = maintenant - os.path.getmtime(live)
        except Exception:
            age = 0
        vps.append({"tache": nom, "lieu": "VPS", "depuis": _age(age),
                    "fige": age > 200, "direct": _lire_fin(live)})
    for st in sorted(glob.glob(os.path.join(OUT, "*.status"))):
        nom = os.path.basename(st)[:-7]
        try:
            age = maintenant - os.path.getmtime(st)
        except Exception:
            age = 0
        txt = ""
        try:
            with open(st, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception:
            pass
        livepc = os.path.join(OUT, nom + ".live-pc")
        detail = _lire_fin(livepc) if os.path.exists(livepc) else ""
        bat = _heartbeat(txt)
        pc.append({"tache": nom, "lieu": "PC", "depuis": _age(age),
                   "fige": age > 200,
                   "direct": detail or (f"En cours sur le PC (battement {bat}, vivant)." if bat is not None
                                        else "En cours sur le PC...")})
    return vps, pc


def etat():
    """Construit l'etat complet de l'atelier (toujours tolerant aux erreurs)."""
    services = services_caches()
    vps, pc = _ouvriers_live()

    # Derniers resultats livres
    resultats = []
    try:
        outs = sorted(glob.glob(os.path.join(OUT, "*.out")),
                      key=os.path.getmtime, reverse=True)[:8]
    except Exception:
        outs = []
    maintenant = time.time()
    for o in outs:
        try:
            age = maintenant - os.path.getmtime(o)
        except Exception:
            age = 0
        tache_nom = os.path.basename(o)[:-4]
        resultats.append({
            "tache": tache_nom,
            "quand": _age(age),
            "apercu": _apercu(o),
            "meta": meta_lire(tache_nom),
        })

    consigne = ""
    try:
        if os.path.exists(CONSIGNES):
            with open(CONSIGNES, "r", encoding="utf-8", errors="replace") as f:
                consigne = f.read().strip()
    except Exception:
        consigne = ""

    nb_ko = len([s for s in services if not s["ok"]])
    return {
        "horodatage": time.strftime("%H:%M:%S"),
        "services": services,
        "sante": {"ok": nb_ko == 0, "ko": nb_ko},
        "ouvriers_vps": vps,
        "ouvriers_pc": pc,
        "file": {"a_faire": _compter(IN), "termines": _compter(DONE),
                 "resultats": _compter(OUT)},
        "resultats": resultats,
        "consigne": consigne,
        "effort": effort_defaut_lire(),
        "modele": modele_defaut_lire(),
        "ultracode": ultracode_defaut_lire(),
        "en_pause": os.path.exists(STOP),
        "drapeaux": lire_drapeaux(),
        "notif": notif_lire(),
    }


def lire_watchdog():
    """Lit le statut JSON du watchdog (disque/RAM/CPU/services, etat global,
    alertes). TOUJOURS tolerant : si le fichier manque ou est illisible, renvoie
    {"present": False} et la salle de controle affiche « pas encore de donnees »
    plutot que de planter."""
    try:
        with open(WATCHDOG_STATUS, "r", encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"present": False}
    except Exception:
        return {"present": False}
    d["present"] = True
    # Anciennete de la derniere verification : repere un watchdog devenu muet
    # (cron tombe). Au-dela de ~90 min (3 cycles de 30 min), on signale "perime".
    try:
        epoch = float(d.get("epoch") or 0)
        if epoch > 0:
            age = max(0.0, time.time() - epoch)
            d["age"] = _age(age)
            d["perime"] = age > 5400
    except Exception:
        pass
    return d


def cerveaux():
    """Quel modele anime chaque agent ? Lecture tolerante des configs (PAS de
    dependance YAML : on scanne les lignes du bloc model:/fallback_providers:)."""
    hermes_principal, hermes_secours = "?", ""
    try:
        with open(CONFIG_HERMES, "r", encoding="utf-8", errors="replace") as f:
            lignes = f.read().splitlines()
        dans_model = False
        dans_fb = False
        for ln in lignes:
            if re.match(r"^model:\s*$", ln):
                dans_model, dans_fb = True, False
                continue
            if re.match(r"^fallback_providers:\s*", ln):
                dans_fb, dans_model = True, False
                continue
            if re.match(r"^\S", ln):  # nouvelle cle de 1er niveau -> on sort des blocs
                dans_model = dans_fb = False
            if dans_model:
                m = re.match(r"^\s+default:\s*(\S+)", ln)
                if m:
                    hermes_principal = joli_modele(m.group(1))
            if dans_fb and not hermes_secours:
                m = re.match(r"^\s*-?\s*model:\s*(\S+)", ln)
                if m:
                    hermes_secours = joli_modele(m.group(1))
    except Exception:
        pass

    ouv_vps = "?"
    # Source STABLE = ouvrier-settings.json (ce que l'ouvrier utilise via --settings).
    # .claude.json est reecrit par Claude Code et peut contenir d'anciennes entrees.
    alias_modele = {"opus": "Opus 4.8", "sonnet": "Sonnet 4.6", "haiku": "Haiku 4.5"}
    try:
        with open(OUVRIER_SETTINGS, "r", encoding="utf-8", errors="replace") as f:
            mid = (json.load(f).get("model") or "").strip()
        if mid:
            ouv_vps = alias_modele.get(mid, joli_modele(mid))
    except Exception:
        pass
    if ouv_vps == "?":
        try:
            with open(OUVRIER_CLAUDE_JSON, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            m = re.search(r'"model"\s*:\s*"(claude-opus-[\d-]+)"', txt)
            if m:
                ouv_vps = joli_modele(m.group(1))
        except Exception:
            pass

    # Un modele par defaut choisi dans la salle PRIME sur le modele de config :
    # c'est lui qui sera vraiment utilise au prochain ordre. On l'affiche donc ici.
    ov = modele_defaut_lire()
    if ov["vps"] != "defaut":
        ouv_vps = joli_modele(ov["vps"])
    ouv_pc = joli_modele(ov["pc"]) if ov["pc"] != "defaut" else "Opus 4.8"
    return {
        "hermes": {"principal": hermes_principal, "secours": hermes_secours or "(aucun)"},
        "ouvrier_vps": ouv_vps,
        # Le PC est une autre machine : la salle ne lit pas sa config. Valeur
        # connue de notre cote (ou le defaut choisi dans la salle s'il existe).
        "ouvrier_pc": ouv_pc,
    }


def _tache_sure(nom):
    """Anti-traversee : on n'accepte qu'un nom de fichier simple (pas de / ..)."""
    s = (nom or "").strip()
    base = os.path.basename(s)
    return base if base and base == s and ".." not in base else ""


def meta_lire(tache):
    """Lit la fiche telemetrie .meta d'une tache (cout/duree/modele/tokens), ecrite
    par format-live a cote du .out. TOUJOURS tolerant : pas de fichier ou JSON
    casse -> None (la salle affiche le resultat sans chiffres). Un champ absent
    reste null cote producteur : on n'invente JAMAIS une valeur ici."""
    base = _tache_sure(tache)
    if not base:
        return None
    chemin = os.path.join(OUT, base + ".meta")
    if not os.path.exists(chemin):
        return None
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def lire_drapeaux(max_age=21600, scan=20):
    """PASTILLE SECURITE : detecte une action MORTELLE bloquee par le copilote sur
    un ordre RECENT. Lecture seule : le marqueur '[MORTEL]' est annexe au .out (cote
    VPS via format-drapeaux.py, cote PC via le releveur). On scanne les derniers .out
    livres (tries par date) et on renvoie le plus recent qui porte ce marqueur, s'il
    date de moins de max_age secondes (defaut 6 h). Sinon {mortel: False}. On
    n'invente rien : pas de marqueur recent = pas de pastille."""
    try:
        outs = sorted(glob.glob(os.path.join(OUT, "*.out")),
                      key=os.path.getmtime, reverse=True)[:scan]
    except Exception:
        outs = []
    maintenant = time.time()
    for o in outs:
        try:
            age = maintenant - os.path.getmtime(o)
        except Exception:
            continue
        if age > max_age:
            break  # tries du plus recent au plus ancien : au-dela, tout est trop vieux
        try:
            with open(o, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read(40000)
        except Exception:
            continue
        if "[MORTEL]" in txt:
            raison = ""
            for ln in txt.splitlines():
                s = ln.strip()
                if s.startswith("- [MORTEL]"):
                    raison = s[len("- [MORTEL]"):].strip(" ]:\"").strip()
                    break
            tache = os.path.basename(o)[:-4]
            return {"mortel": True, "tache": tache, "quand": _age(age),
                    "raison": raison[:160], "sig": tache}
    return {"mortel": False}


def lire_resultat(tache):
    """Renvoie le resultat .out COMPLET d'une tache (capse, lecture seule)."""
    base = _tache_sure(tache)
    if not base:
        return {"ok": False, "message": "Nom de tache invalide."}
    chemin = os.path.join(OUT, base + ".out")
    if not os.path.exists(chemin):
        return {"ok": False, "message": "Resultat introuvable."}
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            contenu = f.read(60000)
        return {"ok": True, "tache": base, "contenu": contenu, "meta": meta_lire(base)}
    except Exception as e:
        return {"ok": False, "message": f"Lecture impossible : {e}"}


def tuer_ouvrier(lieu, tache):
    """Coupe l'ouvrier en cours. VPS : on tue les process de l'utilisateur
    'ouvrier' (un seul ouvrier a la fois). PC : on depose une demande de coupure
    que le releveur honorera a son prochain battement."""
    if lieu == "PC":
        try:
            os.makedirs(PCQUEUE, exist_ok=True)
            _ecrire_atomique(KILL_PC, (tache or "") + "\n", uid=HERMES_UID)
            return {"ok": True, "message": "Coupure de l'ouvrier PC demandee (effet au prochain battement)."}
        except Exception as e:
            return {"ok": False, "message": f"Echec : {e}"}
    # VPS : meme geste que la garde de nuit (pkill -9 -u ouvrier)
    _run(["pkill", "-9", "-u", "ouvrier"])
    return {"ok": True, "message": "Ouvrier VPS coupe (job en cours interrompu)."}


def effort_defaut_lire():
    def _l(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
            return v if v in EFFORTS else "defaut"
        except Exception:
            return "defaut"
    return {"vps": _l(EFFORT_VPS), "pc": _l(EFFORT_PC)}


def effort_defaut_ecrire(lieu, niveau):
    niveau = (niveau or "").strip()
    if niveau not in EFFORTS:
        return {"ok": False, "message": "Niveau d'effort inconnu."}
    cible = EFFORT_VPS if lieu == "vps" else EFFORT_PC if lieu == "pc" else None
    if not cible:
        return {"ok": False, "message": "Machine inconnue."}
    try:
        if niveau == "defaut" and os.path.exists(cible):
            os.remove(cible)
        elif niveau != "defaut":
            _ecrire_atomique(cible, niveau + "\n", uid=HERMES_UID)
        return {"ok": True, "message": f"Effort par defaut ({lieu}) : {niveau}."}
    except Exception as e:
        return {"ok": False, "message": f"Echec : {e}"}


def modele_defaut_lire():
    def _l(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
            return v if v in MODELES else "defaut"
        except Exception:
            return "defaut"
    return {"vps": _l(MODELE_VPS), "pc": _l(MODELE_PC)}


def modele_defaut_ecrire(lieu, mid):
    mid = (mid or "").strip()
    if mid not in MODELES:
        return {"ok": False, "message": "Modele inconnu."}
    cible = MODELE_VPS if lieu == "vps" else MODELE_PC if lieu == "pc" else None
    if not cible:
        return {"ok": False, "message": "Machine inconnue."}
    try:
        if mid == "defaut" and os.path.exists(cible):
            os.remove(cible)
        elif mid != "defaut":
            _ecrire_atomique(cible, mid + "\n", uid=HERMES_UID)
        nom = MODELES_MENU.get(mid, mid)
        return {"ok": True, "message": f"Modele par defaut ({lieu}) : {nom}."}
    except Exception as e:
        return {"ok": False, "message": f"Echec : {e}"}


def ultracode_defaut_lire():
    return {"vps": os.path.exists(ULTRA_VPS), "pc": os.path.exists(ULTRA_PC)}


def ultracode_defaut_ecrire(lieu, on):
    cible = ULTRA_VPS if lieu == "vps" else ULTRA_PC if lieu == "pc" else None
    if not cible:
        return {"ok": False, "message": "Machine inconnue."}
    try:
        if on:
            _ecrire_atomique(cible, "1\n", uid=HERMES_UID)
        elif os.path.exists(cible):
            os.remove(cible)
        return {"ok": True, "message": "Ultracode par defaut (%s) : %s." % (lieu, "ACTIVE" if on else "desactive")}
    except Exception as e:
        return {"ok": False, "message": f"Echec : {e}"}


def ordres_types_lire():
    """Liste des ordres-types (les boutons reutilisables de Mehdi). 3 d'usine si
    le fichier est absent (premiere utilisation)."""
    defaut = [
        {"titre": "Etat serveur", "texte": "Fais-moi un resume clair de l'etat du serveur (disque, RAM, CPU, services)."},
        {"titre": "Espace disque", "texte": "Verifie l'espace disque et dis-moi ce qui prend de la place."},
        {"titre": "Derniers logs", "texte": "Regarde les derniers logs d'erreur des services et resume les problemes eventuels."},
    ]
    try:
        with open(ORDRES_TYPES, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, list):
            out = [{"titre": str(x.get("titre", ""))[:40], "texte": str(x.get("texte", ""))}
                   for x in d if isinstance(x, dict) and x.get("texte")]
            return out if out else defaut
    except Exception:
        pass
    return defaut


def ordres_types_ecrire(action, titre, texte):
    """Ajoute (action=add) ou supprime (action=del, par titre) un ordre-type."""
    liste = ordres_types_lire()
    titre = (titre or "").strip()[:40]
    if action == "add":
        texte = (texte or "").strip()
        if not titre or not texte:
            return {"ok": False, "message": "Donne un titre ET un texte."}
        liste = [x for x in liste if x["titre"] != titre]   # remplace si meme titre
        liste.append({"titre": titre, "texte": texte})
    elif action == "del":
        liste = [x for x in liste if x["titre"] != titre]
    else:
        return {"ok": False, "message": "Action inconnue."}
    try:
        _ecrire_atomique(ORDRES_TYPES, json.dumps(liste, ensure_ascii=False), uid=HERMES_UID)
        return {"ok": True, "message": "Ordres-types mis a jour.", "liste": liste}
    except Exception as e:
        return {"ok": False, "message": f"Echec : {e}"}


def historique_lire(recherche="", limite=40):
    """Carnet de bord : tous les ordres passes (dossier done/), du plus recent au
    plus ancien, avec un apercu de la reponse (.out) et l'heure. Filtre texte
    optionnel (cherche dans le nom, l'ordre et la reponse). Lecture seule."""
    recherche = (recherche or "").strip().lower()
    try:
        fichiers = sorted(glob.glob(os.path.join(DONE, "*")), key=os.path.getmtime, reverse=True)
    except Exception:
        fichiers = []
    maintenant = time.time()
    items = []
    for f in fichiers:
        if not os.path.isfile(f):
            continue
        nom = os.path.basename(f)
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                ordre = fh.read(4000).strip()
        except Exception:
            ordre = ""
        outp = os.path.join(OUT, nom + ".out")
        apercu = _apercu(outp) if os.path.exists(outp) else ""
        if recherche and recherche not in (nom + " " + ordre + " " + apercu).lower():
            continue
        try:
            age = maintenant - os.path.getmtime(f)
        except Exception:
            age = 0
        items.append({"tache": nom, "quand": _age(age),
                      "ordre": ordre[:300], "apercu": apercu,
                      "a_resultat": os.path.exists(outp),
                      "meta": meta_lire(nom)})
        if len(items) >= limite:
            break
    return items


def refaire_ordre(tache):
    """Relance un ordre de l'historique : relit done/<tache> et le redepose dans
    in/ comme un nouvel ordre (capse anti-traversee). Reutilise donner_ordre."""
    base = _tache_sure(tache)
    if not base:
        return {"ok": False, "message": "Tache invalide."}
    chemin = os.path.join(DONE, base)
    if not os.path.exists(chemin):
        return {"ok": False, "message": "Ordre introuvable dans l'historique."}
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            texte = f.read(8000)
    except Exception as e:
        return {"ok": False, "message": f"Lecture impossible : {e}"}
    return donner_ordre(texte)


def _ecrire_atomique(chemin, contenu, uid=None):
    """Ecrit un fichier de maniere atomique (temp + rename) pour eviter toute
    lecture partielle par l'aiguilleur qui tourne en parallele."""
    d = os.path.dirname(chemin)
    base = os.path.basename(chemin)
    tmp = os.path.join(d, "." + base + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contenu)
        f.flush()
        os.fsync(f.fileno())
    if uid is not None:
        try:
            os.chown(tmp, uid, uid)
        except Exception:
            pass
    os.replace(tmp, chemin)


def poser_consigne(texte):
    _ecrire_atomique(CONSIGNES, texte.strip() + "\n", uid=HERMES_UID)
    return {"ok": True, "message": "Consigne permanente enregistree."}


def donner_ordre(texte):
    texte = texte.strip()
    if not texte:
        return {"ok": False, "message": "Ordre vide."}
    # Nom de tache unique et lisible (horodate). Depose ATOMIQUEMENT dans in/.
    nom = "patron-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(int(time.time() * 1000) % 1000)
    chemin = os.path.join(IN, nom + ".txt")
    _ecrire_atomique(chemin, texte, uid=HERMES_UID)
    return {"ok": True, "message": "Ordre depose : l'aiguilleur va lancer un ouvrier.",
            "tache": nom}


def basculer_pause(activer):
    try:
        if activer:
            _ecrire_atomique(STOP, "pause demandee depuis la salle de controle\n")
            return {"ok": True, "message": "Atelier EN PAUSE (aucun nouvel ordre traite)."}
        if os.path.exists(STOP):
            os.remove(STOP)
        return {"ok": True, "message": "Atelier REPARTI."}
    except Exception as e:
        return {"ok": False, "message": f"Echec : {e}"}


# --- PONT VOCAL SIRI : texte -> Hermes -> texte (pour un Raccourci iPhone) ------
#  Mains-libres : un Raccourci Siri dicte la question (voix->texte cote iOS), la
#  POST ici ; on passe par la MEME boite partagee que la voix Telegram/web (le
#  demon webrtc-reply appelle Hermes), et on renvoie le texte de la reponse ;
#  l'iPhone le lit a voix haute. Pas de STT/TTS ici : iOS s'en charge.
VOICE_DATA = os.environ.get("SC_VOICE_DATA", "/home/ouvrier/travaux/webrtc-vocal/data")
VOICE_INBOX = os.path.join(VOICE_DATA, "inbox")
VOICE_OUTBOX = os.path.join(VOICE_DATA, "outbox")
SIRI_TOKEN = os.environ.get("SC_SIRI_TOKEN", "")
SIRI_TIMEOUT = float(os.environ.get("SC_SIRI_TIMEOUT", "90"))


def hermes_siri(texte):
    texte = (texte or "").strip()
    if not texte:
        return {"ok": False, "reply": "Je n'ai rien entendu."}
    sid = "siri-" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(int(time.time() * 1000) % 1000)
    rec = {"session_id": sid, "text": texte, "status": "pending", "source": "siri"}
    try:
        os.makedirs(VOICE_INBOX, exist_ok=True)
        _ecrire_atomique(os.path.join(VOICE_INBOX, sid + ".json"),
                         json.dumps(rec, ensure_ascii=False))
    except Exception as e:
        return {"ok": False, "reply": f"Souci d'envoi a Hermes ({e})."}
    out = os.path.join(VOICE_OUTBOX, sid + ".json")
    deadline = time.time() + SIRI_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(out):
            try:
                with open(out, encoding="utf-8") as f:
                    reply = (json.load(f).get("reply") or "").strip()
            except Exception:
                time.sleep(0.4)
                continue
            try:
                os.remove(out)
            except Exception:
                pass
            return {"ok": True, "reply": reply or "Hermes n'a rien renvoye."}
        time.sleep(0.4)
    return {"ok": False, "reply": "Hermes met trop de temps a repondre, reessaie."}


# --- La page (statique, mobile d'abord ; les donnees arrivent via /api/*) -----
PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<base href="__PREFIX__">
<title>Atelier - Salle de controle</title>
<style>
  /* =========================================================================
     SALLE DE CONTROLE — Refonte "Apple Home + Tab Bar"
     Esprit : beaucoup d'air, hierarchie forte, complexite cachee derriere des onglets.
     Un ACCUEIL epure (le verdict + les ouvriers + l'action), une BARRE D'ONGLETS en bas (iOS).
     Mobile-first, polices systeme, 0 ressource externe.
     Grammaire couleur STRICTE : bleu=ACTION a declencher, vert=ETAT sain, rouge=DANGER, ambre=ATTENTION/fige.
     Tous les IDs cibles par le JS sont PRESERVES a l'identique. Le <script> est recopie verbatim.
     ========================================================================= */

  /* ---------------------- TOKENS : mode SOMBRE (defaut) -------------------- */
  :root {
    color-scheme: dark light;

    /* Surfaces a 3 niveaux (profondeur douce, sans bruit) */
    --bg:        #0b0e14;
    --card:      #161b24;
    --raise:     #1e2530;
    --sunk:      #0a0d12;

    --txt:       #eef1f6;
    --txt-2:     #c2cad6;
    --mut:       #aab4c2;

    --line:      #2c3442;
    --line-2:    #3a4456;

    --acc:       #4c9bff;
    --acc-press: #2f7fe6;
    --acc-soft:  #15233a;
    --ok:        #46c25a;
    --ok-soft:   #102a17;
    --ok-txt:    #84e29a;
    --warn:      #f0a93b;
    --warn-soft: #2c2210;
    --warn-txt:  #ffce80;
    --ko:        #ff5d52;
    --ko-soft:   #2e1418;
    --ko-txt:    #ff9d96;

    /* Echelle typographique elargie (hero genereux facon Apple) */
    --t-hero:    40px;      /* verdict en grand */
    --t-display: 22px;      /* titre d'onglet */
    --t-title:   13px;      /* titre de section */
    --t-body:    17px;
    --t-legend:  13px;

    /* Rythme d'espacement plus genereux (de l'air) */
    --sp-1: 6px; --sp-2: 10px; --sp-3: 16px; --sp-4: 22px; --sp-5: 32px; --sp-6: 44px;

    /* Rayons doux et grands */
    --r-xl: 24px; --r-lg: 18px; --r-md: 13px; --r-sm: 10px; --r-pill: 999px;

    /* Elevation a 4 crans (profondeur, pas des bordures) */
    --e1: 0 1px 2px rgba(0,0,0,.40), 0 0 1px rgba(0,0,0,.30);
    --e2: 0 6px 18px -6px rgba(0,0,0,.55), 0 1px 2px rgba(0,0,0,.40);
    --e3: 0 12px 30px -10px rgba(0,0,0,.60), 0 2px 6px rgba(0,0,0,.40);
    --e4: 0 22px 48px -14px rgba(0,0,0,.66), 0 4px 12px rgba(0,0,0,.42);
    --sh-acc: 0 0 0 1px var(--acc), 0 12px 28px -10px rgba(76,155,255,.50);

    --focus: #8cc2ff;
    --tap: 44px;
    --wrap: 720px;
    --tabbar: 64px;          /* hauteur barre d'onglets */
    --ease: cubic-bezier(.2,.7,.3,1);
  }

  /* ---------------------- TOKENS : mode CLAIR (bascule manuelle, ou auto via JS) --------------- */
  :root[data-theme="light"] {
      --bg:        #eef1f5;
      --card:      #ffffff;
      --raise:     #f4f7fb;
      --sunk:      #eef2f7;

      --txt:       #11161f;
      --txt-2:     #2c3744;
      --mut:       #4a5765;

      --line:      #dce2ea;
      --line-2:    #c2ccd8;

      --acc:       #0a63d6;
      --acc-press: #074fb0;
      --acc-soft:  #e6f0fd;
      --ok:        #1f9d3a;
      --ok-soft:   #e2f6e8;
      --ok-txt:    #11702a;
      --warn:      #b5740a;
      --warn-soft: #fbefd8;
      --warn-txt:  #80510a;
      --ko:        #d5342a;
      --ko-soft:   #fde6e4;
      --ko-txt:    #9c1f18;

      --e1: 0 1px 2px rgba(16,24,40,.08);
      --e2: 0 6px 18px -8px rgba(16,24,40,.16), 0 1px 2px rgba(16,24,40,.07);
      --e3: 0 12px 30px -10px rgba(16,24,40,.20), 0 2px 6px rgba(16,24,40,.09);
      --e4: 0 22px 48px -16px rgba(16,24,40,.26), 0 4px 12px rgba(16,24,40,.11);
      --sh-acc: 0 0 0 1px var(--acc), 0 12px 28px -10px rgba(10,99,214,.32);
      --focus: #0a63d6;
  }

  /* Bouton de bascule de theme (dans le hero, a cote de l'heure) */
  .hero-top-right { display:flex; align-items:center; gap:10px; }
  .theme-btn { background:transparent; border:0; width:auto; min-width:38px; min-height:38px; margin:0;
               padding:6px; font-size:18px; line-height:1; cursor:pointer; color:var(--mut);
               border-radius:var(--r-pill); transition:background-color .15s var(--ease); }
  .theme-btn:hover { background:var(--raise); }
  .theme-btn:active { transform:scale(.92); }

  /* Ordres-types : bouton + sa petite croix de suppression, et le bouton memoriser */
  .quick .qwrap { display:inline-flex; align-items:stretch; }
  .quick .qwrap > button:first-child { border-top-right-radius:0; border-bottom-right-radius:0; }
  .quick .qdel { width:auto; min-width:34px; margin:0; padding:0 8px; border-radius:0 var(--r-sm) var(--r-sm) 0;
                 background:var(--sunk); color:var(--mut); border-left:1px solid var(--line); font-size:15px; }
  .quick .qdel:hover { background:var(--ko-soft); color:var(--ko-txt); }
  .quick .qadd { width:auto; margin:0; background:transparent; color:var(--mut);
                 border:1px dashed var(--line-2); font-weight:600; }
  .quick .qadd:hover { color:var(--acc); border-color:var(--acc); }

  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html { -webkit-text-size-adjust:100%; }

  body {
    margin:0; background:var(--bg); color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    font-size:var(--t-body); line-height:1.5; letter-spacing:-.01em;
    /* place pour la tab bar fixe + le safe-area iOS */
    padding-bottom:calc(var(--tabbar) + 28px + env(safe-area-inset-bottom,0));
    -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
  }

  :focus-visible {
    outline:3px solid var(--focus);
    outline-offset:2px;
    border-radius:8px;
  }
  :focus:not(:focus-visible) { outline:none; }

  /* ============================ HERO (toujours visible) ================== */
  .hero {
    padding:calc(22px + env(safe-area-inset-top,0)) 22px 26px;
    display:flex; flex-direction:column; gap:18px;
  }
  /* ligne haute : titre discret + heure (pulse) */
  .hero-top {
    display:flex; align-items:center; justify-content:space-between; gap:12px;
    min-height:32px;
  }
  .hero-top .marque {
    font-size:13px; font-weight:700; letter-spacing:.2px;
    color:var(--mut); text-transform:uppercase;
    display:inline-flex; align-items:center; gap:7px; min-width:0;
  }
  .hero-top .marque .ico { font-size:16px; }
  /* l'heure / connexion : pastille discrete (reutilise #pulse) */
  .pulse {
    font-size:12.5px; color:var(--mut); font-weight:600;
    white-space:nowrap; display:inline-flex; align-items:center; gap:7px;
    padding:6px 12px; border-radius:var(--r-pill);
    background:var(--raise); border:1px solid var(--line); min-height:32px;
  }
  .pulse::before {
    content:""; width:8px; height:8px; border-radius:50%;
    background:var(--ok); box-shadow:0 0 7px var(--ok); flex:0 0 auto;
    transition:background-color .25s var(--ease);
  }
  .pulse[data-offline="1"]::before { background:var(--ko); box-shadow:none; }

  /* LE VERDICT : grand point + gros texte d'etat (reutilise #health / #healthTxt) */
  .health {
    display:flex; align-items:center; gap:18px;
    padding:4px 2px; min-height:84px;
    transition:color .3s var(--ease);
  }
  .health .dot {
    width:24px; height:24px; flex:0 0 auto;
    transition:background-color .3s var(--ease), box-shadow .3s var(--ease);
  }
  .health #healthTxt {
    font-size:var(--t-hero); font-weight:800; letter-spacing:-.025em;
    line-height:1.04; color:var(--txt);
  }
  /* etat sain : point vert qui respire, texte calme */
  .health.ok .dot { background:var(--ok); box-shadow:0 0 16px var(--ok); animation:live 2.4s ease-in-out infinite; }
  .health.ok #healthTxt { color:var(--txt); }
  /* alerte : point + texte rouges (le verdict crie) */
  .health.ko .dot { background:var(--ko); box-shadow:0 0 16px var(--ko); animation:none; }
  .health.ko #healthTxt { color:var(--ko-txt); }

  /* ---- Conteneur des onglets ---- */
  .wrap {
    padding:0 18px; max-width:var(--wrap); margin:0 auto;
  }

  /* ---- Bandeau PAUSE : danger global, visible sur TOUS les onglets ---- */
  .pause-banner {
    background:var(--ko-soft); color:var(--ko-txt);
    border:1px solid var(--ko); border-left:5px solid var(--ko);
    text-align:center; padding:14px; font-weight:800; font-size:15px;
    letter-spacing:.3px; border-radius:var(--r-md); display:none;
    box-shadow:var(--e1); margin:0 18px 18px;
  }
  .pause-banner.show { display:block; animation:slideDown .28s var(--ease); }
  /* Pastille securite : action MORTELLE bloquee par le copilote (ordre recent). */
  .drapeau-banner { font-size:14px; line-height:1.5; }
  .drapeau-banner .db-x {
    display:inline-block; margin-left:10px; padding:5px 14px; min-height:0;
    width:auto; font-size:13px; font-weight:700; cursor:pointer;
    color:var(--ko-txt); background:transparent;
    border:1.5px solid var(--ko); border-radius:999px;
  }
  .drapeau-banner .db-x:hover { background:var(--ko); color:#fff; }

  /* ============================== ONGLETS ================================ */
  .tab { display:none; }
  .tab.actif { display:grid; gap:var(--sp-4); animation:tabIn .32s var(--ease); }
  /* titre discret en haut de chaque onglet secondaire (pas sur l'accueil) */
  .tab-titre {
    font-size:var(--t-display); font-weight:800; letter-spacing:-.02em;
    margin:2px 0 -2px;
  }

  /* ============================== CARTES ================================= */
  .card {
    background:var(--card); border:1px solid var(--line);
    border-radius:var(--r-lg); padding:var(--sp-4);
    box-shadow:var(--e2);
  }
  .card h2 {
    font-size:var(--t-title); margin:0 0 var(--sp-3);
    color:var(--txt-2); text-transform:uppercase;
    letter-spacing:.6px; font-weight:700;
    display:flex; align-items:center; gap:8px;
  }
  .card h2::before {
    content:""; width:4px; height:14px; border-radius:2px;
    background:var(--line-2); flex:0 0 auto;
  }
  .card p { color:var(--txt-2); font-size:var(--t-legend); margin:0 0 var(--sp-2); line-height:1.5; }

  /* --- Carte ACTION PRIMAIRE : "Donner un ordre" --- */
  .card--primary {
    background:linear-gradient(180deg, var(--acc-soft) 0%, var(--card) 80px);
    border-color:var(--acc); box-shadow:var(--e3);
    position:relative; border-radius:var(--r-xl); padding:var(--sp-5) var(--sp-4) var(--sp-4);
  }
  .card--primary h2 { color:var(--acc); font-size:var(--t-display); text-transform:none; letter-spacing:-.01em; }
  .card--primary h2::before { display:none; }

  /* --- Cartes attenuees (reglages rares) --- */
  .card--muted { background:var(--bg); border-style:dashed; box-shadow:none; }
  .card--muted h2 { color:var(--mut); }

  /* ============================ SERVICES (pills) ========================= */
  .svc { display:flex; flex-wrap:wrap; gap:var(--sp-2); }
  .pill {
    display:inline-flex; align-items:center; gap:8px;
    background:var(--sunk); border:1px solid var(--line);
    padding:0 14px; min-height:var(--tap); border-radius:var(--r-pill);
    font-size:14px; font-weight:600; color:var(--txt); box-shadow:var(--e1);
  }
  .dot {
    width:10px; height:10px; border-radius:50%; flex:0 0 auto;
    transition:background-color .3s var(--ease);
  }
  .dot.ok { background:var(--ok); box-shadow:0 0 7px var(--ok); }
  .dot.ko { background:var(--ko); box-shadow:0 0 7px var(--ko); }
  .dot.na { background:var(--mut); box-shadow:none; }

  /* ====================== OUVRIERS EN DIRECT (grandes cartes) ============ */
  /* Sur l'accueil, chaque ouvrier est une GRANDE carte aeree, separee. */
  .ouv-bloc {
    background:var(--card); border:1px solid var(--line);
    border-radius:var(--r-xl); padding:var(--sp-4); box-shadow:var(--e2);
  }
  .ouv-bloc > .ph {
    font-weight:800; font-size:16px; color:var(--txt);
    letter-spacing:-.01em;
    margin-bottom:var(--sp-3); display:flex; align-items:center; gap:9px;
  }
  .ouv-bloc > .ph .sub {
    font-size:12.5px; font-weight:600; color:var(--mut);
    text-transform:uppercase; letter-spacing:.4px; margin-left:auto;
  }
  .ouv {
    border:1px solid var(--line); border-left:3px solid var(--ok);
    border-radius:var(--r-md); padding:var(--sp-3); margin-bottom:var(--sp-2);
    background:var(--raise); box-shadow:var(--e1);
  }
  .ouv:last-child { margin-bottom:0; }
  .ouv .t {
    font-weight:700; font-size:15px;
    display:flex; justify-content:space-between; gap:10px; align-items:baseline;
    word-break:break-word;
  }
  .ouv .t > span:first-child { display:inline-flex; align-items:center; gap:7px; }
  .ouv .t > span:first-child::before {
    content:""; width:8px; height:8px; border-radius:50%; flex:0 0 auto;
    background:var(--ok); box-shadow:0 0 6px var(--ok);
    animation:live 1.8s ease-in-out infinite;
  }
  .ouv .meta {
    color:var(--txt-2); font-size:13px; font-weight:600;
    white-space:nowrap; flex:0 0 auto;
  }
  .ouv:has(.meta.fige) { border-left-color:var(--warn); }
  .ouv:has(.meta.fige) .t > span:first-child::before {
    background:var(--warn); box-shadow:0 0 6px var(--warn); animation:none;
  }
  .ouv pre {
    margin:var(--sp-2) 0 0; background:var(--bg);
    border:1px solid var(--line); border-radius:var(--r-sm);
    padding:var(--sp-3); font-size:13px; line-height:1.5;
    font-family:ui-monospace,SFMono-Regular,"SF Mono","Segoe UI Mono",Menlo,Consolas,monospace;
    color:var(--txt); max-height:240px; overflow:auto;
    white-space:pre-wrap; word-break:break-word;
    -webkit-overflow-scrolling:touch; overscroll-behavior:contain;
  }
  .ouv .kill {
    margin:var(--sp-3) 0 0; width:auto; min-height:var(--tap);
    padding:0 16px; font-size:14px; font-weight:700;
    background:transparent; color:var(--ko-txt);
    border:1.5px solid var(--ko); border-radius:var(--r-sm);
    transition:background-color .15s var(--ease), color .15s var(--ease);
  }
  .ouv .kill:hover { background:var(--ko-soft); }
  .ouv .kill:active { background:var(--ko); color:#fff; }

  .fige { color:var(--warn-txt); }

  /* Etats vides : calme (repos) */
  .vide {
    color:var(--mut); font-style:normal; font-size:14px;
    padding:10px 0; display:flex; align-items:center; gap:8px;
  }
  .vide::before { content:"\\1F634"; font-style:normal; opacity:.85; }

  /* ========================= DERNIERS RESULTATS ========================== */
  .res {
    border-top:1px solid var(--line); padding:var(--sp-3) 0;
    cursor:pointer; min-height:var(--tap);
    transition:background-color .15s var(--ease);
    border-radius:var(--r-sm);
    margin:0 calc(var(--sp-2) * -1); padding-left:var(--sp-2); padding-right:var(--sp-2);
  }
  .res:first-of-type { border-top:0; }
  .res:hover { background:var(--raise); }
  .res .t {
    font-weight:700; font-size:15px;
    display:flex; justify-content:space-between; gap:10px; word-break:break-word;
  }
  .res .a { color:var(--txt-2); font-size:13px; margin-top:4px; line-height:1.5; }
  .res .voir {
    color:var(--acc); font-size:13px; font-weight:700; margin-top:6px;
    display:inline-flex; align-items:center; gap:5px;
  }
  /* Ligne telemetrie : modele / effort / cout / duree / tours / tokens. Discrete,
     chiffres alignes (tabular-nums). Vide si l'ordre n'a pas de fiche .meta. */
  .metaline {
    margin-top:6px; font-size:11.5px; color:var(--mut);
    font-variant-numeric:tabular-nums; letter-spacing:.2px; line-height:1.4;
  }
  #modalMeta .metaline { margin:0 0 10px; font-size:12.5px; }

  /* =============================== SAISIE ================================ */
  textarea {
    width:100%; background:var(--sunk); color:var(--txt);
    border:1.5px solid var(--line-2); border-radius:var(--r-md);
    padding:14px; font-size:16px; /* >=16px : iOS ne zoome pas */
    font-family:inherit; line-height:1.5; resize:vertical; min-height:104px;
    transition:border-color .15s var(--ease);
  }
  textarea:focus { border-color:var(--acc); }
  textarea::placeholder { color:var(--mut); }

  select {
    background:var(--sunk); color:var(--txt);
    border:1.5px solid var(--line-2); border-radius:var(--r-sm);
    padding:0 36px 0 12px; min-height:var(--tap);
    font-size:15px; font-family:inherit; font-weight:600;
    cursor:pointer; appearance:none; -webkit-appearance:none;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'><path fill='%23aab4c2' d='M1 1l5 5 5-5'/></svg>");
    background-repeat:no-repeat; background-position:right 12px center;
    transition:border-color .15s var(--ease);
  }
  select:focus { border-color:var(--acc); }
  :root[data-theme="light"] select { background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'><path fill='%234a5765' d='M1 1l5 5 5-5'/></svg>"); }

  /* =============================== BOUTONS =============================== */
  button {
    border:0; border-radius:var(--r-md); padding:0 18px;
    min-height:var(--tap); font-size:15px; font-weight:700;
    color:#fff; background:var(--acc); cursor:pointer; width:100%;
    margin-top:var(--sp-3); line-height:1.2;
    box-shadow:var(--e1);
    transition:background-color .15s var(--ease), box-shadow .15s var(--ease),
               transform .06s var(--ease), opacity .15s var(--ease);
  }
  button:hover { background:var(--acc-press); box-shadow:var(--e2); }
  button:active { transform:translateY(1px); box-shadow:var(--e1); }
  button[disabled], button[aria-busy="true"] {
    opacity:.6; cursor:progress; transform:none;
  }
  button[aria-busy="true"]::after {
    content:""; width:15px; height:15px; margin-left:9px; vertical-align:-2px;
    display:inline-block; border:2px solid rgba(255,255,255,.45);
    border-top-color:#fff; border-radius:50%; animation:spin .7s linear infinite;
  }
  button.ghost { background:var(--raise); color:var(--txt); border:1px solid var(--line-2); box-shadow:none; }
  button.ghost:hover { background:var(--line); }

  button.danger {
    background:var(--ko); color:#fff;
    box-shadow:inset 0 0 0 1px rgba(0,0,0,.15), var(--e1);
  }
  button.danger:hover { background:var(--ko); filter:brightness(1.08); }

  /* .go = grand bouton d'action (ordre / faire repartir). ACTION => BLEU. */
  button.go {
    background:var(--acc); color:#fff; box-shadow:var(--sh-acc);
    font-size:17px; letter-spacing:.2px; min-height:54px; border-radius:var(--r-md);
    margin-top:var(--sp-4);
  }
  button.go:hover { background:var(--acc-press); }

  /* ============================== RANGEES =============================== */
  .row { display:flex; gap:var(--sp-3); flex-wrap:wrap; align-items:stretch; }
  .row > label {
    display:flex; flex-direction:column; align-items:flex-start; gap:6px;
    font-size:13px; color:var(--txt-2); font-weight:600; flex:1 1 140px; min-width:140px;
  }
  .row > label > select { width:100%; }
  .row > label:has(input[type="checkbox"]) {
    flex-direction:row; align-items:center; gap:10px;
    min-height:var(--tap); padding:8px 14px; flex:1 1 100%;
    background:var(--sunk); border:1px solid var(--line);
    border-radius:var(--r-md); cursor:pointer; color:var(--txt);
  }
  .row > label:has(input[type="checkbox"]:checked) {
    border-color:var(--acc); background:var(--acc-soft); color:var(--txt);
  }
  input[type="checkbox"] {
    width:24px; height:24px; flex:0 0 auto; accent-color:var(--acc);
    cursor:pointer; margin:0;
  }
  .hint { color:var(--mut); font-weight:500; font-size:12.5px; }

  /* reglages legers DANS la carte d'ordre : un cran plus discret */
  .reglages-ordre { margin-top:var(--sp-3); }
  .reglages-ordre summary {
    list-style:none; cursor:pointer; color:var(--mut);
    font-size:13.5px; font-weight:600; padding:6px 0;
    display:inline-flex; align-items:center; gap:7px; min-height:34px;
  }
  .reglages-ordre summary::-webkit-details-marker { display:none; }
  .reglages-ordre summary::before { content:"\\2699\\FE0F"; }
  .reglages-ordre[open] summary { color:var(--txt-2); }

  /* =========================== ORDRES RAPIDES =========================== */
  .quick { display:flex; flex-wrap:wrap; gap:var(--sp-2); margin-bottom:var(--sp-3); }
  .quick button {
    width:auto; margin:0; min-height:var(--tap); padding:0 16px;
    font-size:14px; font-weight:600; box-shadow:var(--e1);
    background:var(--raise); color:var(--acc); border:1px solid var(--line-2);
  }
  .quick button:hover { background:var(--line); border-color:var(--acc); color:var(--acc); }

  /* ============================= FLASH (legacy) ======================== */
  .flash {
    margin-top:var(--sp-3); padding:12px 14px; border-radius:var(--r-md);
    font-size:14px; font-weight:600; display:none; line-height:1.45;
  }
  .flash.show { display:block; animation:flashIn .25s var(--ease); }
  .flash.ok { background:var(--ok-soft); color:var(--ok-txt); border:1px solid var(--ok); }
  .flash.ko { background:var(--ko-soft); color:var(--ko-txt); border:1px solid var(--ko); }

  /* =============================== COMPTEURS =========================== */
  .count { display:inline-block; min-width:24px; }
  .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:var(--sp-3); text-align:center; }
  .grid3 .b {
    background:var(--card); border:1px solid var(--line);
    border-radius:var(--r-lg); padding:var(--sp-4) var(--sp-1); box-shadow:var(--e2);
  }
  .grid3 .n { font-size:32px; font-weight:800; line-height:1.05; font-variant-numeric:tabular-nums; }
  .grid3 .l {
    font-size:12px; color:var(--txt-2); margin-top:6px;
    text-transform:uppercase; letter-spacing:.4px; font-weight:600;
  }

  /* =============================== CERVEAUX ============================= */
  .cerveau {
    display:flex; align-items:center; gap:10px; padding:12px 0;
    border-top:1px solid var(--line); font-size:14px; flex-wrap:wrap; min-height:var(--tap);
  }
  .cerveau:first-child { border-top:0; }
  .cerveau .q { color:var(--txt-2); font-weight:600; }
  .cerveau b { font-weight:800; color:var(--txt); }
  .cerveau .sec { color:var(--mut); font-size:13px; }

  /* =============================== WATCHDOG ============================= */
  .wd-status { font-size:16px; font-weight:800; display:flex; align-items:center; gap:8px; }
  .wd-status.ok { color:var(--ok); }
  .wd-status.ko { color:var(--ko); }
  .wd-line {
    display:flex; align-items:center; gap:10px; padding:11px 0;
    border-top:1px solid var(--line); min-height:var(--tap);
  }
  .wd-line:first-child { border-top:0; }
  .wd-line .nom { font-weight:700; font-size:14px; }
  .wd-line .detail {
    color:var(--txt-2); font-size:13px; margin-left:auto;
    text-align:right; padding-left:10px; font-weight:600;
  }
  .wd-alertes { margin-top:var(--sp-3); display:grid; gap:var(--sp-1); }
  .wd-alerte {
    background:var(--ko-soft); color:var(--ko-txt);
    border:1px solid var(--ko); border-left:4px solid var(--ko);
    padding:10px 12px; border-radius:var(--r-sm); font-size:13px; font-weight:600;
  }
  .wd-quand { color:var(--mut); font-size:13px; margin-top:var(--sp-3); }

  /* =============================== MODALE =============================== */
  .modal {
    position:fixed; inset:0; background:rgba(0,0,0,.62);
    backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px);
    display:none; z-index:80; padding:16px;
  }
  .modal.show { display:flex; align-items:flex-start; justify-content:center; animation:fadeIn .2s var(--ease); }
  .modal .box {
    background:var(--card); border:1px solid var(--line-2);
    border-radius:var(--r-lg); padding:var(--sp-4);
    max-width:var(--wrap); width:100%; max-height:86vh;
    display:flex; flex-direction:column; margin-top:24px;
    box-shadow:var(--e4); position:relative;
    animation:modalIn .26s var(--ease);
  }
  .modal .box h3 {
    margin:0 0 var(--sp-3); font-size:16px; font-weight:700;
    word-break:break-word; padding-right:48px;
  }
  .modal .box .x-close {
    position:absolute; top:14px; right:14px; width:40px; height:40px;
    min-height:40px; margin:0; padding:0;
    border-radius:var(--r-sm); background:var(--raise); color:var(--txt);
    border:1px solid var(--line-2); font-size:20px; line-height:1; font-weight:700;
    display:flex; align-items:center; justify-content:center; box-shadow:none;
  }
  .modal .box .x-close:hover { background:var(--line); }
  .modal pre {
    background:var(--sunk); border:1px solid var(--line);
    border-radius:var(--r-sm); padding:var(--sp-3); font-size:13px;
    line-height:1.55; color:var(--txt);
    font-family:ui-monospace,SFMono-Regular,"SF Mono","Segoe UI Mono",Menlo,Consolas,monospace;
    overflow:auto; white-space:pre-wrap; word-break:break-word; flex:1;
    -webkit-overflow-scrolling:touch; overscroll-behavior:contain;
  }
  .modal .box .modal-actions { display:flex; gap:var(--sp-2); }
  .modal .box .modal-actions button { margin-top:var(--sp-3); flex:1; }

  /* ========================= TOAST GLOBAL ============================== */
  #toast {
    position:fixed; left:50%; transform:translateX(-50%) translateY(140%);
    bottom:calc(var(--tabbar) + 18px + env(safe-area-inset-bottom,0));
    z-index:90; max-width:560px; width:calc(100% - 32px);
    padding:14px 16px; border-radius:var(--r-md);
    font-size:14px; font-weight:700; line-height:1.4;
    box-shadow:var(--e4); opacity:0; pointer-events:none;
    transition:transform .3s var(--ease), opacity .3s var(--ease);
    display:flex; align-items:center; gap:10px;
  }
  #toast.show { transform:translateX(-50%) translateY(0); opacity:1; pointer-events:auto; }
  #toast.ok   { background:var(--ok-soft);  color:var(--ok-txt);  border:1px solid var(--ok); }
  #toast.ko   { background:var(--ko-soft);  color:var(--ko-txt);  border:1px solid var(--ko); }
  #toast.info { background:var(--acc-soft); color:var(--txt);     border:1px solid var(--acc); }

  /* ====================== CONFIRMATION MAISON ========================== */
  #confirm {
    position:fixed; inset:0; background:rgba(0,0,0,.62);
    backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px);
    display:none; z-index:100; align-items:center; justify-content:center; padding:18px;
  }
  #confirm.show { display:flex; animation:fadeIn .2s var(--ease); }
  #confirm .cbox {
    background:var(--card); border:1px solid var(--ko);
    border-radius:var(--r-lg); padding:var(--sp-4); max-width:420px; width:100%;
    box-shadow:var(--e4); animation:modalIn .24s var(--ease);
  }
  #confirm .cbox h4 { margin:0 0 8px; font-size:17px; font-weight:800; }
  #confirm .cbox p { color:var(--txt-2); font-size:14px; margin:0 0 var(--sp-4); line-height:1.5; }
  #confirm .cbox .crow { display:flex; gap:var(--sp-2); }
  #confirm .cbox button { margin-top:0; }
  #confirm .cbox .c-ok { background:var(--ko); }

  .sr-only {
    position:absolute; width:1px; height:1px; padding:0; margin:-1px;
    overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0;
  }

  /* ====================== BARRE D'ONGLETS (iOS, en bas) ================= */
  .tabbar {
    position:fixed; left:0; right:0; bottom:0; z-index:70;
    display:flex; justify-content:space-around; align-items:stretch;
    padding-bottom:env(safe-area-inset-bottom,0);
    background:color-mix(in srgb, var(--bg) 82%, transparent);
    -webkit-backdrop-filter:saturate(170%) blur(18px);
    backdrop-filter:saturate(170%) blur(18px);
    border-top:1px solid var(--line);
    box-shadow:0 -8px 24px -12px rgba(0,0,0,.45);
  }
  .tabbar .tabbtn {
    flex:1 1 0; width:auto; margin:0; padding:8px 4px;
    min-height:var(--tabbar);
    background:transparent; border:0; box-shadow:none; border-radius:0;
    color:var(--mut); font-weight:600; font-size:11px; letter-spacing:.1px;
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px;
    transition:color .2s var(--ease);
  }
  .tabbar .tabbtn:hover { background:transparent; color:var(--txt-2); }
  .tabbar .tabbtn .ico {
    font-size:23px; line-height:1; display:block;
    transition:transform .2s var(--ease);
  }
  .tabbar .tabbtn[aria-selected="true"] { color:var(--acc); }
  .tabbar .tabbtn[aria-selected="true"] .ico { transform:translateY(-1px) scale(1.06); }
  /* pastille d'alerte sur l'onglet Etat */
  .tabbar .tabbtn .pip {
    position:absolute; top:9px; margin-left:16px;
    width:8px; height:8px; border-radius:50%; background:var(--ko);
    box-shadow:0 0 6px var(--ko); display:none;
  }
  .tabbar .tabbtn.alerte .pip { display:block; }
  .tabbar .tabbtn { position:relative; }

  /* =============================== ANIMATIONS ========================== */
  @keyframes slideDown{ from { opacity:0; transform:translateY(-8px); } to { opacity:1; transform:none; } }
  @keyframes flashIn  { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }
  @keyframes fadeIn   { from { opacity:0; } to { opacity:1; } }
  @keyframes modalIn  { from { opacity:0; transform:translateY(10px) scale(.98); } to { opacity:1; transform:none; } }
  @keyframes tabIn    { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:none; } }
  @keyframes live     { 0%,100% { opacity:1; } 50% { opacity:.5; } }
  @keyframes spin     { to { transform:rotate(360deg); } }
  @keyframes heroIn   { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }

  /* Apparition douce du hero AU PREMIER RENDU seulement */
  body.boot .hero { animation:heroIn .42s var(--ease) both; }
  body.boot .tab.actif > * { animation:tabIn .42s var(--ease) both; }

  /* ====================== mouvement reduit ============================= */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration:.001ms !important; animation-iteration-count:1 !important;
      transition-duration:.001ms !important; scroll-behavior:auto !important;
    }
    .ouv .t > span:first-child::before { animation:none !important; }
    .health.ok .dot { animation:none !important; }
    .dot.ok, .dot.ko { box-shadow:none !important; }
    .pulse::before { box-shadow:none !important; }
    body.boot .hero, body.boot .tab.actif > * { animation:none !important; opacity:1 !important; transform:none !important; }
  }

  /* ====================== contraste eleve ============================= */
  @media (prefers-contrast: more) {
    :root { --txt-2:#d8dee7; --mut:#c4ccd6; --line:#46505d; --line-2:#5a6573; }
  }

  /* ===================== Tablette / desktop =========================== */
  @media (min-width:680px) {
    :root { --t-hero:48px; }
    .tab.actif { gap:var(--sp-5); }
    .row > label { flex:1 1 200px; }
    .tabbar { max-width:var(--wrap); left:50%; transform:translateX(-50%); border-radius:var(--r-lg) var(--r-lg) 0 0; }
  }
</style>
</head>
<body class="boot">

<div class="hero">
  <div class="hero-top">
    <span class="marque"><span class="ico">&#129520;</span> Atelier</span>
    <span class="hero-top-right">
      <button class="theme-btn" id="themeBtn" type="button" onclick="basculerTheme()" aria-label="Changer le theme (clair / sombre / auto)" title="Clair / sombre / auto">&#127769;</button>
      <span class="pulse" id="pulse">connexion...</span>
    </span>
  </div>
  <div class="health ok" id="health" role="status" aria-live="polite" aria-atomic="true"><span class="dot ok"></span><span id="healthTxt">Verification...</span></div>
</div>

<div class="pause-banner" id="pauseBanner" role="alert">&#9208; ATELIER EN PAUSE</div>
<div class="pause-banner drapeau-banner" id="drapeauBanner" role="alert"></div>

<div class="wrap">

  <!-- ============================ ONGLET ACCUEIL ======================= -->
  <section class="tab actif" id="tab-accueil" role="tabpanel" aria-label="Accueil">

    <div class="ouv-bloc">
      <div class="ph">&#9729;&#65039; Ouvrier VPS <span class="sub">serveur</span></div>
      <div id="ouvVps"><div class="vide">Au repos.</div></div>
    </div>

    <div class="ouv-bloc">
      <div class="ph">&#128421;&#65039; Ouvrier PC <span class="sub">VOTRE_USER</span></div>
      <div id="ouvPc"><div class="vide">Au repos.</div></div>
    </div>

    <div class="grid3">
      <div class="b"><div class="n count" id="cIn">0</div><div class="l">a faire</div></div>
      <div class="b"><div class="n count" id="cOut">0</div><div class="l">resultats</div></div>
      <div class="b"><div class="n count" id="cDone">0</div><div class="l">termines</div></div>
    </div>

    <div class="card card--primary">
      <h2>Donner un ordre</h2>
      <div class="quick" id="quick"></div>
      <textarea id="ordre" placeholder="Ex: verifie l'espace disque et resume-moi l'etat du serveur."></textarea>
      <div class="row" style="margin-top:12px">
        <label><input type="checkbox" id="pc"> sur le PC (@pc)</label>
        <label><input type="checkbox" id="ultracode"> &#129302; ultracode <span class="hint">(multi-agents, + puissant, + couteux)</span></label>
      </div>
      <details class="reglages-ordre">
        <summary>Reglages de cet ordre (effort, modele)</summary>
        <div class="row" style="margin-top:12px">
          <label>Effort
            <select id="effort">
              <option value="defaut">par defaut</option>
              <option value="low">faible</option>
              <option value="medium">moyen</option>
              <option value="high">eleve</option>
              <option value="xhigh">tres eleve</option>
              <option value="max">max</option>
            </select>
          </label>
          <label>Modele
            <select id="modele">
              <option value="defaut">par defaut</option>
              <option value="claude-opus-4-8">Opus 4.8</option>
              <option value="claude-sonnet-4-6">Sonnet 4.6</option>
              <option value="claude-fable-5">Fable 5</option>
              <option value="claude-haiku-4-5">Haiku 4.5</option>
            </select>
          </label>
        </div>
      </details>
      <button class="go" onclick="envoyer('ordre')">Lancer l'ordre &rarr;</button>
      <div class="flash" role="status" aria-live="polite" id="flashOrdre"></div>
    </div>

  </section>

  <!-- ============================ ONGLET JOURNAL ======================= -->
  <section class="tab" id="tab-journal" role="tabpanel" aria-label="Journal" hidden>
    <div class="tab-titre">Journal</div>
    <div class="card">
      <h2>Derniers resultats <span style="color:var(--mut);font-weight:400;font-size:12px">(tape pour lire en entier)</span></h2>
      <div id="resultats"><div class="vide">Rien pour l'instant.</div></div>
    </div>
    <div class="card">
      <h2>Historique cherchable</h2>
      <input type="search" id="histRech" placeholder="Chercher dans tes ordres passes..." oninput="chargerHistorique()"
             style="width:100%;background:var(--sunk);color:var(--txt);border:1.5px solid var(--line-2);border-radius:var(--r-sm);padding:0 12px;min-height:44px;font-size:16px;font-family:inherit;margin-bottom:10px">
      <div id="historique"><div class="vide">Tes ordres passes apparaitront ici.</div></div>
    </div>
  </section>

  <!-- ============================ ONGLET ETAT ========================== -->
  <section class="tab" id="tab-etat" role="tabpanel" aria-label="Etat" hidden>
    <div class="tab-titre">Etat technique</div>
    <div class="card">
      <h2>Etat des services</h2>
      <div class="svc" id="svc"></div>
    </div>
    <div class="card">
      <h2>Surveillance (watchdog)</h2>
      <div id="watchdog"><div class="vide">Chargement de l'etat de surveillance...</div></div>
    </div>
    <div class="card">
      <h2>Cerveaux (modeles en service)</h2>
      <div id="cerveaux"><div class="vide">Lecture des configurations...</div></div>
    </div>
  </section>

  <!-- ============================ ONGLET REGLAGES ====================== -->
  <section class="tab" id="tab-reglages" role="tabpanel" aria-label="Reglages" hidden>
    <div class="tab-titre">Reglages</div>

    <div class="card card--muted">
      <h2>Effort par defaut (par ouvrier)</h2>
      <p style="color:var(--mut);font-size:13px;margin:0 0 8px">
        Niveau applique quand tu ne choisis rien dans l'ordre. &laquo; par defaut &raquo; = comportement historique (tres eleve).</p>
      <div class="row">
        <label>&#9729;&#65039; VPS
          <select id="effVps" onchange="reglerEffort('vps')">
            <option value="defaut">par defaut</option><option value="low">faible</option>
            <option value="medium">moyen</option><option value="high">eleve</option>
            <option value="xhigh">tres eleve</option><option value="max">max</option>
          </select></label>
        <label>&#128421;&#65039; PC
          <select id="effPc" onchange="reglerEffort('pc')">
            <option value="defaut">par defaut</option><option value="low">faible</option>
            <option value="medium">moyen</option><option value="high">eleve</option>
            <option value="xhigh">tres eleve</option><option value="max">max</option>
          </select></label>
      </div>
      <div class="flash" role="status" aria-live="polite" id="flashEffort"></div>
    </div>

    <div class="card card--muted">
      <h2>Modele par defaut (par ouvrier)</h2>
      <p style="color:var(--mut);font-size:13px;margin:0 0 8px">
        Modele utilise quand tu ne choisis rien dans l'ordre. &laquo; par defaut &raquo; = le modele de la
        config de l'ouvrier (Opus 4.8). &#9888; les efforts &laquo; tres eleve &raquo; et &laquo; max &raquo;
        n'existent que sur Opus : avec un autre modele, ils sont ramenes a &laquo; eleve &raquo;.</p>
      <div class="row">
        <label>&#9729;&#65039; VPS
          <select id="mdlVps" onchange="reglerModele('vps')">
            <option value="defaut">par defaut (Opus 4.8)</option>
            <option value="claude-opus-4-8">Opus 4.8</option>
            <option value="claude-sonnet-4-6">Sonnet 4.6</option>
            <option value="claude-fable-5">Fable 5</option>
            <option value="claude-haiku-4-5">Haiku 4.5</option>
          </select></label>
        <label>&#128421;&#65039; PC
          <select id="mdlPc" onchange="reglerModele('pc')">
            <option value="defaut">par defaut (Opus 4.8)</option>
            <option value="claude-opus-4-8">Opus 4.8</option>
            <option value="claude-sonnet-4-6">Sonnet 4.6</option>
            <option value="claude-fable-5">Fable 5</option>
            <option value="claude-haiku-4-5">Haiku 4.5</option>
          </select></label>
      </div>
      <div class="flash" role="status" aria-live="polite" id="flashModele"></div>
    </div>

    <div class="card card--muted">
      <h2>Ultracode par defaut (par ouvrier)</h2>
      <p style="color:var(--mut);font-size:13px;margin:0 0 8px">
        Si actif, &#9888; <b>CHAQUE</b> ordre de cet ouvrier passe en mode multi-agents (&#129302; ultracode) :
        nettement plus puissant mais plus <b>couteux</b>. Laisse eteint pour un usage normal.</p>
      <div class="row">
        <label>&#9729;&#65039; VPS <input type="checkbox" id="ultraVps" onchange="reglerUltracode('vps')"></label>
        <label>&#128421;&#65039; PC <input type="checkbox" id="ultraPc" onchange="reglerUltracode('pc')"></label>
      </div>
      <div class="flash" role="status" aria-live="polite" id="flashUltra"></div>
    </div>

    <div class="card card--muted">
      <h2>Notifications Telegram</h2>
      <p style="color:var(--mut);font-size:13px;margin:0 0 8px">
        Si actif, tu recois un message Telegram &#128276; chaque fois qu'un ordre se
        <b>termine</b> (et une alerte si une action dangereuse a ete bloquee).
        Pratique pour lancer un ordre puis fermer la salle.</p>
      <div class="row">
        <label>&#128276; M'envoyer les notifications <input type="checkbox" id="notif" onchange="reglerNotif()"></label>
        <button class="ghost" type="button" style="width:auto;margin:0;padding:8px 14px" onclick="testerNotif()">Envoyer un test</button>
      </div>
      <div class="flash" role="status" aria-live="polite" id="flashNotif"></div>
    </div>

    <div class="card card--muted">
      <h2>Consigne permanente (canal patron)</h2>
      <p style="color:var(--mut);font-size:13px;margin:0 0 8px">
        Cette consigne est rappelee a CHAQUE ouvrier, avant chaque mission. Laisse vide pour l'effacer.</p>
      <textarea id="consigne" placeholder="Ex: reponds toujours en francais simple, et previens avant toute action irreversible."></textarea>
      <button onclick="envoyer('consigne')">Enregistrer la consigne</button>
      <div class="flash" role="status" aria-live="polite" id="flashConsigne"></div>
    </div>

    <div class="card">
      <h2>Urgence</h2>
      <button class="danger" id="btnPause" onclick="basculer()">Mettre l'atelier en pause</button>
      <div class="flash" role="status" aria-live="polite" id="flashPause"></div>
    </div>

  </section>

</div>

<!-- ====================== BARRE D'ONGLETS (fixee en bas) ================ -->
<nav class="tabbar" role="tablist" aria-label="Navigation">
  <button class="tabbtn" type="button" role="tab" aria-selected="true" id="nav-accueil" onclick="activer('accueil')"><span class="ico">&#127968;</span>Accueil</button>
  <button class="tabbtn" type="button" role="tab" aria-selected="false" id="nav-journal" onclick="activer('journal')"><span class="ico">&#128220;</span>Journal</button>
  <button class="tabbtn" type="button" role="tab" aria-selected="false" id="nav-etat" onclick="activer('etat')"><span class="pip"></span><span class="ico">&#128202;</span>Etat</button>
  <button class="tabbtn" type="button" role="tab" aria-selected="false" id="nav-reglages" onclick="activer('reglages')"><span class="ico">&#9881;&#65039;</span>Reglages</button>
</nav>

<div class="modal" id="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitre" onclick="if(event.target.id==='modal')fermerModal()">
  <div class="box">
    <button class="x-close" type="button" onclick="fermerModal()" aria-label="Fermer">&times;</button>
    <h3 id="modalTitre">Resultat</h3>
    <div id="modalMeta"></div>
    <pre id="modalContenu">Chargement...</pre>
    <div class="modal-actions">
      <button class="ghost" type="button" onclick="copierResultat()">Copier</button>
      <button class="ghost" type="button" onclick="fermerModal()">Fermer</button>
    </div>
  </div>
</div>

<script>
// ===== GESTION DES ONGLETS (ajout) — ne touche a aucune fonction metier =====
// Montre l'onglet demande, cache les autres, met l'onglet actif en valeur.
// 'accueil' par defaut. N'a AUCUN effet sur le polling/fetch ni les IDs cibles.
const ONGLETS = ["accueil", "journal", "etat", "reglages"];
function activer(tab){
  if (ONGLETS.indexOf(tab) === -1) tab = "accueil";
  ONGLETS.forEach(function(t){
    var sec = document.getElementById("tab-" + t);
    var btn = document.getElementById("nav-" + t);
    var on  = (t === tab);
    if (sec){ sec.classList.toggle("actif", on); sec.hidden = !on; }
    if (btn){ btn.setAttribute("aria-selected", on ? "true" : "false"); }
  });
  // remonter en haut quand on change d'onglet (confort telephone)
  try { window.scrollTo({top:0, behavior:"auto"}); } catch(e) { window.scrollTo(0,0); }
}

let ORDRES_TYPES = [];   // tes ordres-types reutilisables (charges du serveur, editables)
let enPause = false;

// --- THEME clair / sombre / auto : bascule manuelle (sinon suit le systeme) ---
function _appliquerTheme(mode){
  var sys = (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
  var eff = (mode === "auto") ? sys : mode;
  document.documentElement.setAttribute("data-theme", eff);
  var b = document.getElementById("themeBtn");
  if (b) b.textContent = (mode === "auto") ? String.fromCodePoint(0x1F310)
                       : (eff === "light" ? String.fromCodePoint(0x2600) : String.fromCodePoint(0x1F319));
}
function basculerTheme(){
  var cur = "auto";
  try { cur = localStorage.getItem("theme") || "auto"; } catch(e){}
  var next = (cur === "auto") ? "light" : (cur === "light" ? "dark" : "auto");
  try { localStorage.setItem("theme", next); } catch(e){}
  _appliquerTheme(next);
}
try { _appliquerTheme(localStorage.getItem("theme") || "auto"); } catch(e){ _appliquerTheme("auto"); }

function esc(s){ return (s||"").replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

// --- TELEMETRIE : transforme la fiche .meta (cout/duree/tokens) en une petite
//     ligne grise sous le resultat. Champ absent (null) = on ne l'affiche pas
//     (jamais de valeur inventee ; voir format-live cote ouvrier).
const _MODNOM = {"claude-opus-4-8":"Opus 4.8","claude-opus-4-7":"Opus 4.7","claude-opus-4-6":"Opus 4.6","claude-sonnet-4-6":"Sonnet 4.6","claude-fable-5":"Fable 5","claude-haiku-4-5":"Haiku 4.5"};
function joliModele(id){ return _MODNOM[id] || id; }
function fmtTok(n){
  if (n == null) return null;
  if (n >= 1000){ const k = n/1000; return (k >= 10 ? Math.round(k).toString() : k.toFixed(1).replace(".", ",")) + "k"; }
  return "" + n;
}
function fmtMeta(m){
  if (!m) return "";
  const p = [];
  if (m.modele) p.push(esc(joliModele(m.modele)));
  if (m.effort) p.push(esc(m.effort));
  if (m.cout_usd != null) p.push("$" + m.cout_usd.toFixed(m.cout_usd < 0.1 ? 3 : 2));
  if (m.duree_ms != null){ const s = Math.round(m.duree_ms/1000); p.push(s < 60 ? s + " s" : Math.floor(s/60) + " min " + (s%60) + " s"); }
  if (m.tours != null) p.push(m.tours + " tours");
  if (m.tokens_in != null && m.tokens_out != null) p.push(fmtTok(m.tokens_in) + "&#8594;" + fmtTok(m.tokens_out) + " tok");
  return p.length ? `<div class="metaline">${p.join(" &#183; ")}</div>` : "";
}

function rendreOuvriers(el, liste){
  if (!liste || liste.length === 0){ el.innerHTML = '<div class="vide">Au repos.</div>'; return; }
  el.innerHTML = liste.map(o =>
    `<div class="ouv"><div class="t"><span>${esc(o.tache)}</span>
     <span class="meta ${o.fige?'fige':''}">${o.fige?'&#9888; fige ':''}${esc(o.depuis)}</span></div>
     <pre>${esc(o.direct)||'(demarrage...)'}</pre>
     <button class="kill" onclick="couper('${o.lieu}','${esc(o.tache)}')">&#9209; Couper cet ouvrier</button></div>`).join("");
}

async function rafraichir(){
  try {
    const r = await fetch("api/state", {cache:"no-store"});
    const d = await r.json();
    document.getElementById("pulse").textContent = "a jour " + d.horodatage;

    const h = document.getElementById("health");
    h.className = "health " + (d.sante.ok ? "ok" : "ko");
    document.getElementById("healthTxt").textContent = d.sante.ok
      ? "Tout va bien" : (d.sante.ko + " service(s) en alerte");

    document.getElementById("svc").innerHTML = d.services.map(s =>
      `<span class="pill"><span class="dot ${s.ok?'ok':'ko'}"></span>${esc(s.nom)}</span>`).join("");

    rendreOuvriers(document.getElementById("ouvVps"), d.ouvriers_vps);
    rendreOuvriers(document.getElementById("ouvPc"), d.ouvriers_pc);

    document.getElementById("cIn").textContent = d.file.a_faire;
    document.getElementById("cOut").textContent = d.file.resultats;
    document.getElementById("cDone").textContent = d.file.termines;

    const res = document.getElementById("resultats");
    res.innerHTML = d.resultats.length ? d.resultats.map(x =>
      `<div class="res" onclick="voirResultat('${esc(x.tache)}')"><div class="t"><span>${esc(x.tache)}</span><span class="a">${esc(x.quand)}</span></div>
       <div class="a">${esc(x.apercu)}</div>${fmtMeta(x.meta)}<div class="voir">&#128065; lire en entier</div></div>`).join("")
      : '<div class="vide">Rien pour l\\'instant.</div>';

    if (document.activeElement.id !== "consigne")
      document.getElementById("consigne").value = d.consigne || "";
    if (d.effort){
      if (document.activeElement.id !== "effVps") document.getElementById("effVps").value = d.effort.vps;
      if (document.activeElement.id !== "effPc")  document.getElementById("effPc").value  = d.effort.pc;
    }
    if (d.modele){
      if (document.activeElement.id !== "mdlVps") document.getElementById("mdlVps").value = d.modele.vps;
      if (document.activeElement.id !== "mdlPc")  document.getElementById("mdlPc").value  = d.modele.pc;
    }
    if (d.ultracode){
      if (document.activeElement.id !== "ultraVps") document.getElementById("ultraVps").checked = d.ultracode.vps;
      if (document.activeElement.id !== "ultraPc")  document.getElementById("ultraPc").checked  = d.ultracode.pc;
    }
    if (typeof d.notif === "boolean" && document.activeElement.id !== "notif")
      document.getElementById("notif").checked = d.notif;

    enPause = d.en_pause;
    document.getElementById("pauseBanner").classList.toggle("show", enPause);
    const b = document.getElementById("btnPause");
    b.textContent = enPause ? "Faire repartir l'atelier" : "Mettre l'atelier en pause";
    b.className = enPause ? "go" : "danger";

    // Pastille securite : action MORTELLE bloquee par le copilote sur un ordre
    // recent. On affiche tant que Mehdi n'a pas dit "Vu" pour CE drapeau precis
    // (signature = la tache) ; un nouveau drapeau different reaffiche le bandeau.
    const dr = d.drapeaux || {mortel:false};
    const db = document.getElementById("drapeauBanner");
    if (dr.mortel && localStorage.getItem("drapeauVu") !== dr.sig) {
      db._sig = dr.sig;
      db.innerHTML = "&#9888; Action DANGEREUSE bloquee par le copilote"
        + (dr.raison ? " : " + esc(dr.raison) : "")
        + ' <span style="opacity:.8;font-weight:600">(' + esc(dr.tache) + ", " + esc(dr.quand) + ")</span>"
        + ' <button class="db-x" type="button" onclick="masquerDrapeau()">Vu</button>';
      db.classList.add("show");
    } else {
      db.classList.remove("show");
    }
  } catch(e) {
    document.getElementById("pulse").textContent = "hors-ligne";
  }
}

async function rafraichirAux(){
  // Le lent : cerveaux + watchdog (toutes les 30 s).
  try {
    const r = await fetch("api/cerveaux", {cache:"no-store"});
    const c = await r.json();
    document.getElementById("cerveaux").innerHTML =
      `<div class="cerveau"><span class="q">Hermes (le chef)</span>&nbsp;<b>${esc(c.hermes.principal)}</b>`
      + `<span class="sec">secours : ${esc(c.hermes.secours)}</span></div>`
      + `<div class="cerveau"><span class="q">Ouvrier VPS</span>&nbsp;<b>${esc(c.ouvrier_vps)}</b></div>`
      + `<div class="cerveau"><span class="q">Ouvrier PC</span>&nbsp;<b>${esc(c.ouvrier_pc)}</b></div>`;
  } catch(e) {}
  try {
    const r = await fetch("api/watchdog", {cache:"no-store"});
    const d = await r.json();
    const el = document.getElementById("watchdog");
    if (!d || !d.present) {
      el.innerHTML = '<div class="vide">Pas encore de donnees du watchdog (premiere verification a venir).</div>';
      return;
    }
    const statut = d.global_ok
      ? '<div class="wd-status ok">&#128994; Surveillance OK</div>'
      : '<div class="wd-status ko">&#128308; Alerte</div>';
    const lignes = (d.checks||[]).map(c => {
      const cls = c.ok===true ? 'ok' : (c.ok===false ? 'ko' : 'na');
      return `<div class="wd-line"><span class="dot ${cls}"></span>`
           + `<span class="nom">${esc(c.nom)}</span>`
           + `<span class="detail">${esc(c.detail||'')}</span></div>`;
    }).join("");
    const alertes = (d.alertes && d.alertes.length)
      ? '<div class="wd-alertes">' + d.alertes.map(a=>`<div class="wd-alerte">${esc(a)}</div>`).join("") + '</div>'
      : "";
    let quand = d.horodatage ? esc(d.horodatage) : "?";
    if (d.age) quand += " (" + esc(d.age) + ")";
    if (d.perime) quand += ' <span class="fige">&#9888; donnees anciennes</span>';
    el.innerHTML = statut
      + '<div style="margin-top:8px">' + (lignes || '<div class="vide">Aucun controle.</div>') + '</div>'
      + alertes
      + '<div class="wd-quand">Derniere verification : ' + quand + '</div>';
  } catch(e) { /* on garde l'affichage precedent en cas de pepin reseau */ }
}

function flash(id, ok, msg){
  const f = document.getElementById(id);
  f.textContent = msg; f.className = "flash show " + (ok?"ok":"ko");
  setTimeout(()=>f.classList.remove("show"), 4000);
}

async function envoyer(type){
  let url, body, flashId;
  if (type === "ordre") {
    let t = document.getElementById("ordre").value.trim();
    if (!t) { flash("flashOrdre", false, "Ecris d'abord un ordre."); return; }
    // ultracode = simple mot-cle place dans le prompt (active le mode multi-agents).
    if (document.getElementById("ultracode").checked) t = "ultracode " + t;
    const mdl = document.getElementById("modele").value;
    if (mdl !== "defaut") t = "@model=" + mdl + " " + t;
    const eff = document.getElementById("effort").value;
    if (eff !== "defaut") t = "@effort=" + eff + " " + t;
    if (document.getElementById("pc").checked)  t = "@pc " + t;
    url = "api/ordre"; body = t; flashId = "flashOrdre";
  } else {
    url = "api/consigne"; body = document.getElementById("consigne").value; flashId = "flashConsigne";
  }
  try {
    const r = await fetch(url, {method:"POST", body});
    const d = await r.json();
    flash(flashId, d.ok, d.message);
    if (type === "ordre" && d.ok) document.getElementById("ordre").value = "";
    rafraichir();
  } catch(e) { flash(flashId, false, "Echec reseau."); }
}

async function reglerEffort(lieu){
  const niveau = document.getElementById(lieu === "vps" ? "effVps" : "effPc").value;
  try {
    const r = await fetch("api/effort", {method:"POST", body: lieu + "=" + niveau});
    const d = await r.json();
    flash("flashEffort", d.ok, d.message);
  } catch(e) { flash("flashEffort", false, "Echec reseau."); }
}

async function reglerModele(lieu){
  const mid = document.getElementById(lieu === "vps" ? "mdlVps" : "mdlPc").value;
  try {
    const r = await fetch("api/modele", {method:"POST", body: lieu + "=" + mid});
    const d = await r.json();
    flash("flashModele", d.ok, d.message);
  } catch(e) { flash("flashModele", false, "Echec reseau."); }
}

async function reglerUltracode(lieu){
  const on = document.getElementById(lieu === "vps" ? "ultraVps" : "ultraPc").checked;
  try {
    const r = await fetch("api/ultracode", {method:"POST", body: lieu + "=" + (on ? "on" : "off")});
    const d = await r.json();
    flash("flashUltra", d.ok, d.message);
  } catch(e) { flash("flashUltra", false, "Echec reseau."); }
}

async function reglerNotif(){
  const on = document.getElementById("notif").checked;
  try {
    const r = await fetch("api/notif", {method:"POST", body: on ? "on" : "off"});
    const d = await r.json();
    flash("flashNotif", d.ok, d.message);
  } catch(e) { flash("flashNotif", false, "Echec reseau."); }
}
async function testerNotif(){
  flash("flashNotif", true, "Envoi du test...");
  try {
    const r = await fetch("api/notif", {method:"POST", body: "test"});
    const d = await r.json();
    flash("flashNotif", d.ok, d.message);
  } catch(e) { flash("flashNotif", false, "Echec reseau."); }
}

async function couper(lieu, tache){
  if (!confirm("Couper l'ouvrier " + lieu + " (tache " + tache + ") ?")) return;
  try {
    const r = await fetch("api/kill", {method:"POST", body: lieu + "|" + tache});
    const d = await r.json();
    flash("flashPause", d.ok, d.message);
    rafraichir();
  } catch(e) { flash("flashPause", false, "Echec reseau."); }
}

async function voirResultat(tache){
  document.getElementById("modalTitre").textContent = tache;
  document.getElementById("modalMeta").innerHTML = "";
  document.getElementById("modalContenu").textContent = "Chargement...";
  document.getElementById("modal").classList.add("show");
  try {
    const r = await fetch("api/result?tache=" + encodeURIComponent(tache), {cache:"no-store"});
    const d = await r.json();
    document.getElementById("modalMeta").innerHTML = d.ok ? fmtMeta(d.meta) : "";
    document.getElementById("modalContenu").textContent = d.ok ? (d.contenu || "(vide)") : (d.message || "Erreur");
  } catch(e) { document.getElementById("modalContenu").textContent = "Echec reseau."; }
}
function fermerModal(){ document.getElementById("modal").classList.remove("show"); }

// "Vu" : on memorise la signature du drapeau acquitte (localStorage) -> il ne
// reapparait plus, mais un NOUVEAU drapeau (autre tache) reaffichera le bandeau.
function masquerDrapeau(){
  const db = document.getElementById("drapeauBanner");
  if (db && db._sig) localStorage.setItem("drapeauVu", db._sig);
  if (db) db.classList.remove("show");
}

async function basculer(){
  try {
    const r = await fetch("api/stop", {method:"POST", body: enPause ? "off" : "on"});
    const d = await r.json();
    flash("flashPause", d.ok, d.message);
    rafraichir();
  } catch(e) { flash("flashPause", false, "Echec reseau."); }
}

// --- Ordres-types : TES boutons reutilisables (charges du serveur, editables) ---
async function chargerOrdresTypes(){
  try { const r = await fetch("api/ordres-types", {cache:"no-store"}); ORDRES_TYPES = await r.json(); }
  catch(e) { ORDRES_TYPES = []; }
  const el = document.getElementById("quick");
  el.innerHTML = ORDRES_TYPES.map((o,i)=>
    `<span class="qwrap"><button onclick="document.getElementById('ordre').value=ORDRES_TYPES[${i}].texte">${esc(o.titre)}</button>`
    + `<button class="qdel" title="Supprimer ce bouton" onclick="supprimerOrdreTypeIdx(${i})">&times;</button></span>`).join("")
    + `<button class="qadd" onclick="memoriserOrdreType()">+ memoriser</button>`;
}
async function memoriserOrdreType(){
  const texte = document.getElementById("ordre").value.trim();
  if (!texte) { flash("flashOrdre", false, "Ecris d'abord un ordre, puis memorise-le."); return; }
  const titre = (prompt("Nom de ton bouton ?") || "").trim();
  if (!titre) return;
  try {
    const r = await fetch("api/ordres-types", {method:"POST",
      body: "action=add&titre=" + encodeURIComponent(titre) + "&texte=" + encodeURIComponent(texte)});
    const d = await r.json(); flash("flashOrdre", d.ok, d.message); if (d.ok) chargerOrdresTypes();
  } catch(e) { flash("flashOrdre", false, "Echec reseau."); }
}
async function supprimerOrdreTypeIdx(i){
  const o = ORDRES_TYPES[i]; if (!o) return;
  if (!confirm('Supprimer le bouton "' + o.titre + '" ?')) return;
  try {
    const r = await fetch("api/ordres-types", {method:"POST",
      body: "action=del&titre=" + encodeURIComponent(o.titre)});
    const d = await r.json(); flash("flashOrdre", d.ok, d.message); if (d.ok) chargerOrdresTypes();
  } catch(e) { flash("flashOrdre", false, "Echec reseau."); }
}
chargerOrdresTypes();

// --- Historique cherchable + Refaire (onglet Journal) ---
async function chargerHistorique(){
  const rech = (document.getElementById("histRech") && document.getElementById("histRech").value || "").trim();
  const el = document.getElementById("historique");
  try {
    const r = await fetch("api/historique?recherche=" + encodeURIComponent(rech), {cache:"no-store"});
    const liste = await r.json();
    el.innerHTML = liste.length ? liste.map(h =>
      `<div class="res"><div class="t"><span>${esc(h.tache)}</span><span class="a">${esc(h.quand)}</span></div>`
      + `<div class="a">${esc(h.ordre)}</div>`
      + (h.apercu ? `<div class="a" style="opacity:.75">&#8594; ${esc(h.apercu)}</div>` : "")
      + fmtMeta(h.meta)
      + `<div style="display:flex;gap:8px;margin-top:8px">`
      + (h.a_resultat ? `<button class="ghost" style="width:auto;margin:0;padding:7px 12px;font-size:13px" onclick="voirResultat('${esc(h.tache)}')">&#128065; lire</button>` : "")
      + `<button class="ghost" style="width:auto;margin:0;padding:7px 12px;font-size:13px" onclick="refaireOrdre('${esc(h.tache)}')">&#8635; Refaire</button>`
      + `</div></div>`).join("")
      : '<div class="vide">Aucun ordre trouve.</div>';
  } catch(e) { el.innerHTML = '<div class="vide">Erreur de chargement.</div>'; }
}
async function refaireOrdre(tache){
  if (!confirm("Relancer cet ordre a l'identique ?")) return;
  try {
    const r = await fetch("api/refaire", {method:"POST", body: tache});
    const d = await r.json();
    flash("flashOrdre", d.ok, d.message);
    if (d.ok) { activer("accueil"); rafraichir(); }
  } catch(e) { flash("flashOrdre", false, "Echec reseau."); }
}
chargerHistorique();

// --- Polling INTELLIGENT : on coupe quand l'ecran/onglet est cache (batterie) -
let tFast = null, tSlow = null;
function demarrer(){
  if (tFast) return;
  rafraichir(); rafraichirAux();
  tFast = setInterval(rafraichir, 3000);
  tSlow = setInterval(rafraichirAux, 30000);
}
function arreter(){ clearInterval(tFast); clearInterval(tSlow); tFast = tSlow = null; }
document.addEventListener("visibilitychange", ()=> document.hidden ? arreter() : demarrer());
demarrer();

// ===== MICRO-INTERACTIONS (redesign Apple x Microsoft) =====
// ====================================================================
// MICRO-INTERACTIONS SURES — n'alterent NI fetch, NI polling, NI les
// fonctions metier (rafraichir, rafraichirAux, envoyer, basculer, couper...).
// A coller a la FIN du <script> existant, apres le demarrage. Tout est
// tolerant (try/catch) et se desactive proprement si un element manque.
// ====================================================================

// 1) APPARITION AU PREMIER RENDU SEULEMENT : retirer .boot apres la 1re peinture.
requestAnimationFrame(function(){ requestAnimationFrame(function(){
  document.body.classList.remove('boot');
}); });

// 2) TOAST GLOBAL au-dessus du pouce (aria-live) — on ENROBE flash() sans la
//    remplacer. flash() continue de gerer les #flashXXX (compat). On affiche EN
//    PLUS un toast central, visible meme si le #flash cible est hors-ecran.
(function(){
  var t = document.getElementById('toast');
  if (!t || typeof window.flash !== 'function') return;
  var _flash = window.flash, timer = null;
  window.flash = function(id, ok, msg){
    try { _flash(id, ok, msg); } catch(e){}            // comportement d'origine intact
    try {
      t.textContent = (ok ? '✅ ' : '⚠️ ') + (msg || '');
      t.className = 'show ' + (ok ? 'ok' : 'ko');
      clearTimeout(timer);
      timer = setTimeout(function(){ t.classList.remove('show'); }, 4500);
    } catch(e){}
  };
})();

// 3) PULSE hors-ligne — bascule data-offline selon le texte ecrit par rafraichir().
//    Observation PASSIVE du #pulse, aucune modif du fetch.
(function(){
  var p = document.getElementById('pulse');
  if (!p || !('MutationObserver' in window)) return;
  var sync = function(){ p.setAttribute('data-offline', /hors-ligne/i.test(p.textContent) ? '1' : '0'); };
  new MutationObserver(sync).observe(p, {childList:true, characterData:true, subtree:true});
  sync();
})();

// 4) ETAT "EN COURS" sur les boutons d'action (anti double-tap, reseau lent).
//    On habille envoyer/basculer pour passer le bouton declencheur en aria-busy
//    le temps de l'appel. La logique fetch reste celle d'origine.
(function(){
  function busy(btn){
    if(!btn) return function(){};
    var old = btn.textContent;
    btn.disabled = true; btn.setAttribute('aria-busy','true'); btn.dataset._lib = old;
    return function(){ btn.disabled=false; btn.removeAttribute('aria-busy');
      if (btn.dataset._lib!=null) btn.textContent = btn.dataset._lib; };
  }
  if (typeof window.envoyer === 'function'){
    var _env = window.envoyer;
    window.envoyer = async function(type){
      var btn = type === 'ordre'
        ? document.querySelector('button.go')
        : (document.querySelector('#consigne') ? document.querySelector('#consigne').closest('.card').querySelector('button') : null);
      var done = busy(btn);
      if (btn && type==='ordre') btn.textContent = 'Envoi en cours…';
      try { return await _env.call(this, type); } finally { setTimeout(done, 250); }
    };
  }
  if (typeof window.basculer === 'function'){
    var _bas = window.basculer;
    window.basculer = async function(){
      var done = busy(document.getElementById('btnPause'));
      try { return await _bas.call(this); } finally { setTimeout(done, 250); }
    };
  }
})();

// 5) CONFIRMATION MAISON pour les actions dangereuses (remplace confirm()).
//    On intercepte couper(lieu,tache) : on ouvre #confirm et on n'execute la
//    coupure (logique d'origine) qu'apres clic "Confirmer". Si #confirm absent,
//    on retombe sur le confirm() natif (aucune regression).
(function(){
  var box = document.getElementById('confirm');
  if (!box || typeof window.couper !== 'function') return;
  var oui = document.getElementById('confirmOui');
  var non = document.getElementById('confirmNon');
  var txt = document.getElementById('confirmTexte');
  var _couper = window.couper, lastFocus = null;
  function close(){ box.classList.remove('show'); if (lastFocus && lastFocus.focus) lastFocus.focus(); }
  function open(lieu, tache){
    lastFocus = document.activeElement;
    txt.textContent = 'Couper l’ouvrier ' + lieu + ' (tache ' + tache + ') ? Le travail en cours sera interrompu.';
    box.classList.add('show'); oui.focus();
    oui.onclick = function(){ close(); _couper.call(window, lieu, tache, true); };
    non.onclick = close;
  }
  box.addEventListener('click', function(e){ if(e.target===box) close(); });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape' && box.classList.contains('show')) close(); });
  // 3e argument "confirme" => on bypass le confirm() interne d'origine.
  window.couper = function(lieu, tache, confirme){
    if (confirme){
      var real = window.confirm; window.confirm = function(){ return true; };
      try { return _couper.call(window, lieu, tache); } finally { window.confirm = real; }
    }
    open(lieu, tache);
  };
})();

// 6) MODALE PRO : Echap pour fermer, focus rendu, bouton Copier.
(function(){
  var modal = document.getElementById('modal');
  if (!modal) return;
  var lastFocus = null;
  document.addEventListener('click', function(ev){
    if (ev.target.closest && ev.target.closest('.res')) lastFocus = ev.target.closest('.res');
  }, true);
  var _fermer = window.fermerModal;
  if (typeof _fermer === 'function'){
    window.fermerModal = function(){ try { _fermer(); } catch(e){} if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch(e){} } };
  }
  document.addEventListener('keydown', function(e){
    if (e.key==='Escape' && modal.classList.contains('show') && typeof window.fermerModal==='function') window.fermerModal();
  });
  window.copierResultat = function(){
    var pre = document.getElementById('modalContenu');
    var t = pre ? pre.textContent : '';
    try { navigator.clipboard.writeText(t); if (typeof flash==='function') flash('flashOrdre', true, 'Resultat copie.'); }
    catch(e){ if (typeof flash==='function') flash('flashOrdre', false, 'Copie impossible.'); }
  };
})();

// 7) PASTILLE D'ALERTE sur l'onglet "Etat" (ajout) : observation PASSIVE du
//    verdict #health (deja peint par rafraichir). Aucune modif du fetch :
//    quand le verdict passe en "ko", on allume une pastille rouge sur l'onglet
//    Etat pour signaler "va voir le tableau technique", sans changer d'onglet.
(function(){
  var h = document.getElementById('health');
  var nav = document.getElementById('nav-etat');
  if (!h || !nav || !('MutationObserver' in window)) return;
  var sync = function(){ nav.classList.toggle('alerte', /\\bko\\b/.test(h.className)); };
  new MutationObserver(sync).observe(h, {attributes:true, attributeFilter:['class']});
  sync();
})();
</script>
<div id="toast" role="status" aria-live="assertive"></div>
<div id="confirm" role="dialog" aria-modal="true"><div class="cbox"><h4 id="confirmTitre">Confirmer</h4><p id="confirmTexte"></p><div class="crow"><button class="ghost" type="button" id="confirmNon">Annuler</button><button class="c-ok" type="button" id="confirmOui">Confirmer</button></div></div></div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # silence : pas de bruit dans le journal

    def _envoyer(self, code, contenu, ctype="application/json; charset=utf-8"):
        data = contenu.encode("utf-8") if isinstance(contenu, str) else contenu
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _corps(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(n).decode("utf-8", "replace") if n else ""
        except Exception:
            return ""

    def do_GET(self):
        chemin, _, q = self.path.partition("?")
        p = chemin.rstrip("/")
        if p in ("", "/index.html"):
            return self._envoyer(200, PAGE.replace("__PREFIX__", PREFIX),
                                 "text/html; charset=utf-8")
        if p == "/api/state":
            return self._envoyer(200, json.dumps(etat()))
        if p == "/api/watchdog":
            return self._envoyer(200, json.dumps(lire_watchdog()))
        if p == "/api/cerveaux":
            return self._envoyer(200, json.dumps(cerveaux()))
        if p == "/api/ordres-types":
            return self._envoyer(200, json.dumps(ordres_types_lire()))
        if p == "/api/historique":
            from urllib.parse import parse_qs
            rech = (parse_qs(q).get("recherche") or [""])[0]
            return self._envoyer(200, json.dumps(historique_lire(rech)))
        if p == "/api/result":
            from urllib.parse import parse_qs
            tache = (parse_qs(q).get("tache") or [""])[0]
            return self._envoyer(200, json.dumps(lire_resultat(tache)))
        return self._envoyer(404, json.dumps({"ok": False, "message": "inconnu"}))

    def do_POST(self):
        p = self.path.split("?", 1)[0].rstrip("/")
        corps = self._corps()
        try:
            if p == "/api/ordre":
                return self._envoyer(200, json.dumps(donner_ordre(corps)))
            if p == "/api/consigne":
                return self._envoyer(200, json.dumps(poser_consigne(corps)))
            if p == "/api/stop":
                return self._envoyer(200, json.dumps(basculer_pause(corps.strip() == "on")))
            if p == "/api/effort":
                lieu, _, niveau = corps.strip().partition("=")
                return self._envoyer(200, json.dumps(effort_defaut_ecrire(lieu.strip(), niveau.strip())))
            if p == "/api/modele":
                lieu, _, mid = corps.strip().partition("=")
                return self._envoyer(200, json.dumps(modele_defaut_ecrire(lieu.strip(), mid.strip())))
            if p == "/api/ultracode":
                lieu, _, val = corps.strip().partition("=")
                return self._envoyer(200, json.dumps(ultracode_defaut_ecrire(lieu.strip(), val.strip() == "on")))
            if p == "/api/notif":
                c = corps.strip()
                if c == "test":   # envoi immediat pour verifier le canal (token jamais expose)
                    ok = _notif_envoyer("\U0001F514 Test salle de controle : si tu lis ceci, les notifications marchent.")
                    return self._envoyer(200, json.dumps({"ok": ok,
                        "message": "Message test envoye sur Telegram." if ok
                        else "Echec de l'envoi (verifie le bot Telegram)."}))
                return self._envoyer(200, json.dumps(notif_ecrire(c == "on")))
            if p == "/api/ordres-types":
                from urllib.parse import parse_qs
                q = parse_qs(corps, keep_blank_values=True)
                return self._envoyer(200, json.dumps(ordres_types_ecrire(
                    (q.get("action") or [""])[0], (q.get("titre") or [""])[0], (q.get("texte") or [""])[0])))
            if p == "/api/refaire":
                return self._envoyer(200, json.dumps(refaire_ordre(corps.strip())))
            if p == "/api/kill":
                lieu, _, tache = corps.strip().partition("|")
                return self._envoyer(200, json.dumps(tuer_ouvrier(lieu.strip(), tache.strip())))
            if p == "/siri":
                # Mains-libres iPhone : jeton dans l'en-tete X-Siri-Token. FAIL-CLOSED :
                # si AUCUN jeton n'est configure, on refuse (pas d'acces ouvert).
                if not SIRI_TOKEN or self.headers.get("X-Siri-Token", "") != SIRI_TOKEN:
                    return self._envoyer(401, json.dumps({"ok": False, "reply": "non autorise"}))
                texte = corps
                if corps[:1] in ("{", "[") or "json" in self.headers.get("Content-Type", "").lower():
                    try:
                        j = json.loads(corps)
                        if isinstance(j, dict):
                            texte = j.get("text") or j.get("q") or j.get("input") or corps
                    except Exception:
                        pass
                return self._envoyer(200, json.dumps(hermes_siri(texte)))
        except Exception as e:
            return self._envoyer(200, json.dumps({"ok": False, "message": f"Erreur: {e}"}))
        return self._envoyer(404, json.dumps({"ok": False, "message": "inconnu"}))


# === NOTIFICATIONS TELEGRAM (fonction #4) ====================================
#  Fil de fond : quand un ordre se termine (nouveau .out), on previent Mehdi sur
#  Telegram -- SEULEMENT si l'interrupteur est actif (OFF par defaut). Le token et
#  le chat_id viennent du .env d'Hermes et ne sortent JAMAIS du serveur (ni log,
#  ni reponse HTTP). Au 1er demarrage on photographie l'existant sans notifier.
def notif_lire():
    """Interrupteur de notifications actif ? (fichier present = ON)."""
    return os.path.exists(NOTIF_FLAG)


def notif_ecrire(on):
    try:
        if on and not os.path.exists(NOTIF_FLAG):
            _ecrire_atomique(NOTIF_FLAG, "on\n", uid=HERMES_UID)
        elif not on and os.path.exists(NOTIF_FLAG):
            os.remove(NOTIF_FLAG)
        return {"ok": True, "message": "Notifications " + ("activees." if on else "coupees.")}
    except Exception as e:
        return {"ok": False, "message": f"Echec : {e}"}


def _notif_config():
    """Lit (token, chat_id) dans le .env d'Hermes. NE LES RENVOIE JAMAIS au client
    ni dans un log : ils restent dans le serveur. (None, None) si introuvables."""
    tok, chat = None, None
    try:
        with open(NOTIF_ENV, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("TELEGRAM_BOT_TOKEN="):
                    tok = ln.split("=", 1)[1].strip().strip('"').strip("'")
                elif ln.startswith("TELEGRAM_ALLOWED_USERS="):
                    v = ln.split("=", 1)[1].strip().strip('"').strip("'")
                    chat = v.split(",")[0].strip() if v else None
    except Exception:
        pass
    return tok, chat


def _notif_envoyer(texte):
    """Envoie un message Telegram a Mehdi. Le token ne sort pas d'ici (aucun log).
    Renvoie True/False sans reveler de detail sensible."""
    tok, chat = _notif_config()
    if not tok or not chat:
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": texte}).encode("utf-8")
        req = urllib.request.Request("https://api.telegram.org/bot" + tok + "/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def _notif_state_lire():
    """Ensemble des .out deja vus (anti-doublon). None = tout 1er demarrage."""
    try:
        with open(NOTIF_STATE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("seen"), list):
            return set(d["seen"])
    except Exception:
        pass
    return None


def _notif_state_ecrire(seen):
    try:
        _ecrire_atomique(NOTIF_STATE,
                         json.dumps({"seen": list(seen)[-500:]}, ensure_ascii=False),
                         uid=HERMES_UID)
    except Exception:
        pass


def _notif_boucle():
    """Detecte les ordres qui VIENNENT de finir (nouveau .out) et previent Mehdi.
    Tolerant a tout : ne plante jamais le serveur."""
    seen = _notif_state_lire()
    if seen is None:                      # 1er demarrage : photo de l'existant, sans notifier
        try:
            seen = set(os.path.basename(o)[:-4] for o in glob.glob(os.path.join(OUT, "*.out")))
        except Exception:
            seen = set()
        _notif_state_ecrire(seen)
    while True:
        try:
            time.sleep(30)
            try:
                actifs = set(os.path.basename(o)[:-4] for o in glob.glob(os.path.join(OUT, "*.out")))
            except Exception:
                actifs = set()
            nouveaux = sorted(t for t in actifs if t not in seen)
            on = notif_lire()
            for t in nouveaux:
                if on:
                    chemin = os.path.join(OUT, t + ".out")
                    apercu = _apercu(chemin, 200)
                    mortel = False
                    try:
                        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
                            mortel = "[MORTEL]" in f.read(40000)
                    except Exception:
                        pass
                    tete = ("⚠️ Atelier : ordre termine AVEC alerte MORTELLE -- "
                            if mortel else "✅ Atelier : ordre termine -- ")
                    _notif_envoyer(tete + t + "\n" + apercu)
                seen.add(t)
            if nouveaux:
                _notif_state_ecrire(seen)
        except Exception:
            try:
                time.sleep(30)
            except Exception:
                pass


def main():
    threading.Thread(target=_notif_boucle, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[salle-controle] en ecoute sur http://{HOST}:{PORT}  (prefixe public {PREFIX})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
