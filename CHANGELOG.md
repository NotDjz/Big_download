# Changelog - BIG DOWNLOADER

## Configuration actuelle

### Informations générales
- **Port** : 5555 (au lieu de 5000)
- **Accès** : localhost uniquement (127.0.0.1)
- **Taille max** : 5 GB par fichier

### Dépendances
- `flask==3.0.0`
- `yt-dlp>=2024.12.6` (version flexible pour mises à jour)
- `requests>=2.32.2` (compatible avec yt-dlp)
- `colorama==0.4.6`

### Structure des dossiers
```
downloads/
├── YouTube/           # Vidéos YouTube MP4
├── YouTube_MP3/       # Audio MP3 (toutes plateformes)
└── Reseaux_Sociaux/   # Instagram, TikTok, X/Twitter
```

## Fonctionnalités

### YouTube Vidéo
- Téléchargement MP4 jusqu'à 4K (2160p)
- Format : `bestvideo[ext=mp4][height<=2160]+bestaudio[ext=m4a]`
- Affichage de la résolution dans l'interface
- Dossier : `downloads/YouTube/`

### MP3 Audio
- Extraction audio en MP3 192 kbps
- Support : YouTube, SoundCloud, Vimeo, etc.
- Dossier : `downloads/YouTube_MP3/`

### Réseaux Sociaux
- Instagram, TikTok, X/Twitter, Facebook
- Dossier : `downloads/Reseaux_Sociaux/`
- Format de fichier : `{platform}_{id}.{ext}`

## Sécurité

### Protections implémentées
- ✅ Validation stricte des URLs YouTube
- ✅ Sanitization des noms de fichiers (path traversal protection)
- ✅ Vérification des chemins de fichiers (pas d'accès hors downloads/)
- ✅ Limite de taille : 5 GB
- ✅ Debug mode désactivé
- ✅ Localhost uniquement

### Évaluation : 7/10
**Usage local personnel** : Configuration largement suffisante

## Modifications récentes

### Session du 2025-12-04

1. **Correction des dépendances**
   - Fix conflit `requests` (2.31.0 → >=2.32.2)
   - Update `yt-dlp` (2024.8.6 → >=2024.12.6)

2. **Changement du port**
   - Port 5000 → 5555

3. **Configuration yt-dlp optimisée**
   - Reprise de la config de `F:\Downloader_ytb` (qui fonctionnait)
   - Suppression des `extractor_args` qui causaient des problèmes de qualité
   - Format optimal pour 4K sans restrictions

4. **Organisation en dossiers**
   - Création de 3 sous-dossiers par type de média
   - Routes de téléchargement avec catégories
   - Affichage de la catégorie dans l'interface

5. **Affichage de la résolution**
   - Détection automatique de la résolution (4K, 1440p, 1080p, etc.)
   - Affichage dans le message de succès
   - Affichage dans les infos vidéo (bouton "🔍 Infos")
   - Format : `{width}x{height} ({quality}) @ {fps}fps`

## Notes importantes

### FFmpeg
**OBLIGATOIRE** pour :
- Merge vidéo + audio pour YouTube
- Conversion MP3
- Sans FFmpeg : téléchargements limités

### Mise à jour recommandée
```bash
pip install --upgrade yt-dlp
```
YouTube change régulièrement ses protections, maintenir yt-dlp à jour est important.

## Problèmes connus et solutions

### "Besoin d'un compte pour télécharger"
- **Cause** : Restrictions YouTube récentes
- **Solution appliquée** : Configuration simple sans `extractor_args`
- **Alternative** : Mettre à jour yt-dlp régulièrement

### Résolution incorrecte
- **Cause** : `extractor_args` avec client Android
- **Solution** : Utiliser la config standard de Downloader_ytb

## Commandes utiles

### Installation
```bash
install.bat
```

### Démarrage
```bash
run.bat
```
Ou :
```bash
python app.py
```

### Accès
```
http://localhost:5555
```

## Projet de référence
Configuration basée sur : `F:\Downloader_ytb`
