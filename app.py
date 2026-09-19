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

# Windows reads MIME types from the registry, where .js can come back as
# text/plain and .woff2 is unknown entirely. With X-Content-Type-Options:
# nosniff the browser would then refuse the script and the interface would sit
# there dead. Pin the types we depend on.
for _ext, _mime in (('.woff2', 'font/woff2'), ('.js', 'text/javascript'),
                    ('.css', 'text/css'), ('.png', 'image/png')):
    mimetypes.add_type(_mime, _ext)

# An absolute path rather than the bare name: CreateProcess searches the
# image's own directory first, and that is the very directory where the
# portable exe writes downloads/, temp_uploads/ and cookies.txt. An explorer.exe
# dropped there would run in our place. Planting it already requires code
# execution under the same identity, so this is not a hole being closed, just
# two lines of hardening.
EXPLORER = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'explorer.exe')


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({'error': 'File too large (2 GB max)'}), 413


# The server listens on loopback, but any web page open on the machine can
# reach it. Checking Host blocks DNS rebinding (an attacker domain repointed at
# 127.0.0.1 would otherwise become same-origin and could list, exfiltrate and
# delete the downloads); checking Origin blocks multipart POSTs and the
# side-effecting GETs a third-party page could fire.
PORT = 5555
ALLOWED_HOSTS = frozenset(f'{h}:{PORT}' for h in ('127.0.0.1', 'localhost'))
ALLOWED_ORIGINS = frozenset(f'http://{h}' for h in ALLOWED_HOSTS)


# 'none' is a top-level navigation: it is what the pywebview window sends when
# it opens the app. Excluding it would lock the application out of itself.
ALLOWED_FETCH_SITES = frozenset(('same-origin', 'none'))


@app.before_request
def _reject_foreign_origin():
    if request.host not in ALLOWED_HOSTS:
        return jsonify({'error': 'Host not allowed'}), 403
    origin = request.headers.get('Origin')
    if origin is not None:
        if origin not in ALLOWED_ORIGINS:
            return jsonify({'error': 'Origin not allowed'}), 403
        return None
    # With no Origin there is nothing to conclude from: browsers omit it on a
    # no-cors GET, so a <video src="http://127.0.0.1:5555/stream/..."> on a
    # third-party page walked through the guard and served as an oracle on which
    # files had been downloaded. Sec-Fetch-Site, on the other hand, is always
    # sent.
    site = request.headers.get('Sec-Fetch-Site')
    if site is not None and site not in ALLOWED_FETCH_SITES:
        return jsonify({'error': 'Origin not allowed'}), 403
    return None


@app.after_request
def _security_headers(resp):
    # frame-ancestors closes clickjacking: framed inside a third-party page,
    # the app fired its own deletions same-origin, two clicks in.
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers.setdefault('Content-Security-Policy', '; '.join((
        "default-src 'self'",
        "img-src 'self' https: data:",  # thumbnails served by the platforms
        "media-src 'self'",
        "frame-ancestors 'none'",
    )))
    return resp


# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('bigdl')

# Download progress registry (download_id -> {queue, start_time, deadline, cancelled})
download_progress = {}
# Cut progress registry (cut_id -> {queue, deadline, cancelled, proc})
cut_progress = {}

# Configuration
MAX_VIDEO_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB
DOWNLOAD_TIMEOUT = 30 * 60  # 30 minutes per download
MAX_DOWNLOADS_PER_MINUTE = 10
CUT_TIMEOUT = 10 * 60  # 10 minutes per cut
SSE_GRACE = 60  # slack for the worker to abort before the SSE stream gives up
TIMEOUT_MSG = f'Timeout: download took too long ({DOWNLOAD_TIMEOUT // 60} min max)'
# Same reason as TIMEOUT_MSG: the message travels to the client, so it must not
# exist as two literals that drift apart the first time one is reworded.
CANCEL_MSG = 'Download stopped'
# How many videos /get-info enumerates to describe a playlist. It shows none of
# them: the count comes from playlist_count, which the page states without
# anyone having to page through the list. This cap exists only as a fallback
# when that field is missing, and above all it keeps a synchronous route from
# paging to the end. Measured: 1593 videos in 7.3 s unbounded, 1.0 s with it.
PLAYLIST_SCAN = 50


# The terminal states: _put_final publishes them and _sse_response stops on
# them. This was written out twice, and 'cancelled' had been added to only one
# of the two places.
TERMINAL_STATUSES = ('complete', 'error', 'cancelled')


class DownloadAborted(Exception):
    """The job must stop. `status` and `message` are what _put_final publishes.

    One class, with the reason carried as data rather than as a subtype. The two
    reasons that exist today, deadline exceeded and user-requested stop, differ
    only in those two fields; a hierarchy separating them forced one except per
    subtype, plus a third laid down "just in case" that no raise could reach.
    Here a future reason adds nothing: it passes a status and a message.

    One class is also what the two loops that re-raise it want (playlist,
    Instagram photos): each sits just above an except Exception that swallows
    and continues, and an incomplete tuple of subtypes would send them off
    again in silence.
    """

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message
DOWNLOAD_FOLDER = BASE_DIR / "downloads"

