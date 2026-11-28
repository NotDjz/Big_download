"""The job kernel shared by downloads and cuts: registries, queue events, the abort signal, sweeps."""

import queue
import shutil
import stat
import time

from bigdl.config import log

# Download progress registry (download_id -> {queue, start_time, deadline, cancelled})
download_progress = {}
# Cut progress registry (cut_id -> {queue, deadline, cancelled, proc})
cut_progress = {}


# The terminal states: _put_final publishes them and _sse_response stops on
# them. This was written out twice, and 'cancelled' had been added to only one
# of the two places.
TERMINAL_STATUSES = ("complete", "error", "cancelled")


class DownloadAborted(Exception):
    """The job must stop. `status` and `message` are what _put_final publishes.

    One class, with the reason carried as data rather than as a subtype. The two
    reasons that exist today, deadline exceeded and user-requested stop, differ
    only in those two fields; a hierarchy separating them forced one except per
    subtype, plus a third laid down "just in case" that no raise could reach.
    Here a future reason adds nothing: it passes a status and a message.

    One class is also what the two loops that re-raise it want (playlist,
    Instagram photos): each sits just above an except Exception that swallows
    and continues, and an incomplete tuple of subtypes would send them off
    again in silence.
    """

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _kill_quietly(proc):
    """Kill a process quietly: it may have finished on its own meanwhile."""
    if not proc:
        return
    try:
        proc.kill()
    except OSError:
        pass


def _put_progress(q, event):
    """Publish a progress event, dropping it if the queue is full.

    A bare put propagated into the generic handler and aborted the whole
    playlist with an empty message, since str(queue.Full()) is ''.
    """
    try:
        q.put_nowait(event)
    except queue.Full:
        pass


def _put_final(q, event):
    """Publish a terminal event, even when the queue is full.

    put_nowait raises queue.Full when the client is not draining; the exception
    propagated into the generic error handler and, for Instagram, wrongly
    triggered the photo fallback.
    """
    try:
        q.put_nowait(event)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()  # single producer: freeing one slot is enough
    except queue.Empty:
        pass
    try:
        q.put_nowait(event)
    except queue.Full:
        log.warning("Terminal event not delivered (queue saturated)")


def _sweep_old_files(folder, max_age):
    """Delete entries in a folder older than max_age.

    Files and directories alike: download threads are daemons, so closing the
    window mid-download kills the worker without running its finally and leaves
    its work directory behind.

    The age guard is the subtle part: these folders are shared, and an
    unconditional sweep erased the .part of a download still in flight, making
    it fail.
    """
    cutoff = time.time() - max_age
    for f in folder.glob("*"):
        try:
            st = f.stat()
            if st.st_mtime >= cutoff:
                continue
            if stat.S_ISDIR(st.st_mode):
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()
        except OSError:
            pass
