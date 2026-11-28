"""The Download tab's HTTP routes: look a URL up, start a download, follow it."""

import queue
import threading
import time
import uuid

import yt_dlp
from flask import Blueprint, jsonify, request

from bigdl.config import (
    DOWNLOAD_TIMEOUT,
    log,
)
from bigdl.download.instagram import (
    _get_cookies_file,
)
from bigdl.download.rules import (
    _instagram_photo_stub,
    clean_youtube_url,
    detect_platform,
    is_playlist_url,
    validate_youtube_url,
)
from bigdl.download.ytdlp import (
    _playlist_info,
    _run_download,
)
from bigdl.jobs import (
    download_progress,
)
from bigdl.web import (
    _rate_limit_check,
    _sse_response,
)

bp = Blueprint("download", __name__)


@bp.route("/start-download", methods=["POST"])
def start_download():
    """Start a download and hand back the id its progress stream will use."""
    try:
        if not _rate_limit_check():
            return jsonify({"error": "Too many requests. Give it a moment."}), 429

        data = request.get_json()
        url = data.get("url")
        download_type = data.get("type", "youtube")
        quality = data.get("quality")

        if not url:
            return jsonify({"error": "Missing URL"}), 400

        if download_type in ("youtube", "playlist"):
            if not validate_youtube_url(url):
                return jsonify({"error": "Invalid YouTube URL"}), 400
            url = clean_youtube_url(url)

        if download_type == "social":
            plat = detect_platform(url)
            if plat not in ("instagram", "tiktok", "x", "facebook"):
                return jsonify({"error": f"{plat} is not a supported platform"}), 400

        if download_type == "photo" and detect_platform(url) != "instagram":
            return jsonify({"error": "Photos are only supported on Instagram"}), 400

        download_id = str(uuid.uuid4())
        q = queue.Queue(maxsize=100)
        now = time.time()
        download_progress[download_id] = {
            "queue": q,
            "start_time": now,
            # Pushed forward per video by the playlist loop; read by the
            # hooks, which abort, and by the SSE net, which frees the client.
            "deadline": now + DOWNLOAD_TIMEOUT,
            "cancelled": False,
        }

        thread = threading.Thread(
            target=_run_download,
            args=(download_id, url, download_type, quality),
            daemon=True,
        )
        thread.start()

        log.info(f"Download started [{download_type}] {url}")
        return jsonify({"download_id": download_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/progress/<download_id>")
def progress_stream(download_id):
    """SSE endpoint carrying a download's progress."""
    return _sse_response(
        download_progress,
        download_id,
        "Unknown download",
        poll=10,
        deadline_key="deadline",
    )


# ==================== COMMUN ====================
@bp.route("/get-info", methods=["POST"])
def get_video_info():
    """Metadata for one URL, plus the formats it can be downloaded in."""
    try:
        data = request.get_json()
        url = data.get("url")

        if not url:
            return jsonify({"error": "Missing URL"}), 400

        # The server alone decides what counts as a playlist: URL semantics
        # belong to it. Restricted to YouTube because _run_playlist_download can
        # only download that, and _playlist_info used to label as 'youtube' any
        # URL carrying ?list=, which start_download then rejected with a 400.
        platform = detect_platform(url)
        if platform == "youtube" and is_playlist_url(url):
            return jsonify(_playlist_info(url))

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            # is_playlist_url() has already ruled that this URL names ONE
            # video, but nothing told yt-dlp: by default it sees the 'list=' and
            # unrolls the playlist. On a YouTube radio ('list=RD...'), generated
            # on demand, it pages forever. Measured: over 120 s without an
            # answer, against 1.1 s with this option. And the freeze landed on
            # /get-info, where the Stop button is not on screen yet, so the
            # interface just sat on "...".
            "noplaylist": True,
        }

        cookies_file = _get_cookies_file()
        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as extract_err:
            if platform == "instagram":
                return jsonify(_instagram_photo_stub())
            raise extract_err

        if info is None:
            if platform == "instagram":
                return jsonify(_instagram_photo_stub())
            raise Exception("Could not extract the information")

        # Pull out the resolution on offer
        height = info.get("height", 0)
        width = info.get("width", 0)
        fps = info.get("fps", 0)

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
        formats = info.get("formats", [])
        # An Instagram photo post does not always fail extraction: when it
        # succeeds, no Photo button was left, since _is_photo is only set on the
        # exception branch.
        has_video_stream = any(f.get("vcodec", "none") != "none" for f in formats)
        is_photo = platform == "instagram" and not has_video_stream

        available_qualities = sorted(
            {
                f["height"]
                for f in formats
                if f.get("height") and f.get("vcodec", "none") != "none"
            },
            reverse=True,
        )

        has_audio = (
            any(f.get("acodec", "none") != "none" for f in formats) if formats else True
        )
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
        is_playlist = info.get("_type") == "playlist" and platform == "youtube"

        return jsonify(
            {
                "success": True,
                "title": info.get("title", "N/A"),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail", ""),
                "uploader": info.get("uploader", "N/A"),
                "platform": platform,
                "resolution": quality,
                "width": width,
                "height": height,
                "fps": fps,
                "available_qualities": available_qualities,
                "has_audio": has_audio,
                "is_playlist": is_playlist,
                "_is_photo": is_photo,
                "video_count": len(info.get("entries", [])) if is_playlist else 0,
            }
        )

    except Exception as e:
        log.error(f"Get-info error: {e}")
        return jsonify({"error": f"Error: {str(e)}"}), 500
