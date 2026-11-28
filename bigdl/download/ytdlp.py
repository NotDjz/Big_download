"""yt-dlp for the download: options, progress and abort hooks, the worker, playlists, and the /get-info lookup."""

import os
import queue
import shutil
import sys
import time
from pathlib import Path

import yt_dlp

from bigdl.config import (
    CANCEL_MSG,
    DOWNLOAD_TIMEOUT,
    FFMPEG_DIR,
    MAX_VIDEO_SIZE,
    MUSIC_FOLDER,
    PHOTOS_FOLDER,
    PLAYLIST_SCAN,
    TIMEOUT_MSG,
    VIDEOS_FOLDER,
    WORK_FOLDER,
    log,
)
from bigdl.download.instagram import (
    _convert_to_jpg,
    _download_instagram_images,
    _get_cookies_file,
)
from bigdl.download.rules import (
    _get_format_string,
    _get_output_template,
)
from bigdl.jobs import (
    DownloadAborted,
    _put_final,
    _put_progress,
    download_progress,
)
from bigdl.library.rules import IMAGE_EXTS, VIDEO_EXTS


def _get_output_folder(download_type):
    """The output folder for a given download type."""
    return MUSIC_FOLDER if download_type == "mp3" else VIDEOS_FOLDER


def _make_progress_hook(q, current_video=None, total_videos=None):
    """Build a yt-dlp progress hook."""

    def progress_hook(d):
        try:
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                event = {
                    "status": "downloading",
                    "percent": round(percent, 1),
                    "speed": d.get("_speed_str", "").strip(),
                    "eta": d.get("_eta_str", "").strip(),
                    "downloaded": downloaded,
                    "total": total,
                }
                if current_video is not None:
                    event["current_video"] = current_video
                    event["total_videos"] = total_videos
                q.put_nowait(event)
            elif d["status"] == "finished":
                msg = "Merging and converting..."
                if current_video is not None:
                    msg = f"Video {current_video}/{total_videos} - {msg}"
                q.put_nowait({"status": "processing", "message": msg})
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
        if entry.get("cancelled"):
            raise DownloadAborted("cancelled", CANCEL_MSG)
        if time.time() > entry["deadline"]:
            raise DownloadAborted("error", TIMEOUT_MSG)

    return abort_hook


def _build_ydl_opts(download_type, url, quality, progress_hook, entry, job_tmp):
    """Assemble the yt-dlp options for one download."""
    abort = _make_abort_hook(entry)
    ydl_opts = {
        "format": _get_format_string(download_type, quality),
        "outtmpl": _get_output_template(download_type, url),
        # Intermediate files go into a directory belonging to this job
        # alone: no more tracking our own .part files, and no more guessing by
        # age which ones belong to some other download.
        "paths": {"home": str(_get_output_folder(download_type)), "temp": str(job_tmp)},
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        # Always a single video here, including when _run_playlist_download
        # calls this function once per video with each one's own URL.
        # The type that makes this option load-bearing is 'mp3': start_download
        # only routes 'youtube' and 'playlist' through clean_youtube_url, which
        # is what strips the '&list='. A YouTube radio URL asked for as MP3
        # therefore arrives intact, and without this line yt-dlp set off to
        # extract the entire radio, which has no end.
        "noplaylist": True,
        # One guard, installed on both families: that is the point of
        # _make_abort_hook's docstring, and the code should show it too.
        "progress_hooks": [abort, progress_hook],
        "postprocessor_hooks": [abort],
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
        "fragment_retries": 10,
        "retries": 10,
    }

    # Set as soon as the cascade knows where FFmpeg is, not only when frozen:
    # in dev, with ffmpeg.exe sitting beside the project, yt-dlp could not find
    # it and refused every video+audio merge, which meant refusing all of 4K.
    if FFMPEG_DIR:
        ydl_opts["ffmpeg_location"] = str(FFMPEG_DIR)

    cookies_file = _get_cookies_file()
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    if download_type in ("youtube", "playlist"):
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"] = [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
            {"key": "FFmpegMetadata"},
        ]
    elif download_type == "mp3":
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata"},
        ]

    return ydl_opts


