# Surcharges systemd du service `webrtc-vocal` (page web vocale)

Ces fichiers sont des **drop-ins** : ils ajoutent des reglages au service `webrtc-vocal`
SANS toucher au `.service` d'origine. On les annule simplement en supprimant le fichier.

| Fichier | Role |
|---|---|
| `voice.conf` | Force la voix Piper **masculine** (`fr_FR-tom-medium`) pour la page web, comme le bot Telegram. Repli feminin = `fr_FR-upmc-medium`. |
| `turn.conf` | Charge le secret TURN depuis `turn.env` (hors git) pour le relais WebRTC. |

## Installer sur le VPS
```bash
sudo install -d /etc/systemd/system/webrtc-vocal.service.d
sudo cp voice.conf turn.conf /etc/systemd/system/webrtc-vocal.service.d/
sudo systemctl daemon-reload
sudo systemctl restart webrtc-vocal
# Verifier que la bonne voix est chargee :
ps -ef | grep "synth.py.*--serve" | grep -v grep   # doit montrer --voice ...fr_FR-tom-medium.onnx
```

## Changer la voix de la page web
Editer `voice.conf` (chemin du `.onnx`), recopier, `daemon-reload` + `restart`. Reversible.
