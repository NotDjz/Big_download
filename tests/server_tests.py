"""Server-side test suite.

    py tests/server_tests.py            # offline, a few seconds
    py tests/server_tests.py --online   # adds the ones that go out to the network

Two rules, each born of an incident:

* **Zero assertions is a failure, not a success.** A counter that drops to zero
  with nothing failing is the worst of both worlds: this suite once came up
  green while a test was no longer receiving its arguments and no longer
  measuring anything.
* **A test without a control run proves nothing.** Each cancellation section
  runs the same code twice, once with the mechanism and once without. If both
  pass, the test is not measuring what it claims to measure.

Monkeypatches are restored after every section, or the order of the tests would
become load-bearing. Patch the module that READS the name: since app.py was split
into bigdl/, a name imported into a module is that module's own binding, so
patching it on `app` changes nothing the code under test sees, silently.
`patched(...)` is for what still lives in app.py; use `patched_on(<module>, ...)`
for the rest (the stall test patches CUT_TIMEOUT on bigdl.cut.ffmpeg).
"""

import ast
import contextlib
import io
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yt_dlp  # noqa: E402

import app  # noqa: E402  (the server is the subject of the test)
from bigdl import config, jobs, web  # noqa: E402  (the kernel app.py is built on)
from bigdl.cut import ffmpeg as cut_ffmpeg  # noqa: E402
from bigdl.cut import routes as cut_routes  # noqa: E402
from bigdl.cut import rules as cut_rules  # noqa: E402
from bigdl.download import instagram, ytdlp  # noqa: E402
from bigdl.download import routes as dl_routes  # noqa: E402
from bigdl.download import rules as dl_rules  # noqa: E402
from bigdl.library import rules as lib_rules  # noqa: E402

ONLINE = "--online" in sys.argv
# app.PORT is the single source of the port number across the whole project.
# Copying it here would turn the suite red with 403s the first time it changed,
# for a reason having nothing to do with what the suite tests.
H = {"Host": "127.0.0.1:%d" % app.PORT}
BS = "\\"

_results = []


def section(title):
    print("\n--- %s ---" % title)


def first_frame(response):
    """The first SSE frame, as text, along with something closeable.

    `Response.response` is typed Iterable: neither `.close()` nor an `in` test
    is statically available on it, and Flask may hand back bytes.
    """
    gen: Any = response
    frame = next(iter(gen))
    if isinstance(frame, bytes):
        frame = frame.decode("utf-8", "replace")
    return frame, gen


def check(name, ok, detail=""):
    _results.append(bool(ok))
    print("  [%s] %s %s" % ("OK  " if ok else "FAIL ", name.ljust(46), detail))


@contextlib.contextmanager
def patched_on(target, **replacements):
    """Replace attributes, then put them back.

    Without the restore, a fake yt-dlp installed by one section would stay in
    place for all the others, and the order of the file would become a tacit
    contract nobody reads.
    """
    previous = {k: getattr(target, k) for k in replacements}
    for k, v in replacements.items():
        setattr(target, k, v)
    try:
        yield
    finally:
        for k, v in previous.items():
            setattr(target, k, v)


def patched(**replacements):
    return patched_on(app, **replacements)


def fake_ydl(cls):
    return patched_on(yt_dlp, YoutubeDL=cls)


def fake_ydl_class(extract, journal=None):
    """Build a stand-in yt_dlp.YoutubeDL class.

    The three sections that needed one rewrote the same
    __init__/__enter__/__exit__; only extract_info differs. `journal` collects
    the options of each instantiation, which is enough to know what yt-dlp was
    asked for.
    """

    class Faux:
        def __init__(self, opts):
            if journal is not None:
                journal.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=True):
            return extract(url, download)

    return Faux


client = app.app.test_client()


# ===================================================== origin guard
def test_origin_guard():
    section("Origin guard")
    cases = [
        ("legitimate Host 127.0.0.1", {"Host": "127.0.0.1:%d" % app.PORT}, "allow"),
        ("legitimate Host localhost", {"Host": "localhost:%d" % app.PORT}, "allow"),
        (
            "attacker Host (DNS rebinding)",
            {"Host": "evil.example:%d" % app.PORT},
            "refuse",
        ),
        ("third-party Origin", {**H, "Origin": "https://evil.example"}, "refuse"),
        (
            "legitimate Origin",
            {**H, "Origin": "http://localhost:%d" % app.PORT},
            "allow",
        ),
    ]
    for name, headers, expected in cases:
        r = client.get("/list-downloads", headers=headers)
        got = "refuse" if r.status_code == 403 else "allow"
        check(
            name,
            got == expected,
            "-> %s %s (expected %s)" % (r.status_code, got, expected),
        )

    # Being multipart, this route escaped the accidental CORS protection the
    # others enjoyed.
    r = client.post(
        "/upload-for-cut",
        headers={**H, "Origin": "https://evil.example"},
        data={"file": (io.BytesIO(b"x"), "x.mp4")},
        content_type="multipart/form-data",
    )
    check(
        "cross-origin multipart upload refused",
        r.status_code == 403,
        "-> %s" % r.status_code,
    )


def test_sec_fetch_site():
    section("Sec-Fetch-Site fallback, and the security headers")
    # Browsers omit Origin on a no-cors GET: a <video src> placed on a
    # third-party site walked through the guard and served as an oracle on which
    # files were present.
    cases = [
        ("pywebview navigation (none)", "none", "allow"),
        ("same-origin subresource", "same-origin", "allow"),
        ("<video> cross-site (the oracle)", "cross-site", "refuse"),
        ("same-site, different origin", "same-site", "refuse"),
    ]
    for name, sfs, expected in cases:
        r = client.get("/stream/Videos/x.mp4", headers={**H, "Sec-Fetch-Site": sfs})
        got = "refuse" if r.status_code == 403 else "allow"
        check(
            name,
            got == expected,
            "-> %s %s (expected %s)" % (r.status_code, got, expected),
        )

    r = client.get("/stream/Videos/x.mp4", headers=H)
    check(
        "client with no Sec-Fetch (curl) allowed",
        r.status_code != 403,
        "-> %s" % r.status_code,
    )

    r = client.get("/", headers={**H, "Sec-Fetch-Site": "none"})
    xfo = r.headers.get("X-Frame-Options")
    csp = r.headers.get("Content-Security-Policy", "")
    check("X-Frame-Options: DENY", xfo == "DENY", "-> %s" % xfo)
    check("CSP frame-ancestors", "frame-ancestors 'none'" in csp, csp[:60])


# ===================================================== path security
def test_sanitize():
    section("sanitize_filename and containment")
    sf = lib_rules.sanitize_filename
    cases = [
        # Inner '..' must NOT be mangled: stripped of a separator they no
        # longer name a parent, and neutralising them broke legitimate names,
        # which were listed and then could not be found on playback.
        ("legitimate name with dots", "Wait... What.mp4", "Wait... What.mp4"),
        ("plain legitimate name", "Video 2024.mp4", "Video 2024.mp4"),
        ("unix traversal", "../../etc/passwd", None),
        ("windows traversal", ".." + BS + ".." + BS + "a.exe", None),
        ("name empty after scrubbing", "..", "_"),
    ]
    for name, entry, expected in cases:
        got = sf(entry)
        if expected is None:
            ok, detail = ("/" not in got and BS not in got), "no separator survives"
        else:
            ok, detail = (got == expected), "expected %r" % expected
        check(name, ok, "%r -> %r (%s)" % (entry, got, detail))

    folder = config.VIDEOS_FOLDER.resolve()
    for entry in [
        "../../etc/passwd",
        ".." + BS + ".." + BS + "a.exe",
        "Wait... What.mp4",
    ]:
        p = (folder / sf(entry)).resolve()
        check(
            "containment %s" % repr(entry)[:26],
            str(p).startswith(str(folder)),
            "stays inside Videos/",
        )


