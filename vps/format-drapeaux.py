#!/usr/bin/env python3
# ============================================================================
#  format-drapeaux.py  --  met en forme les drapeaux du copilote pour Hermes.
#  Lit alertes.jsonl sur stdin (1 JSON par ligne), ecrit un bloc texte lisible.
#  ASCII uniquement (pas d'emoji) : robuste meme si LANG=C sur le VPS.
# ============================================================================
import sys, json

lignes = [l for l in sys.stdin if l.strip()]
if not lignes:
    sys.exit(0)

mortels, risques = [], []
for l in lignes:
    try:
        d = json.loads(l)
    except Exception:
        continue
    niveau = d.get('niveau', '?')
    raison = d.get('raison', '')
    extrait = (d.get('extrait', '') or '')[:120]
    item = "  - [%s] %s\n      \"%s\"" % (niveau, raison, extrait)
    (mortels if niveau == 'MORTEL' else risques).append(item)

n = len(mortels) + len(risques)
if n == 0:
    sys.exit(0)

print("")
print("--- COPILOTE : %d drapeau(x) leve(s) pendant cet ordre ---" % n)
if mortels:
    print(">>> %d ALERTE(S) MORTELLE(S) -- A SIGNALER A MEHDI <<<" % len(mortels))
    print("\n".join(mortels))
if risques:
    print("Surveillance (%d action(s) a risque laissee(s) passer) :" % len(risques))
    print("\n".join(risques))
