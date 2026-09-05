# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Big Downloader (BIG DL) — application native Windows (pywebview + Flask) pour telecharger des medias depuis YouTube, Instagram, TikTok, X/Twitter et SoundCloud. Inclut un outil de decoupe video/audio. Utilise yt-dlp + FFmpeg. Repo GitHub : `NotDjz/Big_download`.

## Architecture

Tout le backend tient dans **`app.py`** (~1340 lignes). Il n'y a pas de modules : les helpers prefixes `_` sont prives, les `@app.route` forment l'API consommee par `static/js/app.js`.

### Frozen vs dev — le point le plus piegeur

Deux racines distinctes, a ne jamais confondre :

- `BUNDLE_DIR` — ressources en lecture seule (templates, static, ffmpeg). Frozen : `sys._MEIPASS`. Dev : `__file__`.parent.
- `BASE_DIR` — donnees ecrites par l'app (`downloads/`, `temp_uploads/`, `cookies.txt`). Frozen : dossier de l'exe. Dev : `__file__`.parent.

`FFMPEG_PATH` / `FFPROBE_PATH` suivent une cascade a **trois** branches (app.py:63) : bundle si frozen+Windows, sinon `ffmpeg.exe` a cote de `BASE_DIR` s'il existe, sinon le PATH systeme. Le repo contient deja ffmpeg.exe/ffprobe.exe localement, donc en dev c'est la branche du milieu qui gagne.

Sur Windows, tout `subprocess` passe `**_SUBPROCESS_FLAGS` (= `CREATE_NO_WINDOW`) pour eviter les fenetres console qui clignotent depuis l'exe windowed.

### Modele de concurrence : thread + queue + SSE

Le meme pattern sert aux downloads et aux decoupes, et c'est le squelette a respecter pour toute nouvelle operation longue :

1. La route POST cree un `uuid`, une `queue.Queue`, un thread daemon, et repond immediatement avec l'id.
2. Le worker (`_run_download`, `_run_ffmpeg_cut`) pousse des evenements dict dans la queue.
3. Le client ouvre un `EventSource` sur la route SSE correspondante. Les deux flux partagent **`_sse_response(registry, key, unknown_msg, poll, deadline_key)`** : ils etaient deux copies, le correctif de fuite a du etre ecrit deux fois et les copies avaient deja diverge (seul le download avait recu un filet cote client). `poll` vaut 10 s pour les downloads, 15 s pour les decoupes.
4. Le stream se termine sur `status` `complete` ou `error`. Le retrait du registre est dans un `finally` : sans lui, une deconnexion client (`GeneratorExit`) laissait l'entree et sa queue en memoire definitivement.
5. Les evenements terminaux passent par `_put_final`, qui fait de la place si la queue est pleine — un `put_nowait` nu levait `queue.Full`, l'exception remontait dans le gestionnaire generique et declenchait a tort le repli photo Instagram.

Ces deux registres sont des dicts en memoire : l'etat ne survit pas a un redemarrage.

**Timeouts.** L'echeance appartient au job : elle vit dans `entry['deadline']` du registre, et **toutes** les couches la relisent la. C'est ce qui les empeche de se contredire — la boucle playlist la repousse video par video (une playlist longue est un usage legitime), et le filet SSE suit ; quand il etait ancre sur `start_time`, il coupait le client a 31 min pendant que le worker continuait.

Deux formes selon le runtime, et c'est voulu :

- **yt-dlp tourne dans le processus**, donc rien ne peut le tuer de l'exterieur : `_make_deadline_hook(entry)` leve `DownloadTimeout` depuis les `progress_hooks` *et* les `postprocessor_hooks` (les premiers sont muets pendant un merge ou une extraction mp3). Le generateur SSE ne garde qu'un filet a `+SSE_GRACE` pour liberer le client si yt-dlp bloque la ou aucun hook ne passe.
- **FFmpeg est un sous-processus**, donc un `threading.Timer` le tue pour de bon (`CUT_TIMEOUT`). L'echeance etait evaluee par ligne de stderr, ce qui ne rattrapait jamais un FFmpeg bloque qui n'ecrit plus rien.