# ===================================================== baseline behaviour
def test_baseline_behaviour():
    section("Saturated queue, sweeping, format, SSE leak")
    # A bare put_nowait raised queue.Full, the exception propagated into the
    # generic handler and wrongly triggered the Instagram photo fallback.
    q = queue.Queue(maxsize=3)
    for i in range(3):
        q.put_nowait({"status": "downloading", "n": i})
    try:
        jobs._put_final(q, {"status": "complete", "filename": "ok.mp4"})
        drained = []
        while not q.empty():
            drained.append(q.get_nowait())
        check(
            "terminal event on a full queue",
            any(e.get("status") == "complete" for e in drained),
            "complete delivered",
        )
    except queue.Full:
        check(
            "terminal event on a full queue",
            False,
            "queue.Full raised (the original bug)",
        )

    folder = Path(tempfile.mkdtemp(prefix="sweep-"))
    fresh, stale = folder / "fresh.tmp", folder / "stale.tmp"
    fresh.write_bytes(b"x")
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 9999, time.time() - 9999))
    jobs._sweep_old_files(folder, 300)
    check(
        "sweep by age",
        fresh.exists() and not stale.exists(),
        "fresh kept, old one deleted",
    )
    shutil.rmtree(folder, ignore_errors=True)

    # merge_output_format and the MP4 converter are only set for
    # youtube/playlist: a bestvideo+bestaudio here would yield an unplayable
    # .mkv.
    fs = dl_rules._get_format_string("social", 480)
    check("quality honoured for social", "height<=480" in fs, fs[:56])

    # Without the generator's finally, a client disconnect left the entry and
    # its queue in memory for good.
    app.download_progress["zz"] = {
        "queue": queue.Queue(maxsize=10),
        "start_time": time.time(),
        "deadline": time.time() + 1800,
    }
    app.download_progress["zz"]["queue"].put_nowait(
        {"status": "downloading", "percent": 1}
    )
    with app.app.test_request_context("/progress/zz", headers=H):
        _, gen = first_frame(dl_routes.progress_stream("zz").response)
        gen.close()
    check("SSE leak on disconnect", "zz" not in app.download_progress, "entry removed")


