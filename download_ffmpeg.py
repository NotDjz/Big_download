"""Fetch ffmpeg.exe and ffprobe.exe for the PyInstaller bundle.

FFmpeg ships inside the exe, so it is pinned: this exact build, checked by
SHA-256 before anything is extracted. The hash is the one GitHub computed for
the release asset (the API's `digest`), not a file published next to the zip.
To upgrade, change both constants together.
"""

import hashlib
import io
import os
import urllib.request
import zipfile

URL = ("https://github.com/GyanD/codexffmpeg/releases/download/8.1.1/"
       "ffmpeg-8.1.1-essentials_build.zip")
SHA256 = "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541"
DEST = os.path.dirname(os.path.abspath(__file__))
NEEDED = {"ffmpeg.exe", "ffprobe.exe"}


def main():
    if all(os.path.exists(os.path.join(DEST, n)) for n in NEEDED):
        print("ffmpeg.exe and ffprobe.exe already here, skipping.")
        return

    print("Downloading FFmpeg 8.1.1 (~105 MB)...")
    data = urllib.request.urlopen(URL, timeout=120).read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != SHA256:
        print(f"ERROR: FFmpeg zip SHA-256 is {actual}, expected {SHA256}. Refusing to use it.")
        raise SystemExit(1)
    print("SHA-256 verified. Extracting ffmpeg.exe and ffprobe.exe...")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for entry in zf.namelist():
            basename = os.path.basename(entry)
            if basename in NEEDED:
                print(f"  -> {basename}")
                with zf.open(entry) as src, open(os.path.join(DEST, basename), "wb") as dst:
                    dst.write(src.read())

    missing = [n for n in NEEDED if not os.path.exists(os.path.join(DEST, n))]
    if missing:
        print(f"ERROR: missing files: {missing}")
        raise SystemExit(1)

    print("OK!")


if __name__ == "__main__":
    main()
