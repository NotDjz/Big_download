@echo off
title Big Downloader - Server
cd /d "%~dp0"
chcp 65001 >nul

REM Check the virtual environment
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found
    echo Run install.bat first
    pause
    exit /b 1
)

REM Activate it
call venv\Scripts\activate.bat

REM Update yt-dlp on every launch. Platforms tighten their protections
REM regularly and a stale yt-dlp fails with a 403 that explains nothing.
echo [UPDATE] Updating yt-dlp...
pip install --upgrade yt-dlp >nul 2>nul
if errorlevel 1 (
    echo [WARNING] Could not update. Carrying on with the current version.
) else (
    echo [OK] yt-dlp up to date
)
echo.

REM Open the browser after a 2 second delay
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5555"

REM Start the server
echo ========================================
echo   Starting the server...
echo ========================================
echo.

python app.py

REM Once the server stops
echo.
echo Server stopped.
pause