# ===================================================== deadlines
def test_deadlines():
    section("The deadline belongs to the job")
    start = time.time() - 3000
    entry = {
        "queue": queue.Queue(maxsize=10),
        "start_time": start,
        "deadline": start + 1800,
        "cancelled": False,
    }
    app.download_progress["pl"] = entry
    hook = ytdlp._make_abort_hook(entry)

    raised = False
    try:
        hook({})
    except jobs.DownloadAborted as e:
        raised = e.message == config.TIMEOUT_MSG
    check("deadline passed: the hook raises", raised)

    # The playlist loop pushes it forward per video: a long playlist is a
    # legitimate use. While the SSE net was anchored on start_time, it cut the
    # client off at 31 min while the worker carried on.
    entry["deadline"] = time.time() + config.DOWNLOAD_TIMEOUT
    # except Exception and not DownloadAborted: filtering on the message let a
    # stop of another kind through, and the assertion stayed green.
    leve_encore = None
    try:
        hook({})
    except Exception as e:
        leve_encore = type(e).__name__
    check(
        "deadline pushed forward: the hook stays quiet",
        leve_encore is None,
        "raised: %s" % leve_encore,
    )

    entry["queue"].put_nowait({"status": "downloading", "percent": 50})
    with app.app.test_request_context("/progress/pl", headers=H):
        frame, _ = first_frame(dl_routes.progress_stream("pl").response)
    check(
        "the SSE follows the deadline, not start_time",
        "Timeout" not in frame,
        frame.strip()[:50],
    )
    check(
        "message derived from the constant",
        str(config.DOWNLOAD_TIMEOUT // 60) in config.TIMEOUT_MSG,
        config.TIMEOUT_MSG,
    )


def test_playlist_pushes_deadline():
    section("The playlist loop pushes the deadline forward")

    def extract(url, download):
        if not download:
            return {
                "title": "PL",
                "entries": [{"id": "v%d" % i, "title": "V%d" % i} for i in range(3)],
            }
        return {"title": "ok"}

    start = time.time() - 3000
    entry = {
        "queue": queue.Queue(maxsize=100),
        "start_time": start,
        "deadline": start + config.DOWNLOAD_TIMEOUT,
        "cancelled": False,
    }
    with fake_ydl(fake_ydl_class(extract)):
        ytdlp._run_playlist_download(entry["queue"], "http://x/pl", None, entry, set())
    check(
        "registry deadline pushed forward",
        entry["deadline"] > time.time(),
        "%+d s" % (entry["deadline"] - time.time()),
    )

    app.download_progress["pl2"] = entry
    entry["queue"].put_nowait({"status": "downloading", "percent": 10})
    with app.app.test_request_context("/progress/pl2", headers=H):
        frame, _ = first_frame(dl_routes.progress_stream("pl2").response)
    check(
        "the SSE does not cut the client off",
        "Timeout" not in frame,
        frame.strip()[:50],
    )


# ===================================================== URL routing
RADIO = "https://www.youtube.com/watch?v=MEKERDMnC48&list=RDMEKERDMnC48&start_radio=1"


def test_routing():
    section("Playlist versus video routing, and noplaylist")
    calls = []

    def extract(url, download):
        if calls[-1].get("extract_flat"):
            # 4 entries returned, 1593 announced: exactly the shape
            # playlistend produces, the bound set on the enumeration.
            return {
                "title": "Ma Playlist",
                "uploader": "Moi",
                "playlist_count": 1593,
                "entries": [
                    {"title": "V%d" % i, "duration": 10, "url": "u%d" % i}
                    for i in range(4)
                ],
            }
        return {
            "title": "A video",
            "formats": [],
            "duration": 12,
            "extractor_key": "Youtube",
        }

    with fake_ydl(fake_ydl_class(extract, journal=calls)):
        calls.clear()
        d = client.post(
            "/get-info",
            headers=H,
            json={"url": "https://www.youtube.com/playlist?list=PLabc"},
        ).get_json()
        check("playlist recognised by the server", d.get("is_playlist") is True)
        check("flat extraction used", any(o.get("extract_flat") for o in calls))
        # The count comes from playlist_count, not from the enumerated
        # length: without that, bounding the preview would have made the
        # display lie.
        check(
            "total count, not the truncated length",
            d.get("video_count") == 1593,
            str(d.get("video_count")),
        )
        check(
            "no video list in the response",
            "videos" not in d,
            "nobody was reading that payload",
        )
        check(
            "the enumeration is bounded",
            any(o.get("playlistend") == config.PLAYLIST_SCAN for o in calls),
            "playlistend=%s" % [o.get("playlistend") for o in calls],
        )
        check(
            "playlist: noplaylist absent", not any(o.get("noplaylist") for o in calls)
        )

        calls.clear()
        d2 = client.post(
            "/get-info",
            headers=H,
            json={"url": "https://www.youtube.com/watch?v=abc12345678"},
        ).get_json()
        check(
            "single video: no flat extraction",
            not any(o.get("extract_flat") for o in calls),
        )
        check("single video: is_playlist false", d2.get("is_playlist") is False)
        check(
            "single video: noplaylist passed through",
            bool(calls) and all(o.get("noplaylist") for o in calls),
            "%d call(s)" % len(calls),
        )

        # The reported link: a YouTube radio. is_playlist_url already says
        # "one video", but without noplaylist yt-dlp unrolled the radio, which
        # has no end. Measured: over 100 s with no answer, against 1 s with.
        calls.clear()
        d3 = client.post("/get-info", headers=H, json={"url": RADIO}).get_json()
        check("radio: treated as a video", d3.get("is_playlist") is False)
        check(
            "radio: no flat extraction", not any(o.get("extract_flat") for o in calls)
        )
        check(
            "radio: noplaylist passed through",
            bool(calls) and all(o.get("noplaylist") for o in calls),
            "%d call(s)" % len(calls),
        )

    # 'mp3' and not only 'youtube': that is the type where the option really
    # carries weight. For youtube/playlist, start_download goes through
    # clean_youtube_url, which already strips the '&list='.
    for t in ("mp3", "youtube", "social"):
        o = ytdlp._build_ydl_opts(
            t,
            RADIO,
            None,
            lambda d: None,
            {"deadline": 9e9, "cancelled": False},
            "C:" + BS + "tmp",
        )
        check("worker '%s': noplaylist set" % t, o.get("noplaylist") is True)

    check(
        "flat extractions: noplaylist absent",
        "noplaylist" not in ytdlp._flat_ydl_opts(),
        str(sorted(ytdlp._flat_ydl_opts())),
    )
    # The download needs the WHOLE list: the bound must exist only where it is
    # asked for explicitly.
    check(
        "the flat options bound nothing",
        "playlistend" not in ytdlp._flat_ydl_opts(),
        "the preview sets it, not the download",
    )
    check(
        "clean_youtube_url strips the list=",
        "list=" not in dl_rules.clean_youtube_url(RADIO),
        dl_rules.clean_youtube_url(RADIO),
    )

    r = client.post("/get-playlist-info", headers=H, json={})
    check("old endpoint removed", r.status_code == 404, "code=%s" % r.status_code)


# ===================================================== Instagram photos
def test_photo_has_no_audio():
    section("A photo post must not offer an MP3")
    calls = []

    def extract(url, download):
        # Extraction succeeds but yields no formats: that is the real shape of
        # a photo post, and it is the very branch that makes _is_photo true.
        return {
            "title": "A photo",
            "formats": [],
            "duration": 0,
            "extractor_key": "Instagram",
        }

    with fake_ydl(fake_ydl_class(extract, journal=calls)):
        d = (
            client.post(
                "/get-info", headers=H, json={"url": "https://www.instagram.com/p/ABC/"}
            ).get_json()
            or {}
        )
    check("recognised as a photo", d.get("_is_photo") is True, str(d.get("_is_photo")))
    # has_audio defaulted to True "for lack of formats", the very branch that
    # makes _is_photo true. The client therefore drew an MP3 button after the
    # Photo one, and the MP3 is what ended up selected.
    check(
        "no audio track announced",
        d.get("has_audio") is False,
        "has_audio = %s" % d.get("has_audio"),
    )
    check("no video quality", not d.get("available_qualities"))


def test_playlist_partial_count():
    section("Playlist count: a floor announced as one")
    for playlist_count, entrees, attendu_partiel in (
        (1593, 4, False),
        (None, 60, True),
    ):
        calls = []

        def extract(url, download, pc=playlist_count, n=entrees):
            info = {
                "title": "PL",
                "uploader": "Moi",
                "entries": [{"title": "V%d" % i, "url": "u%d" % i} for i in range(n)],
            }
            if pc:
                info["playlist_count"] = pc
            return info

        with fake_ydl(fake_ydl_class(extract, journal=calls)):
            d = (
                client.post(
                    "/get-info",
                    headers=H,
                    json={"url": "https://www.youtube.com/playlist?list=PLx"},
                ).get_json()
                or {}
            )
        # Without playlist_count the fallback is the ENUMERATED number, itself
        # capped by playlistend: announcing "50" for 3000 videos would be a
        # silent lie, hence the flag the client renders as "50+".
        check(
            "partial = %s (playlist_count=%s)" % (attendu_partiel, playlist_count),
            d.get("video_count_partial") is attendu_partiel,
            "video_count=%s partiel=%s"
            % (d.get("video_count"), d.get("video_count_partial")),
        )


def test_photo_strategy():
    section("Instagram photos: a strategy chosen upfront")
    # Without this patch the route starts a real thread that calls the
    # Instagram API with whatever cookies sit in BASE_DIR, and a success would
    # write into the real downloads/Photos/. The test was green by accident of
    # configuration. The route's own worker is replaced, not what it calls: the
    # thread's target is bound when the route creates it, inside this block, so
    # a thread scheduled after the block ends still runs the no-op. Patching
    # _download_instagram_images instead let a late thread read the real one.
    with patched_on(dl_routes, _run_download=lambda *a, **k: None):
        r = client.post(
            "/start-download",
            headers=H,
            json={"url": "https://www.instagram.com/p/ABC/", "type": "photo"},
        )
        check(
            "photo accepted on Instagram",
            r.status_code == 200,
            "code=%s" % r.status_code,
        )
        r = client.post(
            "/start-download",
            headers=H,
            json={"url": "https://www.tiktok.com/@x/video/1", "type": "photo"},
        )
        check(
            "photo refused elsewhere", r.status_code == 400, "code=%s" % r.status_code
        )

    seen = {}

    instantiations = []

    def refuse(url, download):
        raise RuntimeError("yt-dlp must not be called")

    # entry is positional and mandatory, with no default: it is what carries
    # the cancellation flag. A default would let a caller forget it, and
    # stopping photos would go inert again without anything breaking.
    def fake_images(q, url, entry):
        seen["img"] = True
        seen["entry"] = entry
        jobs._put_final(q, {"status": "complete", "title": "ok", "filename": "x.jpg"})

    q = queue.Queue(maxsize=100)
    now = time.time()
    app.download_progress["t1"] = {
        "queue": q,
        "start_time": now,
        "deadline": now + 1800,
        "cancelled": False,
    }
    with (
        fake_ydl(fake_ydl_class(refuse, journal=instantiations)),
        patched_on(ytdlp, _download_instagram_images=fake_images),
    ):
        ytdlp._run_download("t1", "https://www.instagram.com/p/ABC/", "photo")

    check(
        "yt-dlp not called at all",
        not instantiations,
        "%d instantiation(s)" % len(instantiations),
    )
    check("image downloader called directly", seen.get("img") is True)
    ev = []
    while not q.empty():
        ev.append(q.get_nowait())
    check("terminal event emitted", any(e.get("status") == "complete" for e in ev))
    check(
        "the registry entry is passed through",
        seen.get("entry") is app.download_progress.get("t1"),
    )


# ===================================================== cancellation
def test_cancel_route():
    section("Route /cancel")
    now = time.time()
    app.download_progress["dl1"] = {
        "queue": queue.Queue(maxsize=10),
        "start_time": now,
        "deadline": now + 1800,
        "cancelled": False,
    }
    r = client.post("/cancel/dl1", headers=H)
    check(
        "cancelling a download",
        r.status_code == 200 and app.download_progress["dl1"]["cancelled"],
    )

    class FauxProc:
        def __init__(self):
            self.tue = False

        def kill(self):
            self.tue = True

    proc = FauxProc()
    app.cut_progress["cut1"] = {
        "queue": queue.Queue(maxsize=10),
        "cancelled": False,
        "proc": proc,
    }
    r = client.post("/cancel/cut1", headers=H)
    check(
        "cancelling a cut: the process is killed",
        r.status_code == 200 and proc.tue and app.cut_progress["cut1"]["cancelled"],
    )

    r = client.post("/cancel/inexistant", headers=H)
    d = r.get_json()
    check(
        "unknown job: success, no error",
        r.status_code == 200 and d.get("success") is True,
        str(d),
    )

    hook = ytdlp._make_abort_hook(app.download_progress["dl1"])
    raised = None
    try:
        hook({})
    except jobs.DownloadAborted as e:
        raised = e.status
    # Assert on the published status, not on the class name: the status is
    # what the client sees, and it is what has to stay stable.
    check(
        "the hook asks for a 'cancelled' stop",
        raised == "cancelled",
        "raised: %s" % raised,
    )

    app.download_progress["dl1"]["cancelled"] = False
    rien = None
    try:
        hook({})
    except Exception as e:
        rien = type(e).__name__
    check("no flag: the hook stays quiet", rien is None, "raised: %s" % rien)


def _instagram_continue(cancel):
    """One run of the photo loop where EVERY conversion fails.

    That is the `continue` path, which skipped the check entirely while it sat
    at the end of the loop body.
    """
    NB = 5
    scratch = Path(tempfile.mkdtemp(prefix="ig-"))
    cookies = scratch / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    entry = {"queue": queue.Queue(maxsize=100), "cancelled": False}
    gets = []

    class Resp:
        def __init__(self, payload=None):
            self.status_code = 200
            self._p = payload
            self.content = b"\xff\xd8\xff\xe0 fausses donnees"

        def json(self):
            return self._p

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self):
            self.headers = {}
            self.cookies = None

        def get(self, url, timeout=None):
            assert timeout and timeout > 0, "call with no timeout"
            gets.append(url)
            if "/api/v1/" in url:
                media = [
                    {"image_versions2": {"candidates": [{"url": "https://img/%d" % i}]}}
                    for i in range(NB)
                ]
                return Resp({"items": [{"carousel_media": media}]})
            if cancel and len([g for g in gets if g.startswith("https://img/")]) >= 2:
                entry["cancelled"] = True
            return Resp()

    fake_requests = types.SimpleNamespace(Session=Session)

    photos = scratch / "Photos"
    photos.mkdir()
    raised = None
    try:
        with patched_on(
            instagram,
            _get_cookies_file=lambda: str(cookies),
            PHOTOS_FOLDER=photos,
            _convert_to_jpg=lambda p: p,
            requests=fake_requests,
        ):
            instagram._download_instagram_images(
                entry["queue"], "https://www.instagram.com/p/ABCDEFG/", entry
            )
    except jobs.DownloadAborted as e:
        raised = e.status
    except Exception as e:
        raised = "AUTRE: %s" % type(e).__name__

    ev = []
    while not entry["queue"].empty():
        ev.append(entry["queue"].get_nowait())
    final = next((e for e in ev if e.get("status") in jobs.TERMINAL_STATUSES), {})
    images = [g for g in gets if g.startswith("https://img/")]
    shutil.rmtree(scratch, ignore_errors=True)
    return raised, len(images), final.get("status"), NB


