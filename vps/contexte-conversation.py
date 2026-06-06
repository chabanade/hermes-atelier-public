#!/usr/bin/env python3
"""
Mémoire de conversation CENTRALE et TRANS-CANAL pour Hermès.

Tous les canaux (page web, Telegram, Siri — et demain Alexa / Apple Home…) déposent
leurs messages dans la MÊME boîte aux lettres et passent par le MÊME pont
(webrtc-reply.sh). Ce helper gère UN SEUL fil de conversation partagé : Mehdi peut
commencer sur un canal et continuer sur un autre sans perdre le fil.

C'est la mémoire COURT TERME (le fil de la discussion en cours).
AGEA reste la mémoire LONG TERME (qui est Mehdi, son métier, ses décisions).

Appelé par le pont :
  inject   : lit le message brut sur STDIN, écrit sur STDOUT le prompt enrichi du fil récent
  save     : argv[2]=message  argv[3]=reply  -> ajoute l'échange au fil

Garde-fous : fil borné (CONV_MAX_TOURS échanges, CONV_MAX_CHARS caractères) pour ne pas
ralentir Hermès ; reset automatique après CONV_GAP_S de silence (= nouvelle conversation).
Écriture atomique (.tmp + os.replace) : le fichier n'est jamais lu à moitié écrit.
"""
import json
import os
import sys
import time

STORE = os.environ.get("CONV_STORE", "/home/ouvrier/travaux/webrtc-vocal/data/conversation.json")
MAX_TOURS = int(os.environ.get("CONV_MAX_TOURS", "6"))       # échanges (Mehdi+Hermès) gardés
GAP_S = int(os.environ.get("CONV_GAP_S", str(30 * 60)))      # >30 min sans parler -> conversation neuve
MAX_CHARS = int(os.environ.get("CONV_MAX_CHARS", "1500"))    # plafond du contexte injecté


def _load():
    """Charge le fil ; repart à neuf si la dernière prise de parole est trop ancienne."""
    try:
        with open(STORE, encoding="utf-8") as f:
            d = json.load(f)
        if (time.time() - float(d.get("ts", 0))) > GAP_S:
            return {"ts": 0.0, "tours": []}
        if not isinstance(d.get("tours"), list):
            return {"ts": 0.0, "tours": []}
        return d
    except Exception:
        return {"ts": 0.0, "tours": []}


def _atomic_write(d):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, STORE)


def inject(message):
    tours = _load().get("tours", [])
    if not tours:
        return message  # conversation neuve : rien à rappeler
    lignes = []
    for tour in tours[-(MAX_TOURS * 2):]:
        role, txt = tour[0], tour[1]
        qui = "Mehdi" if role == "user" else "Toi (Hermès)"
        lignes.append(f"{qui}: {txt}")
    contexte = "\n".join(lignes)
    if len(contexte) > MAX_CHARS:
        contexte = "…" + contexte[-MAX_CHARS:]
    return ("(Rappel de notre conversation en cours, tous canaux confondus, pour ton "
            "contexte — ne le commente pas, sers-t'en juste pour comprendre :)\n"
            f"{contexte}\n\n"
            "(Nouveau message de Mehdi, réponds-y directement :)\n"
            f"{message}")


def save(message, reply):
    d = _load()
    tours = d.get("tours", [])
    tours.append(["user", message])
    tours.append(["hermes", reply])
    d["tours"] = tours[-(MAX_TOURS * 2):]
    d["ts"] = time.time()
    _atomic_write(d)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: contexte-conversation.py inject | save <message> <reply>")
    cmd = sys.argv[1]
    if cmd == "inject":
        sys.stdout.write(inject(sys.stdin.read()))
    elif cmd == "save":
        if len(sys.argv) < 4:
            sys.exit("usage: save <message> <reply>")
        save(sys.argv[2], sys.argv[3])
    else:
        sys.exit(f"commande inconnue : {cmd}")


if __name__ == "__main__":
    main()
