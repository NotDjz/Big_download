@echo off
echo.
echo   ======================================
echo       COMPILATION DE BIG DOWNLOADER
echo   ======================================
echo.

REM Telecharger FFmpeg si absent
py download_ffmpeg.py
if errorlevel 1 (
    echo [ERREUR] Impossible de telecharger FFmpeg
    pause
    exit /b 1
)
echo.

REM Installer PyInstaller
py -m pip install pyinstaller

REM Compiler
py -m PyInstaller --noconfirm --onefile --windowed --name BigDownloader ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "ffmpeg.exe;." ^
    --add-data "ffprobe.exe;." ^
    --hidden-import yt_dlp ^
    --hidden-import webview ^
    --collect-all webview ^
    app.py

echo.
echo   Compilation terminee !
echo   L'executable se trouve dans le dossier "dist".
echo.
pause
