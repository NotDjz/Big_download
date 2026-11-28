"""Where things are and how long they may take: paths, the FFmpeg cascade, limits, messages.

The only module that reads the environment (sys.frozen, SystemRoot, the bundle). Everything else receives these as
plain values. Importing it creates the download folders, as importing app.py always did.
"""

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

_SUBPROCESS_FLAGS = {}
if platform.system() == "Windows":
    _SUBPROCESS_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS)
    BASE_DIR = Path(sys.executable).parent
else:
    # The repository root, where templates/, static/ and ffmpeg.exe are: this file sits one level down.
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    BASE_DIR = BUNDLE_DIR


# An absolute path rather than the bare name: CreateProcess searches the
# image's own directory first, and that is the very directory where the
# portable exe writes downloads/, temp_uploads/ and cookies.txt. An explorer.exe
# dropped there would run in our place. Planting it already requires code
# execution under the same identity, so this is not a hole being closed, just
# two lines of hardening.
EXPLORER = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")


# The server listens on loopback, but any web page open on the machine can
# reach it. Checking Host blocks DNS rebinding (an attacker domain repointed at
# 127.0.0.1 would otherwise become same-origin and could list, exfiltrate and
# delete the downloads); checking Origin blocks multipart POSTs and the
# side-effecting GETs a third-party page could fire.
PORT = 5555
ALLOWED_HOSTS = frozenset(f"{h}:{PORT}" for h in ("127.0.0.1", "localhost"))
ALLOWED_ORIGINS = frozenset(f"http://{h}" for h in ALLOWED_HOSTS)


# 'none' is a top-level navigation: it is what the pywebview window sends when
# it opens the app. Excluding it would lock the application out of itself.
ALLOWED_FETCH_SITES = frozenset(("same-origin", "none"))


# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bigdl")


# Configuration
MAX_VIDEO_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB
DOWNLOAD_TIMEOUT = 30 * 60  # 30 minutes per download
MAX_DOWNLOADS_PER_MINUTE = 10
# Seconds a cut may go without progress. Not a total: a fixed 10-minute clock
# killed every long clip, measured at 4x slower than real time on 1080p 240 fps.
CUT_TIMEOUT = 120
CUT_TIMEOUT_MSG = f"Timeout: FFmpeg made no progress for {CUT_TIMEOUT} s"
SSE_GRACE = 60  # slack for the worker to abort before the SSE stream gives up
TIMEOUT_MSG = f"Timeout: download took too long ({DOWNLOAD_TIMEOUT // 60} min max)"
# Same reason as TIMEOUT_MSG: the message travels to the client, so it must not
# exist as two literals that drift apart the first time one is reworded.
CANCEL_MSG = "Download stopped"
# How many videos /get-info enumerates to describe a playlist. It shows none of
# them: the count comes from playlist_count, which the page states without
# anyone having to page through the list. This cap exists only as a fallback
# when that field is missing, and above all it keeps a synchronous route from
# paging to the end. Measured: 1593 videos in 7.3 s unbounded, 1.0 s with it.
PLAYLIST_SCAN = 50


DOWNLOAD_FOLDER = BASE_DIR / "downloads"

if getattr(sys, "frozen", False) and platform.system() == "Windows":
    FFMPEG_PATH = str(BUNDLE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BUNDLE_DIR / "ffprobe.exe")
    FFMPEG_DIR = BUNDLE_DIR
elif (BASE_DIR / "ffmpeg.exe").exists():
    FFMPEG_PATH = str(BASE_DIR / "ffmpeg.exe")
    FFPROBE_PATH = str(BASE_DIR / "ffprobe.exe")
    FFMPEG_DIR = BASE_DIR
else:
    FFMPEG_PATH = "ffmpeg"
    FFPROBE_PATH = "ffprobe"
    FFMPEG_DIR = None  # FFmpeg comes from the system PATH
DOWNLOAD_FOLDER.mkdir(exist_ok=True)


# One folder per media type
VIDEOS_FOLDER = DOWNLOAD_FOLDER / "Videos"
MUSIC_FOLDER = DOWNLOAD_FOLDER / "Music"
PHOTOS_FOLDER = DOWNLOAD_FOLDER / "Photos"
# Scratch space for downloads: one subfolder per job, destroyed when it ends.
# On the same volume as the output folders so that yt-dlp's final move stays a
# rename rather than a copy.
WORK_FOLDER = BASE_DIR / ".work"

VIDEOS_FOLDER.mkdir(exist_ok=True)
MUSIC_FOLDER.mkdir(exist_ok=True)
PHOTOS_FOLDER.mkdir(exist_ok=True)
WORK_FOLDER.mkdir(exist_ok=True)


TEMP_FOLDER = BASE_DIR / "temp_uploads"
TEMP_FOLDER.mkdir(exist_ok=True)