`TIMEOUT_MSG` est derive de `DOWNLOAD_TIMEOUT` : le message existait en trois litteraux qui auraient menti au premier changement de constante.

### Downloads

- Formats (`_get_format_string`) : `bestvideo+bestaudio` sans contrainte de conteneur en priorite — c'est ce qui debloque la 4K (VP9/AV1) — puis fallback mp4/m4a. Le postprocessor `FFmpegVideoConvertor` reconvertit en MP4.
- Pour `social`, la qualite est honoree mais **uniquement sur un flux progressif** (`best[height<=N]/best`) : `merge_output_format` et le convertisseur MP4 ne sont poses que pour `youtube`/`playlist`, donc un `bestvideo+bestaudio` ici sortirait un `.mkv`/`.webm` illisible dans le player WebView2.
- `download_type` : `youtube` | `mp3` | `social` | `playlist`. Le type pilote a la fois le format, le dossier de sortie et le template de nom (`_get_output_template` : `platform_%(id)s` pour `social`, `%(title)s` sinon).
- **Playlists** : `_run_playlist_download` fait un `extract_flat` puis telecharge video par video, en emettant `playlist_start` / `playlist_video_start` / `playlist_video_error`. Une video en echec n'interrompt pas la playlist.
- **Instagram photos** : le chemin nominal yt-dlp echoue sur les posts photo. `_run_download` rattrape l'exception et, si la plateforme est Instagram, bascule sur `_download_instagram_images` — API privee Instagram via `_shortcode_to_media_id` + cookies. Necessite `cookies.txt` ou `www.instagram.com_cookies.txt` dans `BASE_DIR`. Les images sont converties en JPG (Pillow) et deplacees dans `Photos/`.

### Decoupe

- FFmpeg avec `-ss` **avant** `-i` + `-t` duration : input seeking rapide, re-encode pour la precision.
- Progression temps reel en parsant `out_time_us=` sur `-progress pipe:2`. `stdout` va vers `DEVNULL` : rien ne le lisait, et un tube jamais draine peut bloquer FFmpeg une fois plein.
- Nommage : nom original + `_v2`, `_v3`… (`_next_versioned_name`).
- La decoupe se fait **uniquement depuis l'onglet Decouper** (upload → `temp_uploads/` → preview → coupe). Les controles de trim du player modal ont ete retires (commit 6768038) : il n'y a plus de route `/cut-video`.
- `temp_cleanup` supprime le fichier temporaire sur tous les chemins de sortie, via la closure `fail()` — succes, erreur FFmpeg, timeout, exception.
- `_sweep_temp_uploads()` (appele a chaque upload) purge les fichiers de plus d'une heure : abandonner une decoupe via « Changer de fichier » laissait sinon jusqu'a 2 GB sur le disque indefiniment.

### Nettoyage des fichiers temporaires

`_sweep_old_files(folder, max_age, patterns)` est le seul balayage ; `_cleanup_partial_files` et `_sweep_temp_uploads` en sont deux appels. **La garde d'age est le point subtil** : les dossiers de sortie sont partages, et un balayage inconditionnel effacait le `.part` d'un telechargement encore en cours. En complement, chaque download suit ses propres fichiers partiels (`own_partials`, alimente par `d['tmpfilename']`) et les supprime tout de suite meme en echec — la garde d'age ne peut pas les atteindre.

### Frontend

