# 🎙️ Parler à Hermès en vrai — comment tester (Mehdi)

> Pendant ton déplacement, j'ai fait marcher la voix **des deux côtés** (Telegram **et** web).
> Tout est testé côté serveur ; il ne reste que **toi + ton téléphone** pour valider le rendu.
> En cherchant, j'ai trouvé **2 vrais bugs** qui empêchaient Hermès de répondre à la voix
> depuis le début (web compris) — c'est réparé.

---

## 1) Bot Telegram vocal — **@ermes_Voc_bot**

C'est un bot **séparé** d'Hermès, dédié à la voix. Il n'accepte que **toi** (sécurité).

**Pour tester :**
1. Dans Telegram, ouvre la conversation avec **@ermes_Voc_bot** (cherche-le, puis appuie sur **Démarrer**).
2. **Envoie-lui une note vocale** (parle normalement, en français).
3. Tu dois recevoir, ~10-12 s après : **la réponse d'Hermès en texte _et_ en note vocale** 🔊.

**Tu peux aussi lui ÉCRIRE** (clavier ou **dictée Siri**) : même résultat, réponse texte + voix.
→ Mains-libres iPhone : « Dis Siri… » pour dicter ton message, ou un **Raccourci Siri** (je peux t'aider à le créer).

**Ce qui est « bon » :** la transcription correspond à ce que tu as dit, et la réponse est
du vrai Hermès (ton, pertinence). **Si ça coince**, dis-moi quoi exactement (rien ne revient ?
texte mais pas de voix ? transcription fausse ?) — chaque cas a une cause précise que je corrige.

---

## 2) Page web vocale (conversation continue)

`https://votre-domaine.example/` — sur ton iPhone (Safari).

**Pour tester :** ouvre la page → **Démarrer** → autorise le micro → parle → écoute la réponse.
*(Le web était déjà optimisé — transcription et voix « chaudes », relève 1 s — et il bénéficie
des 2 corrections de fond, donc il devrait répondre maintenant alors qu'avant il restait muet.)*

---

## Ce que j'ai réparé (pour info)
- **Bug 1** : le pont appelait Hermès avec une commande qui **n'existait pas** (`hermes ask`) → corrigé (`-z`).
- **Bug 2** : Hermès répondait, mais sa réponse était écrite **illisible** pour le bot/web (droits) → corrigé.
- **Ajouté** : transcription locale (RGPD), synthèse voix locale (Piper), conversion en note vocale (ffmpeg).
- **Tout est local** sur ton VPS : rien de ta voix ne part chez un tiers.

## Ce qu'on pourra faire ensuite (à ta demande)
- **Mémoire de conversation** dans la voix (qu'Hermès se souvienne du tour précédent).
- **Raccourci Siri** « Dis à Hermès… » vraiment mains-libres en voiture.
- **WhatsApp** comme 2ᵉ canal, ou le **pont d'appel** (tu appelles un numéro et tu parles).

Réponds-moi juste « la voix marche » ou dis-moi ce qui cloche, et j'ajuste. 🎙️
