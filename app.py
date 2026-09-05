from flask import Flask, request, jsonify, send_file, render_template, Response
import yt_dlp
import os
import json
import re
import logging
import threading
import time
import uuid
import queue
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
import subprocess
import platform

_SUBPROCESS_FLAGS = {}
if platform.system() == 'Windows':
    _SUBPROCESS_FLAGS['creationflags'] = subprocess.CREATE_NO_WINDOW
from PIL import Image
import mimetypes
import shutil
import stat
import sys
import webview

if getattr(sys, 'frozen', False):
    BUNDLE_DIR = Path(sys._MEIPASS)
    BASE_DIR = Path(sys.executable).parent
else:
    BUNDLE_DIR = Path(__file__).parent
    BASE_DIR = BUNDLE_DIR

app = Flask(__name__,
            template_folder=str(BUNDLE_DIR / "templates"),
            static_folder=str(BUNDLE_DIR / "static"))
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2 GB

# Windows ne connait pas .woff2 : sans ca les polices partaient en
# application/octet-stream.
mimetypes.add_type('font/woff2', '.woff2')


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({'error': 'Fichier trop volumineux (max 2 GB)'}), 413


# Le serveur ecoute sur la loopback, mais n'importe quelle page web ouverte sur
# la machine peut l'atteindre. Verifier Host bloque le DNS rebinding (un domaine
# attaquant repointe sur 127.0.0.1 deviendrait sinon same-origin et pourrait
# lister, exfiltrer et supprimer les telechargements) ; verifier Origin bloque
# les POST multipart et les GET a effet de bord declenches par une page tierce.
PORT = 5555
ALLOWED_HOSTS = frozenset(f'{h}:{PORT}' for h in ('127.0.0.1', 'localhost'))
ALLOWED_ORIGINS = frozenset(f'http://{h}' for h in ALLOWED_HOSTS)


# 'none' est la navigation de premier niveau : c'est ce que la fenetre pywebview
# envoie en ouvrant l'app. L'exclure fermerait l'application a elle-meme.
ALLOWED_FETCH_SITES = frozenset(('same-origin', 'none'))


@app.before_request
def _reject_foreign_origin():
    if request.host not in ALLOWED_HOSTS:
        return jsonify({'error': 'Hote non autorise'}), 403
    origin = request.headers.get('Origin')
    if origin is not None:
        if origin not in ALLOWED_ORIGINS:
            return jsonify({'error': 'Origine non autorisee'}), 403
        return None
    # Sans Origin, on ne peut pas conclure : les navigateurs ne l'envoient pas
    # sur un GET no-cors, donc un <video src="http://127.0.0.1:5555/stream/...">
    # depuis une page tierce passait la garde et servait d'oracle sur les
    # fichiers telecharges. Sec-Fetch-Site, lui, est toujours envoye.
    site = request.headers.get('Sec-Fetch-Site')
    if site is not None and site not in ALLOWED_FETCH_SITES:
        return jsonify({'error': 'Origine non autorisee'}), 403
    return None


@app.after_request
def _security_headers(resp):
    # frame-ancestors ferme le clickjacking : encadree dans une page tierce,
    # l'app declenchait ses propres suppressions en same-origin sur deux clics.
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers.setdefault('Content-Security-Policy', '; '.join((
        "default-src 'self'",
        "img-src 'self' https: data:",  # vignettes servies par les plateformes
        "media-src 'self'",
        "frame-ancestors 'none'",
    )))
    return resp


# Logging structuré
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('bigdl')

# Suivi de progression des telechargements (download_id -> {queue, start_time})
download_progress = {}
# Suivi de progression des decoupes (cut_id -> {queue, thread})
cut_progress = {}

# Configuration
MAX_VIDEO_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB
DOWNLOAD_TIMEOUT = 30 * 60  # 30 minutes max par téléchargement
MAX_DOWNLOADS_PER_MINUTE = 10
CUT_TIMEOUT = 10 * 60  # 10 minutes max par decoupe
SSE_GRACE = 60  # marge laissee au worker pour s'annuler avant que le SSE lache
TIMEOUT_MSG = f'Timeout: telechargement trop long ({DOWNLOAD_TIMEOUT // 60} min max)'


class DownloadTimeout(Exception):
    """Levee depuis le progress hook pour interrompre reellement yt-dlp."""
DOWNLOAD_FOLDER = BASE_DIR / "downloads"

if getattr(sys, 'frozen', False) and platform.system() == 'Windows':
    FFMPEG_PATH = str(BUNDLE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BUNDLE_DIR / "ffprobe.exe")
elif (BASE_DIR / "ffmpeg.exe").exists():
    FFMPEG_PATH = str(BASE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BASE_DIR / "ffprobe.exe")
else:
    FFMPEG_PATH = "ffmpeg"
    FFPROBE_PATH = "ffprobe"
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# Rate limiting
_request_times = []
_rate_lock = threading.Lock()

# Dossiers par type de média
VIDEOS_FOLDER = DOWNLOAD_FOLDER / "Videos"
MUSIC_FOLDER = DOWNLOAD_FOLDER / "Music"
PHOTOS_FOLDER = DOWNLOAD_FOLDER / "Photos"

VIDEOS_FOLDER.mkdir(exist_ok=True)
MUSIC_FOLDER.mkdir(exist_ok=True)
PHOTOS_FOLDER.mkdir(exist_ok=True)

def _next_versioned_name(folder, stem, ext):
    """Trouve le prochain nom disponible: stem_v2.ext, stem_v3.ext, etc."""
    version = 2
    while True:
        name = f"{stem}_v{version}{ext}"
        if not (folder / name).exists():
            return name
        version += 1


