@echo off
echo.
echo   ======================================
echo       BUILDING BIG DOWNLOADER
echo   ======================================
echo.

REM The interpreter: py here, the one setup-python installed in CI (PYTHON=python).
if not defined PYTHON set PYTHON=py

REM Fetch FFmpeg if it is not already here
%PYTHON% download_ffmpeg.py
if errorlevel 1 (
    echo [ERROR] Could not download FFmpeg
    pause
    exit /b 1
)
echo.

REM The exact versions the CI tested, each checked by hash. yt-dlp is frozen
REM into the exe and can never update itself, and a stale one gets 403s: it is
REM kept fresh by Dependabot bumping requirements.txt, not by an unpinned
REM upgrade here.
%PYTHON% -m pip install --require-hashes -r requirements-build.txt
if errorlevel 1 (
    echo [ERROR] Could not install the pinned build dependencies
    pause
    exit /b 1
)

REM Build
%PYTHON% -m PyInstaller --noconfirm --onefile --windowed --name BigDownloader ^
    --icon "icon.ico" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "ffmpeg.exe;." ^
    --add-data "ffprobe.exe;." ^
    --hidden-import yt_dlp ^
    --hidden-import webview ^
    --collect-all webview ^
    app.py

echo.
echo   Build finished.
echo   The executable is in the "dist" folder.
echo.
pause
