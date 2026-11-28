# Changelog

Each version's section is its release notes: the release workflow publishes it as-is.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [SemVer](https://semver.org/).

## [Unreleased]

### Fixed
- A failed cut deleted the uploaded file, so a single bad attempt meant uploading again. Now only a successful cut
  removes it: after an error, a timeout or an empty result the file stays, ready for another try.
- Impossible bounds (not a number, infinite, negative, past the end of the file, or a range shorter than a
  millisecond) are refused before FFmpeg runs, and an end past the file is cut at its end.
- A cut that produced nothing was reported as "FFmpeg not found", or even as finished with an empty clip. It now
  says FFmpeg wrote no output.
- A file with a very long name could be uploaded but not previewed or cut.
- Two cuts of the same file started a moment apart could get the same name and overwrite each other.
- The library could go blank when a file disappeared while it was being listed, and Windows files such as
  desktop.ini showed up as videos that would not play.
- Deleting a file that is still open now says so instead of failing with an unreadable error.
- The check that keeps every play, delete and reveal request inside its own folder compared names as text, so a
  sibling folder whose name started the same way could pass for it; it now compares real paths.
- Looking up or starting a download with an empty request, or on a video whose resolution is unknown, no longer
  ends in a server error.

## [2.3.2] - 2026-11-28

### Fixed
- Installing an update or uninstalling while Big Downloader was open left part of it running: after an update the
  old version kept running and its file was only deleted at the next restart, and after an uninstall the app
  started again. The installer now closes Big Downloader before it touches any file. Updating from 2.3.1 itself
  still goes through 2.3.1's own uninstaller, so close the app first for that one update.
- The cut's progress bar no longer steps back when FFmpeg reports an earlier time, and no longer shows a huge
  negative percentage before the first frame.

### Changed
- Every change and every release now installs the MSI on a clean Windows machine and checks its whole life: a cut
  with the installed app, an update and an uninstall with the app open, downloads and cookies kept.

## [2.3.1] - 2026-11-28

### Fixed
- Long videos can be cut. A cut used to be stopped after 10 minutes whatever its progress, and re-encoding
  1080p at 240 fps runs at about a quarter of real time, so anything past a few minutes failed with
  "the cut took too long". Cuts now encode about 7 times faster, and are stopped only when FFmpeg makes no
  progress for 2 minutes.
- A video whose metadata held certain non-ASCII characters failed to cut, and the failure deleted the uploaded
  file.

### Changed
- Releases are built and published by GitHub Actions from the tag, with a provenance attestation for the exe and
  the MSI.
- FFmpeg is pinned to 8.1.1 and checked by SHA-256. Python dependencies are pinned to exact versions with hashes.
