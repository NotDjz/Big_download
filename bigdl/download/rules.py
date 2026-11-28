"""The download's rules, with no I/O: which URLs are accepted, what they name, what to ask yt-dlp for."""

import re
from urllib.parse import parse_qs, urlparse


def validate_youtube_url(url):
    """True if the URL is a YouTube URL we know how to handle."""
    if not url:
        return False

    youtube_patterns = [
        r"^https?://(www\.)?youtube\.com/watch\?v=[\w-]+",
        r"^https?://(www\.)?youtu\.be/[\w-]+",
        r"^https?://(www\.)?youtube\.com/shorts/[\w-]+",
        r"^https?://(www\.)?youtube\.com/playlist\?list=[\w-]+",
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
        if "v" in params:
            return False
        # youtu.be/<id> carries the id in the path
        if parsed.netloc.endswith("youtu.be") and parsed.path.strip("/"):
            return False
        # Equality and not a substring test: '/@channel/playlists' is a
        # channel's playlist index, not a downloadable playlist.
        if parsed.path.rstrip("/") == "/playlist":
            return True
        return "list" in params
    except Exception:
        return False


def clean_youtube_url(url):
    """Reduce a YouTube URL to the video id it names."""
    try:
        if is_playlist_url(url):
            parsed = urlparse(url)
            list_id = parse_qs(parsed.query).get("list", [None])[0]
            if list_id:
                return f"https://www.youtube.com/playlist?list={list_id}"
            return url
        if "youtube.com/watch" in url:
            parsed = urlparse(url)
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[-1].split("?")[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        elif "youtube.com/shorts/" in url:
            video_id = url.split("shorts/")[-1].split("?")[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        return url
    except Exception:
        return url


def detect_platform(url):
    """Identify the platform from the URL."""
    domain = urlparse(url).netloc.lower()

    platforms = {
        "youtube.com": "youtube",
        "youtu.be": "youtube",
        "instagram.com": "instagram",
        "twitter.com": "x",
        "x.com": "x",
        "tiktok.com": "tiktok",
        "soundcloud.com": "soundcloud",
        "facebook.com": "facebook",
        "vimeo.com": "vimeo",
    }

    for domain_key, name in platforms.items():
        if domain_key in domain:
            return name

    return "unknown"


def _get_format_string(download_type, quality=None):
    """The yt-dlp format string for a given download type."""
    if download_type == "mp3":
        return "bestaudio/best"
    elif download_type == "social":
        # A bare 'best' ignored the chosen quality: picking 480p still
        # downloaded the heaviest stream on offer.
        if quality:
            # Progressive streams only: merge_output_format and the MP4
            # converter are set for youtube/playlist alone, so a
            # bestvideo+bestaudio here would produce a .mkv/.webm the player
            # cannot read.
            return f"best[height<={quality}]/best"
        return "best"
    elif quality:
        return (
            f"bestvideo[height<={quality}]+bestaudio/"
            f"bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/"
            "best"
        )
    else:
        return "bestvideo+bestaudio/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"


def _get_output_template(download_type, url):
    """The output name template, deliberately *relative*.

    Relative and not absolute: yt-dlp silently ignores the 'paths' option when
    outtmpl is an absolute path (measured, with an absolute outtmpl the temp
    directory falls back to the final folder). The folder therefore comes from
    paths['home'], set by _build_ydl_opts.
    """
    if download_type == "social":
        return f"{detect_platform(url)}_%(id)s.%(ext)s"
    return "%(title)s.%(ext)s"


def _shortcode_to_media_id(shortcode):
    """Turn an Instagram shortcode into its numeric media id."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + alphabet.index(char)
    return str(media_id)


# ==================== PLAYLISTS ====================
def _instagram_photo_stub():
    """The /get-info reply for an Instagram post whose extraction fails.

    The same 15-key dict was written twice inside one function, on the
    exception branch and on the 'info is None' branch.
    """
    return {
        "success": True,
        "title": "Post Instagram",
        "duration": 0,
        "thumbnail": "",
        "uploader": "",
        "platform": "instagram",
        "resolution": "",
        "width": 0,
        "height": 0,
        "fps": 0,
        "available_qualities": [],
        "has_audio": False,
        "is_playlist": False,
        "video_count": 0,
        "_is_photo": True,
    }
