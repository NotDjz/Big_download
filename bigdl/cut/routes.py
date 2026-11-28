"""The Cut tab's HTTP routes: upload, preview, cut, progress."""

import os
import queue
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from bigdl.config import CUT_TIMEOUT, MUSIC_FOLDER, TEMP_FOLDER, VIDEOS_FOLDER, log
from bigdl.cut.ffmpeg import _get_media_duration, _run_ffmpeg_cut
from bigdl.cut.rules import parse_cut_bounds
from bigdl.jobs import _sweep_old_files, cut_progress
from bigdl.library.rules import fit_stem, sanitize_filename
from bigdl.web import _sse_response

bp = Blueprint("cut", __name__)


# Held while a cut's output name is chosen and claimed in cut_progress: two cuts
# started before FFmpeg had created either file both got stem_v2 and wrote to it.
_naming_lock = threading.Lock()


def _next_versioned_name(folder, stem, ext, claimed=()):
    """Next free name: stem_v2.ext, stem_v3.ext, and so on, skipping paths in `claimed`.

    The stem is shortened so the suffix still fits: past MAX_STEM, the next
    sanitize_filename cut the name back to a file that does not exist.
    """
    version = 2
    while True:
        suffix = f"_v{version}"
        name = f"{fit_stem(stem, len(suffix))}{suffix}{ext}"
        if not (folder / name).exists() and folder / name not in claimed:
            return name
        version += 1


@bp.route("/cut-progress/<cut_id>")
def cut_progress_stream(cut_id):
    """SSE endpoint carrying a cut's progress."""
    return _sse_response(
        cut_progress, cut_id, "Unknown cut", poll=15, deadline_key="deadline"
    )


def _sweep_temp_uploads():
    """Purge abandoned uploads.

    A file was only deleted along the cut path: walking away through "Change
    file" left it on disk indefinitely, up to 2 GB each time.
    """
    _sweep_old_files(TEMP_FOLDER, 3600)


@bp.route("/upload-for-cut", methods=["POST"])
def upload_for_cut():
    """Receive a file so it can be previewed before cutting."""
    _sweep_temp_uploads()
    if "file" not in request.files:
        return jsonify({"error": "No file sent"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (
        ".mp3",
        ".mp4",
        ".mkv",
        ".webm",
        ".m4a",
        ".wav",
        ".flac",
        ".ogg",
        ".avi",
        ".mov",
    ):
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    safe_name = sanitize_filename(file.filename)
    temp_id = str(uuid.uuid4())[:8]
    stem, ext = os.path.splitext(safe_name)
    temp_name = f"{temp_id}_{fit_stem(stem, len(temp_id) + 1)}{ext}"
    temp_path = TEMP_FOLDER / temp_name

    file.save(str(temp_path))

    file_size = temp_path.stat().st_size
    duration = _get_media_duration(temp_path)

    if duration <= 0:
        temp_path.unlink(missing_ok=True)
        return jsonify(
            {
                "error": "Could not read the file duration. Unsupported format, or the file is damaged."
            }
        ), 400

    log.info(f"Upload for cut: {safe_name} ({file_size / 1024 / 1024:.1f} MB)")
    return jsonify(
        {
            "success": True,
            "temp_name": temp_name,
            "original_name": file.filename,
            "size": file_size,
            "duration": duration,
        }
    )


@bp.route("/stream-temp/<filename>")
def stream_temp(filename):
    """Serve an uploaded file back for the preview."""
    safe_name = sanitize_filename(filename)
    file_path = (TEMP_FOLDER / safe_name).resolve()
    if not str(file_path).startswith(str(TEMP_FOLDER.resolve())):
        return jsonify({"error": "Access denied"}), 403
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(file_path)


@bp.route("/cut-uploaded", methods=["POST"])
def cut_uploaded():
    """Cut an uploaded file and hand back the id its progress stream will use."""
    # silent: a body that is not JSON, or JSON that is not an object, is a 400 here, not a 500 from .get().
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Missing parameters"}), 400
    temp_name = data.get("temp_name")
    end = data.get("end")
    original_name = data.get("original_name", "")

    if not temp_name or end is None:
        return jsonify({"error": "Missing parameters"}), 400
    bounds, error = parse_cut_bounds(data.get("start", 0), end)
    if error:
        return jsonify({"error": error}), 400
    start, end = bounds

    safe_temp = sanitize_filename(temp_name)
    temp_path = (TEMP_FOLDER / safe_temp).resolve()
    if not str(temp_path).startswith(str(TEMP_FOLDER.resolve())):
        return jsonify({"error": "Access denied"}), 403
    if not temp_path.exists():
        return jsonify({"error": "Source file not found"}), 404

    stem = Path(original_name).stem if original_name else temp_path.stem
    stem = sanitize_filename(stem)
    ext = temp_path.suffix

    audio_exts = (".mp3", ".m4a", ".wav", ".flac", ".ogg")
    dest_folder = MUSIC_FOLDER if ext.lower() in audio_exts else VIDEOS_FOLDER
    cut_id = str(uuid.uuid4())[:8]
    duration = end - start
    with _naming_lock:
        claimed = {e.get("out") for e in cut_progress.values()}
        out_path = dest_folder / _next_versioned_name(dest_folder, stem, ext, claimed)
        cut_progress[cut_id] = {
            "queue": queue.Queue(maxsize=200),
            "deadline": time.time() + CUT_TIMEOUT,
            "cancelled": False,
            "out": out_path,  # claimed until the entry goes: FFmpeg creates the file later
        }
    thread = threading.Thread(
        target=_run_ffmpeg_cut,
        args=(cut_id, temp_path, out_path, start, duration, temp_path),
        daemon=True,
    )
    thread.start()
    return jsonify({"cut_id": cut_id})
