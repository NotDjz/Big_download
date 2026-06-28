# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Big Downloader (BIG DL) — application native Windows (pywebview + Flask) pour telecharger des medias depuis YouTube, Instagram, TikTok, X/Twitter et SoundCloud. Inclut un outil de decoupe video/audio. Utilise yt-dlp + FFmpeg. Repo GitHub : `NotDjz/Big_download`.

## Architecture

- **`app.py`** (~1300 lignes) — backend Flask + lancement pywebview. Tout en un seul fichier.
  - Mode frozen (PyInstaller) : `BUNDLE_DIR` = `sys._MEIPASS` (templates/static/ffmpeg), `BASE_DIR` = dossier de l'exe (downloads/cookies)
  - Mode dev : les deux pointent vers `__file__`.parent
  - Downloads en threads daemon, progression via SSE (Server-Sent Events) avec `queue.Queue` par download
  - Formats video : priorite `bestvideo+bestaudio` (VP9/AV1 inclus pour la 4K), converti en MP4 par FFmpeg
  - Instagram photos : necessite `cookies.txt` ou `www.instagram.com_cookies.txt` a cote de l'exe
  - Decoupe : FFmpeg avec `-ss` avant `-i` + `-t` duration (input seeking rapide, re-encode pour precision)
  - Progression decoupe en temps reel via SSE (parse `out_time_us` de `-progress pipe:2`)
  - Nommage des coupes : conserve le nom original + `_v2`, `_v3`, etc.
  - Upload de fichiers pour decoupe via `temp_uploads/`, nettoyage auto apres coupe reussie ou echouee
  - Rate limiting (10 req/min), timeout downloads (30 min), taille max upload (2 GB via Flask MAX_CONTENT_LENGTH)
  - Migration auto des anciens dossiers (YouTube/ → Videos/, YouTube_MP3/ → Music/) au demarrage
- **`templates/index.html`** — page unique, 2 onglets (Telecharger / Decouper)
- **`static/js/app.js`** — logique UI, lecteur video/audio modal avec controles custom, editeur de decoupe avec range slider double-handle + cleanup des event listeners
- **`static/css/style.css`** — theme dark glass (fond #0a0a18, accents violet #8b5cf6), responsive
- **`create_icon.py`** — genere icon.ico (logo fleche download sur degrade violet, 4x supersampling)

## Key Routes

- `POST /start-download` → spawn thread → `GET /progress/<id>` SSE stream
- `POST /cut-video` → spawn thread → `GET /cut-progress/<id>` SSE stream (decoupe depuis le player)
- `POST /upload-for-cut` — upload fichier pour l'onglet Decouper (validation format + duree ffprobe)
- `GET /stream-temp/<filename>` — sert un fichier temporaire pour la preview avant decoupe
- `POST /cut-uploaded` → spawn thread → `GET /cut-progress/<id>` SSE stream (decoupe fichier uploade)
- `GET /list-downloads` — liste tous les fichiers telecharges avec metadonnees
- `GET /open-folder` — ouvre le dossier downloads dans l'explorateur

## Build

```bash
# Compiler l'exe Windows (necessite ffmpeg.exe et ffprobe.exe dans le dossier)
py download_ffmpeg.py          # telecharge FFmpeg si absent
py -m PyInstaller --noconfirm --onefile --windowed --name BigDownloader ^
    --icon "icon.ico" ^
    --add-data "templates;templates" --add-data "static;static" ^
    --add-data "ffmpeg.exe;." --add-data "ffprobe.exe;." ^
    --hidden-import yt_dlp --hidden-import webview --collect-all webview app.py

# Ou simplement :
build.bat
```

L'exe sort dans `dist/BigDownloader.exe`. Supprimer `build/` et `*.spec` apres.

## Dev

```bash
# Lancer en mode dev (ouvre une fenetre pywebview sur localhost:5555)
py app.py
```

Pas de tests ni de linting configures.

## Structure des downloads

```
(a cote de l'exe ou du projet)
downloads/
  Videos/    — MP4 YouTube et reseaux sociaux
  Music/     — MP3 audio
  Photos/    — Instagram photos (JPG)
```

## Release GitHub

```bash
gh release delete vX.Y --yes
gh release create vX.Y dist/BigDownloader.exe --title "..." --notes "..."
```

## Points importants

- L'UI et les messages sont en francais
- Les cookies Instagram ne doivent JAMAIS etre commites (dans .gitignore)
- ffmpeg.exe et ffprobe.exe sont dans .gitignore (telecharges par download_ffmpeg.py)
- pywebview utilise Edge WebView2 sur Windows, GTK+WebKitGTK sur Linux
- Le volume du lecteur demarre a 10% par defaut
- Les paths fichiers sont securises via `sanitize_filename()` + verification resolve/startswith contre path traversal
- FFMPEG_PATH/FFPROBE_PATH pointent vers le bundle en mode frozen, vers le PATH systeme en dev
