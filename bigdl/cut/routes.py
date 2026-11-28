"""The Cut tab's HTTP routes: upload, preview, cut, progress."""

import os
import queue
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from bigdl.config import CUT_TIMEOUT, MUSIC_FOLDER, TEMP_FOLDER, VIDEOS_FOLDER, log
from bigdl.cut.ffmpeg import (
    _get_media_duration,
    _run_ffmpeg_cut,
    claim_lock,
    claimed_outputs,
)
from bigdl.cut.rules import parse_cut_bounds
from bigdl.jobs import _sweep_old_files, cut_progress
from bigdl.library.rules import (
    AUDIO_EXTS,
    VIDEO_EXTS,
    fit_stem,
    is_inside,
    sanitize_filename,
)
from bigdl.web import _sse_response

bp = Blueprint("cut", __name__)

# temp_name -> the duration ffprobe measured at upload.
# shortcut: one small entry per upload, never pruned; prune with the hourly temp sweep if uploads ever pile up.
_upload_durations = {}


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
    if ext not in AUDIO_EXTS + VIDEO_EXTS:
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

    _upload_durations[temp_name] = (
        duration  # after the check: a refused upload leaves nothing
    )
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
    if not is_inside(str(file_path), str(TEMP_FOLDER.resolve())):
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

    safe_temp = sanitize_filename(temp_name)
    temp_path = (TEMP_FOLDER / safe_temp).resolve()
    if not is_inside(str(temp_path), str(TEMP_FOLDER.resolve())):
        return jsonify({"error": "Access denied"}), 403
    if not temp_path.exists():
        return jsonify({"error": "Source file not found"}), 404

    # Against the length measured at upload; a second ffprobe ran synchronously on
    # every click, with nothing to cancel while it hung. 0 if never measured: no clamp.
    bounds, error = parse_cut_bounds(
        data.get("start", 0), end, _upload_durations.get(safe_temp, 0)
    )
    if error:
        return jsonify({"error": error}), 400
    start, end = bounds

    stem = Path(original_name).stem if original_name else temp_path.stem
    stem = sanitize_filename(stem)
    ext = temp_path.suffix

    dest_folder = MUSIC_FOLDER if ext.lower() in AUDIO_EXTS else VIDEOS_FOLDER
    cut_id = str(uuid.uuid4())[:8]
    duration = end - start
    with claim_lock:
        out_path = dest_folder / _next_versioned_name(
            dest_folder, stem, ext, claimed_outputs
        )
        claimed_outputs.add(out_path)  # released by the worker when the cut ends
    cut_progress[cut_id] = {
        "queue": queue.Queue(maxsize=200),
        "deadline": time.time() + CUT_TIMEOUT,
        "cancelled": False,
    }
    try:
        threading.Thread(
            target=_run_ffmpeg_cut,
            args=(cut_id, temp_path, out_path, start, duration, temp_path),
            daemon=True,
        ).start()
    except BaseException:
        # The worker releases its claim; one that never started cannot.
        with claim_lock:
            claimed_outputs.discard(out_path)
        cut_progress.pop(cut_id, None)
        raise
    return jsonify({"cut_id": cut_id})