def sanitize_filename(filename):
    """Nettoie le nom de fichier pour eviter les path traversal attacks.

    Remplacer les separateurs suffit a empecher toute sortie du dossier : prive
    de separateur, '..' ne designe plus un parent. On ne mutile donc plus les
    points internes d'un nom legitime ('Wait... What.mp4'), qui ne correspondait
    autrement plus au fichier ecrit par yt-dlp et renvoyait un 404 a la lecture,
    a la suppression et a la localisation.
    """
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.rstrip(' .')
    if not filename:
        filename = '_'
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200] + ext
    return filename


def validate_youtube_url(url):
    """Valide que l'URL est bien une URL YouTube valide"""
    if not url:
        return False

    youtube_patterns = [
        r'^https?://(www\.)?youtube\.com/watch\?v=[\w-]+',
        r'^https?://(www\.)?youtu\.be/[\w-]+',
        r'^https?://(www\.)?youtube\.com/shorts/[\w-]+',
        r'^https?://(www\.)?youtube\.com/playlist\?list=[\w-]+',
    ]

    for pattern in youtube_patterns:
        if re.match(pattern, url):
            return True

    return False


def is_playlist_url(url):
    """Detecte si l'URL est une playlist YouTube"""
    if not url:
        return False
    if 'youtube.com/playlist' in url:
        return True
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    return 'list' in params and 'v' not in params


def clean_youtube_url(url):
    """Nettoie l'URL YouTube pour garder seulement l'ID de la vidéo"""
    try:
        if is_playlist_url(url):
            parsed = urlparse(url)
            list_id = parse_qs(parsed.query).get('list', [None])[0]
            if list_id:
                return f'https://www.youtube.com/playlist?list={list_id}'
            return url
        if 'youtube.com/watch' in url:
            parsed = urlparse(url)
            video_id = parse_qs(parsed.query).get('v', [None])[0]
            if video_id:
                return f'https://www.youtube.com/watch?v={video_id}'
        elif 'youtu.be/' in url:
            video_id = url.split('youtu.be/')[-1].split('?')[0]
            return f'https://www.youtube.com/watch?v={video_id}'
        elif 'youtube.com/shorts/' in url:
            video_id = url.split('shorts/')[-1].split('?')[0]
            return f'https://www.youtube.com/watch?v={video_id}'
        return url
    except Exception:
        return url


def detect_platform(url):
    """Détecte la plateforme depuis l'URL"""
    domain = urlparse(url).netloc.lower()

    platforms = {
        'youtube.com': 'youtube',
        'youtu.be': 'youtube',
        'instagram.com': 'instagram',
        'twitter.com': 'x',
        'x.com': 'x',
        'tiktok.com': 'tiktok',
        'soundcloud.com': 'soundcloud',
        'facebook.com': 'facebook',
        'vimeo.com': 'vimeo'
    }

    for domain_key, platform in platforms.items():
        if domain_key in domain:
            return platform

    return 'unknown'


def _get_format_string(download_type, quality=None):
    """Retourne le format string yt-dlp selon le type de telechargement"""
    if download_type == 'mp3':
        return 'bestaudio/best'
    elif download_type == 'social':
        # 'best' nu ignorait la qualite choisie : selectionner 480p
        # telechargeait quand meme le flux le plus lourd disponible.
        if quality:
            # Flux progressif uniquement : merge_output_format et le convertisseur
            # MP4 ne sont poses que pour youtube/playlist, donc un bestvideo+
            # bestaudio ici sortirait un .mkv/.webm illisible dans le player.
            return f'best[height<={quality}]/best'
        return 'best'
    elif quality:
        return (
            f'bestvideo[height<={quality}]+bestaudio/'
            f'bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/'
            'best'
        )
    else:
        return (
            'bestvideo+bestaudio/'
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
            'best'
        )


def _get_output_folder(download_type):
    """Retourne le dossier de sortie selon le type"""
    return MUSIC_FOLDER if download_type == 'mp3' else VIDEOS_FOLDER


def _get_output_template(download_type, url=''):
    """Retourne le template de nom de fichier"""
    folder = _get_output_folder(download_type)
    if download_type == 'social':
        plat = detect_platform(url)
        return str(folder / f'{plat}_%(id)s.%(ext)s')
    return str(folder / '%(title)s.%(ext)s')


def _make_progress_hook(q, current_video=None, total_videos=None, partials=None):
    """Cree un progress hook pour yt-dlp."""
    def progress_hook(d):
        if partials is not None and d.get('tmpfilename'):
            partials.add(d['tmpfilename'])
        try:
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                event = {
                    'status': 'downloading',
                    'percent': round(percent, 1),
                    'speed': d.get('_speed_str', '').strip(),
                    'eta': d.get('_eta_str', '').strip(),
                    'downloaded': downloaded,
                    'total': total,
                }
                if current_video is not None:
                    event['current_video'] = current_video
                    event['total_videos'] = total_videos
                q.put_nowait(event)
            elif d['status'] == 'finished':
                msg = 'Fusion/conversion en cours...'
                if current_video is not None:
                    msg = f'Video {current_video}/{total_videos} - {msg}'
                q.put_nowait({'status': 'processing', 'message': msg})
        except queue.Full:
            pass
    return progress_hook


def _make_deadline_hook(entry):
    """Garde d'echeance, posee sur les deux familles de hooks.

    Lever depuis un hook est la seule facon d'annuler yt-dlp, qui tourne dans
    le processus. Le controle vivait avant dans le generateur SSE, ou il
    n'arretait que le rapport pendant que le thread continuait a telecharger.
    Les hooks de progression sont muets pendant un merge ou une extraction
    mp3, d'ou la pose sur les postprocessor_hooks aussi.

    L'echeance est relue dans l'entree du registre a chaque appel : la boucle
    playlist la repousse video par video, et le filet SSE lit la meme valeur.
    """
    def deadline_hook(d):
        if time.time() > entry['deadline']:
            raise DownloadTimeout(TIMEOUT_MSG)
    return deadline_hook


