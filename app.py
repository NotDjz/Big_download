from flask import Flask, request, jsonify, send_file, render_template
import yt_dlp
import os
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
import subprocess
import platform

app = Flask(__name__)

# Configuration
MAX_VIDEO_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB
DOWNLOAD_FOLDER = Path("downloads")
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# Dossiers par type de média
YOUTUBE_FOLDER = DOWNLOAD_FOLDER / "YouTube"
YOUTUBE_MP3_FOLDER = DOWNLOAD_FOLDER / "YouTube_MP3"
SOCIAL_FOLDER = DOWNLOAD_FOLDER / "Reseaux_Sociaux"

# Créer les sous-dossiers
YOUTUBE_FOLDER.mkdir(exist_ok=True)
YOUTUBE_MP3_FOLDER.mkdir(exist_ok=True)
SOCIAL_FOLDER.mkdir(exist_ok=True)


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
        r'^https?://(www\.)?youtube\.com/shorts/[\w-]+'
    ]

    for pattern in youtube_patterns:
        if re.match(pattern, url):
            return True

    return False


def clean_youtube_url(url):
    """Nettoie l'URL YouTube pour garder seulement l'ID de la vidéo"""
    try:
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
    except:
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


@app.route('/')
def index():
    """Page principale"""
    return render_template('index.html')


