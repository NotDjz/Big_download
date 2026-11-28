"""FFmpeg for the cut: the subprocess, its stall watchdog, its progress pump, and ffprobe's duration."""

import json
import subprocess
import threading
import time

from bigdl.config import (
    _SUBPROCESS_FLAGS,
    CUT_TIMEOUT,
    CUT_TIMEOUT_MSG,
    FFMPEG_PATH,
    FFPROBE_PATH,
    log,
)
from bigdl.cut.rules import _cut_progress
from bigdl.jobs import _kill_quietly, _put_final, _put_progress, cut_progress


def _watch_cut(entry, proc, timed_out):
    """Kill FFmpeg once the cut's deadline passes, checked once a second.

    On its own thread: the deadline used to be evaluated inside the read loop,
    so a stuck FFmpeg that wrote no further line to stderr was never killed.
    The reader pushes the deadline on progress; this only reads it, like the
    SSE net.
    """
    while proc.poll() is None:
        if time.time() > entry["deadline"]:
            timed_out.set()
            _kill_quietly(proc)
            return
        time.sleep(1)


def _pump_cut_progress(proc, entry, q, duration):
    """Relay FFmpeg's progress lines, pushing the deadline when the cut advances."""
    last_us = -1
    for line in proc.stderr:
        progress = _cut_progress(line, last_us, duration)
        if progress is None:
            continue
        last_us, percent, advanced = progress
        if advanced:
            entry["deadline"] = time.time() + CUT_TIMEOUT
        # put_nowait: removing the entry in the finally orphans the queue as
        # soon as the client leaves, and a blocking put then froze the worker
        # (stderr no longer drained, cleanup never done) until the process died.
        _put_progress(q, {"status": "progress", "percent": percent})


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
    q = entry["queue"]

    def fail(message, status="error"):
        """Leaving with nothing to show: one place to clean up.

        A cancellation keeps the source file. Stopping a cut is precisely what
        you do to redo it with different bounds: deleting the source answered
        404 "source file not found" when relaunching, and killed the preview on
        the way. The hourly temp_uploads/ sweep takes care of it instead.
        """
        doomed = (out_path,) if status == "cancelled" else (out_path, temp_cleanup)
        for path in doomed:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        _put_final(q, {"status": status, "message": message})

    try:
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-ss",
            str(start),
            "-i",
            str(input_path),
            "-t",
            str(duration),
            # About 7x faster than the default 'medium', measured on 1080p.
            "-preset",
            "veryfast",
            "-avoid_negative_ts",
            "make_zero",
            "-progress",
            "pipe:2",
            str(out_path),
        ]
        # stdout to DEVNULL: nothing was reading it, and a pipe that is never
        # drained can block FFmpeg as soon as it fills up.
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                # Not the locale codec: cp1252 choked on UTF-8 metadata in the
                # banner, and the error path deleted the source mid-cut.
                text=True,
                encoding="utf-8",
                errors="replace",
                **_SUBPROCESS_FLAGS,
            )
        except FileNotFoundError:  # only here does it mean FFmpeg itself is missing
            fail("FFmpeg not found")
            return
        # Published so /cancel can kill it. Unlike yt-dlp, FFmpeg is a
        # subprocess: stopping it is immediate and certain.
        entry["proc"] = proc
        # Re-read the flag right after: a cancellation landing between
        # creating the process and publishing it would have seen no process to
        # kill, and FFmpeg would have re-encoded the whole clip for nothing.
        if entry.get("cancelled"):
            _kill_quietly(proc)

        timed_out = threading.Event()
        threading.Thread(
            target=_watch_cut, args=(entry, proc, timed_out), daemon=True
        ).start()
        _pump_cut_progress(proc, entry, q, duration)
        proc.wait()

        if entry.get("cancelled"):
            fail("Cut stopped", status="cancelled")
            return

        # returncode too: FFmpeg may finish on its own between the watchdog's
        # poll and its kill, and a finished cut must not be thrown away.
        if timed_out.is_set() and proc.returncode != 0:
            fail(CUT_TIMEOUT_MSG)
            return

        if proc.returncode != 0:
            fail("FFmpeg failed while cutting")
            return

        # FFmpeg can exit 0 having written nothing (a range past the end, after
        # seeking): stat() then raised FileNotFoundError, read as a missing FFmpeg.
        if not out_path.is_file():
            fail("FFmpeg wrote no output")
            return

        if temp_cleanup:
            temp_cleanup.unlink(missing_ok=True)
        size = out_path.stat().st_size
        log.info(f"Cut done: {out_path.name} ({size / 1024 / 1024:.1f} MB)")
        _put_final(
            q,
            {
                "status": "complete",
                "filename": out_path.name,
                "size": size,
                "message": f"Cut finished: {out_path.name}",
            },
        )
    except Exception as e:
        log.error(f"Cut error: {e}")
        fail(str(e))


def _get_media_duration(filepath):
    """Read a media file's duration with ffprobe."""
    try:
        cmd = [
            FFPROBE_PATH,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(filepath),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, **_SUBPROCESS_FLAGS
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0