def _build_ydl_opts(download_type, url, quality, progress_hook, entry):
    """Construit les options yt-dlp"""
    ydl_opts = {
        'format': _get_format_string(download_type, quality),
        'outtmpl': _get_output_template(download_type, url),
        'quiet': True,
        'no_warnings': True,
        'no_color': True,
        'progress_hooks': [_make_deadline_hook(entry), progress_hook],
        'postprocessor_hooks': [_make_deadline_hook(entry)],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'fragment_retries': 10,
        'retries': 10,
    }

    if getattr(sys, 'frozen', False):
        ydl_opts['ffmpeg_location'] = str(BUNDLE_DIR)

    cookies_file = _get_cookies_file()
    if cookies_file:
        ydl_opts['cookiefile'] = cookies_file

    if download_type in ('youtube', 'playlist'):
        ydl_opts['merge_output_format'] = 'mp4'
        ydl_opts['postprocessors'] = [
            {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
            {'key': 'FFmpegMetadata'},
        ]
    elif download_type == 'mp3':
        ydl_opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
            {'key': 'FFmpegMetadata'},
        ]

    return ydl_opts


def _get_cookies_file():
    """Retourne le chemin du fichier cookies s'il existe"""
    base = BASE_DIR
    for name in ('cookies.txt', 'www.instagram.com_cookies.txt'):
        path = base / name
        if path.exists():
            return str(path)
    return None


def _convert_to_jpg(filepath):
    """Convertit une image (webp, png, etc.) en JPG"""
    jpg_path = filepath.with_suffix('.jpg')
    try:
        img = Image.open(filepath)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        img.save(jpg_path, 'JPEG', quality=92)
        if filepath != jpg_path:
            filepath.unlink()
        return jpg_path
    except Exception:
        return filepath


def _shortcode_to_media_id(shortcode):
    """Convertit un shortcode Instagram en media_id numerique"""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + alphabet.index(char)
    return str(media_id)


def _download_instagram_images(q, url):
    """Telecharge les images d'un post Instagram via l'API avec cookies"""
    import http.cookiejar

    _put_progress(q, {'status': 'processing', 'message': 'Telechargement image Instagram...'})

    cookies_file = _get_cookies_file()
    if not cookies_file:
        _put_final(q, {
            'status': 'error',
            'message': 'Photos Instagram necessitent un fichier cookies.txt (exporte depuis ton navigateur avec l\'extension "Get cookies.txt LOCALLY")'
        })
        return

    parsed_url = urlparse(url)
    path_parts = [p for p in parsed_url.path.split('/') if p]
    # Find shortcode: it's the part after 'p', 'reel', or 'tv' in the path
    shortcode = None
    for i, part in enumerate(path_parts):
        if part in ('p', 'reel', 'tv') and i + 1 < len(path_parts):
            shortcode = path_parts[i + 1]
            break

    if not shortcode:
        _put_final(q, {'status': 'error', 'message': 'URL Instagram invalide'})
        return

    media_id = _shortcode_to_media_id(shortcode)

    cj = http.cookiejar.MozillaCookieJar(cookies_file)
    cj.load(ignore_discard=True, ignore_expires=True)

    session = requests.Session()
    session.cookies = cj
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-IG-App-ID': '936619743392459',
    })

    api_url = f'https://www.instagram.com/api/v1/media/{media_id}/info/'
    try:
        resp = session.get(api_url, timeout=15)
    except Exception as e:
        _put_final(q, {'status': 'error', 'message': f'Erreur API Instagram: {e}'})
        return

    if resp.status_code != 200:
        _put_final(q, {'status': 'error', 'message': f'API Instagram erreur {resp.status_code}. Cookies peut-etre expires.'})
        return

    data = resp.json()
    items = data.get('items', [])
    if not items:
        _put_final(q, {'status': 'error', 'message': 'Post Instagram vide ou inaccessible'})
        return

    item = items[0]
    image_urls = []

    carousel = item.get('carousel_media', [])
    if carousel:
        for cm in carousel:
            candidates = cm.get('image_versions2', {}).get('candidates', [])
            if candidates:
                image_urls.append(candidates[0]['url'])
    else:
        candidates = item.get('image_versions2', {}).get('candidates', [])
        if candidates:
            image_urls.append(candidates[0]['url'])

    if not image_urls:
        _put_final(q, {'status': 'error', 'message': 'Aucune image trouvee dans le post'})
        return

    downloaded = 0
    total = len(image_urls)

    for i, img_url in enumerate(image_urls, 1):
        try:
            img_resp = session.get(img_url, timeout=30)
            img_resp.raise_for_status()
            suffix = f'_{i}' if total > 1 else ''
            temp_filename = f'instagram_{shortcode}{suffix}.tmp'
            temp_filepath = PHOTOS_FOLDER / sanitize_filename(temp_filename)
            temp_filepath.write_bytes(img_resp.content)
            _convert_to_jpg(temp_filepath)
            downloaded += 1
            _put_progress(q, {
                'status': 'downloading',
                'percent': round(i / total * 100, 1),
                'speed': '',
                'eta': '',
            })
        except Exception:
            pass

    if downloaded > 0:
        title = f'instagram_{shortcode}'
        if downloaded > 1:
            title += f' ({downloaded} images)'
        _put_final(q, {
            'status': 'complete',
            'title': title,
            'filename': f'instagram_{shortcode}_1.jpg' if total > 1 else f'instagram_{shortcode}.jpg',
        })
    else:
        _put_final(q, {'status': 'error', 'message': 'Echec telechargement. Cookies expires ou acces refuse.'})


