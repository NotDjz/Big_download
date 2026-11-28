"""The cut's rules, with no I/O: data in, data out (Clean Architecture: tested alone, imports nothing outside)."""

import math


def parse_cut_bounds(start, end, media_duration=None):
    """Validate a cut's bounds from the client: ((start, end), None), or (None, the reason).

    float() accepts "nan", "inf", JSON's NaN/Infinity and booleans, and
    `end <= start` is False whenever NaN is involved: such bounds reached
    FFmpeg, which failed, and the error path deleted the user's upload. Given
    the media's length, a start past it is refused (FFmpeg would write nothing)
    and the end is clamped to it.
    """
    if isinstance(start, bool) or isinstance(end, bool):
        return None, "Times must be numbers"
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return None, "Times must be numbers"
    if not (math.isfinite(start) and math.isfinite(end)):
        return None, "Times must be finite numbers"
    if start < 0:
        return None, "The start time cannot be negative"
    if end <= start:
        return None, "The end time must come after the start"
    if media_duration:
        if start >= media_duration:
            return None, "The start time is past the end of the file"
        end = min(end, media_duration)
    return (start, end), None


def _cut_progress(line, last_us, duration):
    """Read one line of FFmpeg's -progress output: the cut's progress rule, no I/O.

    Returns None for a line that carries no time (another key, or N/A before the
    first frame). Otherwise (last_us, percent, advanced): the furthest time seen,
    its percentage of `duration` (at most 100.0, 0.0 without a duration), and
    whether this line moved past last_us. Only an advancing time counts as
    progress: FFmpeg keeps reporting the same one while it is stuck. The
    percentage follows the furthest time, so the bar never steps back, and a
    negative time (AV_NOPTS_VALUE, before the first frame) never goes below the
    caller's starting last_us of -1.
    """
    if not line.startswith("out_time_us="):
        return None
    try:
        us = int(line.split("=")[1].strip())
    except ValueError:
        return None
    furthest = max(us, last_us)
    pct = min(furthest / 1_000_000 / duration, 1.0) if duration > 0 else 0.0
    return furthest, round(pct * 100, 1), us > last_us
