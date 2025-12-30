// ==================== DARK MODE ====================
// Charger le thème au démarrage
(function() {
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
})();

// Gestion des onglets
document.addEventListener('DOMContentLoaded', () => {
    initDarkMode();
    initTabs();
    initDownloadButtons();
    loadDownloadsList();
    loadStats();

    // Auto-refresh de la liste et des stats toutes les 10 secondes
    setInterval(() => {
        loadDownloadsList();
        loadStats();
    }, 10000);
});

function initDarkMode() {
    const themeToggle = document.getElementById('theme-toggle');
    const themeIcon = document.getElementById('theme-icon');
    const currentTheme = localStorage.getItem('theme') || 'light';

    // Mettre à jour l'icône selon le thème actuel
    updateThemeIcon(currentTheme, themeIcon);

    // Écouter le clic sur le bouton
    themeToggle.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

        // Changer le thème
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);

        // Mettre à jour l'icône
        updateThemeIcon(newTheme, themeIcon);
    });
}

function updateThemeIcon(theme, iconElement) {
    if (theme === 'dark') {
        iconElement.textContent = '☀️';
    } else {
        iconElement.textContent = '🌙';
    }
}

// ==================== ONGLETS ====================
function initTabs() {
    const tabButtons = document.querySelectorAll('.tab-button');
    const tabPanes = document.querySelectorAll('.tab-pane');

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const tabName = button.getAttribute('data-tab');

            // Désactiver tous les onglets
            tabButtons.forEach(btn => btn.classList.remove('active'));
            tabPanes.forEach(pane => pane.classList.remove('active'));

            // Activer l'onglet sélectionné
            button.classList.add('active');
            document.getElementById(`${tabName}-tab`).classList.add('active');
        });
    });
}

// ==================== BOUTONS DE TÉLÉCHARGEMENT ====================
function initDownloadButtons() {
    // YouTube
    document.getElementById('youtube-info-btn').addEventListener('click', () => {
        const url = document.getElementById('youtube-url').value.trim();
        if (url) getVideoInfo(url, 'youtube');
    });

    document.getElementById('youtube-download-btn').addEventListener('click', () => {
        const url = document.getElementById('youtube-url').value.trim();
        if (url) downloadYoutube(url);
    });

    // MP3
    document.getElementById('mp3-info-btn').addEventListener('click', () => {
        const url = document.getElementById('mp3-url').value.trim();
        if (url) getVideoInfo(url, 'mp3');
    });

    document.getElementById('mp3-download-btn').addEventListener('click', () => {
        const url = document.getElementById('mp3-url').value.trim();
        if (url) downloadMP3(url);
    });

    // Social
    document.getElementById('social-info-btn').addEventListener('click', () => {
        const url = document.getElementById('social-url').value.trim();
        if (url) getVideoInfo(url, 'social');
    });

    document.getElementById('social-download-btn').addEventListener('click', () => {
        const url = document.getElementById('social-url').value.trim();
        if (url) downloadSocial(url);
    });

    // Refresh liste et stats
    document.getElementById('refresh-list-btn').addEventListener('click', () => {
        loadDownloadsList();
        loadStats();
    });
}

// ==================== GET VIDEO INFO ====================
async function getVideoInfo(url, type) {
    if (!url) {
        showStatus(type, 'Veuillez entrer une URL', 'error');
        return;
    }

    showStatus(type, '🔍 Récupération des informations...', 'loading');

    try {
        const response = await fetch('/get-info', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url }),
        });

        const data = await response.json();

        if (data.error) {
            showStatus(type, `❌ ${data.error}`, 'error');
            hideVideoInfo(type);
            return;
        }

        showVideoInfo(type, data);
        showStatus(type, '✅ Informations récupérées', 'success');

    } catch (error) {
        showStatus(type, `❌ Erreur: ${error.message}`, 'error');
        hideVideoInfo(type);
    }
}

// ==================== DOWNLOAD YOUTUBE ====================
async function downloadYoutube(url) {
    if (!url) {
        showStatus('youtube', 'Veuillez entrer une URL YouTube', 'error');
        return;
    }

    const btn = document.getElementById('youtube-download-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Téléchargement...';

    showStatus('youtube', '⬇️ Téléchargement en cours... Cela peut prendre quelques minutes.', 'loading');

    try {
        const response = await fetch('/download-youtube', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url }),
        });

        const data = await response.json();

        if (data.error) {
            showStatus('youtube', `❌ ${data.error}`, 'error');
        } else {
            let message = `✅ ${data.message}`;
            if (data.resolution && data.width && data.height) {
                message += `\n📺 Résolution: ${data.width}x${data.height} (${data.resolution})`;
                if (data.fps) {
                    message += ` @ ${data.fps}fps`;
                }
            }
            showStatus('youtube', message, 'success');
            loadDownloadsList();
            loadStats();
        }

    } catch (error) {
        showStatus('youtube', `❌ Erreur: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '⬇️ Télécharger Vidéo';
    }
}

// ==================== DOWNLOAD MP3 ====================
async function downloadMP3(url) {
    if (!url) {
        showStatus('mp3', 'Veuillez entrer une URL', 'error');
        return;
    }

    const btn = document.getElementById('mp3-download-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Téléchargement...';

    showStatus('mp3', '⬇️ Téléchargement en cours... Extraction de l\'audio.', 'loading');

    try {
        const response = await fetch('/download-mp3', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url }),
        });

        const data = await response.json();

        if (data.error) {
            showStatus('mp3', `❌ ${data.error}`, 'error');
        } else {
            showStatus('mp3', `✅ ${data.message}`, 'success');
            loadDownloadsList();
            loadStats();
        }

    } catch (error) {
        showStatus('mp3', `❌ Erreur: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '⬇️ Télécharger MP3';
    }
}

