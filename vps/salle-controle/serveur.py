#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SALLE DE CONTROLE de l'atelier Hermes  --  le 3e canal (apres Telegram et la voix).

Une page web SIMPLE, pensee pour le telephone, qui permet a Mehdi (artisan, pas
developpeur), meme en deplacement, de :
  - VOIR ses ouvriers en direct (qui travaille, sur quoi, ou ca en est) ;
  - VOIR les derniers resultats et l'etat des services ;
  - DONNER un ordre directement a un ouvrier (sans passer par Hermes) ;
  - POSER une consigne permanente (le "canal patron", relaye a chaque mission) ;
  - METTRE EN PAUSE l'atelier en un clic (kill-switch STOP) en cas de pepin.

Conception :
  - Python 3 STANDARD LIBRARY uniquement (zero dependance a installer).
  - Ecoute en local (127.0.0.1:8787) : il n'est JAMAIS expose directement.
    C'est Caddy (HTTPS + mot de passe) qui le publie sous https://<domaine>/atelier/.
  - Tourne en root (systemd) : il lit la file (root) et ecrit les consignes /
    depose les ordres de maniere ATOMIQUE (fichier temporaire puis renommage),
    pour ne jamais entrer en collision avec l'aiguilleur qui tourne en parallele.

Echelle artisan : la barriere = HTTPS + mot de passe Caddy + ecoute localhost.
Pas de sur-ingenierie ; robuste et lisible avant tout.
"""
import json
import os
import time
import glob
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# --- Emplacements (surchargeable par variables d'env pour les tests) ---------
QUEUE = os.environ.get("SC_QUEUE", "/root/.hermes/claude-queue")
CONSIGNES = os.environ.get("SC_CONSIGNES", "/root/.hermes/consignes-patron.txt")
HERMES_UID = int(os.environ.get("SC_HERMES_UID", "10000"))
HOST = os.environ.get("SC_HOST", "127.0.0.1")
PORT = int(os.environ.get("SC_PORT", "8787"))
PREFIX = os.environ.get("SC_PREFIX", "/atelier/")  # chemin public (pour <base>)

IN = os.path.join(QUEUE, "in")
OUT = os.path.join(QUEUE, "out")
DONE = os.path.join(QUEUE, "done")
STOP = os.path.join(QUEUE, "STOP")

SERVICES = [
    ("aiguilleur", "Routeur (aiguilleur)"),
    ("webrtc-vocal", "Voix : ecoute"),
    ("webrtc-reply", "Voix : reponse"),
    ("coturn", "Voix : relais reseau"),
    ("caddy", "Site web (HTTPS)"),
    ("garde-nuit.timer", "Garde de nuit"),
]
CONTENEURS = [("hermes", "Hermes (le chef)"), ("speaches", "Transcription voix")]


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


def etat():
    """Construit l'etat complet de l'atelier (toujours tolerant aux erreurs)."""
    maintenant = time.time()

    services = []
    for unit, libelle in SERVICES:
        actif = _run(["systemctl", "is-active", unit]) == "active"
        services.append({"nom": libelle, "ok": actif})
    for nom, libelle in CONTENEURS:
        run = _run(["docker", "inspect", "-f", "{{.State.Running}}", nom]) == "true"
        services.append({"nom": libelle, "ok": run})

    # Ouvriers EN COURS = les fichiers .live presents dans out/
    ouvriers = []
    for live in sorted(glob.glob(os.path.join(OUT, "*.live"))):
        nom = os.path.basename(live)[:-5]
        try:
            age = maintenant - os.path.getmtime(live)
        except Exception:
            age = 0
        ouvriers.append({
            "tache": nom,
            "depuis": _age(age),
            "fige": age > 200,  # plus de ~3 min sans nouvelle ligne = a surveiller
            "direct": _lire_fin(live),
        })

    # Derniers resultats livres
    resultats = []
    try:
        outs = sorted(glob.glob(os.path.join(OUT, "*.out")),
                      key=os.path.getmtime, reverse=True)[:8]
    except Exception:
        outs = []
    for o in outs:
        try:
            age = maintenant - os.path.getmtime(o)
        except Exception:
            age = 0
        resultats.append({
            "tache": os.path.basename(o)[:-4],
            "quand": _age(age),
            "apercu": _apercu(o),
        })

    consigne = ""
    try:
        if os.path.exists(CONSIGNES):
            with open(CONSIGNES, "r", encoding="utf-8", errors="replace") as f:
                consigne = f.read().strip()
    except Exception:
        consigne = ""

    return {
        "horodatage": time.strftime("%H:%M:%S"),
        "services": services,
        "ouvriers": ouvriers,
        "file": {"a_faire": _compter(IN), "termines": _compter(DONE),
                 "resultats": _compter(OUT)},
        "resultats": resultats,
        "consigne": consigne,
        "en_pause": os.path.exists(STOP),
    }


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


# --- La page (statique, mobile d'abord ; les donnees arrivent via /api/state) -
PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="__PREFIX__">
<title>Atelier - Salle de controle</title>
<style>
  :root { --bg:#0e1116; --card:#1a1f27; --line:#2a313c; --txt:#e6e9ef; --mut:#9aa4b2;
          --ok:#3fb950; --ko:#f85149; --acc:#388bfd; --warn:#d29922; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--txt); font-family:-apple-system,
         BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; font-size:16px; padding-bottom:40px; }
  header { position:sticky; top:0; background:#0e1116ee; backdrop-filter:blur(6px);
           padding:14px 16px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; justify-content:space-between; }
  header h1 { font-size:17px; margin:0; font-weight:600; }
  .pulse { font-size:12px; color:var(--mut); }
  .wrap { padding:16px; max-width:760px; margin:0 auto; display:grid; gap:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; }
  .card h2 { font-size:14px; margin:0 0 12px; color:var(--mut); text-transform:uppercase;
             letter-spacing:.5px; font-weight:600; }
  .svc { display:flex; flex-wrap:wrap; gap:8px; }
  .pill { display:flex; align-items:center; gap:7px; background:#0e1116; border:1px solid var(--line);
          padding:7px 11px; border-radius:999px; font-size:13px; }
  .dot { width:9px; height:9px; border-radius:50%; flex:0 0 auto; }
  .dot.ok { background:var(--ok); box-shadow:0 0 6px var(--ok); }
  .dot.ko { background:var(--ko); box-shadow:0 0 6px var(--ko); }
  .ouv { border:1px solid var(--line); border-radius:12px; padding:12px; margin-bottom:10px; background:#0e1116; }
  .ouv .t { font-weight:600; display:flex; justify-content:space-between; gap:8px; align-items:baseline; }
  .ouv .meta { color:var(--mut); font-size:12px; }
  .ouv pre { margin:10px 0 0; background:#070a0e; border-radius:8px; padding:10px; font-size:12px;
             color:#c9d1d9; max-height:160px; overflow:auto; white-space:pre-wrap; word-break:break-word; }
  .fige { color:var(--warn); }
  .vide { color:var(--mut); font-style:italic; }
  .res { border-top:1px solid var(--line); padding:10px 0; }
  .res:first-of-type { border-top:0; }
  .res .t { font-weight:600; font-size:14px; display:flex; justify-content:space-between; gap:8px; }
  .res .a { color:var(--mut); font-size:13px; margin-top:3px; }
  textarea { width:100%; background:#0e1116; color:var(--txt); border:1px solid var(--line);
             border-radius:10px; padding:12px; font-size:16px; font-family:inherit; resize:vertical; min-height:80px; }
  button { border:0; border-radius:10px; padding:13px 16px; font-size:15px; font-weight:600;
           color:#fff; background:var(--acc); cursor:pointer; width:100%; margin-top:10px; }
  button.ghost { background:#21262d; }
  button.danger { background:var(--ko); }
  button.go { background:var(--ok); }
  .row { display:flex; gap:10px; }
  .row label { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--mut); }
  .flash { margin-top:10px; padding:10px 12px; border-radius:10px; font-size:14px; display:none; }
  .flash.show { display:block; }
  .flash.ok { background:#13351b; color:#7ee787; }
  .flash.ko { background:#3a1518; color:#ffa198; }
  .count { display:inline-block; min-width:22px; }
  .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; text-align:center; }
  .grid3 .b { background:#0e1116; border:1px solid var(--line); border-radius:10px; padding:12px 6px; }
  .grid3 .n { font-size:22px; font-weight:700; }
  .grid3 .l { font-size:11px; color:var(--mut); margin-top:2px; }
  .pause-banner { background:#3a1518; color:#ffa198; text-align:center; padding:10px; font-weight:600;
                  border-radius:10px; display:none; }
  .pause-banner.show { display:block; }
</style>
</head>
<body>
<header>
  <h1>&#129520; Atelier &mdash; salle de controle</h1>
  <span class="pulse" id="pulse">connexion...</span>
</header>
<div class="wrap">

  <div class="pause-banner" id="pauseBanner">&#9208; ATELIER EN PAUSE</div>

  <div class="card">
    <h2>Etat des services</h2>
    <div class="svc" id="svc"></div>
  </div>

  <div class="card">
    <h2>Ouvriers en cours</h2>
    <div id="ouvriers"><div class="vide">Aucun ouvrier ne travaille en ce moment.</div></div>
    <div class="grid3" style="margin-top:12px">
      <div class="b"><div class="n count" id="cIn">0</div><div class="l">a faire</div></div>
      <div class="b"><div class="n count" id="cOut">0</div><div class="l">resultats</div></div>
      <div class="b"><div class="n count" id="cDone">0</div><div class="l">termines</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Donner un ordre a un ouvrier</h2>
    <textarea id="ordre" placeholder="Ex: verifie l'espace disque et resume-moi l'etat du serveur."></textarea>
    <div class="row" style="margin-top:8px">
      <label><input type="checkbox" id="max"> effort maximum (@max)</label>
      <label><input type="checkbox" id="pc"> sur le PC (@pc)</label>
    </div>
    <button class="go" onclick="envoyer('ordre')">Lancer l'ordre &rarr;</button>
    <div class="flash" id="flashOrdre"></div>
  </div>

  <div class="card">
    <h2>Consigne permanente (canal patron)</h2>
    <p style="color:var(--mut);font-size:13px;margin:0 0 8px">
      Cette consigne est rappelee a CHAQUE ouvrier, avant chaque mission. Laisse vide pour l'effacer.</p>
    <textarea id="consigne" placeholder="Ex: reponds toujours en francais simple, et previens avant toute action irreversible."></textarea>
    <button onclick="envoyer('consigne')">Enregistrer la consigne</button>
    <div class="flash" id="flashConsigne"></div>
  </div>

  <div class="card">
    <h2>Derniers resultats</h2>
    <div id="resultats"><div class="vide">Rien pour l'instant.</div></div>
  </div>

  <div class="card">
    <h2>Urgence</h2>
    <button class="danger" id="btnPause" onclick="basculer()">Mettre l'atelier en pause</button>
    <div class="flash" id="flashPause"></div>
  </div>

</div>
<script>
let enPause = false;
function esc(s){ return (s||"").replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

async function rafraichir(){
  try {
    const r = await fetch("api/state", {cache:"no-store"});
    const d = await r.json();
    document.getElementById("pulse").textContent = "a jour " + d.horodatage;

    document.getElementById("svc").innerHTML = d.services.map(s =>
      `<span class="pill"><span class="dot ${s.ok?'ok':'ko'}"></span>${esc(s.nom)}</span>`).join("");

    const ouv = document.getElementById("ouvriers");
    if (d.ouvriers.length === 0) {
      ouv.innerHTML = '<div class="vide">Aucun ouvrier ne travaille en ce moment.</div>';
    } else {
      ouv.innerHTML = d.ouvriers.map(o =>
        `<div class="ouv"><div class="t"><span>${esc(o.tache)}</span>
         <span class="meta ${o.fige?'fige':''}">${o.fige?'&#9888; fige ':''}${esc(o.depuis)}</span></div>
         <pre>${esc(o.direct)||'(demarrage...)'}</pre></div>`).join("");
    }
    document.getElementById("cIn").textContent = d.file.a_faire;
    document.getElementById("cOut").textContent = d.file.resultats;
    document.getElementById("cDone").textContent = d.file.termines;

    const res = document.getElementById("resultats");
    res.innerHTML = d.resultats.length ? d.resultats.map(x =>
      `<div class="res"><div class="t"><span>${esc(x.tache)}</span><span class="a">${esc(x.quand)}</span></div>
       <div class="a">${esc(x.apercu)}</div></div>`).join("")
      : '<div class="vide">Rien pour l\\'instant.</div>';

    if (document.activeElement.id !== "consigne")
      document.getElementById("consigne").value = d.consigne || "";

    enPause = d.en_pause;
    document.getElementById("pauseBanner").classList.toggle("show", enPause);
    const b = document.getElementById("btnPause");
    b.textContent = enPause ? "Faire repartir l'atelier" : "Mettre l'atelier en pause";
    b.className = enPause ? "go" : "danger";
  } catch(e) {
    document.getElementById("pulse").textContent = "hors-ligne";
  }
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
    if (document.getElementById("max").checked) t = "@max " + t;
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

async function basculer(){
  try {
    const r = await fetch("api/stop", {method:"POST", body: enPause ? "off" : "on"});
    const d = await r.json();
    flash("flashPause", d.ok, d.message);
    rafraichir();
  } catch(e) { flash("flashPause", false, "Echec reseau."); }
}

rafraichir();
setInterval(rafraichir, 3000);
</script>
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
        p = self.path.split("?", 1)[0].rstrip("/")
        if p in ("", "/index.html"):
            return self._envoyer(200, PAGE.replace("__PREFIX__", PREFIX),
                                 "text/html; charset=utf-8")
        if p == "/api/state":
            return self._envoyer(200, json.dumps(etat()))
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
            if p == "/siri":
                # Mains-libres iPhone : jeton dans l'en-tete X-Siri-Token. La question
                # arrive soit en texte brut, soit en JSON {"text": "..."} (selon le Raccourci).
                if SIRI_TOKEN and self.headers.get("X-Siri-Token", "") != SIRI_TOKEN:
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


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[salle-controle] en ecoute sur http://{HOST}:{PORT}  (prefixe public {PREFIX})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
