# BIG DOWNLOADER

Telechargeur web local pour YouTube, Instagram, TikTok et X/Twitter.

## Installation

**Prerequis:** Python 3.8+, FFmpeg dans le PATH.

```bash
install.bat
```

## Utilisation

```bash
run.bat
```

Ouvrir `http://localhost:5555` — coller un lien, choisir le format, telecharger.

## Fonctionnalites

- **YouTube** — Video MP4 jusqu'a 4K, playlists completes
- **MP3** — Extraction audio 192 kbps depuis toute plateforme
- **Reseaux sociaux** — Instagram (photos + videos), TikTok, X/Twitter
- **Photos Instagram** — Conversion automatique en JPG, apercu integre
- Progression en temps reel (SSE)
- Lecteur video/audio/photo integre
- Bouton ouverture dossier
- Rate limiting, timeout 30 min, nettoyage fichiers partiels

## Structure des telechargements

```
downloads/
├── Videos/    # YouTube MP4, reseaux sociaux video
├── Music/     # MP3 audio
└── Photos/    # Instagram photos (JPG)
```

## Cookies Instagram

Pour les photos Instagram, exporter les cookies avec l'extension
"Get cookies.txt LOCALLY" et placer le fichier `cookies.txt` ou
`www.instagram.com_cookies.txt` a la racine du projet.

## Technologies

- Flask, yt-dlp, Pillow, FFmpeg
- Frontend vanilla JS (pas de framework)
