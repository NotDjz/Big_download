# BIG DL

Telecharge des videos, audios et photos depuis YouTube, Instagram, TikTok, X/Twitter et SoundCloud. Decoupe tes fichiers audio/video avec un editeur visuel integre.

## Fonctionnalites

- **YouTube** — video (jusqu'a 4K), audio MP3, shorts, playlists
- **Instagram** — reels, videos, photos (cookies requis pour les photos)
- **TikTok** — videos sans watermark
- **X / Twitter** — videos
- **SoundCloud** — audio MP3
- **Decoupe** — onglet dedie pour couper un fichier audio/video avec un slider visuel, precision a la frame

## Installation

### Option 1 : Executable Windows (recommande)

Telecharger `BigDownloader.exe` depuis la [derniere release](https://github.com/NotDjz/Big_download/releases/latest) et lancer. C'est tout.

### Option 2 : Depuis le code source

```bash
git clone https://github.com/NotDjz/Big_download.git
cd Big_download

# Windows
install.bat
run.bat

# Linux/macOS
chmod +x install.sh run.sh
./install.sh
./run.sh
```

Prerequis : Python 3.10+, FFmpeg dans le PATH (ou lance `py download_ffmpeg.py` pour le telecharger automatiquement).

## Utilisation

1. Lancer l'application
2. Coller un lien dans la barre de recherche
3. Choisir le format (video, audio, photo)
4. Telecharger

Les fichiers sont ranges automatiquement dans `downloads/Videos/`, `downloads/Music/` et `downloads/Photos/`.

### Outil de decoupe

Onglet **Decouper** : glisser un fichier audio ou video, ajuster les poignees du slider pour selectionner la portion a garder, cliquer Couper. Le fichier est sauvegarde avec le nom original + `_v2`, `_v3`, etc.

## Cookies Instagram

Les **photos** Instagram necessitent un fichier cookies (les videos/reels fonctionnent sans).

1. Installer l'extension navigateur [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
2. Aller sur instagram.com, se connecter
3. Exporter les cookies
4. Placer le fichier `cookies.txt` a cote de l'executable

## Build

```bash
py download_ffmpeg.py
build.bat
```

L'executable sort dans `dist/BigDownloader.exe`.

## Licence

[MIT](LICENSE)
