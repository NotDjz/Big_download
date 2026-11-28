"""Fetch ffmpeg.exe and ffprobe.exe for the PyInstaller bundle.

FFmpeg ships inside the exe, so it is pinned: this exact build, the zip checked
by SHA-256 before anything is extracted, and each binary checked again, whether
just extracted or already on disk. Binaries that are already here but do not
match are replaced, so a local build cannot bundle an older, unchecked FFmpeg.

The zip's hash is the one GitHub computed for the release asset (the API's
`digest`), not a file published next to the zip. To upgrade, change VERSION and
the three hashes together; release.yml reads VERSION for the notes.
"""

import hashlib
import io
import os
import sys
import urllib.request
import zipfile

VERSION = "8.1.1"
URL = (f"https://github.com/GyanD/codexffmpeg/releases/download/{VERSION}/"
       f"ffmpeg-{VERSION}-essentials_build.zip")
ZIP_SHA256 = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541"
BINARIES = {
    "ffmpeg.exe": "228d7a8556258de907fdb55f36850078ebc7680b84ec30d84ea02e99bec1d1eb",
    "ffprobe.exe": "0fde260f5abd35c9cafd96f594cc76365a780c1b73a90e35b6a3409ea1db1bf0",
}
DEST = os.path.dirname(os.path.abspath(__file__))


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _pinned(name):
    path = os.path.join(DEST, name)
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        return _sha256(f.read()) == BINARIES[name]


def main():
    if all(_pinned(n) for n in BINARIES):
        print(f"FFmpeg {VERSION} already here and verified, skipping.")
        return

    print(f"Downloading FFmpeg {VERSION} (~105 MB)...")
    data = urllib.request.urlopen(URL, timeout=120).read()
    if _sha256(data) != ZIP_SHA256:
        print(f"ERROR: FFmpeg zip SHA-256 is {_sha256(data)}, expected {ZIP_SHA256}. Refusing to use it.")
        sys.exit(1)
    print("Zip SHA-256 verified. Extracting ffmpeg.exe and ffprobe.exe...")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for entry in zf.namelist():
            name = os.path.basename(entry)
            if name in BINARIES:
                blob = zf.read(entry)
                if _sha256(blob) != BINARIES[name]:
                    print(f"ERROR: {name} inside the verified zip does not match its pinned hash.")
                    sys.exit(1)
                print(f"  -> {name}")
                with open(os.path.join(DEST, name), "wb") as dst:
                    dst.write(blob)

    missing = [n for n in BINARIES if not _pinned(n)]
    if missing:
        print(f"ERROR: missing files: {missing}")
        sys.exit(1)

    print("OK!")


if __name__ == "__main__":
    main()