def _put_progress(q, event):
    """Publie un evenement de progression, en le jetant si la file est pleine.

    Un put nu remontait dans le gestionnaire generique et avortait toute la
    playlist avec un message vide (str(queue.Full()) est '').
    """
    try:
        q.put_nowait(event)
    except queue.Full:
        pass


def _put_final(q, event):
    """Publie un evenement terminal, meme si la file est pleine.

    put_nowait leve queue.Full quand le client ne draine pas ; l'exception
    remontait dans le gestionnaire d'erreur generique et, pour Instagram,
    declenchait a tort le repli photo.
    """
    try:
        q.put_nowait(event)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()  # un seul producteur : liberer une place suffit
    except queue.Empty:
        pass
    try:
        q.put_nowait(event)
    except queue.Full:
        log.warning('Evenement terminal non transmis (file saturee)')


def _sweep_old_files(folder, max_age, patterns=('*',)):
    """Supprime les fichiers d'un dossier plus vieux que max_age.

    La garde d'age est le point subtil : les dossiers sont partages, et un
    balayage inconditionnel effacait le .part d'un telechargement encore en
    cours, le faisant echouer.
    """
    cutoff = time.time() - max_age
    for pattern in patterns:
        for f in folder.glob(pattern):
            try:
                st = f.stat()
                if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def _cleanup_partial_files(folder):
    """Supprime les fichiers partiels abandonnes dans un dossier de sortie."""
    _sweep_old_files(folder, 300, ('*.part', '*.ytdl', '*.temp'))


def _check_filesize(file_path):
    """Vérifie la taille d'un fichier téléchargé, supprime s'il dépasse la limite"""
    if file_path.exists():
        size = file_path.stat().st_size
        if size > MAX_VIDEO_SIZE:
            file_path.unlink()
            size_gb = size / (1024**3)
            max_gb = MAX_VIDEO_SIZE / (1024**3)
            raise Exception(f'Fichier trop volumineux ({size_gb:.1f} GB). Maximum: {max_gb:.0f} GB')


def _run_download(download_id, url, download_type, quality=None):
    """Execute le telechargement dans un thread avec progress hooks"""
    entry = download_progress.get(download_id)
    if not entry:
        return
    q = entry['queue']

    own_partials = set()

    try:
        if download_type == 'playlist':
            _run_playlist_download(q, url, quality, entry, own_partials)
        else:
            hook = _make_progress_hook(q, partials=own_partials)
            ydl_opts = _build_ydl_opts(download_type, url, quality, hook, entry)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if info is None:
                    _put_final(q, {'status': 'error', 'message': "Impossible d'extraire les informations"})
                    return

                title = info.get('title', 'media')
                filename = ydl.prepare_filename(info)
                file_path = Path(filename)
                if not file_path.exists():
                    for ext in ('.mp4', '.webm', '.mkv', '.webp', '.jpg', '.png'):
                        candidate = Path(filename).with_suffix(ext)
                        if candidate.exists():
                            file_path = candidate
                            break

                image_exts = ('.webp', '.png', '.gif', '.jpg', '.jpeg')
                if file_path.exists() and file_path.suffix.lower() in image_exts:
                    if file_path.suffix.lower() != '.jpg':
                        file_path = _convert_to_jpg(file_path)
                    if file_path.exists() and file_path.parent.resolve() != PHOTOS_FOLDER.resolve():
                        dest = PHOTOS_FOLDER / file_path.name
                        shutil.move(str(file_path), str(dest))
                        file_path = dest

                _check_filesize(file_path)

                result = {
                    'status': 'complete',
                    'title': title,
                    'filename': file_path.name if file_path.exists() else os.path.basename(filename),
                }

                if download_type == 'youtube':
                    result['resolution'] = f"{info.get('height', 0)}p"
                    result['width'] = info.get('width', 0)
                    result['height'] = info.get('height', 0)
                    result['fps'] = info.get('fps', 0)

                _put_final(q, result)

    except DownloadTimeout as e:
        log.warning(f"Download timeout {url}")
        _put_final(q, {'status': 'error', 'message': str(e)})
    except Exception as e:
        error_msg = str(e)
        log.error(f"Download failed [{download_type}] {url}: {error_msg}")
        if detect_platform(url) == 'instagram':
            try:
                _download_instagram_images(q, url)
            except Exception as img_err:
                _put_final(q, {'status': 'error', 'message': f'Video: {error_msg} | Image: {str(img_err)}'})
        else:
            _put_final(q, {'status': 'error', 'message': error_msg})
    finally:
        # Nos propres fichiers partiels partent tout de suite, meme en echec ;
        # ceux des autres telechargements sont laisses a la garde d'age.
        for tmp in own_partials:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
        _cleanup_partial_files(_get_output_folder(download_type))


