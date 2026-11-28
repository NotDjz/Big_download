"""The library's rules, with no I/O: which names are safe to put in a folder."""

import os
import re


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
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200] + ext
    return filename
