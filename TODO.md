# 📋 TODO - BIG DOWNLOADER

Liste des fonctionnalités à implémenter pour améliorer l'application.

---

## 🎯 Fonctionnalités Prioritaires

### 1. Dark Mode / Thème sombre
- [ ] Ajouter un bouton de basculement thème clair/sombre
- [ ] Créer les variables CSS pour le thème sombre
- [ ] Sauvegarder la préférence dans localStorage
- [ ] Animation fluide de transition entre thèmes

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐⭐⭐ (Très utile)

---

### 2. Supprimer les fichiers
- [ ] Ajouter un bouton "🗑️ Supprimer" pour chaque fichier
- [ ] Créer la route `/delete/<category>/<filename>` dans Flask
- [ ] Ajouter une confirmation avant suppression
- [ ] Rafraîchir automatiquement la liste après suppression

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐⭐⭐ (Très utile)

---

### 3. Stats et informations
- [ ] Afficher le nombre total de fichiers téléchargés
- [ ] Calculer et afficher l'espace disque total utilisé
- [ ] Afficher le fichier le plus volumineux
- [ ] Widget de stats en haut de la section téléchargements

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐⭐ (Utile)

---

### 4. Documentation et déploiement
- [ ] Créer un fichier requirements.txt
- [ ] Créer un README.md avec les deux méthodes d'installation (Docker et venv)
- [ ] Documenter les commandes Docker
- [ ] Documenter l'installation avec venv local
- [ ] Ajouter des captures d'écran de l'interface

**Difficulté:** ⭐ (Très facile)
**Utilité:** ⭐⭐⭐⭐⭐ (Essentiel)

---

## 🚀 Fonctionnalités Avancées

### 5. Barre de progression en temps réel
- [ ] Implémenter WebSocket ou Server-Sent Events (SSE)
- [ ] Afficher le pourcentage de progression
- [ ] Afficher la vitesse de téléchargement (MB/s)
- [ ] Afficher le temps restant estimé
- [ ] Hook yt-dlp pour récupérer la progression

**Difficulté:** ⭐⭐⭐⭐ (Difficile)
**Utilité:** ⭐⭐⭐⭐⭐ (Très impressionnant)

---

### 6. Choix de la qualité vidéo
- [ ] Récupérer les formats disponibles avec `/get-info`
- [ ] Afficher une liste déroulante (4K, 1440p, 1080p, 720p, 480p)
- [ ] Permettre à l'utilisateur de choisir avant téléchargement
- [ ] Modifier les options yt-dlp selon le choix

**Difficulté:** ⭐⭐⭐ (Moyen)
**Utilité:** ⭐⭐⭐⭐ (Très utile)

---

### 7. Queue de téléchargements
- [ ] Interface pour ajouter plusieurs URLs
- [ ] File d'attente avec ordre de priorité
- [ ] Télécharger automatiquement une par une
- [ ] Afficher l'état de la queue (en attente, en cours, terminé, échoué)
- [ ] Possibilité d'annuler un téléchargement en queue

**Difficulté:** ⭐⭐⭐⭐ (Difficile)
**Utilité:** ⭐⭐⭐⭐⭐ (Très pratique)

---

### 8. Téléchargement de playlists YouTube
- [ ] Détecter automatiquement les URLs de playlists
- [ ] Extraire la liste des vidéos avec yt-dlp
- [ ] Afficher la liste avec preview et durée totale
- [ ] Option pour sélectionner/désélectionner des vidéos
- [ ] Télécharger toutes les vidéos sélectionnées

**Difficulté:** ⭐⭐⭐⭐ (Difficile)
**Utilité:** ⭐⭐⭐⭐⭐ (Très impressionnant)

---

### 9. Recherche et filtres
- [ ] Barre de recherche dans les fichiers téléchargés
- [ ] Recherche par nom de fichier
- [ ] Filtrer par catégorie (YouTube Vidéo, MP3, Réseaux Sociaux)
- [ ] Filtrer par taille (< 100MB, 100MB-1GB, > 1GB)
- [ ] Tri par nom, taille, date

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐⭐ (Utile)

---

### 10. Lecteur intégré
- [ ] Intégrer un lecteur vidéo HTML5 (video.js ou plyr.js)
- [ ] Bouton "▶️ Lire" à côté de chaque fichier
- [ ] Modal avec lecteur pour vidéos/audios
- [ ] Contrôles complets (play, pause, volume, plein écran)

