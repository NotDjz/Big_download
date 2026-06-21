#!/bin/bash
echo "========================================"
echo "  Installation de Big Downloader"
echo "========================================"
echo

# Vérifier Python 3
if ! command -v python3 &> /dev/null; then
    echo "[ERREUR] Python 3 non trouvé. Installez-le avec:"
    echo "  sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
echo "[OK] Python 3 détecté"

# Vérifier FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "[ERREUR] FFmpeg non trouvé. Installez-le avec:"
    echo "  sudo apt install ffmpeg"
    exit 1
fi
echo "[OK] FFmpeg détecté"

# Créer le venv
echo
echo "Création de l'environnement virtuel..."
python3 -m venv venv
source venv/bin/activate

# Installer les dépendances
echo "Installation des dépendances..."
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "========================================"
echo "  Installation terminée !"
echo "========================================"
echo
echo "Pour lancer : ./run.sh"