def test_instagram_cancellation():
    section("Instagram photos: stopping on the `continue` path")
    raised, n, status, total = _instagram_continue(cancel=True)
    check("the stop is seen", raised == "cancelled", "raised: %s" % raised)
    check("the loop stops early", n < total, "%d image(s) out of %d" % (n, total))
    check("no misleading terminal event", status is None, "published: %s" % status)

    # The control: without the flag, the same loop must visit EVERY image and
    # end in an error. If both runs stopped at two images, the test would be
    # measuring something other than cancellation.
    raised, n, status, total = _instagram_continue(cancel=False)
    check("control: no stop raised", raised is None, "raised: %s" % raised)
    check("control: every image visited", n == total, "%d of %d" % (n, total))
    check("control: ends in 'error'", status == "error", "published: %s" % status)


def _build_source(target):
    """A video long enough that there is time to cancel its cut."""
    subprocess.run(
        [
            config.FFMPEG_PATH,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=30",
            "-t",
            "40",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(target),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **config._SUBPROCESS_FLAGS,
    )
    return target.exists()


def _cut_run(mode, template):
    """One cut run: cancelled, driven into an error, or a short cut that succeeds ("ok").

    Only a success may delete the source: a cancellation or an error keeps it,
    for the user to cut again. The success run is the control: if it kept the
    source too, the test would be measuring a cleanup gone inert, not the
    other branches.

    Each run gets its OWN copy, since the control deletes its own and that is
    exactly what it checks, but the video is encoded only once.
    """
    scratch = Path(tempfile.mkdtemp(prefix="cut-"))
    source = scratch / "source.mp4"
    shutil.copy(template, source)

    cut_id = "testcut"
    q = queue.Queue(maxsize=100)
    # A deadline, as the route sets one: the watchdog reads it.
    app.cut_progress[cut_id] = {
        "queue": q,
        "cancelled": False,
        "deadline": time.time() + config.CUT_TIMEOUT,
    }
    # In error mode: an extension FFmpeg refuses, so it exits non-zero.
    target = scratch / ("output.unknownext" if mode == "error" else "output.mp4")

    t = threading.Thread(
        target=cut_ffmpeg._run_ffmpeg_cut,
        args=(cut_id, source, target, 0, 1 if mode == "ok" else 40, source),
        daemon=True,
    )
    t.start()
    if mode == "cancel":
        limit = time.time() + 20
        while time.time() < limit and not app.cut_progress[cut_id].get("proc"):
            time.sleep(0.05)
        time.sleep(0.5)
        # Through the ROUTE, not by raising the flag by hand: _run_ffmpeg_cut
        # only re-reads that flag after proc.wait(), so it is /cancel, through
        # _kill_quietly, that actually interrupts FFmpeg. Raising it directly
        # let the cut run to completion and checked nothing but the bookkeeping
        # afterwards.
        client.post("/cancel/%s" % cut_id, headers=H)
    t.join(timeout=90)

    ev = []
    while not q.empty():
        ev.append(q.get_nowait())
    final = next((e for e in ev if e.get("status") in jobs.TERMINAL_STATUSES), {})
    progress = max(
        [e.get("percent", 0) for e in ev if e.get("status") == "progress"] or [0]
    )
    result = (final.get("status"), source.exists(), target.exists(), progress)
    shutil.rmtree(scratch, ignore_errors=True)
    return result


def test_cut_cancellation():
    section("Cut: only a success deletes the source")
    workshop = Path(tempfile.mkdtemp(prefix="cut-template-"))
    template = workshop / "template.mp4"
    if not _build_source(template):
        check("FFmpeg available for this test", False, "test source could not be built")
        shutil.rmtree(workshop, ignore_errors=True)
        return
    status, source, output, progress = _cut_run("cancel", template)
    check("terminal state = cancelled", status == "cancelled", "got: %s" % status)
    # The point of the test: FFmpeg must be INTERRUPTED, not merely recorded
    # as cancelled. Without the kill, the cut reached 99.8%.
    check(
        "FFmpeg really is interrupted",
        progress < 50,
        "peak progress %.1f %%" % progress,
    )
    # You stop a cut in order to redo it with different bounds: deleting the
    # source answered 404 "source file not found" when relaunching, dead
    # preview included.
    check("the source is kept", source is True)
    check("no partial output left behind", output is False)

    # An error keeps the source too: bad bounds or a broken file are the cut's fault, not the upload's, and
    # deleting it on every failure turned each new kind of bad input into a lost file.
    status, source, output, _ = _cut_run("error", template)
    check("terminal state = error", status == "error", "got: %s" % status)
    check("an error keeps the source", source is True)
    status, source, output, _ = _cut_run("ok", template)
    check(
        "control: terminal state = complete", status == "complete", "got: %s" % status
    )
    check("control: a success deletes the source", source is False)
    check("control: and leaves its output", output is True)
    shutil.rmtree(workshop, ignore_errors=True)


# Stand-ins for FFmpeg, run with the real interpreter: argv[1] is the output path.
_FFMPEG_SLOW = r"""
import sys, time
print('out_time_us=N/A', file=sys.stderr, flush=True)  # as the real one does at first
for i in range(10):
    print('out_time_us=%d' % ((i + 1) * 1000000), file=sys.stderr, flush=True)
    time.sleep(0.3)
open(sys.argv[1], 'wb').write(b'x')
"""
# Still talking, but out_time_us never moves: that is a stall too.
_FFMPEG_FROZEN = r"""
import sys, time
for _ in range(30):
    print('out_time_us=1000000', file=sys.stderr, flush=True)
    time.sleep(0.3)
"""
# FFmpeg's banner echoes metadata as UTF-8; 0x9D is undefined in cp1252.
_FFMPEG_UTF8 = r"""
import sys
sys.stderr.buffer.write(b'  title : \xe2\x80\x9d\n')
sys.stderr.flush()
print('out_time_us=10000000', file=sys.stderr, flush=True)
open(sys.argv[1], 'wb').write(b'x')
"""
_FFMPEG_SILENT = r"""
import sys, time
print('out_time_us=1000000', file=sys.stderr, flush=True)
time.sleep(10)
"""


def _stall_run(script):
    """One cut through a fake FFmpeg, with CUT_TIMEOUT shrunk to 1 s."""
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kw):
        return real_popen([sys.executable, "-c", script, cmd[-1]], **kw)

    scratch = Path(tempfile.mkdtemp(prefix="cut-stall-"))
    cut_id = "stallcut"
    q = queue.Queue(maxsize=100)
    started = time.time()
    entry = {"queue": q, "cancelled": False, "deadline": started + 1}
    app.cut_progress[cut_id] = entry
    with (
        patched_on(cut_ffmpeg, CUT_TIMEOUT=1),
        patched_on(subprocess, Popen=fake_popen),
    ):
        t = threading.Thread(
            target=cut_ffmpeg._run_ffmpeg_cut,
            args=(cut_id, scratch / "in.mp4", scratch / "out.mp4", 0, 10),
            daemon=True,
        )
        t.start()
        t.join(timeout=20)
    elapsed = time.time() - started
    ev = []
    while not q.empty():
        ev.append(q.get_nowait())
    final = next((e for e in ev if e.get("status") in jobs.TERMINAL_STATUSES), {})
    shutil.rmtree(scratch, ignore_errors=True)
    return final, entry["deadline"] - started, elapsed