if getattr(sys, 'frozen', False) and platform.system() == 'Windows':
    FFMPEG_PATH = str(BUNDLE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BUNDLE_DIR / "ffprobe.exe")
    FFMPEG_DIR = BUNDLE_DIR
elif (BASE_DIR / "ffmpeg.exe").exists():
    FFMPEG_PATH = str(BASE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BASE_DIR / "ffprobe.exe")
    FFMPEG_DIR = BASE_DIR
else:
    FFMPEG_PATH = "ffmpeg"
    FFPROBE_PATH = "ffprobe"
    FFMPEG_DIR = None  # FFmpeg comes from the system PATH
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# Rate limiting
_request_times = []
_rate_lock = threading.Lock()

# One folder per media type
VIDEOS_FOLDER = DOWNLOAD_FOLDER / "Videos"
MUSIC_FOLDER = DOWNLOAD_FOLDER / "Music"
PHOTOS_FOLDER = DOWNLOAD_FOLDER / "Photos"
# Scratch space for downloads: one subfolder per job, destroyed when it ends.
# On the same volume as the output folders so that yt-dlp's final move stays a
# rename rather than a copy.
WORK_FOLDER = BASE_DIR / ".work"

VIDEOS_FOLDER.mkdir(exist_ok=True)
MUSIC_FOLDER.mkdir(exist_ok=True)
PHOTOS_FOLDER.mkdir(exist_ok=True)
WORK_FOLDER.mkdir(exist_ok=True)

def _next_versioned_name(folder, stem, ext):
    """Next free name: stem_v2.ext, stem_v3.ext, and so on."""
    version = 2
    while True:
        name = f"{stem}_v{version}{ext}"
        if not (folder / name).exists():
            return name
        version += 1


def sanitize_filename(filename):
    """Scrub a filename so it cannot escape its folder.

    Replacing the separators is enough to prevent any escape: stripped of a
    separator, '..' no longer names a parent. So the inner dots of a legitimate
    name are left alone ('Wait... What.mp4'), which otherwise stopped matching
    the file yt-dlp had written and returned 404 on playback, deletion and
    reveal-in-explorer alike.
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
    """True if the URL is a YouTube URL we know how to handle."""
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
    """True if the URL names a whole playlist, not one video seen from inside one.

    'youtu.be/<id>?list=<pl>' and 'watch?v=<id>&list=<pl>' are the share links
    for ONE video viewed from a playlist: treating them as playlists downloaded
    the entire album instead of the track that was asked for.
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'v' in params:
            return False
        # youtu.be/<id> carries the id in the path
        if parsed.netloc.endswith('youtu.be') and parsed.path.strip('/'):
            return False
        # Equality and not a substring test: '/@channel/playlists' is a
        # channel's playlist index, not a downloadable playlist.
        if parsed.path.rstrip('/') == '/playlist':
            return True
        return 'list' in params
    except Exception:
        return False


def clean_youtube_url(url):
    """Reduce a YouTube URL to the video id it names."""
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
    """Identify the platform from the URL."""
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
    """The yt-dlp format string for a given download type."""
    if download_type == 'mp3':
        return 'bestaudio/best'
    elif download_type == 'social':
        # A bare 'best' ignored the chosen quality: picking 480p still
        # downloaded the heaviest stream on offer.
        if quality:
            # Progressive streams only: merge_output_format and the MP4
            # converter are set for youtube/playlist alone, so a
            # bestvideo+bestaudio here would produce a .mkv/.webm the player
            # cannot read.
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
    """The output folder for a given download type."""
    return MUSIC_FOLDER if download_type == 'mp3' else VIDEOS_FOLDER


def _get_output_template(download_type, url):
    """The output name template, deliberately *relative*.

    Relative and not absolute: yt-dlp silently ignores the 'paths' option when
    outtmpl is an absolute path (measured, with an absolute outtmpl the temp
    directory falls back to the final folder). The folder therefore comes from
    paths['home'], set by _build_ydl_opts.
    """
    if download_type == 'social':
        return f'{detect_platform(url)}_%(id)s.%(ext)s'
    return '%(title)s.%(ext)s'


def _make_progress_hook(q, current_video=None, total_videos=None):
    """Build a yt-dlp progress hook."""
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
                msg = 'Merging and converting...'
                if current_video is not None:
                    msg = f'Video {current_video}/{total_videos} - {msg}'
                q.put_nowait({'status': 'processing', 'message': msg})
        except queue.Full:
            pass
    return progress_hook


def _make_abort_hook(entry):
    """The stop guard, installed on both families of hooks.

    Raising from a hook is the only way to interrupt yt-dlp, which runs in this
    process: no outside signal can stop it, and Python cannot kill a thread.
    The check used to live in the SSE generator, where it stopped only the
    reporting while the thread kept downloading. Progress hooks go silent
    during a merge or an mp3 extraction, hence installing it on the
    postprocessor hooks as well.

    Two reasons to stop, one mechanism: the deadline passing and the user
    asking. Both are re-read from the registry entry on every call, so the
    playlist loop can push the deadline forward and the cancel route can raise
    the flag without the worker needing to be notified.
    """
    def abort_hook(d):
        if entry.get('cancelled'):
            raise DownloadAborted('cancelled', CANCEL_MSG)
        if time.time() > entry['deadline']:
            raise DownloadAborted('error', TIMEOUT_MSG)
    return abort_hook