def _run_playlist_download(q, url, quality=None, entry=None, partials=None):
    """Telecharge une playlist video par video avec suivi"""
    # D'abord recuperer la liste des videos
    flat_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        playlist_info = ydl.extract_info(url, download=False)

    if not playlist_info:
        _put_final(q, {'status': 'error', 'message': "Impossible de lire la playlist"})
        return

    entries = [e for e in playlist_info.get('entries', []) if e]
    total = len(entries)
    playlist_title = playlist_info.get('title', 'Playlist')

    if total == 0:
        _put_final(q, {'status': 'error', 'message': "Playlist vide"})
        return

    _put_progress(q, {
        'status': 'playlist_start',
        'title': playlist_title,
        'total_videos': total,
    })

    # 'item' et pas 'entry' : le nom masquait le parametre portant l'entree du
    # registre, donc l'echeance etait ecrite dans le dict de la video yt-dlp et
    # le filet SSE continuait de lire une valeur jamais repoussee.
    for i, item in enumerate(entries, 1):
        video_url = item.get('url') or item.get('id')
        if not video_url:
            continue

        if not video_url.startswith('http'):
            video_url = f'https://www.youtube.com/watch?v={video_url}'

        _put_progress(q, {
            'status': 'playlist_video_start',
            'current_video': i,
            'total_videos': total,
            'video_title': item.get('title', f'Video {i}'),
        })

        # Echeance repoussee a chaque video : un budget global de 30 min
        # abandonnerait une playlist longue en cours de route, ce qui est un
        # usage legitime. Le filet SSE lit la meme valeur, donc les deux
        # couches ne peuvent plus se contredire.
        entry['deadline'] = time.time() + DOWNLOAD_TIMEOUT
        hook = _make_progress_hook(q, current_video=i, total_videos=total, partials=partials)
        ydl_opts = _build_ydl_opts('youtube', video_url, quality, hook, entry)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(video_url, download=True)
        except DownloadTimeout:
            raise  # sinon chaque video suivante expirerait a son tour
        except Exception as e:
            _put_progress(q, {
                'status': 'playlist_video_error',
                'current_video': i,
                'total_videos': total,
                'message': str(e),
            })

    _put_final(q, {
        'status': 'complete',
        'title': playlist_title,
        'filename': f'{total} videos',
        'is_playlist': True,
        'total_videos': total,
    })


def _rate_limit_check():
    """Vérifie le rate limiting. Retourne True si la requête est autorisée."""
    now = time.time()
    with _rate_lock:
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= MAX_DOWNLOADS_PER_MINUTE:
            return False
        _request_times.append(now)
        return True


@app.route('/start-download', methods=['POST'])
def start_download():
    """Lance un telechargement avec suivi de progression"""
    try:
        if not _rate_limit_check():
            return jsonify({'error': 'Trop de requetes. Attendez un moment.'}), 429

        data = request.get_json()
        url = data.get('url')
        download_type = data.get('type', 'youtube')
        quality = data.get('quality')

        if not url:
            return jsonify({'error': 'URL manquante'}), 400

        if download_type in ('youtube', 'playlist'):
            if not validate_youtube_url(url):
                return jsonify({'error': 'URL YouTube invalide'}), 400
            url = clean_youtube_url(url)

        if download_type == 'social':
            plat = detect_platform(url)
            if plat not in ('instagram', 'tiktok', 'x', 'facebook'):
                return jsonify({'error': f'Plateforme {plat} non supportee'}), 400

        download_id = str(uuid.uuid4())
        q = queue.Queue(maxsize=100)
        now = time.time()
        download_progress[download_id] = {
            'queue': q,
            'start_time': now,
            # Repoussee video par video par la boucle playlist ; lue par les
            # hooks (qui annulent) et par le filet SSE (qui libere le client).
            'deadline': now + DOWNLOAD_TIMEOUT,
        }

        thread = threading.Thread(
            target=_run_download,
            args=(download_id, url, download_type, quality),
            daemon=True,
        )
        thread.start()

        log.info(f"Download started [{download_type}] {url}")
        return jsonify({'download_id': download_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _sse_response(registry, key, unknown_msg, poll, deadline_key=None):
    """Draine la queue d'un job et la sert en Server-Sent Events.

    Les deux flux (download, decoupe) etaient deux copies : le correctif de
    fuite a du etre ecrit deux fois, et les copies avaient deja diverge - seul
    le download avait recu un filet cote client.
    """
    def generate():
        entry = registry.get(key)
        if not entry:
            yield f'data: {json.dumps({"status": "error", "message": unknown_msg})}\n\n'
            return
        q = entry['queue']
        try:
            while True:
                # Filet de securite. Le worker s'annule desormais lui-meme (hook
                # yt-dlp ou watchdog FFmpeg) ; on lui laisse SSE_GRACE d'avance,
                # puis on libere le client plutot que de le laisser attendre.
                if deadline_key and time.time() > entry[deadline_key] + SSE_GRACE:
                    yield f'data: {json.dumps({"status": "error", "message": TIMEOUT_MSG})}\n\n'
                    break
                try:
                    event = q.get(timeout=poll)
                    yield f'data: {json.dumps(event)}\n\n'
                    if event.get('status') in ('complete', 'error'):
                        break
                except queue.Empty:
                    yield f'data: {json.dumps({"status": "heartbeat"})}\n\n'
        finally:
            # finally, sinon une deconnexion client (GeneratorExit) laissait
            # l'entree et sa queue en memoire definitivement.
            registry.pop(key, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/progress/<download_id>')
def progress_stream(download_id):
    """Endpoint SSE pour le suivi de progression"""
    return _sse_response(download_progress, download_id,
                         'Telechargement inconnu', poll=10, deadline_key='deadline')


@app.route('/')
def index():
    """Page principale"""
    return render_template('index.html')


# ==================== PLAYLISTS ====================
@app.route('/get-playlist-info', methods=['POST'])
def get_playlist_info():
    """Recupere les informations d'une playlist YouTube"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL manquante'}), 400

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
                raise Exception("Impossible d'extraire les informations de la playlist")

            entries = info.get('entries', [])
            videos = []
            for entry in entries:
                if entry:
                    videos.append({
                        'title': entry.get('title', 'Sans titre'),
                        'duration': entry.get('duration', 0),
                        'url': entry.get('url', ''),
                    })

            return jsonify({
                'success': True,
                'title': info.get('title', 'Playlist'),
                'uploader': info.get('uploader', 'Inconnu'),
                'video_count': len(videos),
                'videos': videos[:50],
            })

    except Exception as e:
        log.error(f"Playlist error: {e}")
        return jsonify({'error': f'Erreur: {str(e)}'}), 500


# ==================== COMMUN ====================
@app.route('/get-info', methods=['POST'])
def get_video_info():
    """Récupère les informations d'une vidéo"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL manquante'}), 400

        platform = detect_platform(url)

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
        }

        cookies_file = _get_cookies_file()
        if cookies_file:
            ydl_opts['cookiefile'] = cookies_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as extract_err:
            if platform == 'instagram':
                return jsonify({
                    'success': True,
                    'title': 'Post Instagram',
                    'duration': 0,
                    'thumbnail': '',
                    'uploader': '',
                    'platform': 'instagram',
                    'resolution': '',
                    'width': 0,
                    'height': 0,
                    'fps': 0,
                    'available_qualities': [],
                    'has_audio': False,
                    'is_playlist': False,
                    'video_count': 0,
                    '_is_photo': True,
                })
            raise extract_err

        if info is None:
            if platform == 'instagram':
                return jsonify({
                    'success': True,
                    'title': 'Post Instagram',
                    'duration': 0,
                    'thumbnail': '',
                    'uploader': '',
                    'platform': 'instagram',
                    'resolution': '',
                    'width': 0,
                    'height': 0,
                    'fps': 0,
                    'available_qualities': [],
                    'has_audio': False,
                    'is_playlist': False,
                    'video_count': 0,
                    '_is_photo': True,
                })
            raise Exception("Impossible d'extraire les informations")

        # Récupérer la résolution disponible
        height = info.get('height', 0)
        width = info.get('width', 0)
        fps = info.get('fps', 0)

        # Déterminer la qualité
        if height >= 2160:
            quality = "4K (2160p)"
        elif height >= 1440:
            quality = "1440p"
        elif height >= 1080:
            quality = "1080p"
        elif height >= 720:
            quality = "720p"
        elif height >= 480:
            quality = "480p"
        else:
            quality = f"{height}p" if height > 0 else "Inconnue"

        # Extraire les qualites disponibles
        formats = info.get('formats', [])
        available_qualities = sorted(
            {f['height'] for f in formats
             if f.get('height') and f.get('vcodec', 'none') != 'none'},
            reverse=True,
        )

        has_audio = any(
            f.get('acodec', 'none') != 'none'
            for f in formats
        ) if formats else True

        is_playlist = info.get('_type') == 'playlist'

        return jsonify({
            'success': True,
            'title': info.get('title', 'N/A'),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail', ''),
            'uploader': info.get('uploader', 'N/A'),
            'platform': platform,
            'resolution': quality,
            'width': width,
            'height': height,
            'fps': fps,
            'available_qualities': available_qualities,
            'has_audio': has_audio,
            'is_playlist': is_playlist,
            'video_count': len(info.get('entries', [])) if is_playlist else 0,
        })

    except Exception as e:
        log.error(f"Get-info error: {e}")
        return jsonify({'error': f'Erreur: {str(e)}'}), 500


