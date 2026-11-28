"""Housekeeping run once at startup: abandoned partials, orphaned job folders, and the pre-2.0 folder layout."""

import shutil

from bigdl.config import (
    DOWNLOAD_FOLDER,
    DOWNLOAD_TIMEOUT,
    MUSIC_FOLDER,
    PHOTOS_FOLDER,
    SSE_GRACE,
    VIDEOS_FOLDER,
    WORK_FOLDER,
    log,
)
from bigdl.jobs import (
    _sweep_old_files,
)
from bigdl.library.rules import IMAGE_EXTS, LEGACY_FOLDERS, PARTIAL_EXTS


def _sweep_stale_partials():
    """Remove orphaned partial files from the output folders.

    Since the move to a per-job directory yt-dlp no longer writes any here, but
    the ones left by older versions are now filtered out of the listing and so
    are invisible: without this sweep they would stay forever. Called at
    startup, when no download is running.
    """
    for folder in (VIDEOS_FOLDER, MUSIC_FOLDER, PHOTOS_FOLDER):
        for ext in PARTIAL_EXTS:
            for f in folder.glob("*" + ext):
                try:
                    f.unlink()
                except OSError:
                    pass


def _sweep_work_folder():
    """Purge work directories abandoned by a worker that was killed.

    The threshold exceeds a download's maximum lifetime, so this sweep can
    never reach a job that is still alive.
    """
    _sweep_old_files(WORK_FOLDER, DOWNLOAD_TIMEOUT + SSE_GRACE)


def _migrate_legacy_folders():
    """Move files from the old folder names into the current structure."""
    folders = {"Videos": VIDEOS_FOLDER, "Music": MUSIC_FOLDER}
    migrations = [
        (DOWNLOAD_FOLDER / name, folders[category])
        for name, category in LEGACY_FOLDERS.items()
    ]
    moved = 0
    for src_folder, dest_folder in migrations:
        if not src_folder.exists():
            continue
        for f in src_folder.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() in IMAGE_EXTS:
                target = PHOTOS_FOLDER / f.name
            else:
                target = dest_folder / f.name
            if target.exists():
                continue
            try:
                shutil.move(str(f), str(target))
                moved += 1
            except OSError:
                pass
        if not any(src_folder.iterdir()):
            try:
                src_folder.rmdir()
            except OSError:
                pass
    if moved:
        log.info(f"Migration: moved {moved} file(s) into the current structure")
