"""Big Downloader's entry point: the Flask app, its HTTP edge and blueprints, and the pywebview window.

Everything else lives in bigdl/, one folder per feature (download, cut, library), each with a pure rules.py
that tests/server_tests.py checks for the Dependency Rule.
"""

import threading
import time

import webview
from flask import Flask, jsonify, render_template

from bigdl.config import (
    BUNDLE_DIR,
    PORT,
)
from bigdl.cut.routes import bp as cut_routes
from bigdl.download.routes import bp as download_routes
from bigdl.jobs import (
    _kill_quietly,
    cut_progress,
    download_progress,
)
from bigdl.library.routes import bp as library_routes
from bigdl.library.storage import (
    _migrate_legacy_folders,
    _sweep_stale_partials,
    _sweep_work_folder,
)
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
app.register_blueprint(library_routes)


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
