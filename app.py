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
from PIL import Image
import shutil
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


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({'error': 'Fichier trop volumineux (max 2 GB)'}), 413


# Logging structuré
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('bigdl')

# Suivi de progression des telechargements (download_id -> {queue, thread, start_time})
download_progress = {}

# Configuration
MAX_VIDEO_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB
DOWNLOAD_TIMEOUT = 30 * 60  # 30 minutes max par téléchargement
MAX_DOWNLOADS_PER_MINUTE = 10
DOWNLOAD_FOLDER = BASE_DIR / "downloads"

if getattr(sys, 'frozen', False) and platform.system() == 'Windows':
    FFMPEG_PATH = str(BUNDLE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BUNDLE_DIR / "ffprobe.exe")
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

# Compatibilité anciens dossiers (pour lister les fichiers existants)
LEGACY_FOLDERS = [
    DOWNLOAD_FOLDER / "YouTube",
    DOWNLOAD_FOLDER / "YouTube_MP3",
    DOWNLOAD_FOLDER / "Reseaux_Sociaux",
]


def _next_versioned_name(folder, stem, ext):
    """Trouve le prochain nom disponible: stem_v2.ext, stem_v3.ext, etc."""
    version = 2
    while True:
        name = f"{stem}_v{version}{ext}"
        if not (folder / name).exists():
            return name
        version += 1


def sanitize_filename(filename):
    """Nettoie le nom de fichier pour éviter les path traversal attacks"""
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.replace('..', '_')
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
    if download_type == 'mp3':
        return MUSIC_FOLDER
    elif download_type == 'social':
        return VIDEOS_FOLDER
    else:
        return VIDEOS_FOLDER


def _get_output_template(download_type, url=''):
    """Retourne le template de nom de fichier"""
    folder = _get_output_folder(download_type)
    if download_type == 'social':
        plat = detect_platform(url)
        return str(folder / f'{plat}_%(id)s.%(ext)s')
    return str(folder / '%(title)s.%(ext)s')


def _make_progress_hook(q, current_video=None, total_videos=None):
    """Cree un progress hook pour yt-dlp"""
    def progress_hook(d):
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


