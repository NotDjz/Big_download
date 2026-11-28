"""Instagram photos, which yt-dlp does not fetch: the private API with the user's cookies, then JPG conversion."""

from urllib.parse import urlparse

import requests
from PIL import Image

from bigdl.config import (
    BASE_DIR,
    CANCEL_MSG,
    PHOTOS_FOLDER,
)
from bigdl.download.rules import (
    _shortcode_to_media_id,
)
from bigdl.jobs import (
    DownloadAborted,
    _put_final,
    _put_progress,
)
from bigdl.library.rules import (
    sanitize_filename,
)


def _get_cookies_file():
    """Path to the cookies file, if one is there."""
    base = BASE_DIR
    for name in ("cookies.txt", "www.instagram.com_cookies.txt"):
        path = base / name
        if path.exists():
            return str(path)
    return None


def _convert_to_jpg(filepath):
    """Convert an image (webp, png, and so on) to JPG."""
    jpg_path = filepath.with_suffix(".jpg")
    try:
        img = Image.open(filepath)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(jpg_path, "JPEG", quality=92)
        if filepath != jpg_path:
            filepath.unlink()
        return jpg_path
    except Exception:
        return filepath


def _download_instagram_images(q, url, entry):
    """Download an Instagram post's images through the private API, with cookies."""
    import http.cookiejar

    _put_progress(
        q, {"status": "processing", "message": "Downloading Instagram image..."}
    )

    cookies_file = _get_cookies_file()
    if not cookies_file:
        _put_final(
            q,
            {
                "status": "error",
                "message": 'Instagram photos need a cookies.txt file (export it from your browser with the "Get cookies.txt LOCALLY" extension)',
            },
        )
        return

    parsed_url = urlparse(url)
    path_parts = [p for p in parsed_url.path.split("/") if p]
    # Find shortcode: it's the part after 'p', 'reel', or 'tv' in the path
    shortcode = None
    for i, part in enumerate(path_parts):
        if part in ("p", "reel", "tv") and i + 1 < len(path_parts):
            shortcode = path_parts[i + 1]
            break

    if not shortcode:
        _put_final(q, {"status": "error", "message": "Invalid Instagram URL"})
        return

    media_id = _shortcode_to_media_id(shortcode)

    cj = http.cookiejar.MozillaCookieJar(cookies_file)
    cj.load(ignore_discard=True, ignore_expires=True)

    session = requests.Session()
    session.cookies = cj
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-IG-App-ID": "936619743392459",
        }
    )

    api_url = f"https://www.instagram.com/api/v1/media/{media_id}/info/"
    try:
        resp = session.get(api_url, timeout=15)
    except Exception as e:
        _put_final(q, {"status": "error", "message": f"Instagram API error: {e}"})
        return

    if resp.status_code != 200:
        _put_final(
            q,
            {
                "status": "error",
                "message": f"Instagram API returned {resp.status_code}. The cookies may have expired.",
            },
        )
        return

    data = resp.json()
    items = data.get("items", [])
    if not items:
        _put_final(
            q, {"status": "error", "message": "Instagram post is empty or unreachable"}
        )
        return

    item = items[0]
    image_urls = []

    carousel = item.get("carousel_media", [])
    if carousel:
        for cm in carousel:
            candidates = cm.get("image_versions2", {}).get("candidates", [])
            if candidates:
                image_urls.append(candidates[0]["url"])
    else:
        candidates = item.get("image_versions2", {}).get("candidates", [])
        if candidates:
            image_urls.append(candidates[0]["url"])

    # A video post carries image_versions2 too, holding its thumbnail: without
    # this check we saved the cover image and reported a completed download to
    # someone who had asked for a video.
    if item.get("video_versions") or any(
        m.get("video_versions") for m in (item.get("carousel_media") or [])
    ):
        _put_final(
            q,
            {
                "status": "error",
                "message": "This post contains a video: pick the video format.",
            },
        )
        return

    if not image_urls:
        _put_final(q, {"status": "error", "message": "No image found in that post"})
        return

    downloaded = 0
    total = len(image_urls)

    for i, img_url in enumerate(image_urls, 1):
        # At the top of the loop and outside the try. At the end of the body,
        # the `continue` taken when a conversion fails skipped this check
        # entirely, and one more image means up to 30 s of waiting before the
        # stop is noticed.
        if entry.get("cancelled"):
            raise DownloadAborted("cancelled", CANCEL_MSG)
        try:
            img_resp = session.get(img_url, timeout=30)
            img_resp.raise_for_status()
            suffix = f"_{i}" if total > 1 else ""
            temp_filename = f"instagram_{shortcode}{suffix}.tmp"
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
            if not converted.with_suffix(".jpg").exists():
                temp_filepath.unlink(missing_ok=True)
                continue
            downloaded += 1
            _put_progress(
                q,
                {
                    "status": "downloading",
                    "percent": round(i / total * 100, 1),
                    "speed": "",
                    "eta": "",
                },
            )
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
        title = f"instagram_{shortcode}"
        if downloaded > 1:
            title += f" ({downloaded} images)"
        _put_final(
            q,
            {
                "status": "complete",
                "title": title,
                "filename": f"instagram_{shortcode}_1.jpg"
                if total > 1
                else f"instagram_{shortcode}.jpg",
            },
        )
    else:
        _put_final(
            q,
            {
                "status": "error",
                "message": "Download failed. Cookies expired, or access denied.",
            },
        )
