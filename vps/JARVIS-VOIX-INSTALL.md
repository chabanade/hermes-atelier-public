# Voix JARVIS (à la demande) — recette d'installation reproductible

> Donne à Hermès le **timbre de JARVIS** en **français**, gratuitement et en local (CPU).
> Principe : on ne clone pas la langue, on **convertit le timbre** — Piper FR (la « bouche »)
> puis **RVC** avec un modèle JARVIS (le « timbre »). Le modèle RVC est entraîné sur l'anglais
> (voix Paul Bettany) mais convertit n'importe quelle langue → **JARVIS qui parle français**.
> Lent sur CPU sans GPU (~15-25 s/phrase) → branché **à la demande** seulement.
> **Usage perso/privé uniquement** (voix d'acteur + personnage Marvel : pas de diffusion/commercial).

## Les pièges (chèrement appris) et leurs correctifs
1. **rvc-python a des deps anciennes mal formées** (`omegaconf 2.0.6` déclare `PyYAML >=5.1.*`,
   syntaxe illégale que le pip moderne refuse) → installer avec un **pip tolérant** (`pip==23.0.1`)
   et le **résolveur historique** (`--use-deprecated=legacy-resolver`).
2. **fairseq exige Python 3.10** (pas 3.11/3.12 : 3.12 a même supprimé `distutils`) → venv en **3.10**.
3. **PyTorch ≥ 2.6 met `weights_only=True`** par défaut → casse le chargement du modèle hubert
   (format fairseq) → on **patche `torch.load`** en `weights_only=False` (cf. `jarvis_convert.py`).
4. **rvc-python 0.1.5 a un bug d'écriture** (`vc_single()` renvoie un tuple) → on appelle `vc_single`
   nous-mêmes et on extrait le tableau audio (cf. `jarvis_convert.py`).

## Étapes (sur le VPS, ~15 min)
```bash
# 1) Python 3.10 + ffmpeg
add-apt-repository -y ppa:deadsnakes/ppa && apt-get update
apt-get install -y python3.10 python3.10-venv python3.10-dev ffmpeg

# 2) Dossier + modèle JARVIS RVC (.pth + .index)
D=/home/ouvrier/travaux/rvc-jarvis; mkdir -p $D/model
curl -fL -o $D/jarvis.zip "https://huggingface.co/ronangrant/rvc_jarvis/resolve/main/jarvis_test.zip?download=true"
python3 -c "import zipfile; zipfile.ZipFile('$D/jarvis.zip').extractall('$D/model')"

# 3) venv 3.10 + pip tolérant + rvc-python
python3.10 -m venv $D/.venv
$D/.venv/bin/pip install "pip==23.0.1" "setuptools<66" wheel
$D/.venv/bin/pip install rvc-python --use-deprecated=legacy-resolver

# 4) le convertisseur (déjà versionné : vps/jarvis_convert.py -> $D/jarvis_convert.py)
#    1er appel : télécharge hubert + rmvpe automatiquement.
$D/.venv/bin/python $D/jarvis_convert.py entree.wav sortie.wav
```

## Côté bot (déjà fait)
- `hermes-voc-bot/bot.py` : mode par chat (`tom` par défaut / `jarvis`), commandes `/jarvis` et
  `/normal`, détection vocale (« passe en mode jarvis » / « voix normale »). En mode jarvis, la
  synthèse fait Piper → `jarvis_convert.py` (RVC) → ffmpeg → note vocale.
- Voix de base masculine : `fr_FR-tom-medium` (Piper).

## Notes / pistes
- L'install a tiré des libs **CUDA inutiles** (torch CPU les ignore) ; on peut alléger avec une roue
  torch **CPU-only** si on veut récupérer du disque.
- Une **carte graphique** rendrait JARVIS quasi temps réel (mais coût). Sur CPU, c'est « à la demande ».
- L'accent anglais (léger) se règle via `index_rate` dans `jarvis_convert.py` si besoin.
