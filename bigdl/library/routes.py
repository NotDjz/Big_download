"""The library's HTTP routes: list, play, delete, and reveal in Explorer what has been downloaded."""

import os
import platform
import subprocess

from flask import Blueprint, jsonify, send_file

from bigdl.config import (
    _SUBPROCESS_FLAGS,
    DOWNLOAD_FOLDER,
    EXPLORER,
    MUSIC_FOLDER,
    PHOTOS_FOLDER,
    VIDEOS_FOLDER,
)
from bigdl.library.rules import (
    sanitize_filename,
)

bp = Blueprint("library", __name__)


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


@bp.route("/delete/<category>/<filename>", methods=["DELETE"])
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


@bp.route("/list-downloads")
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


@bp.route("/stream/<category>/<filename>")
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


@bp.route("/open-folder", methods=["POST"])
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


@bp.route("/open-file/<category>/<filename>", methods=["POST"])
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
