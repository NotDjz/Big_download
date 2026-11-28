"""The library's rules, with no I/O: which names are safe to put in a folder, which files are media, and
whether a path stays inside its folder."""

import re
from os.path import commonpath, normcase, splitext

# One place for these lists: the listing, the startup sweep, the legacy migration
# and the cut each had their own copy, and a type added to one would have been
# hidden by the listing yet never swept, or swept yet still listed.
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac")
VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v")
# Intermediate files are not media: they showed up as openable rows and the
# player failed on them.
PARTIAL_EXTS = (".part", ".ytdl", ".temp", ".tmp")
# Files Windows (or a Mac share) writes into folders: never media.
JUNK_NAMES = ("desktop.ini", "thumbs.db", ".ds_store")
# Folder names from before 2.0, still read and migrated, with the category each one's files now belong to. One
# table: the listing reads the names and the migration their destinations, so they cannot fall out of step.
LEGACY_FOLDERS = {
    "YouTube": "Videos",
    "YouTube_MP3": "Music",
    "Reseaux_Sociaux": "Videos",
}


def media_type(name):
    """'photo', 'audio' or 'video' for a file in the library; None for what is not media.

    A denylist: partial downloads and the junk Windows writes into folders
    (desktop.ini, Thumbs.db) are left out, and everything else is a video, as in
    2.3.2. An allowlist hid .flv from some extractors and hand-copied .opus or
    .aac, which could then be neither played nor deleted from the app.
    """
    ext = splitext(name)[1].lower()
    if ext in PARTIAL_EXTS or name.lower() in JUNK_NAMES:
        return None
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in AUDIO_EXTS:
        return "audio"
    return "video"


def is_inside(path, folder):
    """True if `path` lies within `folder`: both resolved, compared as Windows does (case-insensitively).

    Not str.startswith: that let ...\\Videos2\\x pass for ...\\Videos, the last
    guard of every <category>/<filename> route, reachable through a link.
    """
    path, folder = normcase(path), normcase(folder)
    try:
        return path != folder and commonpath([path, folder]) == folder
    except ValueError:  # different drives
        return False


# The longest stem a name may keep: sanitize_filename truncates to it, and what
# the server composes around a name (an upload's id prefix, a cut's _v2) must
# fit inside it, or the next sanitize truncates it to a file that does not exist.
MAX_STEM = 200


def fit_stem(stem, reserved):
    """Shorten a stem so that `reserved` more characters still fit within MAX_STEM."""
    return stem[: MAX_STEM - reserved]


def sanitize_filename(filename):
    """Scrub a filename so it cannot escape its folder.

    Replacing the separators is enough to prevent any escape: stripped of a
    separator, '..' no longer names a parent. So the inner dots of a legitimate
    name are left alone ('Wait... What.mp4'), which otherwise stopped matching
    the file yt-dlp had written and returned 404 on playback, deletion and
    reveal-in-explorer alike.
    """
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    filename = filename.rstrip(" .")
    if not filename:
        filename = "_"
    if len(filename) > MAX_STEM:
        name, ext = splitext(filename)
        filename = name[:MAX_STEM] + ext
    return filename
