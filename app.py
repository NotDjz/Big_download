import os
import platform
import shutil
import subprocess
import threading
import time

import webview
from flask import Flask, jsonify, render_template, send_file

from bigdl.config import (
    _SUBPROCESS_FLAGS,
    BUNDLE_DIR,
    DOWNLOAD_FOLDER,
    DOWNLOAD_TIMEOUT,
    EXPLORER,
    MUSIC_FOLDER,
    PHOTOS_FOLDER,
    PORT,
    SSE_GRACE,
    VIDEOS_FOLDER,
    WORK_FOLDER,
    log,
)
from bigdl.cut.routes import bp as cut_routes
from bigdl.download.routes import bp as download_routes
from bigdl.jobs import (
    _kill_quietly,
    _sweep_old_files,
    cut_progress,
    download_progress,
)
from bigdl.library.rules import sanitize_filename
from bigdl.web import (
    _reject_foreign_origin,
    _security_headers,
    request_entity_too_large,
)

app = Flask(
    __name__,
    template_folder=str(BUNDLE_DIR / "templates"),
    static_folder=str(BUNDLE_DIR / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB

# The HTTP edge lives in bigdl/web.py; registered here, on the one Flask app.
app.errorhandler(413)(request_entity_too_large)
app.before_request(_reject_foreign_origin)
app.after_request(_security_headers)
app.register_blueprint(cut_routes)
app.register_blueprint(download_routes)


@app.route("/cancel/<job_id>", methods=["POST"])
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
        return jsonify({"success": True})

    entry["cancelled"] = True
    _kill_quietly(entry.get("proc"))
    return jsonify({"success": True})


@app.route("/")
def index():
    """The single page."""
    return render_template("index.html")


def _resolve_category_folder(category):
    """Resolve a category name, new or legacy, to a folder on disk."""
    folder_map = {
        "Videos": VIDEOS_FOLDER,
        "Music": MUSIC_FOLDER,
        "Photos": PHOTOS_FOLDER,
        # Legacy (mapped to actual on-disk folders)
        "YouTube": DOWNLOAD_FOLDER / "YouTube",
        "YouTube_MP3": DOWNLOAD_FOLDER / "YouTube_MP3",
        "Reseaux_Sociaux": DOWNLOAD_FOLDER / "Reseaux_Sociaux",
    }
    return folder_map.get(category)


@app.route("/delete/<category>/<filename>", methods=["DELETE"])
def delete_file(category, filename):
    """Delete a downloaded file."""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({"error": "Invalid category"}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({"error": "Access denied"}), 403

    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404

    file_path.unlink()
    return jsonify({"success": True, "message": f"Deleted: {safe_filename}"})


@app.route("/list-downloads")
def list_downloads():
    """List every downloaded file, with its category."""
    files = []
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    audio_exts = (".mp3", ".m4a", ".wav", ".flac", ".ogg")
    # Intermediate files are not media: they showed up as openable rows and
    # the player failed on them.
    partial_exts = (".part", ".ytdl", ".temp", ".tmp")

    folders = [
        (VIDEOS_FOLDER, "Videos"),
        (MUSIC_FOLDER, "Music"),
        (PHOTOS_FOLDER, "Photos"),
    ]
    # Include legacy folders if they exist (use their real folder names as categories)
    legacy_map = [
        (DOWNLOAD_FOLDER / "YouTube", "YouTube"),
        (DOWNLOAD_FOLDER / "YouTube_MP3", "YouTube_MP3"),
        (DOWNLOAD_FOLDER / "Reseaux_Sociaux", "Reseaux_Sociaux"),
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
                    media_type = "photo"
                elif ext in audio_exts:
                    media_type = "audio"
                else:
                    media_type = "video"
                files.append(
                    {
                        "name": file.name,
                        "size": stat.st_size,
                        "category": category,
                        "media_type": media_type,
                        "timestamp": stat.st_mtime,
                    }
                )

    files.sort(key=lambda f: f["timestamp"], reverse=True)
    return jsonify(files)


@app.route("/stream/<category>/<filename>")
def stream_file(category, filename):
    """Serve a file to the video/audio/photo player."""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({"error": "Invalid category"}), 400

    file_path = (folder / safe_filename).resolve()
    folder_path = folder.resolve()

    if not str(file_path).startswith(str(folder_path)):
        return jsonify({"error": "Access denied"}), 403

    if file_path.exists():
        return send_file(file_path)
    return jsonify({"error": "File not found"}), 404


def _sweep_stale_partials():
    """Remove orphaned partial files from the output folders.

    Since the move to a per-job directory yt-dlp no longer writes any here, but
    the ones left by older versions are now filtered out of the listing and so
    are invisible: without this sweep they would stay forever. Called at
    startup, when no download is running.
    """
    for folder in (VIDEOS_FOLDER, MUSIC_FOLDER, PHOTOS_FOLDER):
        for pattern in ("*.part", "*.ytdl", "*.temp", "*.tmp"):
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


@app.route("/open-folder", methods=["POST"])
def open_folder():
    """Open the downloads folder in Explorer."""
    folder = str(DOWNLOAD_FOLDER.resolve())
    try:
        if platform.system() == "Windows":
            os.startfile(folder)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/open-file/<category>/<filename>", methods=["POST"])
def open_file(category, filename):
    """Open Explorer with the file selected."""
    safe_name = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({"error": "Invalid category"}), 400
    file_path = (folder / safe_name).resolve()
    if not str(file_path).startswith(str(folder.resolve())):
        return jsonify({"error": "Access denied"}), 403
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    try:
        if platform.system() == "Windows":
            subprocess.Popen(
                [EXPLORER, "/select,", str(file_path)], **_SUBPROCESS_FLAGS
            )
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(file_path)])
        else:
            subprocess.Popen(["xdg-open", str(file_path.parent)])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _migrate_legacy_folders():
    """Move files from the old folder names into the current structure."""
    migrations = [
        (DOWNLOAD_FOLDER / "YouTube", VIDEOS_FOLDER),
        (DOWNLOAD_FOLDER / "YouTube_MP3", MUSIC_FOLDER),
        (DOWNLOAD_FOLDER / "Reseaux_Sociaux", VIDEOS_FOLDER),
    ]
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
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


if __name__ == "__main__":
    _migrate_legacy_folders()
    _sweep_work_folder()
    _sweep_stale_partials()

    flask_thread = threading.Thread(
        target=lambda: app.run(debug=False, host="127.0.0.1", port=PORT, threaded=True),
        daemon=True,
    )
    flask_thread.start()

    import socket

    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    webview.create_window(
        "Big Downloader",
        f"http://localhost:{PORT}",
        width=1100,
        height=800,
        min_size=(800, 600),
    )
    webview.start()
