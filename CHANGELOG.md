# Changelog

Each version's section is its release notes: the release workflow publishes it as-is.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [SemVer](https://semver.org/).

## [Unreleased]

### Changed
- Releases are built and published by GitHub Actions from the tag, with a provenance attestation for the exe and
  the MSI.
- FFmpeg is pinned to 8.1.1 and checked by SHA-256. Python dependencies are pinned to exact versions with hashes.
