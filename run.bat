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

REM Sync with the lock on every launch: the versions CI tested and the exe
REM bundles, not whatever PyPI serves today. A stale yt-dlp fails with a 403
REM that explains nothing; Dependabot bumps it in requirements.txt weekly.
echo [UPDATE] Syncing dependencies with requirements.txt...
pip install --require-hashes -r requirements.txt >nul 2>nul
if errorlevel 1 (
    echo [WARNING] Could not sync. Carrying on with the current versions.
) else (
    echo [OK] Dependencies match the lock
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