- **`templates/index.html`** — page unique, 2 onglets (Telecharger / Decouper) + un player modal partage.
- **`static/js/app.js`** — un seul `DOMContentLoaded`, toutes les fonctions en closure, **aucune globale** (les handlers du player sont cables par `addEventListener`, condition d'une CSP sans `unsafe-inline`). Player video/audio a controles custom, editeur de decoupe avec range slider double-handle et cleanup explicite des event listeners.
- **`static/css/style.css`** — theme dark glass (fond #0a0a18, accents violet #8b5cf6), responsive.
- **`create_icon.py`** — genere icon.ico (fleche download sur degrade violet, 4x supersampling).

## Routes

| Route | Role |
|---|---|
| `POST /start-download` → `GET /progress/<id>` | download (SSE) |
| `POST /get-info` | metadonnees + qualites dispo ; fallback "post photo" si Instagram echoue |
| `POST /get-playlist-info` | metadonnees playlist |
| `POST /upload-for-cut` | upload vers `temp_uploads/` (validation format + duree ffprobe) |
| `GET /stream-temp/<filename>` | preview du fichier uploade avant decoupe |
| `POST /cut-uploaded` → `GET /cut-progress/<id>` | decoupe (SSE) |
| `GET /list-downloads`, `GET /get-stats` | listing et stats |
| `GET /stream/<category>/<filename>` | lecture inline dans le player |
| `DELETE /delete/<category>/<filename>` | suppression |
| `POST /open-folder`, `POST /open-file/<category>/<filename>` | explorateur Windows (`explorer /select,`). **POST et pas GET** : un GET est cense etre sans effet, or ces deux-la lancent un process — n'importe quel `<img src>` les declenchait. |
| `GET /downloads/<category>/<filename>` | telechargement `as_attachment` — **plus aucun appelant** depuis que le bouton download a ete remplace par l'explorateur (commit 35faee1) |

## Securite des paths

Les routes a `<category>/<filename>` combinent trois controles, tous necessaires :

1. `_resolve_category_folder(category)` — whitelist stricte (Videos / Music / Photos + 3 dossiers legacy). Toute autre valeur → 400.
2. `sanitize_filename(filename)` — remplace `<>:"/\|?*`, retire les points et espaces de fin, tronque a 200 caracteres. Il ne mutile **pas** les `..` internes : prive de separateur, `..` ne designe plus un parent, et les neutraliser cassait les noms legitimes (`Wait... What.mp4` etait liste puis renvoyait 404 a la lecture, la suppression et la localisation).
3. `resolve()` + `str(...).startswith(str(folder.resolve()))` — garde-fou final contre le path traversal.

Autres limites : rate limiting 10 req/min (`_rate_limit_check`), `MAX_VIDEO_SIZE` 5 GB, upload max 2 GB (`MAX_CONTENT_LENGTH` + errorhandler 413).

## Garde d'origine

`_reject_foreign_origin` (`@app.before_request`) refuse tout `Host` hors `127.0.0.1:{PORT}` / `localhost:{PORT}`, et tout `Origin` present qui ne soit pas celui de l'app. `PORT` est la seule source du numero de port (serveur, attente socket, URL pywebview, listes d'autorisation). **Ne pas retirer** : le serveur ecoute sur la loopback, mais n'importe quelle page web ouverte sur la machine peut l'atteindre. Sans cette garde, un domaine attaquant repointe sur `127.0.0.1` (DNS rebinding) devenait same-origin et pouvait lister, exfiltrer puis supprimer les telechargements ; et `POST /upload-for-cut`, en `multipart/form-data`, echappait a la protection CORS accidentelle des autres routes.

**`Origin` seul ne suffit pas.** Les navigateurs ne l'envoient pas sur un GET no-cors : un `<video src="http://127.0.0.1:5555/stream/Videos/X.mp4">` place dans une page tierce traversait la garde et servait d'oracle sur les fichiers telecharges (`onloadedmetadata` = le fichier existe, plus duree et resolution lisibles en cross-origin). Quand `Origin` est absent, la garde se rabat donc sur **`Sec-Fetch-Site`**, toujours envoye. `none` doit rester autorise : c'est ce qu'envoie la navigation de premier niveau de la fenetre pywebview, l'exclure fermerait l'app a elle-meme.

`_security_headers` (`@app.after_request`) pose `X-Frame-Options: DENY` et une CSP avec `frame-ancestors 'none'`. Sans eux, une page tierce encadrait l'app et, par clickjacking, lui faisait declencher ses propres suppressions — en same-origin, donc parfaitement autorisees par la garde. La CSP reste stricte (`default-src 'self'`) parce que `templates/index.html` n'a plus d'attribut `onclick` : les quatre handlers du player sont cables par `addEventListener`, ce qui a aussi supprime les quatre fonctions exposees sur `window`.

## Structure des downloads

```
(a cote de l'exe ou du projet)
downloads/
  Videos/    — MP4 YouTube et reseaux sociaux
  Music/     — MP3 audio
  Photos/    — Instagram photos (JPG)
```

`_migrate_legacy_folders()` s'execute au demarrage (uniquement sous `__main__`) et deplace `YouTube/` → `Videos/`, `YouTube_MP3/` → `Music/`, `Reseaux_Sociaux/` → `Videos/`, en routant les fichiers image vers `Photos/` quelle que soit leur origine. Les dossiers vides sont supprimes.

## Dev

```bash
py app.py       # thread Flask sur 127.0.0.1:5555 + fenetre pywebview 1100x800
```

`install.bat` cree le venv, `run.bat` le reactive et met yt-dlp a jour avant de lancer. Attention : `run.bat` ouvre aussi un onglet navigateur sur localhost:5555, ce qui fait doublon avec la fenetre pywebview depuis le passage en app native.

Pas de linting. Les deux verifications rapides (deja autorisees dans `.claude/settings.local.json`) :

```bash
python -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('syntax OK')"
python -c "import app; print('OK - app loads without errors')"
```

Le second execute tout le code au niveau module : il cree `downloads/`, ses sous-dossiers et `temp_uploads/` en effet de bord, mais **pas** la migration legacy (protegee par `__main__`).

### Test de fumee de l'interface

```bash
py tests/run_ui_smoke.py     # demarre Flask + Chrome headless, pilote l'UI, code de sortie 0/1
```

Pilote un vrai Chromium via le **Chrome DevTools Protocol** — pas de dependance : `WebSocket` est natif depuis Node 20, et Chrome expose CDP avec `--remote-debugging-port`. Le profil est jetable, cree dans le dossier temp ; le depot n'est jamais touche.

Il couvre ce qu'un test cote serveur ne peut pas voir : les quatre handlers du player sont-ils cables (il n'y a plus d'attribut `onclick`, cf. la garde d'origine), la CSP bloque-t-elle une ressource, la boucle rAF avance-t-elle en lecture et se fige-t-elle en pause. **Il a besoin d'au moins un fichier dans `downloads/`** ; sans quoi il s'arrete apres le premier test en le signalant.

