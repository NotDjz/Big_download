"""Start Flask and headless Chrome, run tests/ui_smoke.mjs, clean up.

    py tests/run_ui_smoke.py

Requires Node 22+ (for the global WebSocket) and Chrome or Edge. It touches no
file in the repository; the Chrome profile is disposable, created under temp.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CDP_PORT = 9223
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def _busy(port):
    """True if something is already listening on that port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main():
    sys.path.insert(0, str(ROOT))
    import app  # noqa: E402  (the server is the subject of the test)

    # Without these guards, a BIG DL already running or a forgotten Chrome
    # would answer the wait, and the suite would test a different process than
    # it believes, reporting all green for code that was never loaded.
    if _busy(app.PORT):
        print(f"FAILED: port {app.PORT} is already taken. Close BIG DL before running the test.")
        return 2
    if _busy(CDP_PORT):
        print(f"FAILED: port {CDP_PORT} is already taken (a forgotten Chrome?).")
        return 2

    threading.Thread(
        target=lambda: app.app.run(host="127.0.0.1", port=app.PORT, threaded=True),
        daemon=True,
    ).start()
    if not _wait(app.PORT):
        print("FAILED: the Flask server did not start")
        return 2

    chrome = next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
    if not chrome:
        print("FAILED: neither Chrome nor Edge found")
        return 2

    profile = tempfile.mkdtemp(prefix="bigdl-uismoke-")
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", "--disable-gpu", "--mute-audio",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **app._SUBPROCESS_FLAGS,
    )
    try:
        if not _wait(CDP_PORT):
            print("FAILED: Chrome is not exposing CDP")
            return 2
        env = {**os.environ, "CDP_PORT": str(CDP_PORT), "APP_URL": f"http://localhost:{app.PORT}"}
        try:
            return subprocess.call(
                ["node", str(ROOT / "tests" / "ui_smoke.mjs")], env=env, timeout=300)
        except FileNotFoundError:
            print("FAILED: node not found on PATH (Node 22+ required)")
            return 2
        except subprocess.TimeoutExpired:
            print("FAILED: the test ran past 5 minutes")
            return 2
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(profile, ignore_errors=True)
        # The test uploads a video to cut it, and a cancelled cut deliberately
        # keeps its source: without this cleanup, every run leaves several
        # hundred MB in temp_uploads/ until the hourly sweep. No risk to work in
        # progress: this function refuses to start when the application's port
        # is already taken.
        for leftover in app.TEMP_FOLDER.glob('*'):
            if leftover.is_file():
                try:
                    leftover.unlink()
                except OSError:
                    # missing_ok does not cover a Windows lock: FFmpeg may
                    # still hold the file for a moment. The application's hourly
                    # sweep will get it.
                    pass


if __name__ == "__main__":
    sys.exit(main())
