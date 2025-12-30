# 🚀 BIG DOWNLOADER

**Téléchargeur Universel Multi-Plateformes** - Interface web unifiée pour télécharger des vidéos, audios et médias depuis diverses plateformes.

---

## ✨ Fonctionnalités

### 📹 YouTube Vidéo Downloader
- Téléchargement de vidéos YouTube en **MP4**
- Qualité jusqu'à **4K (2160p)**
- Audio haute qualité inclus
- Formats: 4K, 1440p, 1080p, 720p

### 🎵 MP3 Audio Downloader
- Conversion audio en **MP3 (192 kbps)**
- Plateformes supportées:
  - YouTube
  - SoundCloud
  - Vimeo
  - Et bien d'autres...
- Métadonnées incluses (artiste, titre, etc.)

### 📱 Réseaux Sociaux Downloader
- **Instagram** (posts, reels, stories)
- **TikTok** (vidéos)
- **X/Twitter** (vidéos, images)
- Téléchargement direct des médias

---

## 🔧 Installation

### Prérequis
- **Python 3.8+** installé
- **FFmpeg** installé et dans le PATH (pour la conversion vidéo/audio)
- Connexion Internet

### Installation rapide

1. **Cloner ou télécharger le projet**
   ```bash
   git clone <url-du-repo>
   cd Big_download
   ```

2. **Installer FFmpeg** (si pas déjà installé)
   - Windows: Télécharger depuis [ffmpeg.org](https://ffmpeg.org/download.html)
   - Ajouter FFmpeg au PATH système

3. **Lancer l'installation**
   ```bash
   install.bat
   ```

   Ce script va:
   - Créer un environnement virtuel Python
   - Installer toutes les dépendances
   - Configurer le projet

---

## 🚀 Utilisation

### Démarrer le serveur

```bash
run.bat
```

Le serveur démarre sur **http://localhost:5555**

### Interface web

1. Ouvrir votre navigateur
2. Aller sur `http://localhost:5555`
3. Choisir l'onglet correspondant:
   - **YouTube Vidéo** pour les vidéos MP4
   - **MP3 Audio** pour l'audio
   - **Réseaux Sociaux** pour Instagram/TikTok/X

4. Coller l'URL du média
5. Cliquer sur "🔍 Infos" pour prévisualiser
6. Cliquer sur "⬇️ Télécharger"

### Fichiers téléchargés

Tous les fichiers sont sauvegardés dans le dossier `downloads/`

Vous pouvez les télécharger directement depuis l'interface web (section "Fichiers téléchargés")

---

## 🐳 Utilisation avec Docker

### Prérequis Docker
- **Docker** installé ([Installer Docker](https://docs.docker.com/get-docker/))
- **Docker Compose** installé (inclus avec Docker Desktop)

### Démarrage rapide

1. **Construire et lancer le conteneur**
   ```bash
   docker compose up -d
   ```

2. **Accéder à l'application**
   - Ouvrir votre navigateur
   - Aller sur `http://localhost:5555`

3. **Voir les logs**
   ```bash
   docker compose logs -f
   ```

4. **Arrêter le conteneur**
   ```bash
   docker compose down
   ```

### Avantages Docker

✅ **Pas d'installation manuelle** - FFmpeg et Python déjà inclus
✅ **Isolation complète** - N'affecte pas votre système
✅ **Fichiers persistants** - Les téléchargements sont sauvegardés dans `./downloads/`
✅ **Portable** - Fonctionne sur Windows, Linux, MacOS

### Commandes utiles

```bash
# Reconstruire l'image après une modification
docker-compose up -d --build

# Voir les conteneurs en cours
docker ps

# Arrêter et supprimer le conteneur
docker-compose down

# Supprimer aussi les volumes
docker-compose down -v
```

---

## 🔒 Sécurité

- ✅ **Localhost uniquement** - Accessible uniquement depuis votre PC
- ✅ **Taille maximum** - 5 GB par fichier
- ✅ **Validation des URLs** - Vérification des URLs
- ✅ **Noms de fichiers sécurisés** - Protection contre les path traversal
- ✅ **Pas de debug en production**

---

## 📁 Structure du projet

```
Big_download/
├── app.py                  # Application Flask principale
├── templates/
│   └── index.html         # Interface web
├── static/
│   ├── css/
│   │   └── style.css      # Styles
│   └── js/
│       └── app.js         # JavaScript
├── downloads/             # Fichiers téléchargés
├── venv/                  # Environnement virtuel Python (Windows)
├── requirements.txt       # Dépendances Python
├── install.bat           # Script d'installation (Windows)
├── run.bat               # Script de démarrage (Windows)
├── Dockerfile            # Configuration Docker
├── docker-compose.yml    # Orchestration Docker
├── .dockerignore         # Fichiers exclus du build Docker
└── README.md             # Ce fichier
```

---

## 🛠️ Technologies utilisées

- **Backend**: Flask (Python)
- **Téléchargement**: yt-dlp
- **Frontend**: HTML5, CSS3, JavaScript (Vanilla)
- **Conversion média**: FFmpeg
- **Conteneurisation**: Docker (optionnel)

---

## 📝 Notes importantes

### FFmpeg requis
FFmpeg est **obligatoire** pour la conversion et le merge des vidéos/audios. Sans FFmpeg:
- ❌ Pas de conversion MP3
- ❌ Pas de merge vidéo+audio pour YouTube
- ❌ Qualité vidéo limitée

### Plateformes supportées

| Plateforme | Vidéo | Audio | Notes |
|-----------|-------|-------|-------|
| YouTube | ✅ | ✅ | Jusqu'à 4K |
| SoundCloud | ❌ | ✅ | Audio uniquement |
| Instagram | ✅ | ✅ | Posts publics |
| TikTok | ✅ | ✅ | Vidéos publiques |
| X/Twitter | ✅ | ✅ | Tweets publics |
| Vimeo | ✅ | ✅ | Vidéos publiques |

### Limitations
- Taille max: **5 GB** par fichier
- Seuls les médias **publics** sont téléchargeables
- Certaines plateformes peuvent bloquer les téléchargements

---

## 🐛 Dépannage

### Le serveur ne démarre pas
- Vérifier que Python est installé: `python --version`
- Réinstaller les dépendances: `install.bat`

### FFmpeg non trouvé
- Vérifier que FFmpeg est dans le PATH
- Tester: `ffmpeg -version`

### Téléchargement échoue
- Vérifier que l'URL est publique
- Essayer avec une autre URL
- Vérifier la console du serveur pour les erreurs

### Problème de qualité vidéo
- Vérifier que FFmpeg est installé
- Certaines vidéos n'ont pas de qualité 4K disponible

---

## 👨‍💻 Auteur

**Jeremy**

---

## 📄 Licence

Ce projet est à usage personnel et éducatif uniquement.

**⚠️ Important**: Respectez les conditions d'utilisation des plateformes et les droits d'auteur des contenus téléchargés.

---

## 🎉 Enjoy!

Bon téléchargement ! 🚀
