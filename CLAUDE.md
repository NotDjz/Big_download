# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Big Downloader is a Flask web app for downloading media from YouTube, Instagram, TikTok, and X/Twitter. It runs locally on `http://localhost:5555` and uses yt-dlp + FFmpeg for media extraction and conversion.

## Commands

```bash
# Install (Windows) - creates venv, installs deps
install.bat

# Run server (Windows) - activates venv, auto-updates yt-dlp, starts Flask on port 5555
run.bat

# Manual run (after activating venv)
python app.py
```

There are no tests or linting configured.

## Architecture

Single-file Flask backend (`app.py`) with vanilla JS frontend:

- **Backend**: `app.py` — all routes and download logic. Downloads run in daemon threads with progress reported via Server-Sent Events (SSE) through per-download `queue.Queue` instances.
- **Frontend**: `templates/index.html` + `static/js/app.js` + `static/css/style.css` — single-page UI with tab-based navigation.
- **Downloads** are organized into `downloads/YouTube/`, `downloads/YouTube_MP3/`, and `downloads/Reseaux_Sociaux/`.

### Key backend patterns

- Download flow: `POST /start-download` → spawns thread → client connects to `GET /progress/<id>` SSE stream for real-time updates.
- Legacy synchronous endpoints (`/download-youtube`, `/download-mp3`, `/download-social`) still exist alongside the async flow.
- Instagram image posts (no video) fall back to a custom API-based downloader (`_download_instagram_images`) that requires a Netscape-format cookies file (`cookies.txt` or `www.instagram.com_cookies.txt`) in the project root.
- Platform detection is URL-based (`detect_platform()`). YouTube URLs are validated and cleaned before use.

## Dependencies

- Python 3.8+, Flask, yt-dlp, requests
- FFmpeg must be installed and in PATH (required for video/audio merging and MP3 conversion)
- Windows-oriented (bat scripts), but the Python code is cross-platform

## Language

The UI and log messages are in French.