# ==================== YOUTUBE VIDEO ====================
@app.route('/download-youtube', methods=['POST'])
def download_youtube_video():
    """Télécharge une vidéo YouTube en MP4"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL manquante'}), 400

        if not validate_youtube_url(url):
            return jsonify({'error': 'Seules les URLs YouTube sont acceptées'}), 400

        url = clean_youtube_url(url)
        print(f"[YOUTUBE] URL nettoyée: {url}")

        # Configuration yt-dlp pour la MEILLEURE qualité possible
        ydl_opts = {
            # Format : meilleure vidéo + meilleur audio (jusqu'à 4K si disponible)
            # bestvideo* = meilleure résolution disponible (1080p, 1440p, 2160p/4K)
            # bestaudio* = meilleur audio disponible (jusqu'à 256kbps)
            'format': (
                'bestvideo[ext=mp4][height<=2160]+bestaudio[ext=m4a]/'  # 4K
                'bestvideo[ext=mp4][height<=1440]+bestaudio[ext=m4a]/'  # 1440p
                'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/'  # 1080p
                'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'                # Meilleur dispo
                'bestvideo+bestaudio/'                                  # N'importe quel format
                'best'                                                  # Fallback
            ),
            'outtmpl': str(YOUTUBE_FOLDER / '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',

            # Options audio pour meilleure qualité
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }, {
                'key': 'FFmpegMetadata',
            }],

            # Audio quality
            'audio_quality': 0,  # Meilleure qualité (0 = best)

            'quiet': False,
            'no_warnings': False,
            'ignoreerrors': False,
            'no_color': True,

            # Headers pour éviter les blocages
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            },

            # Retry en cas d'échec
            'fragment_retries': 10,
            'retries': 10,
        }

        print(f"[YOUTUBE] Début du téléchargement...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
                raise Exception("Impossible d'extraire les informations de la vidéo")

            filesize = info.get('filesize') or info.get('filesize_approx') or 0
            if filesize > MAX_VIDEO_SIZE:
                size_gb = filesize / (1024**3)
                max_gb = MAX_VIDEO_SIZE / (1024**3)
                return jsonify({'error': f'Vidéo trop volumineuse ({size_gb:.1f} GB). Maximum: {max_gb} GB'}), 400

            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            title = info.get('title', 'video')

            # Récupérer la résolution
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

            print(f"[YOUTUBE] Résolution: {width}x{height} ({quality}) @ {fps}fps")

            safe_title = sanitize_filename(title)
            safe_filename = str(YOUTUBE_FOLDER / f"{safe_title}.mp4")

            actual_file = Path(filename)
            if not actual_file.exists():
                for ext in ['.mp4', '.webm', '.mkv']:
                    test_file = Path(filename).with_suffix(ext)
                    if test_file.exists():
                        actual_file = test_file
                        break

            if actual_file.exists():
                actual_size = actual_file.stat().st_size
                if actual_size > MAX_VIDEO_SIZE:
                    actual_file.unlink()
                    size_gb = actual_size / (1024**3)
                    max_gb = MAX_VIDEO_SIZE / (1024**3)
                    return jsonify({'error': f'Fichier trop volumineux ({size_gb:.1f} GB)'}), 400

                if str(actual_file) != safe_filename:
                    actual_file.rename(safe_filename)
                    filename = safe_filename

        print(f"[YOUTUBE] Téléchargement terminé: {title}")
        return jsonify({
            'success': True,
            'title': title,
            'filename': os.path.basename(filename),
            'resolution': quality,
            'width': width,
            'height': height,
            'fps': fps,
            'message': f'Vidéo téléchargée : {title} ({quality})'
        })

    except Exception as e:
        print(f"[ERROR YOUTUBE] {str(e)}")
        return jsonify({'error': f'Erreur: {str(e)}'}), 500


# ==================== MP3 AUDIO ====================
@app.route('/download-mp3', methods=['POST'])
def download_mp3():
    """Télécharge l'audio en MP3 depuis diverses plateformes"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL manquante'}), 400

        platform = detect_platform(url)
        print(f"[MP3] Plateforme détectée: {platform}")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(YOUTUBE_MP3_FOLDER / '%(title)s.%(ext)s'),
            'extractaudio': True,
            'audioformat': 'mp3',
            'audioquality': '192K',
            'embed_metadata': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'ignoreerrors': True,
            'no_warnings': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }, {
                'key': 'FFmpegMetadata',
            }]
        }

        print(f"[MP3] Début du téléchargement...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
                raise Exception("Impossible d'extraire les informations")

            title = info.get('title', 'audio')
            uploader = info.get('uploader', 'Inconnu')

            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            # Chercher le fichier MP3
            mp3_file = Path(filename).with_suffix('.mp3')
            if not mp3_file.exists():
                # Chercher dans le dossier
                for file in YOUTUBE_MP3_FOLDER.glob(f"*{title}*.mp3"):
                    mp3_file = file
                    break

        print(f"[MP3] Téléchargement terminé: {title}")
        return jsonify({
            'success': True,
            'title': title,
            'uploader': uploader,
            'filename': mp3_file.name if mp3_file.exists() else 'audio.mp3',
            'message': f'Audio téléchargé : {title}'
        })

    except Exception as e:
        print(f"[ERROR MP3] {str(e)}")
        return jsonify({'error': f'Erreur: {str(e)}'}), 500


# ==================== SOCIAL MEDIA ====================
@app.route('/download-social', methods=['POST'])
def download_social():
    """Télécharge depuis les réseaux sociaux (Instagram, TikTok, X)"""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL manquante'}), 400

        platform = detect_platform(url)
        print(f"[SOCIAL] Plateforme détectée: {platform}")

        if platform not in ['instagram', 'tiktok', 'x', 'facebook']:
            return jsonify({'error': f'Plateforme {platform} non supportée pour le téléchargement social'}), 400

        ydl_opts = {
            'outtmpl': str(SOCIAL_FOLDER / f'{platform}_%(id)s.%(ext)s'),
            'format': 'best',
            'writeinfojson': False,
            'no_warnings': True,
        }

        print(f"[SOCIAL] Début du téléchargement {platform}...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
                raise Exception(f"Impossible d'extraire les informations depuis {platform}")

            title = info.get('title', f'{platform}_media')
            uploader = info.get('uploader', 'Inconnu')

            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            actual_file = Path(filename)
            if not actual_file.exists():
                # Chercher le fichier téléchargé
                for file in SOCIAL_FOLDER.glob(f"{platform}_*"):
                    if file.is_file():
                        actual_file = file
                        break

        print(f"[SOCIAL] Téléchargement terminé: {title}")
        return jsonify({
            'success': True,
            'title': title,
            'uploader': uploader,
            'platform': platform,
            'filename': actual_file.name if actual_file.exists() else f'{platform}_media',
            'message': f'Média téléchargé depuis {platform.upper()}'
        })

    except Exception as e:
        print(f"[ERROR SOCIAL] {str(e)}")
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

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
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
            })

    except Exception as e:
        print(f"[ERROR INFO] {str(e)}")
        return jsonify({'error': f'Erreur: {str(e)}'}), 500


