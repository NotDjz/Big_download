@echo off
title Big Downloader - Server
cd /d "%~dp0"
chcp 65001 >nul

REM Verification de l'environnement virtuel
if not exist "venv\Scripts\activate.bat" (
    echo [ERREUR] Environnement virtuel non trouve
    echo Veuillez executer install.bat d'abord
    pause
    exit /b 1
)

REM Activation de l'environnement virtuel
call venv\Scripts\activate.bat

REM Mise a jour automatique de yt-dlp
echo [UPDATE] Mise a jour de yt-dlp...
pip install --upgrade yt-dlp >nul 2>nul
if errorlevel 1 (
    echo [ATTENTION] Mise a jour impossible. Continuation avec la version actuelle.
) else (
    echo [OK] yt-dlp a jour
)
echo.

REM Ouverture du navigateur avec un delai de 2 secondes
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5555"

REM Lancement du serveur
echo ========================================
echo   Demarrage du serveur...
echo ========================================
echo.

python app.py

REM Si le serveur s'arrete
echo.
echo Serveur arrete.
pause
