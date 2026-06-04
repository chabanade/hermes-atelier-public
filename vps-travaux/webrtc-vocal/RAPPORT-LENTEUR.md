# Pourquoi le vocal est lent — diagnostic et solutions

*Mesures faites le 2026-06-04 sur la machine qui sert en prod (2 cœurs). Chiffres
réels, pas des estimations — sauf le « cerveau » Hermès, voir plus bas.*

---

## En une phrase

Entre le moment où vous **arrêtez de parler** et celui où Hermès **commence à
répondre**, la machine perd aujourd'hui **~7 secondes** en plus du temps de
réflexion d'Hermès. Et là-dedans, **~5 secondes sont du gaspillage évitable** :
on rallume les moteurs à chaque phrase, et une « boîte aux lettres » n'est
relue que toutes les 5 secondes.

---

## Le trajet d'une phrase, chronométré

| Étape | Temps mesuré | Évitable ? |
|---|---|---|
| 1. On attend d'être sûr que la phrase est finie | **0,8 s** | en partie |
| 2. Transcription (comprendre ce qui a été dit) | **~2,0 s** (jusqu'à 3,7 s à froid) | ~1 s gaspillée |
| 3. La question attend d'être ramassée et transmise à Hermès | **0 à 5 s** (≈ 2,5 s en moyenne) | **quasi tout** |
| 4. Hermès réfléchit | **? (non mesuré)** | hors de ce rapport |
| 5. Synthèse de la voix de réponse | **~1,7 s** | ~1,5 s gaspillée |
| 6. Petite pause de fin | 0,25 s | non |
| **Total machine (hors réflexion d'Hermès)** | **≈ 7 s** | **≈ 5 s** |

> ⚠️ L'étape 4 (le « cerveau ») n'a **pas pu être chronométrée** : Hermès n'était
> pas lancé pendant les tests, tous les essais ont expiré. En usage réel, son
> temps de réflexion **s'ajoute** à ces 7 secondes et c'est sans doute le plus
> gros morceau. **À mesurer en priorité** une fois les correctifs ci-dessous posés.

---

## Ce qui est RAPIDE (inutile d'y toucher)

- **La transcription elle-même** : ~1,3 s pour 5 s de parole. Correct.
- **La synthèse vocale elle-même** : 10 s de voix produites en **moins de 0,1 s**
  une fois le moteur chargé. Piper est très rapide.
- **Le réseau, l'écriture des fichiers, le découpage de l'audio** : quelques
  millièmes de seconde. Aucun problème.

## Ce qui est LENT (et pourquoi)

1. **On rallume les moteurs à chaque phrase.** À chaque tour, le programme
   recharge depuis le disque le modèle de transcription (~1 s) puis, plus tard,
   la voix (~1,5 s), avant de tout éteindre. C'est comme **couper le moteur de
   la voiture à chaque feu rouge** : la synthèse ne coûte presque rien, c'est le
   *rallumage* qui coûte. → **~2,5 s perdues par échange.**
2. **La boîte aux lettres n'est relue que toutes les 5 secondes.** Le petit
   programme qui transmet votre question à Hermès et rapporte la réponse ne
   regarde le courrier qu'une fois toutes les 5 s. Vous avez fini de parler, la
   transcription est prête en 2 s… et ça attend la prochaine ronde. → **jusqu'à
   5 s perdues, ~2,5 s en moyenne**, avant même qu'Hermès commence à réfléchir.
3. **0,8 s d'attente systématique** pour confirmer que vous avez fini de parler.
   Nécessaire, mais un peu trop long pour une conversation vive.

---

## 3 actions, classées par impact

### Action 1 — Relever le courrier tout de suite (gain ~2,5 s · risque quasi nul)
**Aujourd'hui** : la question attend jusqu'à 5 s d'être ramassée.
**Changement** : déclencher la transmission *dès* que la question est prête (sur
événement de fichier), ou au minimum relire toutes les 1 s au lieu de 5.
**Pour vous** : jusqu'à 5 s gagnées (≈ 2,5 s en moyenne) **avant** qu'Hermès
réfléchisse.
**Risque** : négligeable. Ça ne réveille pas le LLM (on regarde juste un dossier,
donc toujours « 0 dépense au repos »). On garde un repli de sécurité à 1 s.
👉 *Le meilleur rapport gain/effort : presque gratuit, gros gain.*

### Action 2 — Garder les moteurs allumés (gain ~2,5 s/tour · risque modéré)
**Aujourd'hui** : transcription et voix sont rechargées à chaque phrase.
**Changement** : les garder en mémoire (un petit service qui reste allumé pour
la transcription, un pour la voix) au lieu de les redémarrer à chaque fois.
**Pour vous** : ~2,5 s gagnées à **chaque** échange.
**Risque** : demande une réécriture modérée du code et ~300–500 Mo de mémoire
occupés en permanence (on a 4 Go libres → OK). Il faut prévoir un redémarrage
automatique si un service plante.
👉 *Le plus gros gain technique, mais un peu de travail.*

### Action 3 — Réglages immédiats et gratuits (gain ~0,5 s · risque faible)
- **Transcription** : passer la « largeur de recherche » de 5 à 1. Mesuré :
  −0,2 s, **texte identique** en français.
- **Attente de fin de phrase** : 0,8 s → 0,6 s. Conversation plus vive (−0,2 s).
  *Risque* : si on descend trop, ça coupe quelqu'un qui marque une pause ; 0,6 s
  reste sûr, à confirmer à l'usage.
- **Option « modèle tiny »** (à réserver si on veut le maximum de vitesse) :
  2× plus rapide à transcrire (−0,45 s) **mais** mesuré une faute en français
  (« sympathise » au lieu de « synthèse »). Acceptable seulement si on tolère
  quelques imprécisions. **Recommandation : garder `base`.**

---

## Résultat attendu

| | Attente machine (hors réflexion Hermès) |
|---|---|
| Aujourd'hui | **~7 s** |
| + Action 1 | ~4,5 s |
| + Action 2 | ~2 s |
| + Action 3 | **~1,5 s** |

On passe d'environ **7 s à ~1,5 s** de « temps machine » par échange — une
conversation **3 à 4× plus réactive** — sans changer la qualité du français.

**Ensuite seulement** : mesurer le temps de réflexion d'Hermès (le « cerveau »),
qui s'ajoute dans tous les cas et reste aujourd'hui l'inconnue n°1.