def _build_ydl_opts(download_type, url, quality, progress_hook, entry, job_tmp):
    """Assemble the yt-dlp options for one download."""
    abort = _make_abort_hook(entry)
    ydl_opts = {
        'format': _get_format_string(download_type, quality),
        'outtmpl': _get_output_template(download_type, url),
        # Intermediate files go into a directory belonging to this job
        # alone: no more tracking our own .part files, and no more guessing by
        # age which ones belong to some other download.
        'paths': {'home': str(_get_output_folder(download_type)), 'temp': str(job_tmp)},
        'quiet': True,
        'no_warnings': True,
        'no_color': True,
        # Always a single video here, including when _run_playlist_download
        # calls this function once per video with each one's own URL.
        # The type that makes this option load-bearing is 'mp3': start_download
        # only routes 'youtube' and 'playlist' through clean_youtube_url, which
        # is what strips the '&list='. A YouTube radio URL asked for as MP3
        # therefore arrives intact, and without this line yt-dlp set off to
        # extract the entire radio, which has no end.
        'noplaylist': True,
        # One guard, installed on both families: that is the point of
        # _make_abort_hook's docstring, and the code should show it too.
        'progress_hooks': [abort, progress_hook],
        'postprocessor_hooks': [abort],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        },
        'fragment_retries': 10,
        'retries': 10,
    }

    # Set as soon as the cascade knows where FFmpeg is, not only when frozen:
    # in dev, with ffmpeg.exe sitting beside the project, yt-dlp could not find
    # it and refused every video+audio merge, which meant refusing all of 4K.
    if FFMPEG_DIR:
        ydl_opts['ffmpeg_location'] = str(FFMPEG_DIR)

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
    """Path to the cookies file, if one is there."""
    base = BASE_DIR
    for name in ('cookies.txt', 'www.instagram.com_cookies.txt'):
        path = base / name
        if path.exists():
            return str(path)
    return None


