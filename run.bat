@echo off
title Big Downloader - Server

REM Verification de l'environnement virtuel
if not exist "venv\Scripts\activate.bat" (
    echo [ERREUR] Environnement virtuel non trouve
    echo Veuillez executer install.bat d'abord
    pause
    exit /b 1
)

REM Activation de l'environnement virtuel
call venv\Scripts\activate.bat

REM Verification de Flask
python -c "import flask" 2>nul
if errorlevel 1 (
    echo [ERREUR] Flask n'est pas installe
    echo Veuillez executer install.bat d'abord
    pause
    exit /b 1
)

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