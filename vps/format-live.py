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

result_text = ""
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
        r = e.get("result")
        if isinstance(r, str):
            result_text = r

try:
    with open(result_path, "w", encoding="utf-8") as rf:
        rf.write(result_text)
except Exception:
    pass
emit("")
emit("=== Ordre termine, reponse livree (voir la reponse complete). ===")
try:
    lf.close()
except Exception:
    pass
