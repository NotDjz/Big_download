FROM python:3.11-slim

# Métadonnées
LABEL maintainer="Jeremy"
LABEL description="BIG DOWNLOADER - Téléchargeur Universel Multi-Plateformes"

# Installer FFmpeg et dépendances système
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Créer le répertoire de travail
WORKDIR /app

# Copier les fichiers de dépendances
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copier le reste de l'application
COPY . .

# Créer le dossier downloads
RUN mkdir -p downloads/YouTube downloads/YouTube_MP3 downloads/Reseaux_Sociaux

# Exposer le port
EXPOSE 5555

# Lancer l'application
CMD ["python", "app.py"]