# Standard-library modules a rule may not use either: they reach a process, the disk, the network, a thread, the
# clock, the interpreter or another module by name.
_RULE_IO = {
    "subprocess",
    "socket",
    "threading",
    "urllib",
    "http",
    "shutil",
    "time",
    "tempfile",
    "asyncio",
    "os",
    "io",
    "pathlib",
    "glob",
    "importlib",
    "sys",
}
# The pure corners of two of them: path arithmetic and URL parsing, no os.remove, no urlopen.
_RULE_PURE = {"os.path", "urllib.parse"}


def _rule_violations(path, source=None):
    """Imports of one rules.py that break the Dependency Rule, as readable strings."""
    tree = ast.parse(source if source is not None else path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level:
            # relative: inside bigdl, so only another rules module may be named
            names = ["bigdl." + (node.module or "")]
        elif isinstance(node, ast.ImportFrom):
            # `from urllib import request` names urllib.request, not urllib
            names = (
                [f"{node.module}.{a.name}" for a in node.names]
                if node.module in _RULE_IO
                else [node.module]
            )
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if any(name == ok or name.startswith(ok + ".") for ok in _RULE_PURE):
                continue
            if top == "bigdl":
                if not name.endswith("rules"):
                    bad.append(
                        name
                    )  # a rule leaning on an adapter, a route or the config
            elif top not in sys.stdlib_module_names or top in _RULE_IO:
                bad.append(name)
    return bad


def test_architecture():
    section("Architecture: rules depend on nothing outside")
    # Clean Architecture's Dependency Rule, checked on every change rather than at review: a rules module may
    # import the standard library (no I/O) and other rules modules, nothing else. No Flask, no yt-dlp, no
    # requests, no subprocess, no config: that is what lets a rule be tested alone.
    files = sorted((ROOT / "bigdl").rglob("rules.py"))
    check(
        "rules modules found",
        len(files) >= 2,
        ", ".join(str(f.relative_to(ROOT)) for f in files),
    )
    for f in files:
        bad = _rule_violations(f)
        check(
            f"{f.relative_to(ROOT).as_posix()} imports nothing outside",
            not bad,
            ", ".join(bad),
        )
    # Control: the same scan on a module that does I/O must flag it, or the check above proves nothing.
    flagged = _rule_violations(ROOT / "bigdl" / "cut" / "ffmpeg.py")
    check(
        "control: an adapter is flagged",
        "subprocess" in flagged and "bigdl.config" in flagged,
        flagged,
    )
    # Control: the network through every spelling of urllib is refused, its pure parse module is not.
    spellings = (
        "import urllib\nfrom urllib import request\nimport urllib.request\n"
        "from urllib.parse import urlparse\nfrom urllib import parse\nimport os\nfrom os.path import splitext\n"
    )
    flagged = _rule_violations(None, spellings)
    check(
        "control: urllib and os are refused, their pure parts are not",
        flagged == ["urllib", "urllib.request", "urllib.request", "os"],
        flagged,
    )


def test_cut_bounds_rule():
    section("Cut: bounds and names, the rules on their own")
    bounds = cut_rules.parse_cut_bounds
    check("a normal range passes", bounds(1, 5.5) == ((1.0, 5.5), None), bounds(1, 5.5))
    # float() takes all of these, and each one sent FFmpeg off to fail, the error path deleting the upload.
    for start, end in (
        ("nan", 5),
        (0, "inf"),
        (0, float("inf")),
        (-1, 5),
        (3, 3),
        (5, 2),
        ("x", 2),
        (None, 2),
        (False, True),  # float(True) is 1.0: a malformed body passed as the range 0-1 s
    ):
        result = bounds(start, end)
        check(
            f"refused: start={start!r} end={end!r}",
            result[0] is None and result[1],
            result,
        )
    # Against the media's own length: a start past the end made FFmpeg write nothing.
    check(
        "a start past the media is refused",
        bounds(20, 30, 10)[0] is None,
        bounds(20, 30, 10),
    )
    check(
        "an end past the media is clamped to it",
        bounds(2, 30, 10) == ((2.0, 10.0), None),
        bounds(2, 30, 10),
    )
    check(
        "no known length: no clamp", bounds(2, 30) == ((2.0, 30.0), None), bounds(2, 30)
    )
    # Judged as FFmpeg will see them, to the millisecond: 9.9999 was a valid start in a 10 s file, then reached
    # FFmpeg as "-ss 9.999 -t 0.000", which wrote an empty clip.
    check(
        "bounds are judged rounded",
        bounds(9.9999, 20, 10)[0] is None,
        bounds(9.9999, 20, 10),
    )
    # The clamp too: an unrounded length brought back a 0.4 ms range, sent as "-t 0.000".
    check(
        "the clamped range is judged rounded",
        bounds(10.0002, 20, 10.0004)[0] is None,
        bounds(10.0002, 20, 10.0004),
    )
    check(
        "a start just below zero is negative",
        bounds(-0.0004, 5)[0] is None,
        bounds(-0.0004, 5),
    )
    check(
        "a sub-millisecond range is empty",
        bounds(1.00004, 1.00049)[0] is None,
        bounds(1.00004, 1.00049),
    )
    # With no known length, nothing bounded the values: FFmpeg refused 1e23 ("Invalid duration").
    for start, end in ((0, 1e23), (1e300, 1e301)):
        check(
            f"refused as too large: {end!r}",
            bounds(start, end)[0] is None,
            bounds(start, end),
        )

    fit = lib_rules.fit_stem
    long = "a" * 250
    check("a short stem is left alone", fit("clip", 9) == "clip")
    check(
        "a long stem leaves room for what the server adds",
        len(fit(long, 9)) == 200 - 9,
        len(fit(long, 9)),
    )
    # The whole point: what the server composes must survive the sanitize every later route applies.
    name = f"abcd1234_{fit(long, 9)}.mp4"
    check(
        "a composed name survives sanitize_filename",
        lib_rules.sanitize_filename(name) == name,
        len(name),
    )


# Exits 0 having written only a header and no frame (measured: a 262-byte file for -ss past the last frame).
_FFMPEG_HEADER_ONLY = r"""
import sys
print('out_time_us=0', file=sys.stderr, flush=True)
open(sys.argv[1], 'wb').write(b'h' * 262)
"""

# Exits 0 after reporting progress, having written nothing: the output file is missing.
_FFMPEG_NO_OUTPUT = r"""
import sys
print('out_time_us=1000000', file=sys.stderr, flush=True)
"""


def test_cut_hardening():
    section("Cut: the route refuses bad input and keeps the user's file")
    scratch = Path(tempfile.mkdtemp(prefix="cut-hard-"))
    temp, out = scratch / "temp", scratch / "out"
    temp.mkdir()
    out.mkdir()
    started = []

    def record(cut_id, input_path, out_path, start, duration, temp_cleanup=None):
        started.append(
            out_path.name
        )  # writes nothing, as a cut still running would not have yet

    with patched_on(
        cut_routes,
        TEMP_FOLDER=temp,
        VIDEOS_FOLDER=out,
        MUSIC_FOLDER=out,
        _run_ffmpeg_cut=record,
        _get_media_duration=lambda p: 10.0,
    ):
        source = temp / "abcd1234_clip.mp4"
        source.write_bytes(b"x")
        cut_routes._upload_durations["abcd1234_clip.mp4"] = (
            10.0  # as its upload would have measured
        )
        for body in (
            '{"temp_name": "abcd1234_clip.mp4", "start": 0, "end": Infinity}',
            '{"temp_name": "abcd1234_clip.mp4", "start": NaN, "end": 5}',
            '{"temp_name": "abcd1234_clip.mp4", "start": -2, "end": 5}',
            "[]",
            "null",
        ):
            r = client.post(
                "/cut-uploaded", headers=H, data=body, content_type="application/json"
            )
            check(f"400 for {body[:40]}", r.status_code == 400, f"code={r.status_code}")
        check("no cut was started", started == [], started)
        check("the upload is still there", source.exists())

        # Two cuts of the same file before either has written its output: two names, not one _v2 twice.
        started.clear()
        for _ in range(2):
            client.post(
                "/cut-uploaded",
                headers=H,
                json={
                    "temp_name": "abcd1234_clip.mp4",
                    "start": 0,
                    "end": 5,
                    "original_name": "clip.mp4",
                },
            )
        check("concurrent cuts get distinct names", len(set(started)) == 2, started)

        # A long name: the upload's prefix and the cut's _v2 used to push it past 200 characters, and the
        # next sanitize_filename truncated it to a file that does not exist (404 on preview, then on cut).
        long_name = "b" * 195 + ".mp4"
        r = client.post(
            "/upload-for-cut",
            headers=H,
            data={"file": (io.BytesIO(b"y"), long_name)},
            content_type="multipart/form-data",
        )
        temp_name = (r.get_json() or {}).get("temp_name", "")
        r = client.get(f"/stream-temp/{temp_name}", headers=H)
        check(
            "a long upload can be previewed",
            r.status_code == 200,
            f"code={r.status_code}",
        )
        r.close()
        started.clear()
        r = client.post(
            "/cut-uploaded",
            headers=H,
            json={
                "temp_name": temp_name,
                "start": 0,
                "end": 5,
                # 199 characters: + "_v2" would pass 200 without fit_stem.
                "original_name": "c" * 199 + ".mp4",
            },
        )
        named = started[0] if started else ""
        check(
            "a long upload can be cut",
            r.status_code == 200 and named,
            f"code={r.status_code}",
        )
        check(
            "its output name survives sanitize_filename",
            named and lib_rules.sanitize_filename(named) == named,
            len(named),
        )

        # A start past the media's end: refused here, since FFmpeg would have written nothing.
        r = client.post(
            "/cut-uploaded",
            headers=H,
            json={"temp_name": "abcd1234_clip.mp4", "start": 20, "end": 30},
        )
        check(
            "a start past the media is a 400",
            r.status_code == 400,
            f"code={r.status_code}",
        )

        # The claim on a name outlives the progress stream: the client closing it (the SSE finally pops
        # the entry) while FFmpeg had not created the file yet let the next cut take the same _v2.
        started.clear()
        client.post(
            "/cut-uploaded",
            headers=H,
            json={
                "temp_name": "abcd1234_clip.mp4",
                "start": 0,
                "end": 5,
                "original_name": "other.mp4",
            },
        )
        jobs.cut_progress.clear()
        client.post(
            "/cut-uploaded",
            headers=H,
            json={
                "temp_name": "abcd1234_clip.mp4",
                "start": 0,
                "end": 5,
                "original_name": "other.mp4",
            },
        )
        check(
            "a name stays claimed after the stream closes",
            len(set(started)) == 2,
            started,
        )

        # NTFS ignores case: Clip_v2.mp4 and clip_v2.mp4 are one file, so they are one claim.
        started.clear()
        for original in ("Clip.mp4", "clip.mp4"):
            client.post(
                "/cut-uploaded",
                headers=H,
                json={
                    "temp_name": "abcd1234_clip.mp4",
                    "start": 0,
                    "end": 5,
                    "original_name": original,
                },
            )
        check(
            "case variants get distinct names (WindowsPath ignores case)",
            len({n.lower() for n in started}) == 2,
            started,
        )

        # The worker releases a claim; if the worker never starts, the route must.
        cut_ffmpeg.claimed_outputs.clear()

        class NoThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        with patched_on(threading, Thread=NoThread):
            r = client.post(
                "/cut-uploaded",
                headers=H,
                json={"temp_name": "abcd1234_clip.mp4", "start": 0, "end": 5},
            )
        check(
            "a worker that cannot start leaves no claim",
            not cut_ffmpeg.claimed_outputs,
            cut_ffmpeg.claimed_outputs,
        )

        # The upload's measured length is what the cut is judged against, without a second ffprobe.
        r = client.post(
            "/upload-for-cut",
            headers=H,
            data={"file": (io.BytesIO(b"m"), "measured.mp4")},
            content_type="multipart/form-data",
        )
        measured = (r.get_json() or {}).get("temp_name", "")
    with patched_on(
        cut_routes,
        TEMP_FOLDER=temp,
        VIDEOS_FOLDER=out,
        MUSIC_FOLDER=out,
        _run_ffmpeg_cut=record,
        _get_media_duration=lambda p: 0.0,  # a second ffprobe would know nothing
    ):
        r = client.post(
            "/cut-uploaded",
            headers=H,
            json={"temp_name": measured, "start": 20, "end": 30},
        )
        check(
            "the upload's length is reused",
            r.status_code == 400,
            f"code={r.status_code}",
        )
    cut_ffmpeg.claimed_outputs.clear()

    final, _, _ = _stall_run(_FFMPEG_HEADER_ONLY)
    check(
        "a header-only output is no output",
        final.get("message") == "FFmpeg wrote no output",
        final,
    )

    final, _, _ = _stall_run(_FFMPEG_NO_OUTPUT)
    check(
        "no output is reported as such, not as a missing FFmpeg",
        final.get("message") == "FFmpeg wrote no output",
        final,
    )

    # Through the real worker, with a source to lose: no output keeps it, and the bounds reach FFmpeg in
    # fixed-point (it rejects str(1e-05) == "1e-05").
    source = scratch / "keep.mp4"
    source.write_bytes(b"z")
    seen_cmd = []
    real_popen = subprocess.Popen

    def no_output_popen(cmd, **kw):
        seen_cmd.extend(cmd)
        return real_popen([sys.executable, "-c", _FFMPEG_NO_OUTPUT], **kw)

    jobs.cut_progress["keep"] = {
        "queue": queue.Queue(maxsize=100),
        "cancelled": False,
        "deadline": time.time() + 60,
    }
    cut_ffmpeg.claimed_outputs.add(scratch / "keep_v2.mp4")  # as the route claims it
    with patched_on(subprocess, Popen=no_output_popen):
        cut_ffmpeg._run_ffmpeg_cut(
            "keep", source, scratch / "keep_v2.mp4", 0.00001, 2.5, source
        )
    check("no output keeps the upload", source.exists())

    # A cut that never ran (the client left before the worker read the registry) keeps the upload too.
    cut_ffmpeg._run_ffmpeg_cut("gone", source, scratch / "gone_v2.mp4", 0, 1, source)
    check("a cut that never ran keeps the upload", source.exists())

    # A success whose source cannot be deleted (still open for the preview) is still a success.
    class LockedSource:
        def unlink(self, missing_ok=False):
            raise PermissionError("[WinError 32] in use by the preview")

    jobs.cut_progress["locked"] = {
        "queue": queue.Queue(maxsize=100),
        "cancelled": False,
        "deadline": time.time() + 60,
    }
    done = scratch / "locked_v2.mp4"
    real_popen2 = subprocess.Popen
    with patched_on(
        subprocess,
        Popen=lambda cmd, **kw: real_popen2(
            [sys.executable, "-c", _FFMPEG_UTF8, cmd[-1]], **kw
        ),
    ):
        cut_ffmpeg._run_ffmpeg_cut("locked", source, done, 0, 1, LockedSource())
    ev = []
    while not jobs.cut_progress["locked"]["queue"].empty():
        ev.append(jobs.cut_progress["locked"]["queue"].get_nowait())
    final = next((e for e in ev if e.get("status") in jobs.TERMINAL_STATUSES), {})
    check(
        "a locked source does not undo a finished cut",
        final.get("status") == "complete" and done.exists(),
        final,
    )
    check(
        "the worker releases its name when the cut ends",
        scratch / "keep_v2.mp4" not in cut_ffmpeg.claimed_outputs,
    )
    times = [seen_cmd[i + 1] for i, a in enumerate(seen_cmd) if a in ("-ss", "-t")]
    check(
        "bounds reach FFmpeg in fixed-point",
        times and not any("e" in t for t in times),
        times,
    )
    jobs.cut_progress.clear()
    shutil.rmtree(scratch, ignore_errors=True)


def test_cut_progress_rule():
    section("Cut: the progress rule, on its own")
    rule = cut_rules._cut_progress
    # Lines that carry no time move nothing: the deadline must not be pushed by them.
    check("a non-progress line is ignored", rule("frame=12\n", -1, 10) is None)
    check(
        "N/A before the first frame is ignored",
        rule("out_time_us=N/A\n", -1, 10) is None,
    )

    first = rule("out_time_us=5000000\n", -1, 10)
    check("the first time seen advances", first == (5_000_000, 50.0, True), first)
    same = rule("out_time_us=5000000\n", 5_000_000, 10)
    # FFmpeg keeps reporting the same time while stuck: that must not count as progress.
    check(
        "the same time again does not advance", same == (5_000_000, 50.0, False), same
    )
    back = rule("out_time_us=4000000\n", 5_000_000, 10)
    # The bar follows the furthest time: it used to step back from 50 % to 40 %.
    check(
        "an earlier time neither advances nor moves the bar back",
        back == (5_000_000, 50.0, False),
        back,
    )

    # Whole tuples, not over[1]: a None would raise and end the run instead of failing one check.
    over = rule("out_time_us=12000000\n", -1, 10)
    check("the percentage is clamped to 100", over == (12_000_000, 100.0, True), over)
    # FFmpeg can write a negative time, AV_NOPTS_VALUE included, before the first frame: the
    # bar read "-922337203685477.5%".
    negative = rule("out_time_us=-9223372036854775807\n", -1, 10)
    check(
        "a negative time gives 0 %, not a negative bar",
        negative is not None and negative[1] == 0.0,
        negative,
    )
    zero = rule("out_time_us=3000000\n", -1, 0)
    check(
        "no duration gives 0.0 %, a float like every other path",
        zero == (3_000_000, 0.0, True) and isinstance(zero[1], float),
        zero,
    )
    rounded = rule("out_time_us=1234567\n", -1, 10)
    check(
        "the percentage is rounded to 0.1", rounded == (1_234_567, 12.3, True), rounded
    )


def test_cut_stall_timeout():
    section("Cut: the timeout catches a stall, not a long clip")
    # The bug: a fixed 10-minute wall clock killed every cut that took longer,
    # however well it was progressing. A 3 s cut with a 1 s limit must finish.
    final, pushed, _ = _stall_run(_FFMPEG_SLOW)
    check(
        "a slow cut that progresses completes",
        final.get("status") == "complete",
        "got: %s" % final,
    )
    check("progress pushes the deadline forward", pushed > 2, "+%.1f s" % pushed)
    # A decode error used to land in `except Exception`, whose fail() deleted
    # the user's source while FFmpeg was still running.
    final, _, _ = _stall_run(_FFMPEG_UTF8)
    check(
        "non-cp1252 bytes on stderr do not fail the cut",
        final.get("status") == "complete",
        "got: %s" % final,
    )

    # Controls: without them, a watchdog gone inert would pass the test above.
    final, _, elapsed = _stall_run(_FFMPEG_SILENT)
    check(
        "control: a silent FFmpeg is killed",
        final.get("status") == "error" and "Timeout" in final.get("message", ""),
        "got: %s" % final,
    )
    check("control: killed promptly", elapsed < 5, "%.1f s" % elapsed)
    final, _, elapsed = _stall_run(_FFMPEG_FROZEN)
    check(
        "control: a frozen out_time is killed",
        final.get("status") == "error" and "Timeout" in final.get("message", ""),
        "got: %s" % final,
    )
    check("control: frozen killed promptly", elapsed < 5, "%.1f s" % elapsed)


# ===================================================== network (--online)
def test_radio_online():
    section("YouTube radio, for real")
    check(
        "the radio URL is not seen as a playlist",
        dl_rules.is_playlist_url(RADIO) is False,
    )
    t0 = time.time()
    r = client.post("/get-info", headers=H, json={"url": RADIO})
    dt = time.time() - t0
    d = r.get_json() or {}
    # Without noplaylist, this call did not come back within 120 s.
    check("/get-info answers within 30 s", dt < 30, "%.1f s" % dt)
    check(
        "response carries no error",
        r.status_code == 200 and not d.get("error"),
        str(d.get("error"))[:50],
    )
    check("resolved as a video", d.get("is_playlist") is not True)

    t0 = time.time()
    d2 = (
        client.post(
            "/get-info", headers=H, json={"url": "https://youtu.be/MEKERDMnC48"}
        ).get_json()
        or {}
    )
    check(
        "the short link still answers",
        not d2.get("error"),
        "%.1f s" % (time.time() - t0),
    )
    check("both links give the same video", d.get("title") == d2.get("title"))


def test_real_cancellation():
    section("Cancelling a real download")
    URL = "https://download.samplelib.com/mp4/sample-30s.mp4"

    def run(cancel):
        scratch = Path(tempfile.mkdtemp(prefix="cancel-"))
        home = scratch / "out"
        home.mkdir()
        travail = scratch / "work"
        travail.mkdir()
        job = "testjob"
        q = queue.Queue(maxsize=100)
        now = time.time()
        app.download_progress[job] = {
            "queue": q,
            "start_time": now,
            "deadline": now + config.DOWNLOAD_TIMEOUT,
            "cancelled": False,
        }
        with patched_on(ytdlp, _get_output_folder=lambda t: home, WORK_FOLDER=travail):
            t = threading.Thread(
                target=ytdlp._run_download, args=(job, URL, "social", None), daemon=True
            )
            t.start()
            # Wait until the transfer has really started: cancelling before a
            # single hook has run would prove nothing.
            limit = time.time() + 30
            started = False
            while time.time() < limit:
                if any(travail.rglob("*")):
                    started = True
                    break
                time.sleep(0.1)
            if cancel:
                app.download_progress[job]["cancelled"] = True
            t.join(timeout=60)
        ev = []
        while not q.empty():
            ev.append(q.get_nowait())
        final = next((e for e in ev if e.get("status") in jobs.TERMINAL_STATUSES), {})
        res = (
            started,
            final.get("status"),
            sorted(p.name for p in home.glob("*")),
            sorted(p.name for p in travail.rglob("*")),
            t.is_alive(),
        )
        shutil.rmtree(scratch, ignore_errors=True)
        return res

    started, status, produced, leftovers, alive = run(cancel=True)
    check("the transfer actually started", started)
    check("terminal state = cancelled", status == "cancelled", "got: %s" % status)
    check("no final file produced", not produced, str(produced))
    check("work directory cleaned up", not leftovers, str(leftovers))
    check("the thread has ended", not alive)

    started, status, produced, leftovers, alive = run(cancel=False)
    check(
        "control: terminal state = complete", status == "complete", "got: %s" % status
    )
    check("control: file produced", bool(produced), str(produced))
    check("control: work directory cleaned up", not leftovers, str(leftovers))


# ===================================================== running it
# ===================================================== the landing page
# Commented-out markup is never fetched, and docs/index.html names
# fonts.googleapis.com twice in prose — in the very comments explaining why it
# no longer calls it. Scanning the raw text would flag the explanation as the
# offence.
_COMMENTS = re.compile(r"<!--.*?-->|/\*.*?\*/", re.S)
# One alternation per tag that fetches on its own. It is deliberately long:
# `len(fetched) >= 4` below catches a matcher that has stopped seeing
# everything, not one that only ever saw <link>, <img> and <script>.
_FETCHES = re.compile(
    r"<(?:link|img|script|iframe|video|audio|source|embed|object|track|use)\b"
    r'[^>]*?\b(?:href|src|data)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s">]+))',
    re.I | re.S,
)
_SRCSET = re.compile(r'\bsrcset\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.I)
_STYLE_URL = re.compile(r'(?:\burl\(\s*|@import\s+)[\'"]?([^\'")\s;]+)', re.I)
_REFRESH = re.compile(
    r'<meta\b[^>]*?http-equiv\s*=\s*[\'"]?refresh[^>]*?url\s*=\s*([^\s"\';>]+)',
    re.I | re.S,
)
_ANCHORS = re.compile(r'<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.I | re.S)


def _matched(pattern, text):
    return [
        next(g for g in m.groups() if g is not None) for m in pattern.finditer(text)
    ]


def _page_refs(text):
    """The sub-resources a browser fetches on its own, and the links it does not.

    The distinction is the whole point: an <a href> to github.com is the reader
    choosing to click, while a <link> or a src= is the page calling out before
    they have decided anything. Only the second kind can leak a visit.
    """
    text = _COMMENTS.sub(" ", text)
    fetched = _matched(_FETCHES, text)
    fetched += _matched(_STYLE_URL, text)
    fetched += _matched(_REFRESH, text)
    for value in _matched(_SRCSET, text):
        fetched += [c.split()[0] for c in value.split(",") if c.split()]
    return fetched, _matched(_ANCHORS, text)


def _outbound(refs):
    return [r for r in refs if r.startswith(("http://", "https://", "//"))]


def _ranges(text):
    return [
        re.sub(r"\s+", " ", m.group(1)).strip()
        for m in re.finditer(r"unicode-range:\s*([^;]+);", text)
    ]


# Each of these once slipped past a narrower matcher: a different tag, a single
# quote, no quote at all, or a capital letter.
_ESCAPES = [
    ("iframe", '<iframe src="https://evil.example/x"></iframe>'),
    ("video", '<video src="https://evil.example/x.mp4"></video>'),
    ("source", '<source src="https://evil.example/x.webm">'),
    ("object data", '<object data="https://evil.example/x.swf"></object>'),
    ("embed", '<embed src="https://evil.example/x">'),
    ("svg use", '<use href="https://evil.example/x.svg#i"/>'),
    ("single quotes", "<script src='https://evil.example/a.js'></script>"),
    ("no quotes", "<script src=https://evil.example/a.js></script>"),
    ("uppercase", '<SCRIPT SRC="https://evil.example/a.js"></SCRIPT>'),
    (
        "meta refresh",
        '<meta http-equiv="refresh" content="0;url=https://evil.example/">',
    ),
    ("css @import", '<style>@import "https://evil.example/a.css";</style>'),
    ("srcset", '<img srcset="https://evil.example/a.png 2x" src="icon.png">'),
]


def test_landing_page():
    section("The landing page calls nobody")
    # docs/index.html is not served by this app, so there is no route to drive
    # here. It is checked anyway: it is the one file in the repository that a
    # stranger loads in their own browser, and its declared subject is what the
    # software does with your data. A page arguing that while asking a font CDN
    # to identify its reader was answering its own question. That property is
    # worth exactly as much as the test that holds it.
    docs = ROOT / "docs"
    page = (docs / "index.html").read_text(encoding="utf-8")
    fetched, links = _page_refs(page)

    # A matcher that has stopped matching passes every test built on it.
    check(
        "the page does fetch sub-resources",
        len(fetched) >= 4,
        "%d found: %s" % (len(fetched), ", ".join(sorted(set(fetched)))),
    )
    check(
        "none of them is third-party",
        not _outbound(fetched),
        ", ".join(_outbound(fetched)) or "all relative",
    )
    # The page is HTML and CSS only. Checking that is cheaper and stricter than
    # trying to read an inline script for the call it makes.
    check("the page carries no script at all", "<script" not in page.lower())

    # Control: the same matcher over twelve shapes the leak could take,
    # including the stylesheet link this change removed. Without these runs, a
    # matcher that only ever recognised <link> would still come up green above.
    # The anchors first. A replace() that matches nothing injects nothing, and
    # every control below would then pass by measuring an untouched page.
    check(
        "the control anchors exist",
        page.count("</head>") == 1 and page.count("</body>") == 1,
        "head %d, body %d" % (page.count("</head>"), page.count("</body>")),
    )
    control = page.replace(
        "</head>",
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo">\n</head>',
        1,
    )
    check(
        "control: the googleapis link is caught",
        bool(_outbound(_page_refs(control)[0])),
        "reintroduced -> flagged",
    )
    missed = [
        name
        for name, markup in _ESCAPES
        if not _outbound(_page_refs(page.replace("</body>", markup + "</body>", 1))[0])
    ]
    check(
        "control: every escape shape is caught",
        not missed,
        "missed: %s" % ", ".join(missed) if missed else "%d shapes" % len(_ESCAPES),
    )
    # And the mirror of it: the same link, commented out, must NOT be caught.
    # That is what makes the stripping above a decision rather than an accident.
    inert = page.replace(
        "</head>",
        '<!-- <link rel="stylesheet" href="https://fonts.googleapis.com/x.css"> -->\n</head>',
        1,
    )
    check(
        "control: a commented-out link is not a call",
        _page_refs(inert)[0] == fetched,
        "inert -> same refs as before",
    )
    # And the comments naming fonts.googleapis.com must not count as a call.
    check(
        "a hostname named in a comment is not a call",
        "googleapis" in page and not [f for f in fetched if "googleapis" in f],
        "named %d time(s), fetched 0" % page.count("googleapis"),
    )

    outside = _outbound(links)
    check(
        "every outbound link goes to github.com",
        bool(outside)
        and all(link.startswith("https://github.com/") for link in outside),
        "%d link(s)" % len(outside),
    )

    # Pages publishes docs/ and nothing else, so a reference climbing out of it
    # is a 404 nobody sees until the site is live. A startswith on '..' is not
    # enough — 'fonts/../../app.py' passes it — so this is the containment
    # check the download routes already use: resolve, then compare.
    root = str(docs.resolve())
    for ref in sorted(
        {f for f in fetched if not _outbound([f]) and not f.startswith("data:")}
    ):
        target = (docs / ref.split("?")[0].split("#")[0]).resolve()
        check(
            "%s is served from docs/" % ref,
            str(target).startswith(root) and target.is_file(),
        )
    check(
        "control: a reference climbing out of docs/ is caught",
        not str((docs / "fonts/../../app.py").resolve()).startswith(root),
        "fonts/../../app.py -> outside",
    )

    # The two unicode-range lists are duplicated, not shared: the browser
    # loading the page reads that file alone, and Pages never publishes
    # static/. Re-subset the font and only one of the two would be updated.
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rp, rc = _ranges(page), _ranges(css)
    check(
        "two @font-face blocks on each side",
        len(rp) == 2 and len(rc) == 2,
        "page %d, stylesheet %d" % (len(rp), len(rc)),
    )
    check(
        "the unicode ranges match the application",
        rp == rc,
        "identical" if rp == rc else "DRIFTED",
    )
    check(
        "control: a drifted range is caught",
        _ranges(page.replace("U+0329", "U+0328", 1)) != rc,
        "perturbed -> flagged",
    )


OFFLINE = [
    test_origin_guard,
    test_sec_fetch_site,
    test_sanitize,
    test_baseline_behaviour,
    test_deadlines,
    test_playlist_pushes_deadline,
    test_routing,
    test_photo_has_no_audio,
    test_playlist_partial_count,
    test_photo_strategy,
    test_cancel_route,
    test_instagram_cancellation,
    test_cut_cancellation,
    test_architecture,
    test_cut_bounds_rule,
    test_cut_hardening,
    test_cut_progress_rule,
    test_cut_stall_timeout,
    test_landing_page,
]
ONLINE_ONLY = [test_radio_online, test_real_cancellation]


def main():
    for t in OFFLINE + (ONLINE_ONLY if ONLINE else []):
        t()
        # The two registries are global state just as much as the attributes
        # `patched` restores: leaving them populated would make the order of the
        # sections a tacit contract.
        app.download_progress.clear()
        app.cut_progress.clear()
        # The rate limiter is a third: past 10 POSTs a minute, a section would
        # take a 429 that has nothing to do with its subject.
        web._request_times.clear()
        # And the cut's two: names claimed by cuts, lengths measured at upload.
        cut_ffmpeg.claimed_outputs.clear()
        cut_routes._upload_durations.clear()
    if not ONLINE:
        print("\n  (network tests skipped; pass --online to include them)")

    total, failures = len(_results), _results.count(False)
    print("\n%d assertions, %d failure(s)" % (total, failures))
    if total == 0:
        # A suite that measures nothing must never exit 0: that is exactly how
        # a broken test once passed for green.
        print("NO ASSERTION RAN AT ALL")
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