**Difficulté:** ⭐⭐⭐ (Moyen)
**Utilité:** ⭐⭐⭐ (Pratique)

---

### 11. Historique des téléchargements
- [ ] Base de données SQLite pour stocker l'historique
- [ ] Logger chaque téléchargement (date, URL, statut, taille)
- [ ] Page dédiée pour voir l'historique
- [ ] Statistiques (total téléchargé, succès/échecs)
- [ ] Export de l'historique en CSV

**Difficulté:** ⭐⭐⭐ (Moyen)
**Utilité:** ⭐⭐⭐ (Utile)

---

### 12. Raccourcis clavier
- [ ] Détecter Ctrl+V / Cmd+V pour coller automatiquement dans l'input
- [ ] Entrée pour lancer le téléchargement
- [ ] Ctrl+D / Cmd+D pour basculer le dark mode
- [ ] Esc pour fermer les modals
- [ ] Afficher un guide des raccourcis (?)

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐ (Confort)

---

### 13. Notifications système
- [ ] Notification browser quand téléchargement terminé
- [ ] Demander la permission de notification
- [ ] Notification avec preview (titre, miniature)
- [ ] Son de notification optionnel

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐⭐ (Pratique)

---

## 🛠️ Améliorations Techniques

### 13. Logging structuré
- [ ] Remplacer les `print()` par un système de logging
- [ ] Logs dans un fichier `logs/app.log`
- [ ] Rotation des logs
- [ ] Niveaux de log (DEBUG, INFO, WARNING, ERROR)

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐ (Utile pour debug)

---

### 14. Tests unitaires
- [ ] Tests pour les fonctions de validation
- [ ] Tests pour les routes Flask
- [ ] Tests pour sanitize_filename
- [ ] Coverage > 70%

**Difficulté:** ⭐⭐⭐ (Moyen)
**Utilité:** ⭐⭐⭐ (Qualité du code)

---

### 15. Configuration externe
- [ ] Fichier `config.json` ou `.env`
- [ ] Variables: PORT, MAX_SIZE, DOWNLOAD_FOLDER
- [ ] Faciliter la personnalisation sans toucher au code

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐ (Flexibilité)

---

### 16. Rate limiting
- [ ] Limiter le nombre de requêtes par IP
- [ ] Éviter les abus (même en local)
- [ ] Flask-Limiter

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐ (Sécurité)

---

## 🎨 Améliorations UX/UI

### 17. Animations améliorées
- [ ] Skeleton loading pendant les requêtes
- [ ] Micro-interactions (confetti au succès)
- [ ] Transitions de page plus fluides

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐ (Expérience)

---

### 18. Mode compact
- [ ] Toggle pour afficher la liste en mode compact
- [ ] Plus de fichiers visibles à l'écran
- [ ] Sauvegarde de la préférence

**Difficulté:** ⭐ (Très facile)
**Utilité:** ⭐⭐⭐ (Confort)

---

### 19. Drag & Drop
- [ ] Zone de drop pour glisser-déposer des URLs
- [ ] Support du drag d'un fichier texte avec URLs

**Difficulté:** ⭐⭐ (Facile)
**Utilité:** ⭐⭐⭐ (Confort)

---

### 20. Bouton "Copier URL de téléchargement"
- [ ] Copier le lien direct du fichier téléchargé
- [ ] Partager facilement en local

**Difficulté:** ⭐ (Très facile)
**Utilité:** ⭐⭐ (Pratique)

---

## 📊 Priorisation Recommandée

### Phase 1 - Quick Wins (1-2h)
1. Dark Mode
2. Supprimer les fichiers
3. Stats et informations
4. Raccourcis clavier

### Phase 2 - Fonctionnalités Utiles (3-5h)
5. Choix de la qualité
6. Recherche et filtres
7. Notifications système
8. Lecteur intégré

### Phase 3 - Fonctionnalités Avancées (5-10h)
9. Barre de progression en temps réel
10. Queue de téléchargements
11. Téléchargement de playlists
12. Historique des téléchargements

### Phase 4 - Polish (2-3h)
13. Logging structuré
14. Configuration externe
15. Animations améliorées
16. Drag & Drop

---

## 📝 Notes

- Priorité donnée aux fonctionnalités **faciles** et **très utiles**
- Le projet reste **simple** et **local** (pas de base de données complexe)
- Garder la **légèreté** et la **rapidité** de l'application
- Toutes les fonctionnalités sont **optionnelles**

---

**Dernière mise à jour:** 2025-12-12