@app.route('/downloads/<category>/<filename>')
def download_file(category, filename):
    """Permet de télécharger un fichier depuis une catégorie"""
    safe_filename = sanitize_filename(filename)

    # Déterminer le dossier selon la catégorie
    if category == 'YouTube':
        folder = YOUTUBE_FOLDER
    elif category == 'YouTube_MP3':
        folder = YOUTUBE_MP3_FOLDER
    elif category == 'Reseaux_Sociaux':
        folder = SOCIAL_FOLDER
    else:
        return jsonify({'error': 'Catégorie invalide'}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({'error': 'Accès refusé'}), 403

    if file_path.exists():
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'Fichier non trouvé'}), 404


@app.route('/list-downloads')
def list_downloads():
    """Liste tous les fichiers téléchargés avec leurs catégories"""
    files = []

    # YouTube Vidéos
    for file in YOUTUBE_FOLDER.iterdir():
        if file.is_file():
            files.append({
                'name': file.name,
                'size': file.stat().st_size,
                'category': 'YouTube Vidéo',
                'url': f'/downloads/YouTube/{file.name}'
            })

    # YouTube MP3
    for file in YOUTUBE_MP3_FOLDER.iterdir():
        if file.is_file():
            files.append({
                'name': file.name,
                'size': file.stat().st_size,
                'category': 'YouTube MP3',
                'url': f'/downloads/YouTube_MP3/{file.name}'
            })

    # Réseaux Sociaux
    for file in SOCIAL_FOLDER.iterdir():
        if file.is_file():
            files.append({
                'name': file.name,
                'size': file.stat().st_size,
                'category': 'Réseaux Sociaux',
                'url': f'/downloads/Reseaux_Sociaux/{file.name}'
            })

    return jsonify(files)


if __name__ == '__main__':
    print("""
██████╗ ██╗ ██████╗     ██████╗  ██████╗ ██╗    ██╗███╗   ██╗██╗      ██████╗  █████╗ ██████╗
██╔══██╗██║██╔════╝     ██╔══██╗██╔═══██╗██║    ██║████╗  ██║██║     ██╔═══██╗██╔══██╗██╔══██╗
██████╔╝██║██║  ███╗    ██║  ██║██║   ██║██║ █╗ ██║██╔██╗ ██║██║     ██║   ██║███████║██║  ██║
██╔══██╗██║██║   ██║    ██║  ██║██║   ██║██║███╗██║██║╚██╗██║██║     ██║   ██║██╔══██║██║  ██║
██████╔╝██║╚██████╔╝    ██████╔╝╚██████╔╝╚███╔███╔╝██║ ╚████║███████╗╚██████╔╝██║  ██║██████╔╝
╚═════╝ ╚═╝ ╚═════╝     ╚═════╝  ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═════╝
""")
    print("=" * 80)
    print("BIG DOWNLOADER - Téléchargeur Universel")
    print("=" * 80)
    print("\n✨ FONCTIONNALITÉS :")
    print("  📹 YouTube Vidéo (MP4 jusqu'à 4K)")
    print("  🎵 MP3 Audio (YouTube, SoundCloud, etc.)")
    print("  📱 Réseaux Sociaux (Instagram, TikTok, X/Twitter)")
    print("\n🔒 SÉCURITÉ :")
    print("  ✅ Accès localhost uniquement")
    print("  ✅ Taille max : 5 GB")
    print("  ✅ Noms de fichiers sécurisés")
    print("\n🌐 Ouvrez votre navigateur à l'adresse :")
    print("   http://localhost:5555")
    print("\n⌨️  Appuyez sur Ctrl+C pour arrêter le serveur")
    print("=" * 80)

    # Détection Docker : écouter sur 0.0.0.0 si dans Docker, sinon 127.0.0.1
    host = '0.0.0.0' if os.environ.get('IN_DOCKER') else '127.0.0.1'
    app.run(debug=False, host=host, port=5555)