def _check_filesize(file_path):
    """Check a downloaded file's size, deleting it if it exceeds the limit."""
    if file_path.exists():
        size = file_path.stat().st_size
        if size > MAX_VIDEO_SIZE:
            file_path.unlink()
            size_gb = size / (1024**3)
            max_gb = MAX_VIDEO_SIZE / (1024**3)
            raise Exception(
                f"File too large ({size_gb:.1f} GB). Maximum: {max_gb:.0f} GB"
            )


def _explain_error(msg):
    """Attach the likely cause to a 403, which never states one.

    The exe bundles a frozen yt-dlp and cannot update it: when a platform
    tightens its protections, everything fails with a 403 and nothing hints
    that a newer version is what is needed.
    """
    if "HTTP Error 403" not in msg:
        return msg
    # The advice differs by audience: the exe bundles yt-dlp and cannot update
    # it, while dev mode can. Kept short, because the banner is 268 px wide and
    # disappears after six seconds.
    if getattr(sys, "frozen", False):
        return msg + " - yt-dlp may be too old: install the latest BIG DL."
    return msg + " - yt-dlp may be too old: pip install --upgrade yt-dlp."


def _run_download(download_id, url, download_type, quality=None):
    """Run the download on a worker thread, reporting through progress hooks."""
    entry = download_progress.get(download_id)
    if not entry:
        return
    q = entry["queue"]

    # Private scratch space, on the same volume as the output folders so the
    # final move stays a rename.
    job_tmp = WORK_FOLDER / download_id

    try:
        if download_type != "photo":
            # Photos go straight into PHOTOS_FOLDER and never see this
            # directory: creating it would be two disk operations for nothing.
            job_tmp.mkdir(parents=True, exist_ok=True)

        if download_type == "photo":
            # Chosen upfront rather than inferred from a failure: the
            # exception-based fallback also fired on an expired cookie, a
            # timeout or a dropped connection during an Instagram *video*, and
            # then returned a message describing an operation nobody asked
            # for.
            _download_instagram_images(q, url, entry)
        elif download_type == "playlist":
            _run_playlist_download(q, url, quality, entry, job_tmp)
        else:
            hook = _make_progress_hook(q)
            ydl_opts = _build_ydl_opts(
                download_type, url, quality, hook, entry, job_tmp
            )

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                if info is None:
                    _put_final(
                        q,
                        {
                            "status": "error",
                            "message": "Could not extract the information",
                        },
                    )
                    return

                title = info.get("title", "media")
                filename = ydl.prepare_filename(info)
                file_path = Path(filename)
                if not file_path.exists():
                    for ext in VIDEO_EXTS + IMAGE_EXTS:
                        candidate = Path(filename).with_suffix(ext)
                        if candidate.exists():
                            file_path = candidate
                            break

                if file_path.exists() and file_path.suffix.lower() in IMAGE_EXTS:
                    if file_path.suffix.lower() != ".jpg":
                        file_path = _convert_to_jpg(file_path)
                    if (
                        file_path.exists()
                        and file_path.parent.resolve() != PHOTOS_FOLDER.resolve()
                    ):
                        dest = PHOTOS_FOLDER / file_path.name
                        shutil.move(str(file_path), str(dest))
                        file_path = dest

                _check_filesize(file_path)

                result = {
                    "status": "complete",
                    "title": title,
                    "filename": file_path.name
                    if file_path.exists()
                    else os.path.basename(filename),
                }

                if download_type == "youtube":
                    result["resolution"] = f"{info.get('height', 0)}p"
                    result["width"] = info.get("width", 0)
                    result["height"] = info.get("height", 0)
                    result["fps"] = info.get("fps", 0)

                _put_final(q, result)

    except DownloadAborted as e:
        # One except, because the exception already carries what to publish.
        # This was three branches, one of which no raise in the file could
        # reach.
        log.warning(f"Download aborted [{e.status}] {url}: {e.message}")
        _put_final(q, {"status": e.status, "message": e.message})
    except Exception as e:
        error_msg = _explain_error(str(e))
        log.error(f"Download failed [{download_type}] {url}: {error_msg}")
        # No more automatic fallback to images. For a video post the
        # Instagram API returns the thumbnail anyway, so the fallback
        # "succeeded" by handing over a cover JPG announced as a completed
        # download. Photos have their own download type now.
        _put_final(q, {"status": "error", "message": error_msg})
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
    return {"quiet": True, "no_warnings": True, "extract_flat": True}


