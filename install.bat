@echo off
echo ========================================
echo   Big Downloader setup
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed, or not on the PATH
    echo Install it from https://www.python.org/
    pause
    exit /b 1
)

echo [OK] Python found
echo.

REM Check FFmpeg. This is a hard stop, not a warning: without it, every merge
REM and every cut fails later, with an error that says nothing about FFmpeg.
REM Run "py download_ffmpeg.py" first to fetch it beside the project.
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] FFmpeg is not installed, or not on the PATH
    echo Run "py download_ffmpeg.py" to fetch it beside the project,
    echo or install it from https://ffmpeg.org/download.html
    pause
    exit /b 1
)
echo [OK] FFmpeg found
echo.

REM Create the virtual environment
echo Creating the virtual environment...
python -m venv venv
if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment created
echo.

REM Activate it and install the dependencies
echo Installing dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Installing the dependencies failed
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Setup finished
echo ========================================
echo.
echo To start the server, run: run.bat
echo.
pause