def _resolve_category_folder(category):
    """Resout le dossier a partir du nom de categorie (nouveau ou legacy)"""
    folder_map = {
        'Videos': VIDEOS_FOLDER,
        'Music': MUSIC_FOLDER,
        'Photos': PHOTOS_FOLDER,
        # Legacy (mapped to actual on-disk folders)
        'YouTube': DOWNLOAD_FOLDER / "YouTube",
        'YouTube_MP3': DOWNLOAD_FOLDER / "YouTube_MP3",
        'Reseaux_Sociaux': DOWNLOAD_FOLDER / "Reseaux_Sociaux",
    }
    return folder_map.get(category)


@app.route('/downloads/<category>/<filename>')
def download_file(category, filename):
    """Permet de télécharger un fichier depuis une catégorie"""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Catégorie invalide'}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({'error': 'Accès refusé'}), 403

    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'Fichier non trouvé'}), 404


@app.route('/delete/<category>/<filename>', methods=['DELETE'])
def delete_file(category, filename):
    """Supprime un fichier telecharge"""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Categorie invalide'}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({'error': 'Acces refuse'}), 403

    if not file_path.exists():
        return jsonify({'error': 'Fichier non trouve'}), 404

    file_path.unlink()
    return jsonify({'success': True, 'message': f'Fichier supprime: {safe_filename}'})


@app.route('/get-stats')
def get_stats():
    """Retourne les statistiques des téléchargements"""
    total_files = 0
    total_size = 0
    largest_file = {'name': 'Aucun', 'size': 0, 'category': ''}

    folders = [
        (VIDEOS_FOLDER, 'Videos', 'videos'),
        (MUSIC_FOLDER, 'Music', 'music'),
        (PHOTOS_FOLDER, 'Photos', 'photos'),
    ]

    categories_stats = {
        'videos': {'files': 0, 'size': 0},
        'music': {'files': 0, 'size': 0},
        'photos': {'files': 0, 'size': 0},
    }

    for folder, display_name, stat_key in folders:
        if not folder.exists():
            continue
        for file in folder.iterdir():
            if file.is_file():
                total_files += 1
                file_size = file.stat().st_size
                total_size += file_size
                categories_stats[stat_key]['files'] += 1
                categories_stats[stat_key]['size'] += file_size

                if file_size > largest_file['size']:
                    largest_file = {
                        'name': file.name,
                        'size': file_size,
                        'category': display_name
                    }

    return jsonify({
        'total_files': total_files,
        'total_size': total_size,
        'largest_file': largest_file,
        'categories': categories_stats
    })


