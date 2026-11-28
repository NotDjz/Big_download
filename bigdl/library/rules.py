"""The library's rules, with no I/O: which names are safe to put in a folder."""

import re
from os.path import splitext

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
