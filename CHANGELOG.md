# Changelog

Each version's section is its release notes: the release workflow publishes it as-is.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [SemVer](https://semver.org/).

## [2.3.1] - 2026-11-28

### Fixed
- Long videos can be cut. A cut used to be stopped after 10 minutes whatever its progress, and re-encoding
  1080p at 240 fps runs at about a quarter of real time, so anything past a few minutes failed with
  "the cut took too long". Cuts now encode about 7 times faster, and are stopped only when FFmpeg makes no
  progress for 2 minutes.
- A video whose metadata held certain non-ASCII characters failed to cut, and the failure deleted the uploaded
  file.

### Changed
- Built and published by GitHub Actions from the tag, with a provenance attestation for the exe and the MSI.
- FFmpeg is pinned to 8.1.1 and checked by SHA-256. Python dependencies are pinned to exact versions with hashes.