def _build_ydl_opts(download_type, url, quality, progress_hook):
    """Construit les options yt-dlp"""
    ydl_opts = {
        'format': _get_format_string(download_type, quality),
        'outtmpl': _get_output_template(download_type, url),
        'quiet': True,
        'no_warnings': True,
        'no_color': True,
        'progress_hooks': [progress_hook],
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

    q.put_nowait({'status': 'processing', 'message': 'Telechargement image Instagram...'})

    cookies_file = _get_cookies_file()
    if not cookies_file:
        q.put_nowait({
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
        q.put_nowait({'status': 'error', 'message': 'URL Instagram invalide'})
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
        q.put_nowait({'status': 'error', 'message': f'Erreur API Instagram: {e}'})
        return

    if resp.status_code != 200:
        q.put_nowait({'status': 'error', 'message': f'API Instagram erreur {resp.status_code}. Cookies peut-etre expires.'})
        return

    data = resp.json()
    items = data.get('items', [])
    if not items:
        q.put_nowait({'status': 'error', 'message': 'Post Instagram vide ou inaccessible'})
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
        q.put_nowait({'status': 'error', 'message': 'Aucune image trouvee dans le post'})
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
            final_path = _convert_to_jpg(temp_filepath)
            downloaded += 1
            q.put_nowait({
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
        q.put_nowait({
            'status': 'complete',
            'title': title,
            'filename': f'instagram_{shortcode}.jpg',
        })
    else:
        q.put_nowait({'status': 'error', 'message': 'Echec telechargement. Cookies expires ou acces refuse.'})


def _cleanup_partial_files(folder, pattern='*.part'):
    """Supprime les fichiers partiels (.part, .ytdl) dans un dossier"""
    for ext in ('*.part', '*.ytdl', '*.temp'):
        for f in folder.glob(ext):
            try:
                f.unlink()
            except OSError:
                pass


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

    try:
        if download_type == 'playlist':
            _run_playlist_download(q, url, quality)
        else:
            hook = _make_progress_hook(q)
            ydl_opts = _build_ydl_opts(download_type, url, quality, hook)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if info is None:
                    q.put_nowait({'status': 'error', 'message': "Impossible d'extraire les informations"})
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

                q.put_nowait(result)

    except Exception as e:
        error_msg = str(e)
        log.error(f"Download failed [{download_type}] {url}: {error_msg}")
        if detect_platform(url) == 'instagram':
            try:
                _download_instagram_images(q, url)
            except Exception as img_err:
                q.put_nowait({'status': 'error', 'message': f'Video: {error_msg} | Image: {str(img_err)}'})
        else:
            q.put_nowait({'status': 'error', 'message': error_msg})
    finally:
        _cleanup_partial_files(_get_output_folder(download_type))


def _run_playlist_download(q, url, quality=None):
    """Telecharge une playlist video par video avec suivi"""
    # D'abord recuperer la liste des videos
    flat_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        playlist_info = ydl.extract_info(url, download=False)

    if not playlist_info:
        q.put_nowait({'status': 'error', 'message': "Impossible de lire la playlist"})
        return

    entries = [e for e in playlist_info.get('entries', []) if e]
    total = len(entries)
    playlist_title = playlist_info.get('title', 'Playlist')

    if total == 0:
        q.put_nowait({'status': 'error', 'message': "Playlist vide"})
        return

    q.put_nowait({
        'status': 'playlist_start',
        'title': playlist_title,
        'total_videos': total,
    })

    for i, entry in enumerate(entries, 1):
        video_url = entry.get('url') or entry.get('id')
        if not video_url:
            continue

        if not video_url.startswith('http'):
            video_url = f'https://www.youtube.com/watch?v={video_url}'

        q.put_nowait({
            'status': 'playlist_video_start',
            'current_video': i,
            'total_videos': total,
            'video_title': entry.get('title', f'Video {i}'),
        })

        hook = _make_progress_hook(q, current_video=i, total_videos=total)
        ydl_opts = _build_ydl_opts('youtube', video_url, quality, hook)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(video_url, download=True)
        except Exception as e:
            q.put_nowait({
                'status': 'playlist_video_error',
                'current_video': i,
                'total_videos': total,
                'message': str(e),
            })

    q.put_nowait({
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

        if download_type in ('youtube', 'playlist') and not validate_youtube_url(url):
            return jsonify({'error': 'URL YouTube invalide'}), 400

        if download_type in ('youtube', 'playlist'):
            url = clean_youtube_url(url)

        if download_type == 'social':
            plat = detect_platform(url)
            if plat not in ('instagram', 'tiktok', 'x', 'facebook'):
                return jsonify({'error': f'Plateforme {plat} non supportee'}), 400

        download_id = str(uuid.uuid4())
        q = queue.Queue(maxsize=100)
        download_progress[download_id] = {
            'queue': q,
            'start_time': time.time(),
        }

        thread = threading.Thread(
            target=_run_download,
            args=(download_id, url, download_type, quality),
            daemon=True,
        )
        thread.start()
        download_progress[download_id]['thread'] = thread

        log.info(f"Download started [{download_type}] {url}")
        return jsonify({'download_id': download_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/progress/<download_id>')
def progress_stream(download_id):
    """Endpoint SSE pour le suivi de progression"""
    def generate():
        entry = download_progress.get(download_id)
        if not entry:
            yield f'data: {json.dumps({"status": "error", "message": "Telechargement inconnu"})}\n\n'
            return
        q = entry['queue']
        start_time = entry['start_time']
        while True:
            if time.time() - start_time > DOWNLOAD_TIMEOUT:
                yield f'data: {json.dumps({"status": "error", "message": "Timeout: telechargement trop long (30 min max)"})}\n\n'
                break
            try:
                event = q.get(timeout=10)
                yield f'data: {json.dumps(event)}\n\n'
                if event.get('status') in ('complete', 'error'):
                    break
            except queue.Empty:
                yield f'data: {json.dumps({"status": "heartbeat"})}\n\n'
        download_progress.pop(download_id, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


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
        available_qualities = []
        formats = info.get('formats', [])
        heights_seen = set()
        for f in formats:
            h = f.get('height')
            if h and f.get('vcodec', 'none') != 'none' and h not in heights_seen:
                heights_seen.add(h)
        available_qualities = sorted(heights_seen, reverse=True)

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


@app.route('/cut-video', methods=['POST'])
def cut_video():
    """Decoupe une video/audio entre deux timestamps avec FFmpeg"""
    data = request.get_json()
    category = data.get('category')
    filename = data.get('filename')
    start = data.get('start', 0)
    end = data.get('end')

    if not category or not filename or end is None:
        return jsonify({'error': 'Parametres manquants'}), 400

    if end <= start:
        return jsonify({'error': 'Le temps de fin doit etre apres le debut'}), 400

    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Categorie invalide'}), 400

    file_path = (folder / safe_filename).resolve()
    if not str(file_path).startswith(str(folder.resolve())):
        return jsonify({'error': 'Acces refuse'}), 403
    if not file_path.exists():
        return jsonify({'error': 'Fichier non trouve'}), 404

    stem = file_path.stem
    ext = file_path.suffix
    out_name = _next_versioned_name(folder, stem, ext)
    out_path = folder / out_name

    try:
        cmd = [
            FFMPEG_PATH, '-y',
            '-i', str(file_path),
            '-ss', str(start),
            '-to', str(end),
            '-avoid_negative_ts', 'make_zero',
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            if out_path.exists():
                out_path.unlink()
            log.error(f"FFmpeg cut failed: {result.stderr[-500:]}")
            return jsonify({'error': 'Erreur FFmpeg lors de la decoupe'}), 500

        log.info(f"Video cut: {safe_filename} -> {out_name}")
        return jsonify({
            'success': True,
            'filename': out_path.name,
            'size': out_path.stat().st_size,
            'message': f'Decoupe terminee: {out_path.name}',
        })
    except subprocess.TimeoutExpired:
        if out_path.exists():
            out_path.unlink()
        return jsonify({'error': 'Timeout: decoupe trop longue'}), 500
    except FileNotFoundError:
        return jsonify({'error': 'FFmpeg non trouve. Verifiez qu\'il est installe et dans le PATH.'}), 500
    except Exception as e:
        if out_path.exists():
            out_path.unlink()
        log.error(f"Cut error: {e}")
        return jsonify({'error': str(e)}), 500


TEMP_FOLDER = BASE_DIR / "temp_uploads"
TEMP_FOLDER.mkdir(exist_ok=True)


@app.route('/upload-for-cut', methods=['POST'])
def upload_for_cut():
    """Upload un fichier pour le pre-visualiser avant decoupe"""
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
    dest_folder = MUSIC_FOLDER if ext in audio_exts else VIDEOS_FOLDER
    out_name = _next_versioned_name(dest_folder, stem, ext)
    out_path = dest_folder / out_name

    try:
        cmd = [
            FFMPEG_PATH, '-y',
            '-i', str(temp_path),
            '-ss', str(start),
            '-to', str(end),
            '-avoid_negative_ts', 'make_zero',
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            if out_path.exists():
                out_path.unlink()
            log.error(f"FFmpeg cut failed: {result.stderr[-500:]}")
            return jsonify({'error': 'Erreur FFmpeg lors de la decoupe'}), 500

        temp_path.unlink(missing_ok=True)

        log.info(f"Cut upload: {out_name} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return jsonify({
            'success': True,
            'filename': out_name,
            'size': out_path.stat().st_size,
            'download_url': f'/downloads/{dest_folder.name}/{out_name}',
            'message': f'Decoupe terminee: {out_name}',
        })
    except subprocess.TimeoutExpired:
        if out_path.exists():
            out_path.unlink()
        temp_path.unlink(missing_ok=True)
        return jsonify({'error': 'Timeout: decoupe trop longue'}), 500
    except FileNotFoundError:
        temp_path.unlink(missing_ok=True)
        return jsonify({'error': 'FFmpeg non trouve'}), 500
    except Exception as e:
        if out_path.exists():
            out_path.unlink()
        temp_path.unlink(missing_ok=True)
        log.error(f"Cut upload error: {e}")
        return jsonify({'error': str(e)}), 500


def _get_media_duration(filepath):
    """Obtient la duree d'un fichier media via ffprobe"""
    try:
        cmd = [
            FFPROBE_PATH, '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            str(filepath),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get('format', {}).get('duration', 0))
    except Exception:
        pass
    return 0


@app.route('/open-folder')
def open_folder():
    """Ouvre le dossier de telechargements dans l'explorateur"""
    folder = str(DOWNLOAD_FOLDER.resolve())
    try:
        if platform.system() == 'Windows':
            os.startfile(folder)
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', str(folder)])
        else:
            subprocess.Popen(['xdg-open', str(folder)])
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
        target=lambda: app.run(debug=False, host='127.0.0.1', port=5555, threaded=True),
        daemon=True,
    )
    flask_thread.start()

    import socket
    for _ in range(50):
        try:
            with socket.create_connection(('127.0.0.1', 5555), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    webview.create_window(
        'Big Downloader',
        'http://localhost:5555',
        width=1100,
        height=800,
        min_size=(800, 600),
    )
    webview.start()