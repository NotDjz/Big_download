@echo off
echo.
echo   ======================================
echo       BUILDING BIG DOWNLOADER
echo   ======================================
echo.

REM Fetch FFmpeg if it is not already here
py download_ffmpeg.py
if errorlevel 1 (
    echo [ERROR] Could not download FFmpeg
    pause
    exit /b 1
)
echo.

REM Install PyInstaller
py -m pip install pyinstaller

REM yt-dlp is frozen into the exe and can never update itself: a binary built
REM on an old version starts getting 403s as soon as a platform tightens its
REM protections.
py -m pip install --upgrade yt-dlp

REM Build
py -m PyInstaller --noconfirm --onefile --windowed --name BigDownloader ^
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
