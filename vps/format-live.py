#!/usr/bin/env python3
# ============================================================================
#  FORMAT-LIVE  --  transforme le flux brut de l'ouvrier (claude --output-format
#  stream-json) en un journal LISIBLE et EN DIRECT pour Hermes.
#  Lu sur stdin (une ligne JSON par evenement). Ecrit, AU FUR ET A MESURE :
#    - le fichier .live  ($1) : l'avancement action par action (Hermes le lit
#      pendant que l'ouvrier travaille -> il voit le travail comme Mehdi voit CC).
#    - le fichier resultat ($2) : la REPONSE finale de l'ouvrier (pour le .out).
#  $3 = uid d'Hermes (pour que le .live lui appartienne et soit lisible).
# ============================================================================
import sys, json, os

live_path   = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdout"
result_path = sys.argv[2] if len(sys.argv) > 2 else "/dev/null"
hermes_uid  = int(sys.argv[3]) if len(sys.argv) > 3 else 10000
# --- TELEMETRIE (optionnelle) : si un 4e argument (chemin .meta) est fourni, on
#     ecrit a la fin une petite fiche JSON cout/duree/tokens. Les args 5/6/7 =
#     modele / effort / machine choisis par l'orchestrateur (ce qui a ete DEMANDE).
#     Regle d'or : un champ absent du flux de l'ouvrier = null, JAMAIS invente.
meta_path   = sys.argv[4] if len(sys.argv) > 4 else ""
arg_modele  = sys.argv[5] if len(sys.argv) > 5 else ""
arg_effort  = sys.argv[6] if len(sys.argv) > 6 else ""
arg_machine = sys.argv[7] if len(sys.argv) > 7 else ""

result_text = ""
last_text = ""   # dernier message texte de l'ouvrier (filet anti-"reponse vide")
result_event = {}   # l'event "result" final : porte cout/duree/tokens (s'il arrive)
lf = open(live_path, "w", encoding="utf-8")
lf.write(">>> L'OUVRIER TRAVAILLE -- avancement EN DIRECT (ce fichier grandit a chaque action) <<<\n\n")
lf.flush()
try:
    os.chown(live_path, hermes_uid, hermes_uid)
    os.chmod(live_path, 0o644)
except Exception:
    pass

def emit(line):
    try:
        lf.write(line + "\n"); lf.flush()
    except Exception:
        pass

def short(s, n=240):
    s = " ".join(str(s).split())
    return (s[:n] + " ...") if len(s) > n else s

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    try:
        e = json.loads(raw)
    except Exception:
        continue
    t = e.get("type")
    if t == "assistant":
        for b in e.get("message", {}).get("content", []):
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = (b.get("text") or "").strip()
                if txt:
                    last_text = txt
                    emit("\U0001F4AC " + short(txt, 500))          # 💬 ce que dit l'ouvrier
            elif bt == "tool_use":
                name = b.get("name", "?")
                inp = b.get("input") or {}
                if name == "Bash":
                    emit("\U0001F4BB " + short(inp.get("command", ""), 220))   # 💻 commande
                elif name == "Read":
                    emit("\U0001F4D6 lit " + short(inp.get("file_path", ""), 140))  # 📖
                elif name in ("Edit", "Write", "NotebookEdit"):
                    emit("✏️  ecrit " + short(inp.get("file_path", ""), 140))  # ✏️
                elif name in ("Grep", "Glob"):
                    emit("\U0001F50D cherche " + short(inp.get("pattern", ""), 120))     # 🔍
                elif name == "Task":
                    emit("\U0001F916 delegue a un sous-agent")                  # 🤖
                else:
                    emit("\U0001F527 " + name + " " + short(json.dumps(inp, ensure_ascii=False), 120))  # 🔧
    elif t == "result":
        result_event = e if isinstance(e, dict) else {}
        r = e.get("result")
        if isinstance(r, str):
            result_text = r

# Filet : si l'ouvrier n'a pas emis d'evenement "result" final (max-turns,
# coupure, crash en aval...), on retombe sur son DERNIER message plutot que de
# livrer du vide -> Hermes voit au moins ce que l'ouvrier disait en dernier.
if not result_text and last_text:
    result_text = last_text + "\n\n[note systeme : reponse finale non emise -- ci-dessus le dernier message de l'ouvrier.]"

try:
    with open(result_path, "w", encoding="utf-8") as rf:
        rf.write(result_text)
except Exception:
    pass

# --- FICHE TELEMETRIE : cout/duree/tokens de l'ordre, lue dans l'event "result"
#     emis par l'ouvrier. Tout champ manquant reste null (ordre interrompu, coupe,
#     timeout...) -> la salle affiche ce qu'elle a, jamais une valeur inventee.
if meta_path:
    def _num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    usage = result_event.get("usage") if isinstance(result_event.get("usage"), dict) else {}
    # Modele : ce qui a ete DEMANDE (argv) prime ; a defaut, le modele reellement
    # facture (cle de modelUsage avec le plus de tokens de sortie) ; sinon null.
    modele = (arg_modele or "").strip() or None
    if not modele:
        mu = result_event.get("modelUsage")
        if isinstance(mu, dict) and mu:
            try:
                modele = max(mu.items(),
                             key=lambda kv: (kv[1] or {}).get("outputTokens", 0))[0]
            except Exception:
                modele = next(iter(mu), None)
    meta = {
        "modele":     modele,
        "effort":     (arg_effort or "").strip() or None,
        "machine":    (arg_machine or "").strip() or None,
        "cout_usd":   _num(result_event.get("total_cost_usd")),
        "duree_ms":   _num(result_event.get("duration_ms")),
        "tours":      _num(result_event.get("num_turns")),
        "tokens_in":  _num(usage.get("input_tokens")),
        "tokens_out": _num(usage.get("output_tokens")),
    }
    try:
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False)
        try:
            os.chown(meta_path, hermes_uid, hermes_uid)
            os.chmod(meta_path, 0o644)
        except Exception:
            pass
    except Exception:
        pass

emit("")
emit("=== Ordre termine, reponse livree (voir la reponse complete). ===")
try:
    lf.close()
except Exception:
    pass