@app.route('/list-downloads')
def list_downloads():
    """Liste tous les fichiers telecharges avec leurs categories"""
    files = []
    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg')

    folders = [
        (VIDEOS_FOLDER, 'Videos'),
        (MUSIC_FOLDER, 'Music'),
        (PHOTOS_FOLDER, 'Photos'),
    ]
    # Include legacy folders if they exist (use their real folder names as categories)
    legacy_map = [
        (DOWNLOAD_FOLDER / "YouTube", 'YouTube'),
        (DOWNLOAD_FOLDER / "YouTube_MP3", 'YouTube_MP3'),
        (DOWNLOAD_FOLDER / "Reseaux_Sociaux", 'Reseaux_Sociaux'),
    ]
    for folder, category in legacy_map:
        if folder.exists():
            folders.append((folder, category))

    seen_names = set()
    for folder, category in folders:
        if not folder.exists():
            continue
        for file in folder.iterdir():
            if file.is_file() and file.name not in seen_names:
                seen_names.add(file.name)
                stat = file.stat()
                ext = file.suffix.lower()
                if ext in image_exts:
                    media_type = 'photo'
                elif ext in audio_exts:
                    media_type = 'audio'
                else:
                    media_type = 'video'
                files.append({
                    'name': file.name,
                    'size': stat.st_size,
                    'category': category,
                    'media_type': media_type,
                    'url': f'/downloads/{category}/{file.name}',
                    'timestamp': stat.st_mtime,
                })

    files.sort(key=lambda f: f['timestamp'], reverse=True)
    return jsonify(files)


@app.route('/stream/<category>/<filename>')
def stream_file(category, filename):
    """Sert un fichier pour le player video/audio/photo"""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Categorie invalide'}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({'error': 'Acces refuse'}), 403

    if file_path.exists():
        return send_file(file_path)
    return jsonify({'error': 'Fichier non trouve'}), 404


def _run_ffmpeg_cut(cut_id, input_path, out_path, start, duration, temp_cleanup=None):
    """Execute FFmpeg dans un thread avec progression via stderr."""
    q = cut_progress[cut_id]['queue']

    def fail(message):
        """Sortie en echec : un seul endroit ou nettoyer."""
        for path in (out_path, temp_cleanup):
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        _put_final(q, {'status': 'error', 'message': message})

    try:
        cmd = [
            FFMPEG_PATH, '-y',
            '-ss', str(start),
            '-i', str(input_path),
            '-t', str(duration),
            '-avoid_negative_ts', 'make_zero',
            '-progress', 'pipe:2',
            str(out_path),
        ]
        # stdout vers DEVNULL : rien ne le lisait, et un tube jamais draine peut
        # bloquer FFmpeg des qu'il se remplit.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, **_SUBPROCESS_FLAGS,
        )

        # Chien de garde sur un timer : l'echeance etait auparavant evaluee dans
        # la boucle de lecture, donc un FFmpeg bloque qui n'ecrit plus une seule
        # ligne sur stderr n'etait jamais tue.
        timed_out = threading.Event()

        def _kill_stalled():
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass

        watchdog = threading.Timer(CUT_TIMEOUT, _kill_stalled)
        watchdog.daemon = True
        watchdog.start()

        try:
            for line in proc.stderr:
                if line.startswith('out_time_us='):
                    try:
                        us = int(line.split('=')[1].strip())
                        pct = min(us / 1_000_000 / duration, 1.0) if duration > 0 else 0
                        # put_nowait : le retrait de l'entree en finally orpheline
                        # la queue des que le client part, et un put bloquant
                        # figeait alors le worker (stderr plus draine, nettoyage
                        # jamais fait) jusqu'a la fin du processus.
                        _put_progress(q, {'status': 'progress', 'percent': round(pct * 100, 1)})
                    except (ValueError, ZeroDivisionError):
                        pass
            proc.wait()
        finally:
            watchdog.cancel()

        if timed_out.is_set():
            fail('Timeout: decoupe trop longue')
            return

        if proc.returncode != 0:
            fail('Erreur FFmpeg lors de la decoupe')
            return

        if temp_cleanup:
            temp_cleanup.unlink(missing_ok=True)
        size = out_path.stat().st_size
        log.info(f"Cut done: {out_path.name} ({size / 1024 / 1024:.1f} MB)")
        _put_final(q, {
            'status': 'complete',
            'filename': out_path.name,
            'size': size,
            'message': f'Decoupe terminee: {out_path.name}',
        })
    except FileNotFoundError:
        fail('FFmpeg non trouve')
    except Exception as e:
        log.error(f"Cut error: {e}")
        fail(str(e))


@app.route('/cut-progress/<cut_id>')
def cut_progress_stream(cut_id):
    """Endpoint SSE pour le suivi de progression des decoupes"""
    return _sse_response(cut_progress, cut_id,
                         'Decoupe inconnue', poll=15, deadline_key='deadline')


TEMP_FOLDER = BASE_DIR / "temp_uploads"
TEMP_FOLDER.mkdir(exist_ok=True)


def _sweep_temp_uploads():
    """Purge les uploads abandonnes.

    Un fichier n'etait supprime que sur le chemin de la decoupe : abandonner
    via 'Changer de fichier' le laissait indefiniment sur le disque, jusqu'a
    2 GB par abandon.
    """
    _sweep_old_files(TEMP_FOLDER, 3600)


@app.route('/upload-for-cut', methods=['POST'])
def upload_for_cut():
    """Upload un fichier pour le pre-visualiser avant decoupe"""
    _sweep_temp_uploads()
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier envoye'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Nom de fichier vide'}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.mp3', '.mp4', '.mkv', '.webm', '.m4a', '.wav', '.flac', '.ogg', '.avi', '.mov'):
        return jsonify({'error': f'Format non supporte: {ext}'}), 400

    safe_name = sanitize_filename(file.filename)
    temp_id = str(uuid.uuid4())[:8]
    temp_name = f"{temp_id}_{safe_name}"
    temp_path = TEMP_FOLDER / temp_name

    file.save(str(temp_path))

    file_size = temp_path.stat().st_size
    duration = _get_media_duration(temp_path)

    if duration <= 0:
        temp_path.unlink(missing_ok=True)
        return jsonify({'error': 'Impossible de lire la duree du fichier. Format non supporte ou fichier corrompu.'}), 400

    log.info(f"Upload for cut: {safe_name} ({file_size / 1024 / 1024:.1f} MB)")
    return jsonify({
        'success': True,
        'temp_name': temp_name,
        'original_name': file.filename,
        'size': file_size,
        'duration': duration,
    })


