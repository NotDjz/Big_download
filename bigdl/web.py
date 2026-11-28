"""The HTTP edge: origin guard, security headers, rate limit, Server-Sent Events.

app.py registers the guard and the headers on the Flask app; nothing here knows about downloads or cuts.
"""

import json
import mimetypes
import queue
import threading
import time

from flask import Response, jsonify, request

from bigdl.config import (
    ALLOWED_FETCH_SITES,
    ALLOWED_HOSTS,
    ALLOWED_ORIGINS,
    MAX_DOWNLOADS_PER_MINUTE,
    SSE_GRACE,
    TIMEOUT_MSG,
)
from bigdl.jobs import TERMINAL_STATUSES

# Windows reads MIME types from the registry, where .js can come back as
# text/plain and .woff2 is unknown entirely. With X-Content-Type-Options:
# nosniff the browser would then refuse the script and the interface would sit
# there dead. Pin the types we depend on.
for _ext, _mime in (
    (".woff2", "font/woff2"),
    (".js", "text/javascript"),
    (".css", "text/css"),
    (".png", "image/png"),
):
    mimetypes.add_type(_mime, _ext)


def request_entity_too_large(e):
    return jsonify({"error": "File too large (2 GB max)"}), 413


def _reject_foreign_origin():
    if request.host not in ALLOWED_HOSTS:
        return jsonify({"error": "Host not allowed"}), 403
    origin = request.headers.get("Origin")
    if origin is not None:
        if origin not in ALLOWED_ORIGINS:
            return jsonify({"error": "Origin not allowed"}), 403
        return None
    # With no Origin there is nothing to conclude from: browsers omit it on a
    # no-cors GET, so a <video src="http://127.0.0.1:5555/stream/..."> on a
    # third-party page walked through the guard and served as an oracle on which
    # files had been downloaded. Sec-Fetch-Site, on the other hand, is always
    # sent.
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site not in ALLOWED_FETCH_SITES:
        return jsonify({"error": "Origin not allowed"}), 403
    return None


def _security_headers(resp):
    # frame-ancestors closes clickjacking: framed inside a third-party page,
    # the app fired its own deletions same-origin, two clicks in.
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers.setdefault(
        "Content-Security-Policy",
        "; ".join(
            (
                "default-src 'self'",
                "img-src 'self' https: data:",  # thumbnails served by the platforms
                "media-src 'self'",
                "frame-ancestors 'none'",
            )
        ),
    )
    return resp


# Rate limiting
_request_times = []
_rate_lock = threading.Lock()


def _rate_limit_check():
    """Rate limiting. True when the request is allowed through."""
    now = time.time()
    with _rate_lock:
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= MAX_DOWNLOADS_PER_MINUTE:
            return False
        _request_times.append(now)
        return True


def _sse_response(registry, key, unknown_msg, poll, deadline_key=None):
    """Drain a job's queue and serve it as Server-Sent Events.

    The two streams, download and cut, were two copies: the leak fix had to be
    written twice, and the copies had already drifted apart, with only the
    download having been given a client-side net.
    """

    def generate():
        entry = registry.get(key)
        if not entry:
            yield f"data: {json.dumps({'status': 'error', 'message': unknown_msg})}\n\n"
            return
        q = entry["queue"]
        try:
            while True:
                # A safety net. The worker now aborts itself, through the
                # yt-dlp hook or the FFmpeg watchdog; give it SSE_GRACE of head
                # start, then free the client rather than leave it waiting.
                if deadline_key and time.time() > entry[deadline_key] + SSE_GRACE:
                    yield f"data: {json.dumps({'status': 'error', 'message': TIMEOUT_MSG})}\n\n"
                    break
                try:
                    event = q.get(timeout=poll)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("status") in TERMINAL_STATUSES:
                        break
                except queue.Empty:
                    yield f"data: {json.dumps({'status': 'heartbeat'})}\n\n"
        finally:
            # In a finally, otherwise a client disconnect (GeneratorExit)
            # left the entry and its queue in memory for good.
            registry.pop(key, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