// ==================== DOWNLOAD SOCIAL ====================
async function downloadSocial(url) {
    if (!url) {
        showStatus('social', 'Veuillez entrer une URL', 'error');
        return;
    }

    const btn = document.getElementById('social-download-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Téléchargement...';

    showStatus('social', '⬇️ Téléchargement en cours...', 'loading');

    try {
        const response = await fetch('/download-social', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url }),
        });

        const data = await response.json();

        if (data.error) {
            showStatus('social', `❌ ${data.error}`, 'error');
        } else {
            showStatus('social', `✅ ${data.message}`, 'success');
            loadDownloadsList();
            loadStats();
        }

    } catch (error) {
        showStatus('social', `❌ Erreur: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '⬇️ Télécharger';
    }
}

// ==================== AFFICHAGE ====================
function showStatus(type, message, status) {
    const statusEl = document.getElementById(`${type}-status`);
    statusEl.textContent = message;
    statusEl.className = `status-message ${status} show`;

    // Auto-hide après 5 secondes pour les messages success/error
    if (status === 'success' || status === 'error') {
        setTimeout(() => {
            statusEl.classList.remove('show');
        }, 5000);
    }
}

function showVideoInfo(type, data) {
    const infoEl = document.getElementById(`${type}-info`);

    let html = `
        <h4>${data.title}</h4>
        <p><strong>Auteur:</strong> ${data.uploader}</p>
    `;

    if (data.duration) {
        const duration = formatDuration(data.duration);
        html += `<p><strong>Durée:</strong> ${duration}</p>`;
    }

    if (data.resolution && data.width && data.height) {
        html += `<p><strong>Résolution:</strong> ${data.width}x${data.height} (${data.resolution})`;
        if (data.fps) {
            html += ` @ ${data.fps}fps`;
        }
        html += `</p>`;
    }

    if (data.platform) {
        html += `<p><strong>Plateforme:</strong> ${data.platform.toUpperCase()}</p>`;
    }

    if (data.thumbnail) {
        html += `<img src="${data.thumbnail}" alt="Thumbnail">`;
    }

    infoEl.innerHTML = html;
    infoEl.classList.remove('hidden');
}

function hideVideoInfo(type) {
    const infoEl = document.getElementById(`${type}-info`);
    infoEl.classList.add('hidden');
}

function formatDuration(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
}

// ==================== LISTE DES TÉLÉCHARGEMENTS ====================
async function loadDownloadsList() {
    const listEl = document.getElementById('downloads-list');

    try {
        const response = await fetch('/list-downloads');
        const files = await response.json();

        if (files.length === 0) {
            listEl.innerHTML = '<p class="loading">Aucun fichier téléchargé</p>';
            return;
        }

        let html = '';
        files.sort((a, b) => b.size - a.size); // Trier par taille

        files.forEach(file => {
            const size = formatFileSize(file.size);
            const category = file.category || 'Autre';

            html += `
                <div class="download-item">
                    <div class="download-item-info">
                        <div class="download-item-name">${escapeHtml(file.name)}</div>
                        <div class="download-item-size">${size} • ${category}</div>
                    </div>
                    <a href="${file.url}" class="download-item-link" download>
                        ⬇️ Télécharger
                    </a>
                </div>
            `;
        });

        listEl.innerHTML = html;

    } catch (error) {
        listEl.innerHTML = '<p class="loading">Erreur lors du chargement de la liste</p>';
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== STATISTIQUES ====================
async function loadStats() {
    try {
        const response = await fetch('/get-stats');
        const stats = await response.json();

        // Mettre à jour le nombre total de fichiers
        document.getElementById('stat-total-files').textContent = stats.total_files;

        // Mettre à jour l'espace utilisé
        document.getElementById('stat-total-size').textContent = formatFileSize(stats.total_size);

        // Mettre à jour le fichier le plus gros
        if (stats.largest_file.size > 0) {
            // Tronquer le nom si trop long
            const fileName = stats.largest_file.name;
            const displayName = fileName.length > 30 ? fileName.substring(0, 27) + '...' : fileName;

            document.getElementById('stat-largest-file').textContent = displayName;
            document.getElementById('stat-largest-file').title = fileName; // Tooltip pour le nom complet
            document.getElementById('stat-largest-category').textContent =
                `${formatFileSize(stats.largest_file.size)} • ${stats.largest_file.category}`;
        } else {
            document.getElementById('stat-largest-file').textContent = 'Aucun';
            document.getElementById('stat-largest-category').textContent = '';
        }

        // Mettre à jour les stats par catégorie
        if (stats.categories) {
            // YouTube
            document.getElementById('stat-youtube-files').textContent = stats.categories.youtube.files;
            document.getElementById('stat-youtube-size').textContent = formatFileSize(stats.categories.youtube.size);

            // MP3
            document.getElementById('stat-mp3-files').textContent = stats.categories.mp3.files;
            document.getElementById('stat-mp3-size').textContent = formatFileSize(stats.categories.mp3.size);

            // Social
            document.getElementById('stat-social-files').textContent = stats.categories.social.files;
            document.getElementById('stat-social-size').textContent = formatFileSize(stats.categories.social.size);
        }

    } catch (error) {
        console.error('Erreur lors du chargement des stats:', error);
    }
}
