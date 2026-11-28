"""The cut's rules, with no I/O: data in, data out (Clean Architecture: tested alone, imports nothing outside)."""


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
