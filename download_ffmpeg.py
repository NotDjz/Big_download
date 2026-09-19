"""Fetch ffmpeg.exe and ffprobe.exe from gyan.dev for the PyInstaller bundle."""

import io
import os
import zipfile
import urllib.request

URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
DEST = os.path.dirname(os.path.abspath(__file__))
NEEDED = {"ffmpeg.exe", "ffprobe.exe"}


def main():
    if all(os.path.exists(os.path.join(DEST, n)) for n in NEEDED):
        print("ffmpeg.exe and ffprobe.exe already here, skipping.")
        return

    print("Downloading FFmpeg (~80 MB)...")
    data = urllib.request.urlopen(URL).read()
    print("Extracting ffmpeg.exe and ffprobe.exe...")

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