def _convert_to_jpg(filepath):
    """Convert an image (webp, png, and so on) to JPG."""
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
    """Turn an Instagram shortcode into its numeric media id."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + alphabet.index(char)
    return str(media_id)


def _download_instagram_images(q, url, entry):
    """Download an Instagram post's images through the private API, with cookies."""
    import http.cookiejar

    _put_progress(q, {'status': 'processing', 'message': 'Downloading Instagram image...'})

    cookies_file = _get_cookies_file()
    if not cookies_file:
        _put_final(q, {
            'status': 'error',
            'message': 'Instagram photos need a cookies.txt file (export it from your browser with the "Get cookies.txt LOCALLY" extension)'
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
        _put_final(q, {'status': 'error', 'message': 'Invalid Instagram URL'})
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
        _put_final(q, {'status': 'error', 'message': f'Instagram API error: {e}'})
        return

    if resp.status_code != 200:
        _put_final(q, {'status': 'error', 'message': f'Instagram API returned {resp.status_code}. The cookies may have expired.'})
        return

    data = resp.json()
    items = data.get('items', [])
    if not items:
        _put_final(q, {'status': 'error', 'message': 'Instagram post is empty or unreachable'})
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

    # A video post carries image_versions2 too, holding its thumbnail: without
    # this check we saved the cover image and reported a completed download to
    # someone who had asked for a video.
    if item.get('video_versions') or any(m.get('video_versions') for m in (item.get('carousel_media') or [])):
        _put_final(q, {'status': 'error',
                       'message': 'This post contains a video: pick the video format.'})
        return

    if not image_urls:
        _put_final(q, {'status': 'error', 'message': 'No image found in that post'})
        return

    downloaded = 0
    total = len(image_urls)

    for i, img_url in enumerate(image_urls, 1):
        # At the top of the loop and outside the try. At the end of the body,
        # the `continue` taken when a conversion fails skipped this check
        # entirely, and one more image means up to 30 s of waiting before the
        # stop is noticed.
        if entry.get('cancelled'):
            raise DownloadAborted('cancelled', CANCEL_MSG)
        try:
            img_resp = session.get(img_url, timeout=30)
            img_resp.raise_for_status()
            suffix = f'_{i}' if total > 1 else ''
            temp_filename = f'instagram_{shortcode}{suffix}.tmp'
            temp_filepath = PHOTOS_FOLDER / sanitize_filename(temp_filename)
            temp_filepath.write_bytes(img_resp.content)
            converted = _convert_to_jpg(temp_filepath)
            # Judge on the file, not on the path returned: _convert_to_jpg
            # also returns the original path when the save succeeded but
            # deleting the .tmp did not (antivirus, indexer). Since .tmp files
            # are filtered out of the listing, counting a real failure
            # announced an invisible success, and counting a real success as a
            # failure announced an error while the JPG was sitting right
            # there.
            if not converted.with_suffix('.jpg').exists():
                temp_filepath.unlink(missing_ok=True)
                continue
            downloaded += 1
            _put_progress(q, {
                'status': 'downloading',
                'percent': round(i / total * 100, 1),
                'speed': '',
                'eta': '',
            })
        except DownloadAborted:
            # A net, not the nominal path: the check is at the top of the
            # loop, outside the try. It stays because the except Exception just
            # below swallows everything, including a stop that a helper called
            # from here might raise. That is exactly how cancellation went
            # inert once, with the job publishing 'complete' regardless.
            raise
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
        _put_final(q, {'status': 'error', 'message': 'Download failed. Cookies expired, or access denied.'})


def _kill_quietly(proc):
    """Kill a process quietly: it may have finished on its own meanwhile."""
    if not proc:
        return
    try:
        proc.kill()
    except OSError:
        pass


def _put_progress(q, event):
    """Publish a progress event, dropping it if the queue is full.

    A bare put propagated into the generic handler and aborted the whole
    playlist with an empty message, since str(queue.Full()) is ''.
    """
    try:
        q.put_nowait(event)
    except queue.Full:
        pass


def _put_final(q, event):
    """Publish a terminal event, even when the queue is full.

    put_nowait raises queue.Full when the client is not draining; the exception
    propagated into the generic error handler and, for Instagram, wrongly
    triggered the photo fallback.
    """
    try:
        q.put_nowait(event)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()  # single producer: freeing one slot is enough
    except queue.Empty:
        pass
    try:
        q.put_nowait(event)
    except queue.Full:
        log.warning('Terminal event not delivered (queue saturated)')


def _sweep_old_files(folder, max_age):
    """Delete entries in a folder older than max_age.

    Files and directories alike: download threads are daemons, so closing the
    window mid-download kills the worker without running its finally and leaves
    its work directory behind.

    The age guard is the subtle part: these folders are shared, and an
    unconditional sweep erased the .part of a download still in flight, making
    it fail.
    """
    cutoff = time.time() - max_age
    for f in folder.glob('*'):
        try:
            st = f.stat()
            if st.st_mtime >= cutoff:
                continue
            if stat.S_ISDIR(st.st_mode):
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()
        except OSError:
            pass


def _check_filesize(file_path):
    """Check a downloaded file's size, deleting it if it exceeds the limit."""
    if file_path.exists():
        size = file_path.stat().st_size
        if size > MAX_VIDEO_SIZE:
            file_path.unlink()
            size_gb = size / (1024**3)
            max_gb = MAX_VIDEO_SIZE / (1024**3)
            raise Exception(f'File too large ({size_gb:.1f} GB). Maximum: {max_gb:.0f} GB')


def _explain_error(msg):
    """Attach the likely cause to a 403, which never states one.

    The exe bundles a frozen yt-dlp and cannot update it: when a platform
    tightens its protections, everything fails with a 403 and nothing hints
    that a newer version is what is needed.
    """
    if 'HTTP Error 403' not in msg:
        return msg
    # The advice differs by audience: the exe bundles yt-dlp and cannot update
    # it, while dev mode can. Kept short, because the banner is 268 px wide and
    # disappears after six seconds.
    if getattr(sys, 'frozen', False):
        return msg + ' - yt-dlp may be too old: install the latest BIG DL.'
    return msg + ' - yt-dlp may be too old: pip install --upgrade yt-dlp.'


def _run_download(download_id, url, download_type, quality=None):
    """Run the download on a worker thread, reporting through progress hooks."""
    entry = download_progress.get(download_id)
    if not entry:
        return
    q = entry['queue']

    # Private scratch space, on the same volume as the output folders so the
    # final move stays a rename.
    job_tmp = WORK_FOLDER / download_id

    try:
        if download_type != 'photo':
            # Photos go straight into PHOTOS_FOLDER and never see this
            # directory: creating it would be two disk operations for nothing.
            job_tmp.mkdir(parents=True, exist_ok=True)

        if download_type == 'photo':
            # Chosen upfront rather than inferred from a failure: the
            # exception-based fallback also fired on an expired cookie, a
            # timeout or a dropped connection during an Instagram *video*, and
            # then returned a message describing an operation nobody asked
            # for.
            _download_instagram_images(q, url, entry)
        elif download_type == 'playlist':
            _run_playlist_download(q, url, quality, entry, job_tmp)
        else:
            hook = _make_progress_hook(q)
            ydl_opts = _build_ydl_opts(download_type, url, quality, hook, entry, job_tmp)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if info is None:
                    _put_final(q, {'status': 'error', 'message': "Could not extract the information"})
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

    except DownloadAborted as e:
        # One except, because the exception already carries what to publish.
        # This was three branches, one of which no raise in the file could
        # reach.
        log.warning(f"Download aborted [{e.status}] {url}: {e.message}")
        _put_final(q, {'status': e.status, 'message': e.message})
    except Exception as e:
        error_msg = _explain_error(str(e))
        log.error(f"Download failed [{download_type}] {url}: {error_msg}")
        # No more automatic fallback to images. For a video post the
        # Instagram API returns the thumbnail anyway, so the fallback
        # "succeeded" by handing over a cover JPG announced as a completed
        # download. Photos have their own download type now.
        _put_final(q, {'status': 'error', 'message': error_msg})
    finally:
        # One gesture, covering failure as well as success: the directory
        # belongs to this job alone, so nothing else can be inside it.
        shutil.rmtree(job_tmp, ignore_errors=True)


def _flat_ydl_opts():
    """Options for the flat extractions: the ones that enumerate a playlist.

    The same dict was written out twice, 250 lines apart. Any option added
    later, a timeout or cookies or retries, would have landed on only one of
    them, and the divergence would have shown up only in use.

    A function and not a module constant: a shared dict ends up mutated.

    Contrary to what this comment first claimed, slipping 'noplaylist' in here
    would break nothing: checked against the yt-dlp 2026.08.19 source,
    _yes_playlist() opens with `if not playlist_id or not video_id: return not
    video_id` and so returns before it ever reads the option. Both callers only
    ever see URLs with no `v` (is_playlist_url() requires that, and
    clean_youtube_url() rewrites to `playlist?list=`). The option simply has no
    business here.
    """
    return {'quiet': True, 'no_warnings': True, 'extract_flat': True}


def _run_playlist_download(q, url, quality, entry, job_tmp):
    """Download a playlist one video at a time, reporting as it goes."""
    # Get the list of videos first
    flat_opts = _flat_ydl_opts()
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        playlist_info = ydl.extract_info(url, download=False)

    if not playlist_info:
        _put_final(q, {'status': 'error', 'message': "Could not read the playlist"})
        return

    entries = [e for e in playlist_info.get('entries', []) if e]
    total = len(entries)
    playlist_title = playlist_info.get('title', 'Playlist')

    if total == 0:
        _put_final(q, {'status': 'error', 'message': "Playlist is empty"})
        return

    _put_progress(q, {
        'status': 'playlist_start',
        'title': playlist_title,
        'total_videos': total,
    })

    # 'item' and not 'entry': that name shadowed the parameter holding the
    # registry entry, so the deadline was written into yt-dlp's video dict while
    # the SSE net kept reading a value that was never pushed forward.
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

        # The deadline is pushed forward per video: a global 30 min budget
        # would abandon a long playlist partway through, which is a legitimate
        # use. The SSE net reads the same value, so the two layers can no
        # longer contradict each other.
        entry['deadline'] = time.time() + DOWNLOAD_TIMEOUT
        hook = _make_progress_hook(q, current_video=i, total_videos=total)
        ydl_opts = _build_ydl_opts('youtube', video_url, quality, hook, entry, job_tmp)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(video_url, download=True)
        except DownloadAborted:
            raise  # otherwise the loop would carry on, video by video
        except Exception as e:
            _put_progress(q, {
                'status': 'playlist_video_error',
                'current_video': i,
                'total_videos': total,
                'message': _explain_error(str(e)),
            })

    _put_final(q, {
        'status': 'complete',
        'title': playlist_title,
        'filename': f'{total} videos',
        'is_playlist': True,
        'total_videos': total,
    })


def _rate_limit_check():
    """Rate limiting. True when the request is allowed through."""
    now = time.time()
    with _rate_lock:
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= MAX_DOWNLOADS_PER_MINUTE:
            return False
        _request_times.append(now)
        return True


@app.route('/start-download', methods=['POST'])
def start_download():
    """Start a download and hand back the id its progress stream will use."""
    try:
        if not _rate_limit_check():
            return jsonify({'error': 'Too many requests. Give it a moment.'}), 429

        data = request.get_json()
        url = data.get('url')
        download_type = data.get('type', 'youtube')
        quality = data.get('quality')

        if not url:
            return jsonify({'error': 'Missing URL'}), 400

        if download_type in ('youtube', 'playlist'):
            if not validate_youtube_url(url):
                return jsonify({'error': 'Invalid YouTube URL'}), 400
            url = clean_youtube_url(url)

        if download_type == 'social':
            plat = detect_platform(url)
            if plat not in ('instagram', 'tiktok', 'x', 'facebook'):
                return jsonify({'error': f'{plat} is not a supported platform'}), 400

        if download_type == 'photo' and detect_platform(url) != 'instagram':
            return jsonify({'error': 'Photos are only supported on Instagram'}), 400

        download_id = str(uuid.uuid4())
        q = queue.Queue(maxsize=100)
        now = time.time()
        download_progress[download_id] = {
            'queue': q,
            'start_time': now,
            # Pushed forward per video by the playlist loop; read by the
            # hooks, which abort, and by the SSE net, which frees the client.
            'deadline': now + DOWNLOAD_TIMEOUT,
            'cancelled': False,
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
    """Drain a job's queue and serve it as Server-Sent Events.

    The two streams, download and cut, were two copies: the leak fix had to be
    written twice, and the copies had already drifted apart, with only the
    download having been given a client-side net.
    """
    def generate():
        entry = registry.get(key)
        if not entry:
            yield f'data: {json.dumps({"status": "error", "message": unknown_msg})}\n\n'
            return
        q = entry['queue']
        try:
            while True:
                # A safety net. The worker now aborts itself, through the
                # yt-dlp hook or the FFmpeg watchdog; give it SSE_GRACE of head
                # start, then free the client rather than leave it waiting.
                if deadline_key and time.time() > entry[deadline_key] + SSE_GRACE:
                    yield f'data: {json.dumps({"status": "error", "message": TIMEOUT_MSG})}\n\n'
                    break
                try:
                    event = q.get(timeout=poll)
                    yield f'data: {json.dumps(event)}\n\n'
                    if event.get('status') in TERMINAL_STATUSES:
                        break
                except queue.Empty:
                    yield f'data: {json.dumps({"status": "heartbeat"})}\n\n'
        finally:
            # In a finally, otherwise a client disconnect (GeneratorExit)
            # left the entry and its queue in memory for good.
            registry.pop(key, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/progress/<download_id>')
def progress_stream(download_id):
    """SSE endpoint carrying a download's progress."""
    return _sse_response(download_progress, download_id,
                         'Unknown download', poll=10, deadline_key='deadline')


@app.route('/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    """Stop a download or a cut in progress.

    Answers immediately, without waiting for the worker to acknowledge: the
    client has to be able to start again right away. A download stops at the
    next yt-dlp hook, which is near instant in practice, but if yt-dlp is stuck
    somewhere no hook runs, the thread survives until the application closes.
    Python cannot kill a thread; that is why the interface frees itself without
    waiting for it.
    """
    entry = download_progress.get(job_id) or cut_progress.get(job_id)
    if not entry:
        # Already finished, or never existed: either way there is nothing
        # left to stop, and the caller just wants control back.
        return jsonify({'success': True})

    entry['cancelled'] = True
    _kill_quietly(entry.get('proc'))
    return jsonify({'success': True})


@app.route('/')
def index():
    """The single page."""
    return render_template('index.html')


# ==================== PLAYLISTS ====================
def _instagram_photo_stub():
    """The /get-info reply for an Instagram post whose extraction fails.

    The same 15-key dict was written twice inside one function, on the
    exception branch and on the 'info is None' branch.
    """
    return {
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
    }


def _playlist_info(url):
    """A playlist's metadata, from a flat extraction.

    Internal rather than a route of its own: the client used to pick the
    endpoint from its own URL detection, which could contradict the server's.
    The request then went to the wrong extractor and the failure was opaque.
    /get-info now decides, with is_playlist_url().
    """
    # The bound is set here and nowhere else: the download itself needs the
    # whole list.
    with yt_dlp.YoutubeDL({**_flat_ydl_opts(), 'playlistend': PLAYLIST_SCAN}) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise ValueError("Could not extract the playlist information")

    enumerees = len([e for e in info.get('entries', []) if e])

    return {
        'success': True,
        'is_playlist': True,
        'platform': 'youtube',
        'title': info.get('title', 'Playlist'),
        'uploader': info.get('uploader', 'Inconnu'),
        # playlist_count comes from the page and does not depend on the
        # bound: a playlist of 1593 videos still announces 1593 even though only
        # PLAYLIST_SCAN of them were enumerated. The length is only a fallback
        # for when that field is missing, and that fallback IS capped by the
        # bound, hence the flag: announcing "50" for a playlist of 3000 would be
        # a silent lie.
        'video_count': info.get('playlist_count') or enumerees,
        'video_count_partial': not info.get('playlist_count') and enumerees >= PLAYLIST_SCAN,
    }


# ==================== COMMUN ====================
@app.route('/get-info', methods=['POST'])
def get_video_info():
    """Metadata for one URL, plus the formats it can be downloaded in."""
    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'Missing URL'}), 400

        # The server alone decides what counts as a playlist: URL semantics
        # belong to it. Restricted to YouTube because _run_playlist_download can
        # only download that, and _playlist_info used to label as 'youtube' any
        # URL carrying ?list=, which start_download then rejected with a 400.
        platform = detect_platform(url)
        if platform == 'youtube' and is_playlist_url(url):
            return jsonify(_playlist_info(url))

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            # is_playlist_url() has already ruled that this URL names ONE
            # video, but nothing told yt-dlp: by default it sees the 'list=' and
            # unrolls the playlist. On a YouTube radio ('list=RD...'), generated
            # on demand, it pages forever. Measured: over 120 s without an
            # answer, against 1.1 s with this option. And the freeze landed on
            # /get-info, where the Stop button is not on screen yet, so the
            # interface just sat on "...".
            'noplaylist': True,
        }

        cookies_file = _get_cookies_file()
        if cookies_file:
            ydl_opts['cookiefile'] = cookies_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as extract_err:
            if platform == 'instagram':
                return jsonify(_instagram_photo_stub())
            raise extract_err

        if info is None:
            if platform == 'instagram':
                return jsonify(_instagram_photo_stub())
            raise Exception("Could not extract the information")

        # Pull out the resolution on offer
        height = info.get('height', 0)
        width = info.get('width', 0)
        fps = info.get('fps', 0)

        # Work out the quality label
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

        # Collect the available qualities
        formats = info.get('formats', [])
        # An Instagram photo post does not always fail extraction: when it
        # succeeds, no Photo button was left, since _is_photo is only set on the
        # exception branch.
        has_video_stream = any(
            f.get('vcodec', 'none') != 'none' for f in formats)
        is_photo = platform == 'instagram' and not has_video_stream

        available_qualities = sorted(
            {f['height'] for f in formats
             if f.get('height') and f.get('vcodec', 'none') != 'none'},
            reverse=True,
        )

        has_audio = any(
            f.get('acodec', 'none') != 'none'
            for f in formats
        ) if formats else True
        # An image has no audio track. The fallback above, "no formats
        # extracted, so assume there is audio", is set by the very branch that
        # makes is_photo true: the client then drew an MP3 button on a photo
        # post and, being added after the Photo button, that is the one that
        # ended up selected. Clicking Download started an mp3 that could only
        # fail on an image.
        if is_photo:
            has_audio = False

        # _run_playlist_download only supports YouTube playlists: announcing
        # is_playlist for a SoundCloud set drew a card whose only button was
        # then rejected with a 400 by start_download.
        is_playlist = info.get('_type') == 'playlist' and platform == 'youtube'

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
            '_is_photo': is_photo,
            'video_count': len(info.get('entries', [])) if is_playlist else 0,
        })

    except Exception as e:
        log.error(f"Get-info error: {e}")
        return jsonify({'error': f'Error: {str(e)}'}), 500


def _resolve_category_folder(category):
    """Resolve a category name, new or legacy, to a folder on disk."""
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


@app.route('/delete/<category>/<filename>', methods=['DELETE'])
def delete_file(category, filename):
    """Delete a downloaded file."""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Invalid category'}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({'error': 'Access denied'}), 403

    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404

    file_path.unlink()
    return jsonify({'success': True, 'message': f'Deleted: {safe_filename}'})


@app.route('/list-downloads')
def list_downloads():
    """List every downloaded file, with its category."""
    files = []
    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
    audio_exts = ('.mp3', '.m4a', '.wav', '.flac', '.ogg')
    # Intermediate files are not media: they showed up as openable rows and
    # the player failed on them.
    partial_exts = ('.part', '.ytdl', '.temp', '.tmp')

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
            if file.suffix.lower() in partial_exts:
                continue
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
                    'timestamp': stat.st_mtime,
                })

    files.sort(key=lambda f: f['timestamp'], reverse=True)
    return jsonify(files)


@app.route('/stream/<category>/<filename>')
def stream_file(category, filename):
    """Serve a file to the video/audio/photo player."""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Invalid category'}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({'error': 'Access denied'}), 403

    if file_path.exists():
        return send_file(file_path)
    return jsonify({'error': 'File not found'}), 404


def _run_ffmpeg_cut(cut_id, input_path, out_path, start, duration, temp_cleanup=None):
    """Run FFmpeg on a worker thread, reading progress off stderr."""
    entry = cut_progress.get(cut_id)
    if not entry:
        # The client disconnected before we started: the SSE stream's finally
        # has already removed the entry. Without this guard the KeyError skipped
        # the cleanup and left the temporary file behind, up to 2 GB of it.
        if temp_cleanup:
            temp_cleanup.unlink(missing_ok=True)
        return
    q = entry['queue']

    def fail(message, status='error'):
        """Leaving with nothing to show: one place to clean up.

        A cancellation keeps the source file. Stopping a cut is precisely what
        you do to redo it with different bounds: deleting the source answered
        404 "source file not found" when relaunching, and killed the preview on
        the way. The hourly temp_uploads/ sweep takes care of it instead.
        """
        doomed = (out_path,) if status == 'cancelled' else (out_path, temp_cleanup)
        for path in doomed:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        _put_final(q, {'status': status, 'message': message})

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
        # stdout to DEVNULL: nothing was reading it, and a pipe that is never
        # drained can block FFmpeg as soon as it fills up.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, **_SUBPROCESS_FLAGS,
        )
        # Published so /cancel can kill it. Unlike yt-dlp, FFmpeg is a
        # subprocess: stopping it is immediate and certain.
        entry['proc'] = proc
        # Re-read the flag right after: a cancellation landing between
        # creating the process and publishing it would have seen no process to
        # kill, and FFmpeg would have re-encoded the whole clip for nothing.
        if entry.get('cancelled'):
            _kill_quietly(proc)

        # A watchdog on a timer: the deadline used to be evaluated inside the
        # read loop, so a stuck FFmpeg that writes no further line to stderr was
        # never killed.
        timed_out = threading.Event()

        def _kill_stalled():
            timed_out.set()
            _kill_quietly(proc)

        watchdog = threading.Timer(CUT_TIMEOUT, _kill_stalled)
        watchdog.daemon = True
        watchdog.start()

        try:
            for line in proc.stderr:
                if line.startswith('out_time_us='):
                    try:
                        us = int(line.split('=')[1].strip())
                        pct = min(us / 1_000_000 / duration, 1.0) if duration > 0 else 0
                        # put_nowait: removing the entry in the finally
                        # orphans the queue as soon as the client leaves, and a
                        # blocking put then froze the worker (stderr no longer
                        # drained, cleanup never done) until the process died.
                        _put_progress(q, {'status': 'progress', 'percent': round(pct * 100, 1)})
                    except (ValueError, ZeroDivisionError):
                        pass
            proc.wait()
        finally:
            watchdog.cancel()

        if entry.get('cancelled'):
            fail('Cut stopped', status='cancelled')
            return

        if timed_out.is_set():
            fail('Timeout: the cut took too long')
            return

        if proc.returncode != 0:
            fail('FFmpeg failed while cutting')
            return

        if temp_cleanup:
            temp_cleanup.unlink(missing_ok=True)
        size = out_path.stat().st_size
        log.info(f"Cut done: {out_path.name} ({size / 1024 / 1024:.1f} MB)")
        _put_final(q, {
            'status': 'complete',
            'filename': out_path.name,
            'size': size,
            'message': f'Cut finished: {out_path.name}',
        })
    except FileNotFoundError:
        fail('FFmpeg not found')
    except Exception as e:
        log.error(f"Cut error: {e}")
        fail(str(e))


@app.route('/cut-progress/<cut_id>')
def cut_progress_stream(cut_id):
    """SSE endpoint carrying a cut's progress."""
    return _sse_response(cut_progress, cut_id,
                         'Unknown cut', poll=15, deadline_key='deadline')


TEMP_FOLDER = BASE_DIR / "temp_uploads"
TEMP_FOLDER.mkdir(exist_ok=True)


def _sweep_stale_partials():
    """Remove orphaned partial files from the output folders.

    Since the move to a per-job directory yt-dlp no longer writes any here, but
    the ones left by older versions are now filtered out of the listing and so
    are invisible: without this sweep they would stay forever. Called at
    startup, when no download is running.
    """
    for folder in (VIDEOS_FOLDER, MUSIC_FOLDER, PHOTOS_FOLDER):
        for pattern in ('*.part', '*.ytdl', '*.temp', '*.tmp'):
            for f in folder.glob(pattern):
                try:
                    f.unlink()
                except OSError:
                    pass


def _sweep_work_folder():
    """Purge work directories abandoned by a worker that was killed.

    The threshold exceeds a download's maximum lifetime, so this sweep can
    never reach a job that is still alive.
    """
    _sweep_old_files(WORK_FOLDER, DOWNLOAD_TIMEOUT + SSE_GRACE)


def _sweep_temp_uploads():
    """Purge abandoned uploads.

    A file was only deleted along the cut path: walking away through "Change
    file" left it on disk indefinitely, up to 2 GB each time.
    """
    _sweep_old_files(TEMP_FOLDER, 3600)


@app.route('/upload-for-cut', methods=['POST'])
def upload_for_cut():
    """Receive a file so it can be previewed before cutting."""
    _sweep_temp_uploads()
    if 'file' not in request.files:
        return jsonify({'error': 'No file sent'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.mp3', '.mp4', '.mkv', '.webm', '.m4a', '.wav', '.flac', '.ogg', '.avi', '.mov'):
        return jsonify({'error': f'Unsupported format: {ext}'}), 400

    safe_name = sanitize_filename(file.filename)
    temp_id = str(uuid.uuid4())[:8]
    temp_name = f"{temp_id}_{safe_name}"
    temp_path = TEMP_FOLDER / temp_name

    file.save(str(temp_path))

    file_size = temp_path.stat().st_size
    duration = _get_media_duration(temp_path)

    if duration <= 0:
        temp_path.unlink(missing_ok=True)
        return jsonify({'error': 'Could not read the file duration. Unsupported format, or the file is damaged.'}), 400

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
    """Serve an uploaded file back for the preview."""
    safe_name = sanitize_filename(filename)
    file_path = (TEMP_FOLDER / safe_name).resolve()
    if not str(file_path).startswith(str(TEMP_FOLDER.resolve())):
        return jsonify({'error': 'Access denied'}), 403
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
    return send_file(file_path)


@app.route('/cut-uploaded', methods=['POST'])
def cut_uploaded():
    """Cut an uploaded file and hand back the id its progress stream will use."""
    data = request.get_json()
    temp_name = data.get('temp_name')
    start = data.get('start', 0)
    end = data.get('end')
    original_name = data.get('original_name', '')

    if not temp_name or end is None:
        return jsonify({'error': 'Missing parameters'}), 400
    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return jsonify({'error': 'Times must be numbers'}), 400
    if end <= start:
        return jsonify({'error': 'The end time must come after the start'}), 400

    safe_temp = sanitize_filename(temp_name)
    temp_path = (TEMP_FOLDER / safe_temp).resolve()
    if not str(temp_path).startswith(str(TEMP_FOLDER.resolve())):
        return jsonify({'error': 'Access denied'}), 403
    if not temp_path.exists():
        return jsonify({'error': 'Source file not found'}), 404

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
        'cancelled': False,
    }
    thread = threading.Thread(
        target=_run_ffmpeg_cut,
        args=(cut_id, temp_path, out_path, start, duration, temp_path),
        daemon=True,
    )
    thread.start()
    return jsonify({'cut_id': cut_id})


def _get_media_duration(filepath):
    """Read a media file's duration with ffprobe."""
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
    """Open the downloads folder in Explorer."""
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
    """Open Explorer with the file selected."""
    safe_name = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({'error': 'Invalid category'}), 400
    file_path = (folder / safe_name).resolve()
    if not str(file_path).startswith(str(folder.resolve())):
        return jsonify({'error': 'Access denied'}), 403
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
    try:
        if platform.system() == 'Windows':
            subprocess.Popen([EXPLORER, '/select,', str(file_path)], **_SUBPROCESS_FLAGS)
        elif platform.system() == 'Darwin':
            subprocess.Popen(['open', '-R', str(file_path)])
        else:
            subprocess.Popen(['xdg-open', str(file_path.parent)])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _migrate_legacy_folders():
    """Move files from the old folder names into the current structure."""
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
        log.info(f"Migration: moved {moved} file(s) into the current structure")


if __name__ == '__main__':
    _migrate_legacy_folders()
    _sweep_work_folder()
    _sweep_stale_partials()

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