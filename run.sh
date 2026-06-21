#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f "venv/bin/activate" ]; then
    echo "[ERREUR] Environnement virtuel non trouvé."
    echo "Lancez d'abord : ./install.sh"
    exit 1
fi

source venv/bin/activate

echo "[UPDATE] Mise à jour de yt-dlp..."
pip install --upgrade yt-dlp -q 2>/dev/null || echo "[ATTENTION] Mise à jour impossible."

echo
python3 app.py
