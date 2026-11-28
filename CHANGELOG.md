# Changelog

Each version's section is its release notes: the release workflow publishes it as-is.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [SemVer](https://semver.org/).

## [Unreleased]

### Fixed
- A cut sent with impossible bounds (not a number, infinite, or a negative start) was handed to FFmpeg, whose
  failure deleted the uploaded file. Such bounds are now refused before anything runs, and the file stays.
- A cut that FFmpeg finished without writing anything was reported as "FFmpeg not found". It now says FFmpeg wrote
  no output.
- A file with a very long name could be uploaded but not previewed or cut.
- Two cuts of the same file started a moment apart could get the same name and overwrite each other.

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