@app.route('/stream-temp/<filename>')
def stream_temp(filename):
    """Sert un fichier temporaire pour la pre-visualisation"""
    safe_name = sanitize_filename(filename)
    file_path = (TEMP_FOLDER / safe_name).resolve()
    if not str(file_path).startswith(str(TEMP_FOLDER.resolve())):
        return jsonify({'error': 'Acces refuse'}), 403
    if not file_path.exists():
        return jsonify({'error': 'Fichier non trouve'}), 404
    return send_file(file_path)


@app.route('/cut-uploaded', methods=['POST'])
def cut_uploaded():
    """Decoupe un fichier uploade et renvoie le resultat"""
    data = request.get_json()
    temp_name = data.get('temp_name')
    start = data.get('start', 0)
    end = data.get('end')
    original_name = data.get('original_name', '')

    if not temp_name or end is None:
        return jsonify({'error': 'Parametres manquants'}), 400
    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return jsonify({'error': 'Les temps doivent etre des nombres'}), 400
    if end <= start:
        return jsonify({'error': 'Le temps de fin doit etre apres le debut'}), 400

    safe_temp = sanitize_filename(temp_name)
    temp_path = (TEMP_FOLDER / safe_temp).resolve()
    if not str(temp_path).startswith(str(TEMP_FOLDER.resolve())):
        return jsonify({'error': 'Acces refuse'}), 403
    if not temp_path.exists():
        return jsonify({'error': 'Fichier source non trouve'}), 404

    stem = Path(original_name).stem if original_name else temp_path.stem
    stem = sanitize_filename(stem)
    ext = temp_path.suffix

    audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg')
    dest_folder = MUSIC_FOLDER if ext.lower() in audio_exts else VIDEOS_FOLDER
    out_name = _next_versioned_name(dest_folder, stem, ext)
    out_path = dest_folder / out_name

    cut_id = str(uuid.uuid4())[:8]
    duration = end - start
    cut_progress[cut_id] = {
        'queue': queue.Queue(maxsize=200),
        'deadline': time.time() + CUT_TIMEOUT,
    }
    thread = threading.Thread(
        target=_run_ffmpeg_cut,
        args=(cut_id, temp_path, out_path, start, duration, temp_path),
        daemon=True,
    )
    thread.start()
    return jsonify({'cut_id': cut_id})


def _get_media_duration(filepath):
    """Obtient la duree d'un fichier media via ffprobe"""
    try:
        cmd = [
            FFPROBE_PATH, '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            str(filepath),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **_SUBPROCESS_FLAGS)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get('format', {}).get('duration', 0))
    except Exception:
        pass
    return 0


@app.route('/open-folder', methods=['POST'])
def open_folder():
    """Ouvre le dossier de telechargements dans l'explorateur"""
    folder = str(DOWNLOAD_FOLDER.resolve())
    try:
        if platform.system() == 'Windows':
            os.startfile(folder)
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', str(folder)])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/open-file/<category>/<filename>', methods=['POST'])
def open_file(category, filename):
    """Ouvre l'explorateur avec le fichier selectionne"""
    safe_name = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Categorie invalide'}), 400
    file_path = (folder / safe_name).resolve()
    if not str(file_path).startswith(str(folder.resolve())):
        return jsonify({'error': 'Acces refuse'}), 403
    if not file_path.exists():
        return jsonify({'error': 'Fichier non trouve'}), 404
    try:
        if platform.system() == 'Windows':
            subprocess.Popen(['explorer', '/select,', str(file_path)], **_SUBPROCESS_FLAGS)
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', '-R', str(file_path)])
        else:
            subprocess.Popen(['xdg-open', str(file_path.parent)])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _migrate_legacy_folders():
    """Migre les fichiers des anciens dossiers vers la nouvelle structure"""
    migrations = [
        (DOWNLOAD_FOLDER / "YouTube", VIDEOS_FOLDER),
        (DOWNLOAD_FOLDER / "YouTube_MP3", MUSIC_FOLDER),
        (DOWNLOAD_FOLDER / "Reseaux_Sociaux", VIDEOS_FOLDER),
    ]
    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    moved = 0
    for src_folder, dest_folder in migrations:
        if not src_folder.exists():
            continue
        for f in src_folder.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() in image_exts:
                target = PHOTOS_FOLDER / f.name
            else:
                target = dest_folder / f.name
            if target.exists():
                continue
            try:
                shutil.move(str(f), str(target))
                moved += 1
            except OSError:
                pass
        if not any(src_folder.iterdir()):
            try:
                src_folder.rmdir()
            except OSError:
                pass
    if moved:
        log.info(f"Migration: {moved} fichier(s) deplace(s) vers la nouvelle structure")


if __name__ == '__main__':
    _migrate_legacy_folders()

    flask_thread = threading.Thread(
        target=lambda: app.run(debug=False, host='127.0.0.1', port=PORT, threaded=True),
        daemon=True,
    )
    flask_thread.start()

    import socket
    for _ in range(50):
        try:
            with socket.create_connection(('127.0.0.1', PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    webview.create_window(
        'Big Downloader',
        f'http://localhost:{PORT}',
        width=1100,
        height=800,
        min_size=(800, 600),
    )
    webview.start()