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
    LEGACY_FOLDERS,
    is_inside,
    media_type,
    sanitize_filename,
)

bp = Blueprint("library", __name__)


def _resolve_category_folder(category):
    """Resolve a category name, new or legacy, to a folder on disk."""
    folder_map = {
        "Videos": VIDEOS_FOLDER,
        "Music": MUSIC_FOLDER,
        "Photos": PHOTOS_FOLDER,
    }
    # Legacy names map to their actual on-disk folders.
    folder_map.update({name: DOWNLOAD_FOLDER / name for name in LEGACY_FOLDERS})
    return folder_map.get(category)


@bp.route("/delete/<category>/<filename>", methods=["DELETE"])
def delete_file(category, filename):
    """Delete a downloaded file."""
    safe_filename = sanitize_filename(filename)
    folder = _resolve_category_folder(category)
    if not folder:
        return jsonify({"error": "Invalid category"}), 400

    file_path = (folder / safe_filename).resolve()
    if not is_inside(str(file_path), str(folder.resolve())):
        return jsonify({"error": "Access denied"}), 403

    if not file_path.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        file_path.unlink()
    except PermissionError:
        # Windows refuses to delete an open file (the player, or another program):
        # that was a raw 500 page the client could not read.
        return jsonify({"error": "The file is in use; close it and try again"}), 409
    except FileNotFoundError:  # removed by someone else since the check
        return jsonify({"error": "File not found"}), 404
    except OSError as e:
        return jsonify({"error": f"Could not delete the file: {e.strerror or e}"}), 500
    return jsonify({"success": True, "message": f"Deleted: {safe_filename}"})


@bp.route("/list-downloads")
def list_downloads():
    """List every downloaded file, with its category."""
    files = []
    folders = [
        (VIDEOS_FOLDER, "Videos"),
        (MUSIC_FOLDER, "Music"),
        (PHOTOS_FOLDER, "Photos"),
    ]
    # Legacy folders, when they exist, under their real names as categories.
    folders += [(DOWNLOAD_FOLDER / name, name) for name in LEGACY_FOLDERS]

    seen_names = set()
    for folder, category in folders:
        if not folder.exists():
            continue
        for file in folder.iterdir():
            kind = media_type(file.name)
            if kind is None or file.name in seen_names:
                continue  # partials, desktop.ini, Thumbs.db: not media
            # A file can vanish between the directory read and its stat (deleted,
            # or replaced by a download's final move): that took the whole
            # listing down with a 500.
            try:
                if not file.is_file():
                    continue
                stat = file.stat()
            except OSError:
                continue
            seen_names.add(file.name)
            files.append(
                {
                    "name": file.name,
                    "size": stat.st_size,
                    "category": category,
                    "media_type": kind,
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
    if not is_inside(str(file_path), str(folder.resolve())):
        return jsonify({"error": "Access denied"}), 403

    if file_path.is_file():
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
            subprocess.Popen(["xdg-open", folder])
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
    if not is_inside(str(file_path), str(folder.resolve())):
        return jsonify({"error": "Access denied"}), 403
    if not file_path.is_file():
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