def _run_playlist_download(q, url, quality, entry, job_tmp):
    """Download a playlist one video at a time, reporting as it goes."""
    # Get the list of videos first
    flat_opts = _flat_ydl_opts()
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        playlist_info = ydl.extract_info(url, download=False)

    if not playlist_info:
        _put_final(q, {"status": "error", "message": "Could not read the playlist"})
        return

    entries = [e for e in playlist_info.get("entries", []) if e]
    total = len(entries)
    playlist_title = playlist_info.get("title", "Playlist")

    if total == 0:
        _put_final(q, {"status": "error", "message": "Playlist is empty"})
        return

    _put_progress(
        q,
        {
            "status": "playlist_start",
            "title": playlist_title,
            "total_videos": total,
        },
    )

    # 'item' and not 'entry': that name shadowed the parameter holding the
    # registry entry, so the deadline was written into yt-dlp's video dict while
    # the SSE net kept reading a value that was never pushed forward.
    for i, item in enumerate(entries, 1):
        video_url = item.get("url") or item.get("id")
        if not video_url:
            continue

        if not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_url}"

        _put_progress(
            q,
            {
                "status": "playlist_video_start",
                "current_video": i,
                "total_videos": total,
                "video_title": item.get("title", f"Video {i}"),
            },
        )

        # The deadline is pushed forward per video: a global 30 min budget
        # would abandon a long playlist partway through, which is a legitimate
        # use. The SSE net reads the same value, so the two layers can no
        # longer contradict each other.
        entry["deadline"] = time.time() + DOWNLOAD_TIMEOUT
        hook = _make_progress_hook(q, current_video=i, total_videos=total)
        ydl_opts = _build_ydl_opts("youtube", video_url, quality, hook, entry, job_tmp)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(video_url, download=True)
        except DownloadAborted:
            raise  # otherwise the loop would carry on, video by video
        except Exception as e:
            _put_progress(
                q,
                {
                    "status": "playlist_video_error",
                    "current_video": i,
                    "total_videos": total,
                    "message": _explain_error(str(e)),
                },
            )

    _put_final(
        q,
        {
            "status": "complete",
            "title": playlist_title,
            "filename": f"{total} videos",
            "is_playlist": True,
            "total_videos": total,
        },
    )


def _playlist_info(url):
    """A playlist's metadata, from a flat extraction.

    Internal rather than a route of its own: the client used to pick the
    endpoint from its own URL detection, which could contradict the server's.
    The request then went to the wrong extractor and the failure was opaque.
    /get-info now decides, with is_playlist_url().
    """
    # The bound is set here and nowhere else: the download itself needs the
    # whole list.
    with yt_dlp.YoutubeDL({**_flat_ydl_opts(), "playlistend": PLAYLIST_SCAN}) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        raise ValueError("Could not extract the playlist information")

    enumerated = len([e for e in info.get("entries", []) if e])

    return {
        "success": True,
        "is_playlist": True,
        "platform": "youtube",
        "title": info.get("title", "Playlist"),
        "uploader": info.get("uploader", "Unknown"),
        # playlist_count comes from the page and does not depend on the
        # bound: a playlist of 1593 videos still announces 1593 even though only
        # PLAYLIST_SCAN of them were enumerated. The length is only a fallback
        # for when that field is missing, and that fallback IS capped by the
        # bound, hence the flag: announcing "50" for a playlist of 3000 would be
        # a silent lie.
        "video_count": info.get("playlist_count") or enumerated,
        "video_count_partial": not info.get("playlist_count")
        and enumerated >= PLAYLIST_SCAN,
    }