Le test s'est deja revele discriminant : en neutralisant les quatre `addEventListener`, les assertions LECTURE, MUET, FERMER et FOND echouent. Celle de PAUSE est conditionnee a la reussite de LECTURE — sans cela elle passerait par accident, la video n'ayant jamais demarre.

## Build

```bash
py download_ffmpeg.py   # telecharge ffmpeg.exe/ffprobe.exe si absents
build.bat               # = pyinstaller --onefile --windowed, cf. le .bat pour les flags exacts
```

L'exe sort dans `dist/BigDownloader.exe`. `build/`, `dist/` et `*.spec` sont gitignores — les nettoyer apres.

## Release GitHub

```bash
gh release delete vX.Y --yes
gh release create vX.Y dist/BigDownloader.exe --title "..." --notes "..."
```

## Points importants

- L'UI et les messages sont en francais.
- Les cookies Instagram ne doivent JAMAIS etre commites (dans .gitignore).
- `CLAUDE.md` figure dans `.gitignore` mais reste suivi par git (ajoute avant la regle) — il est bien versionne.
- pywebview utilise Edge WebView2 sur Windows. Les branches Darwin/Linux subsistent dans `open_folder` / `open_file` mais ne sont plus testees (scripts Linux retires, commit db3c540).
- Le volume du lecteur demarre a 10% par defaut.
