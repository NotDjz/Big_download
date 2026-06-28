document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('url-input');
    const goBtn = document.getElementById('go-btn');
    const platformBadge = document.getElementById('platform-badge');
    const resultZone = document.getElementById('result-zone');
    const progressZone = document.getElementById('progress-zone');
    const statusMsg = document.getElementById('status-msg');

    let currentInfo = null;
    let selectedFormat = null;

    urlInput.addEventListener('input', onUrlChange);
    urlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') goBtn.click();
    });
    goBtn.addEventListener('click', onGo);

    loadDownloadsList();
    loadStats();
    setInterval(() => { loadDownloadsList(); loadStats(); }, 15000);

    document.getElementById('open-folder-btn').addEventListener('click', () => {
        fetch('/open-folder').catch(() => {});
    });

    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            document.getElementById('tab-' + tab.dataset.tab).classList.remove('hidden');
        });
    });

    function detectPlatform(url) {
        if (!url) return null;
        const lower = url.toLowerCase();
        if (lower.includes('youtube.com') || lower.includes('youtu.be')) return 'youtube';
        if (lower.includes('instagram.com')) return 'instagram';
        if (lower.includes('tiktok.com')) return 'tiktok';
        if (lower.includes('twitter.com') || lower.includes('x.com')) return 'x';
        if (lower.includes('soundcloud.com')) return 'soundcloud';
        if (lower.includes('facebook.com')) return 'facebook';
        return null;
    }

    function isPlaylistUrl(url) {
        return url.includes('playlist?list=') ||
            (url.includes('youtube.com') && url.includes('list=') && !url.includes('watch?v='));
    }

    function onUrlChange() {
        const url = urlInput.value.trim();
        const platform = detectPlatform(url);

        if (platform) {
            platformBadge.textContent = platform === 'x' ? 'X' : platform;
            platformBadge.className = 'platform-badge ' + platform;
            platformBadge.classList.remove('hidden');
        } else {
            platformBadge.classList.add('hidden');
        }

        hideResult();
        hideProgress();
        hideStatus();
    }

    function onGo() {
        const url = urlInput.value.trim();
        if (!url) return;

        const platform = detectPlatform(url);
        if (!platform) {
            showStatus('Plateforme non reconnue', 'error');
            return;
        }

        if (platform === 'youtube' && isPlaylistUrl(url)) {
            fetchPlaylistInfo(url);
        } else {
            fetchInfo(url);
        }
    }

    async function fetchInfo(url) {
        goBtn.disabled = true;
        goBtn.textContent = '...';
        showStatus('Recuperation des infos...', 'loading');
        hideResult();

        try {
            const resp = await fetch('/get-info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });
            const data = await resp.json();

            if (data.error) {
                showStatus(data.error, 'error');
                return;
            }

            currentInfo = data;
            currentInfo._url = url;
            showResult(data);
            hideStatus();
        } catch (err) {
            showStatus('Erreur: ' + err.message, 'error');
        } finally {
            goBtn.disabled = false;
            goBtn.textContent = 'GO';
        }
    }

    async function fetchPlaylistInfo(url) {
        goBtn.disabled = true;
        goBtn.textContent = '...';
        showStatus('Recuperation de la playlist...', 'loading');
        hideResult();

        try {
            const resp = await fetch('/get-playlist-info', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });
            const data = await resp.json();

            if (data.error) {
                showStatus(data.error, 'error');
                return;
            }

            currentInfo = data;
            currentInfo._url = url;
            currentInfo._isPlaylist = true;
            showPlaylistResult(data);
            hideStatus();
        } catch (err) {
            showStatus('Erreur: ' + err.message, 'error');
        } finally {
            goBtn.disabled = false;
            goBtn.textContent = 'GO';
        }
    }

    function showResult(data) {
        const thumbEl = document.getElementById('result-thumb');
        const titleEl = document.getElementById('result-title');
        const metaEl = document.getElementById('result-meta');
        const formatsEl = document.getElementById('result-formats');

        thumbEl.textContent = '';
        if (data.thumbnail) {
            const img = document.createElement('img');
            img.src = data.thumbnail;
            img.alt = '';
            thumbEl.appendChild(img);
            const play = document.createElement('span');
            play.className = 'thumb-play';
            play.textContent = '▶';
            thumbEl.appendChild(play);
        } else {
            const play = document.createElement('span');
            play.className = 'thumb-play';
            play.textContent = '▶';
            thumbEl.appendChild(play);
        }

        titleEl.textContent = data.title || 'Sans titre';

        const parts = [];
        if (data.uploader && data.uploader !== 'N/A') parts.push(data.uploader);
        if (data.duration) parts.push(formatDuration(data.duration));
        if (data.platform) parts.push(data.platform.toUpperCase());
        metaEl.textContent = parts.join(' · ');

        formatsEl.textContent = '';
        selectedFormat = null;

        const qualities = data.available_qualities || [];
        const hasVideo = qualities.length > 0 || (data.height && data.height > 0);
        const hasAudio = data.has_audio !== false;
        const isPhoto = data._is_photo || (!hasVideo && !hasAudio && data.platform === 'instagram');

        if (isPhoto) {
            const btn = document.createElement('button');
            btn.className = 'format-btn';
            const label = document.createElement('span');
            label.className = 'format-label';
            label.textContent = 'Photo';
            btn.appendChild(label);
            const detail = document.createElement('span');
            detail.className = 'format-detail';
            detail.textContent = 'Image';
            btn.appendChild(detail);
            btn.addEventListener('click', () => selectFormat(btn, 'social', null));
            formatsEl.appendChild(btn);
            selectFormat(btn, 'social', null);
        } else if (hasVideo) {
            if (qualities.length > 0) {
                const labels = { 2160: '4K', 1440: '1440p', 1080: '1080p', 720: '720p', 480: '480p', 360: '360p' };
                qualities.forEach((q, i) => {
                    const btn = document.createElement('button');
                    btn.className = 'format-btn';
                    const label = document.createElement('span');
                    label.className = 'format-label';
                    label.textContent = labels[q] || (q + 'p');
                    btn.appendChild(label);
                    const detail = document.createElement('span');
                    detail.className = 'format-detail';
                    detail.textContent = 'MP4';
                    btn.appendChild(detail);
                    btn.addEventListener('click', () => selectFormat(btn, 'video', q));
                    formatsEl.appendChild(btn);
                    if (i === 0) selectFormat(btn, 'video', q);
                });
            } else {
                const btn = document.createElement('button');
                btn.className = 'format-btn';
                btn.textContent = 'MP4';
                btn.addEventListener('click', () => selectFormat(btn, 'video', null));
                formatsEl.appendChild(btn);
                selectFormat(btn, 'video', null);
            }
        }

        if (hasAudio) {
            const btn = document.createElement('button');
            btn.className = 'format-btn';
            const label = document.createElement('span');
            label.className = 'format-label';
            label.textContent = 'MP3';
            btn.appendChild(label);
            const detail = document.createElement('span');
            detail.className = 'format-detail';
            detail.textContent = 'Audio';
            btn.appendChild(detail);
            btn.addEventListener('click', () => selectFormat(btn, 'mp3', null));
            formatsEl.appendChild(btn);

            if (!hasVideo) selectFormat(btn, 'mp3', null);
        }

        const dlBtn = document.createElement('button');
        dlBtn.className = 'go-btn';
        dlBtn.style.marginLeft = 'auto';
        dlBtn.textContent = 'Telecharger';
        dlBtn.addEventListener('click', startDownload);
        formatsEl.appendChild(dlBtn);

        resultZone.classList.remove('hidden');
    }

    function showPlaylistResult(data) {
        const thumbEl = document.getElementById('result-thumb');
        const titleEl = document.getElementById('result-title');
        const metaEl = document.getElementById('result-meta');
        const formatsEl = document.getElementById('result-formats');

        thumbEl.textContent = '';
        const play = document.createElement('span');
        play.className = 'thumb-play';
        play.textContent = '▶';
        thumbEl.appendChild(play);

        titleEl.textContent = data.title || 'Playlist';
        metaEl.textContent = (data.uploader || '') + ' · ' + data.video_count + ' videos';

        formatsEl.textContent = '';
        selectedFormat = { type: 'playlist', quality: null };

        const vidBtn = document.createElement('button');
        vidBtn.className = 'format-btn selected';
        vidBtn.textContent = 'MP4 (all)';
        vidBtn.addEventListener('click', () => {
            selectedFormat = { type: 'playlist', quality: null };
            formatsEl.querySelectorAll('.format-btn').forEach(b => b.classList.remove('selected'));
            vidBtn.classList.add('selected');
        });
        formatsEl.appendChild(vidBtn);

        const dlBtn = document.createElement('button');
        dlBtn.className = 'go-btn';
        dlBtn.style.marginLeft = 'auto';
        dlBtn.textContent = 'Telecharger';
        dlBtn.addEventListener('click', startDownload);
        formatsEl.appendChild(dlBtn);

        resultZone.classList.remove('hidden');
    }

    function selectFormat(btn, type, quality) {
        const formatsEl = document.getElementById('result-formats');
        formatsEl.querySelectorAll('.format-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');

        const platform = detectPlatform(currentInfo._url);
        if (type === 'mp3') {
            selectedFormat = { type: 'mp3', quality: null };
        } else if (platform === 'youtube') {
            selectedFormat = { type: 'youtube', quality: quality };
        } else {
            selectedFormat = { type: 'social', quality: null };
        }
    }

    function startDownload() {
        if (!currentInfo || !selectedFormat) return;
        const url = currentInfo._url;
        const type = currentInfo._isPlaylist ? 'playlist' : selectedFormat.type;
        const quality = selectedFormat.quality;
        downloadWithProgress(url, type, quality);
    }

    async function downloadWithProgress(url, type, quality) {
        hideResult();
        showProgress();
        updateProgress(0, 'Demarrage...', '', '');

        try {
            const body = { url, type };
            if (quality) body.quality = quality;

            const resp = await fetch('/start-download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();

            if (data.error) {
                hideProgress();
                showStatus(data.error, 'error');
                return;
            }

            const es = new EventSource('/progress/' + data.download_id);

            es.onmessage = (event) => {
                const msg = JSON.parse(event.data);

                if (msg.status === 'downloading') {
                    let status = 'Telechargement...';
                    if (msg.current_video) {
                        status = 'Video ' + msg.current_video + '/' + msg.total_videos;
                    }
                    updateProgress(msg.percent || 0, status, msg.speed || '', msg.eta ? 'ETA: ' + msg.eta : '');
                } else if (msg.status === 'processing') {
                    updateProgress(100, msg.message || 'Traitement...', '', '');
                } else if (msg.status === 'playlist_start') {
                    updateProgress(0, 'Playlist: ' + msg.title + ' (' + msg.total_videos + ' videos)', '', '');
                } else if (msg.status === 'playlist_video_start') {
                    updateProgress(0, 'Video ' + msg.current_video + '/' + msg.total_videos + ': ' + msg.video_title, '', '');
                } else if (msg.status === 'playlist_video_error') {
                    updateProgress(0, 'Erreur video ' + msg.current_video + '/' + msg.total_videos, '', '');
                } else if (msg.status === 'complete') {
                    es.close();
                    hideProgress();
                    let message = 'Telecharge: ' + msg.title;
                    if (msg.is_playlist) {
                        message = 'Playlist terminee: ' + msg.title + ' (' + msg.total_videos + ' videos)';
                    } else if (msg.resolution) {
                        message += ' (' + msg.resolution + ')';
                    }
                    showStatus(message, 'success');
                    loadDownloadsList();
                    loadStats();
                } else if (msg.status === 'error') {
                    es.close();
                    hideProgress();
                    showStatus(msg.message || 'Erreur inconnue', 'error');
                }
            };

            es.onerror = () => {
                es.close();
                hideProgress();
                showStatus('Connexion perdue', 'error');
            };

        } catch (err) {
            hideProgress();
            showStatus('Erreur: ' + err.message, 'error');
        }
    }

    function showProgress() {
        progressZone.classList.remove('hidden');
    }

    function hideProgress() {
        progressZone.classList.add('hidden');
    }

    function updateProgress(percent, status, speed, eta) {
        document.getElementById('progress-fill').style.width = percent + '%';
        document.getElementById('progress-status').textContent = status;
        document.getElementById('progress-percent').textContent = Math.round(percent) + '%';
        document.getElementById('progress-speed').textContent = speed;
        document.getElementById('progress-eta').textContent = eta;
    }

    function hideResult() {
        resultZone.classList.add('hidden');
    }

    function showStatus(message, type) {
        statusMsg.textContent = message;
        statusMsg.className = 'status-msg ' + type;
        statusMsg.classList.remove('hidden');
        if (type === 'success' || type === 'error') {
            setTimeout(() => { statusMsg.classList.add('hidden'); }, 6000);
        }
    }

    function hideStatus() {
        statusMsg.classList.add('hidden');
    }

    function formatDuration(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        return m + ':' + String(s).padStart(2, '0');
    }

    function formatSize(bytes) {
        if (!bytes) return '0 B';
        const k = 1024;
        const units = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + units[i];
    }

    function timeAgo(timestamp) {
        const diff = Math.floor(Date.now() / 1000 - timestamp);
        if (diff < 60) return 'a l\'instant';
        if (diff < 3600) return Math.floor(diff / 60) + ' min';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h';
        return Math.floor(diff / 86400) + 'j';
    }

    async function loadDownloadsList() {
        const listEl = document.getElementById('downloads-list');
        const countEl = document.getElementById('downloads-count');

        try {
            const resp = await fetch('/list-downloads');
            const files = await resp.json();

            countEl.textContent = files.length > 0 ? files.length : '';

            if (files.length === 0) {
                listEl.textContent = '';
                const empty = document.createElement('div');
                empty.className = 'downloads-empty';
                empty.textContent = 'Aucun fichier';
                listEl.appendChild(empty);
                return;
            }

            listEl.textContent = '';

            files.forEach(file => {
                const row = document.createElement('div');
                row.className = 'download-row';

                const icon = document.createElement('div');
                icon.className = 'dl-icon';
                icon.textContent = file.media_type === 'audio' ? '♫' : file.media_type === 'photo' ? '🖼' : '▶';
                row.appendChild(icon);

                const info = document.createElement('div');
                info.className = 'dl-info';

                const name = document.createElement('div');
                name.className = 'dl-name';
                name.textContent = file.name;
                info.appendChild(name);

                const meta = document.createElement('div');
                meta.className = 'dl-meta';
                const metaParts = [];
                metaParts.push(formatSize(file.size));
                metaParts.push(file.category);
                if (file.timestamp) metaParts.push(timeAgo(file.timestamp));
                meta.textContent = metaParts.join(' · ');
                info.appendChild(meta);

                row.appendChild(info);

                const actions = document.createElement('div');
                actions.className = 'dl-actions';

                const playBtn = document.createElement('button');
                playBtn.className = 'dl-action-btn';
                playBtn.textContent = '▶';
                playBtn.title = 'Lire';
                playBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openPlayer(file);
                });
                actions.appendChild(playBtn);

                const dlLink = document.createElement('a');
                dlLink.className = 'dl-action-btn';
                dlLink.href = file.url;
                dlLink.download = '';
                dlLink.textContent = '⬇';
                dlLink.title = 'Sauvegarder';
                dlLink.addEventListener('click', (e) => e.stopPropagation());
                actions.appendChild(dlLink);

                const delBtn = document.createElement('button');
                delBtn.className = 'dl-action-btn delete';
                delBtn.textContent = '✕';
                delBtn.title = 'Supprimer';
                delBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    deleteFile(file, row);
                });
                actions.appendChild(delBtn);

                row.appendChild(actions);

                row.addEventListener('click', () => openPlayer(file));

                listEl.appendChild(row);
            });

        } catch (err) {
            listEl.textContent = '';
            const empty = document.createElement('div');
            empty.className = 'downloads-empty';
            empty.textContent = 'Erreur chargement';
            listEl.appendChild(empty);
        }
    }

    async function deleteFile(file, rowEl) {
        try {
            rowEl.style.opacity = '0.3';
            const resp = await fetch('/delete/' + file.category + '/' + encodeURIComponent(file.name), {
                method: 'DELETE',
            });
            const data = await resp.json();
            if (data.success) {
                rowEl.remove();
                loadStats();
                const count = document.getElementById('downloads-count');
                const remaining = document.querySelectorAll('.download-row').length;
                count.textContent = remaining;
                if (remaining === 0) loadDownloadsList();
            } else {
                rowEl.style.opacity = '1';
                showStatus(data.error || 'Erreur suppression', 'error');
            }
        } catch (err) {
            rowEl.style.opacity = '1';
            showStatus('Erreur: ' + err.message, 'error');
        }
    }

    async function loadStats() {
        try {
            const resp = await fetch('/get-stats');
            const stats = await resp.json();
            const footerStats = document.getElementById('footer-stats');
            footerStats.textContent = stats.total_files + ' fichiers · ' + formatSize(stats.total_size);
        } catch (err) {
            // silent
        }
    }

    // Player modal + custom controls
    let activeMedia = null;
    let seekAnimFrame = null;

    function getMedia() {
        const c = document.getElementById('player-container');
        return c.querySelector('video') || c.querySelector('audio');
    }

    function fmtTime(s) {
        if (!s || !isFinite(s)) return '0:00';
        s = Math.floor(s);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = s % 60;
        if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
        return m + ':' + String(sec).padStart(2, '0');
    }

    function updatePlayerUI() {
        const media = getMedia();
        if (!media) return;

        const fill = document.getElementById('player-seek-fill');
        const timeEl = document.getElementById('player-time');
        const playBtn = document.getElementById('player-play-btn');

        const pct = media.duration ? (media.currentTime / media.duration) * 100 : 0;
        fill.style.width = pct + '%';
        timeEl.textContent = fmtTime(media.currentTime) + ' / ' + fmtTime(media.duration);
        playBtn.textContent = media.paused ? '▶' : '⏸';

        seekAnimFrame = requestAnimationFrame(updatePlayerUI);
    }

    function setupSeekBar() {
        const seekWrap = document.getElementById('player-seek-bar').parentElement;
        const seekBar = document.getElementById('player-seek-bar');

        function seekTo(e) {
            const media = getMedia();
            if (!media || !media.duration) return;
            const rect = seekBar.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            media.currentTime = pct * media.duration;
        }

        let seeking = false;
        seekWrap.addEventListener('mousedown', (e) => { seeking = true; seekTo(e); });
        document.addEventListener('mousemove', (e) => { if (seeking) seekTo(e); });
        document.addEventListener('mouseup', () => { seeking = false; });

        seekWrap.addEventListener('touchstart', (e) => { seeking = true; seekTo(e.touches[0]); }, { passive: true });
        document.addEventListener('touchmove', (e) => { if (seeking) seekTo(e.touches[0]); }, { passive: true });
        document.addEventListener('touchend', () => { seeking = false; });
    }

    function setupVolume() {
        const volSlider = document.getElementById('player-volume');
        volSlider.addEventListener('input', () => {
            const media = getMedia();
            if (media) {
                media.volume = parseFloat(volSlider.value);
                media.muted = false;
                updateVolIcon();
            }
        });
    }

    function updateVolIcon() {
        const media = getMedia();
        const icon = document.getElementById('player-vol-icon');
        if (!media) return;
        if (media.muted || media.volume === 0) {
            icon.innerHTML = '&#128264;';
        } else if (media.volume < 0.5) {
            icon.innerHTML = '&#128265;';
        } else {
            icon.innerHTML = '&#128266;';
        }
    }

    window.togglePlay = function() {
        const media = getMedia();
        if (!media) return;
        if (media.paused) media.play(); else media.pause();
    };

    window.toggleMute = function() {
        const media = getMedia();
        if (!media) return;
        media.muted = !media.muted;
        const volSlider = document.getElementById('player-volume');
        if (media.muted) {
            volSlider.value = 0;
        } else {
            volSlider.value = media.volume;
        }
        updateVolIcon();
    };

    setupSeekBar();
    setupVolume();

    window.openPlayer = function(file) {
        const modal = document.getElementById('player-modal');
        const container = document.getElementById('player-container');
        const titleEl = document.getElementById('modal-title');
        const metaEl = document.getElementById('modal-meta');
        const controls = document.getElementById('player-controls');

        container.textContent = '';
        if (seekAnimFrame) cancelAnimationFrame(seekAnimFrame);

        const streamUrl = '/stream/' + file.category + '/' + encodeURIComponent(file.name);
        const isPhoto = file.media_type === 'photo';
        const isAudio = file.media_type === 'audio';

        if (isPhoto) {
            const img = document.createElement('img');
            img.src = streamUrl;
            img.style.maxWidth = '100%';
            img.style.maxHeight = '70vh';
            img.style.borderRadius = '8px';
            img.style.objectFit = 'contain';
            container.appendChild(img);
            activeMedia = null;
            if (controls) controls.style.display = 'none';
        } else {
            const el = document.createElement(isAudio ? 'audio' : 'video');
            el.src = streamUrl;
            el.autoplay = true;
            container.appendChild(el);
            activeMedia = el;
            if (controls) controls.style.display = '';

            const volSlider = document.getElementById('player-volume');
            el.volume = parseFloat(volSlider.value);

            el.addEventListener('play', updatePlayerUI);
            el.addEventListener('pause', () => {
                if (seekAnimFrame) cancelAnimationFrame(seekAnimFrame);
                updatePlayerUI();
            });
            el.addEventListener('ended', () => {
                if (seekAnimFrame) cancelAnimationFrame(seekAnimFrame);
                updatePlayerUI();
            });
            el.addEventListener('loadedmetadata', updatePlayerUI);

            updatePlayerUI();
        }

        titleEl.textContent = file.name;
        metaEl.textContent = formatSize(file.size) + ' · ' + file.category;

        // Trim controls
        const trimZone = document.getElementById('trim-zone');
        const trimPanel = document.getElementById('trim-panel');
        const trimToggle = document.getElementById('trim-toggle-btn');
        if (!isPhoto) {
            trimZone.classList.remove('hidden');
            trimPanel.classList.add('hidden');
            trimToggle.classList.remove('active');
            window._currentPlayerFile = file;
            document.getElementById('trim-start').value = '0:00';
            document.getElementById('trim-end').value = '';
            document.getElementById('trim-status').classList.add('hidden');
        } else {
            trimZone.classList.add('hidden');
            window._currentPlayerFile = null;
        }

        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
    };

    window.closePlayer = function() {
        const modal = document.getElementById('player-modal');
        const container = document.getElementById('player-container');

        if (seekAnimFrame) cancelAnimationFrame(seekAnimFrame);
        const media = getMedia();
        if (media) media.pause();
        activeMedia = null;

        container.textContent = '';
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    };

    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('player-modal');
        if (modal.classList.contains('hidden')) return;

        if (e.key === 'Escape') {
            closePlayer();
        } else if (e.key === ' ') {
            e.preventDefault();
            togglePlay();
        } else if (e.key === 'ArrowLeft') {
            const media = getMedia();
            if (media) media.currentTime = Math.max(0, media.currentTime - 5);
        } else if (e.key === 'ArrowRight') {
            const media = getMedia();
            if (media) media.currentTime = Math.min(media.duration || 0, media.currentTime + 5);
        } else if (e.key === 'ArrowUp') {
            const media = getMedia();
            if (media) {
                media.volume = Math.min(1, media.volume + 0.1);
                document.getElementById('player-volume').value = media.volume;
                updateVolIcon();
            }
        } else if (e.key === 'ArrowDown') {
            const media = getMedia();
            if (media) {
                media.volume = Math.max(0, media.volume - 0.1);
                document.getElementById('player-volume').value = media.volume;
                updateVolIcon();
            }
        }
    });

    // ==================== TRIM (player modal) ====================

    function parseTime(str) {
        if (!str) return NaN;
        const parts = str.split(':').map(Number);
        if (parts.some(isNaN)) return NaN;
        if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
        if (parts.length === 2) return parts[0] * 60 + parts[1];
        return parts[0];
    }

    function fmtTimeInput(s) {
        if (!s || !isFinite(s)) return '0:00';
        s = Math.floor(s);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = s % 60;
        if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
        return m + ':' + String(sec).padStart(2, '0');
    }

    document.getElementById('trim-toggle-btn').addEventListener('click', () => {
        const panel = document.getElementById('trim-panel');
        const btn = document.getElementById('trim-toggle-btn');
        const isHidden = panel.classList.toggle('hidden');
        btn.classList.toggle('active', !isHidden);
        if (!isHidden) {
            const media = getMedia();
            if (media && media.duration) {
                document.getElementById('trim-end').value = fmtTimeInput(media.duration);
            }
        }
    });

    document.getElementById('trim-set-start').addEventListener('click', () => {
        const media = getMedia();
        if (media) document.getElementById('trim-start').value = fmtTimeInput(media.currentTime);
    });

    document.getElementById('trim-set-end').addEventListener('click', () => {
        const media = getMedia();
        if (media) document.getElementById('trim-end').value = fmtTimeInput(media.currentTime);
    });

    function followCutProgress(cutId, statusEl, progressBarEl, btn, btnLabel) {
        const fill = progressBarEl.querySelector('.cut-progress-fill');
        progressBarEl.classList.remove('hidden');
        fill.style.width = '0%';
        const es = new EventSource('/cut-progress/' + cutId);
        es.onmessage = (e) => {
            const ev = JSON.parse(e.data);
            if (ev.status === 'progress') {
                fill.style.width = ev.percent + '%';
                statusEl.textContent = 'Decoupe en cours... ' + ev.percent + '%';
            } else if (ev.status === 'complete') {
                es.close();
                fill.style.width = '100%';
                statusEl.textContent = ev.message + ' (' + formatSize(ev.size) + ')';
                statusEl.className = 'trim-status success';
                setTimeout(() => progressBarEl.classList.add('hidden'), 1500);
                btn.disabled = false;
                btn.textContent = btnLabel;
                loadDownloadsList();
                loadStats();
            } else if (ev.status === 'error') {
                es.close();
                progressBarEl.classList.add('hidden');
                statusEl.textContent = ev.message;
                statusEl.className = 'trim-status error';
                btn.disabled = false;
                btn.textContent = btnLabel;
            }
        };
        es.onerror = () => {
            es.close();
            progressBarEl.classList.add('hidden');
            statusEl.textContent = 'Connexion perdue';
            statusEl.className = 'trim-status error';
            btn.disabled = false;
            btn.textContent = btnLabel;
        };
    }

    document.getElementById('trim-cut-btn').addEventListener('click', async () => {
        const file = window._currentPlayerFile;
        if (!file) return;
        const start = parseTime(document.getElementById('trim-start').value);
        const end = parseTime(document.getElementById('trim-end').value);
        if (isNaN(start) || isNaN(end) || end <= start) return;

        const btn = document.getElementById('trim-cut-btn');
        const statusEl = document.getElementById('trim-status');
        const progressBar = document.getElementById('trim-progress-bar');
        btn.disabled = true;
        btn.textContent = 'Decoupe...';
        statusEl.textContent = 'Decoupe en cours... 0%';
        statusEl.className = 'trim-status loading';
        statusEl.classList.remove('hidden');

        try {
            const resp = await fetch('/cut-video', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category: file.category, filename: file.name, start, end }),
            });
            const data = await resp.json();
            if (data.cut_id) {
                followCutProgress(data.cut_id, statusEl, progressBar, btn, 'Couper');
            } else {
                statusEl.textContent = data.error || 'Erreur inconnue';
                statusEl.className = 'trim-status error';
                btn.disabled = false;
                btn.textContent = 'Couper';
            }
        } catch (err) {
            statusEl.textContent = 'Erreur: ' + err.message;
            statusEl.className = 'trim-status error';
            btn.disabled = false;
            btn.textContent = 'Couper';
        }
    });

    // ==================== CUT EDITOR (tab) ====================

    let cutState = { tempName: null, originalName: null, duration: 0, startPct: 0, endPct: 1, media: null };

    const cutDropzone = document.getElementById('cut-dropzone');
    const cutFileInput = document.getElementById('cut-file-input');
    const cutEditor = document.getElementById('cut-editor');
    const cutUploadZone = document.getElementById('cut-upload-zone');

    cutDropzone.addEventListener('click', () => cutFileInput.click());
    cutDropzone.addEventListener('dragover', (e) => { e.preventDefault(); cutDropzone.classList.add('dragover'); });
    cutDropzone.addEventListener('dragleave', () => cutDropzone.classList.remove('dragover'));
    cutDropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        cutDropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) uploadCutFile(e.dataTransfer.files[0]);
    });
    cutFileInput.addEventListener('change', () => {
        if (cutFileInput.files.length > 0) uploadCutFile(cutFileInput.files[0]);
    });

    document.getElementById('cut-reset-btn').addEventListener('click', resetCutEditor);

    async function uploadCutFile(file) {
        cutDropzone.classList.add('dragover');
        const origText = document.querySelector('.cut-dropzone-text');
        origText.textContent = 'Upload en cours...';

        const formData = new FormData();
        formData.append('file', file);

        try {
            const resp = await fetch('/upload-for-cut', { method: 'POST', body: formData });
            const data = await resp.json();

            if (data.error) {
                origText.textContent = data.error;
                setTimeout(() => { origText.textContent = 'Glisse un fichier ici'; cutDropzone.classList.remove('dragover'); }, 3000);
                return;
            }

            cutState.tempName = data.temp_name;
            cutState.originalName = data.original_name;
            cutState.duration = data.duration;
            cutState.startPct = 0;
            cutState.endPct = 1;

            showCutEditor(data);
        } catch (err) {
            origText.textContent = 'Erreur upload: ' + err.message;
            setTimeout(() => { origText.textContent = 'Glisse un fichier ici'; cutDropzone.classList.remove('dragover'); }, 3000);
        }
    }

    function showCutEditor(data) {
        cutUploadZone.classList.add('hidden');
        cutEditor.classList.remove('hidden');
        document.getElementById('cut-status').classList.add('hidden');

        const container = document.getElementById('cut-preview-container');
        container.textContent = '';
        const ext = data.original_name.split('.').pop().toLowerCase();
        const isAudio = ['mp3', 'm4a', 'wav', 'flac', 'ogg'].includes(ext);
        const el = document.createElement(isAudio ? 'audio' : 'video');
        el.src = '/stream-temp/' + encodeURIComponent(data.temp_name);
        el.preload = 'metadata';
        container.appendChild(el);
        cutState.media = el;

        el.addEventListener('loadedmetadata', () => {
            if (!cutState.duration || cutState.duration <= 0) cutState.duration = el.duration;
            updateCutLabels();
        });
        el.addEventListener('timeupdate', updateCutPlayhead);

        document.getElementById('cut-filename').textContent = data.original_name;
        document.getElementById('cut-filesize').textContent = formatSize(data.size);

        updateCutLabels();
        updateCutRange();
        setupCutRangeHandles();
    }

    function resetCutEditor() {
        if (_cutRangeCleanup) _cutRangeCleanup();
        cutUploadZone.classList.remove('hidden');
        cutEditor.classList.add('hidden');
        if (cutState.media) cutState.media.pause();
        cutState = { tempName: null, originalName: null, duration: 0, startPct: 0, endPct: 1, media: null };
        cutFileInput.value = '';
        document.querySelector('.cut-dropzone-text').textContent = 'Glisse un fichier ici';
        cutDropzone.classList.remove('dragover');
    }

    document.getElementById('cut-play-btn').addEventListener('click', () => {
        if (!cutState.media) return;
        if (cutState.media.paused) {
            cutState.media.currentTime = cutState.startPct * cutState.duration;
            cutState.media.play();
        } else {
            cutState.media.pause();
        }
    });

    function updateCutPlayhead() {
        if (!cutState.media || !cutState.duration) return;
        const pct = cutState.media.currentTime / cutState.duration;
        document.getElementById('cut-playhead').style.left = (pct * 100) + '%';
        document.getElementById('cut-current-time').textContent = fmtTimeInput(cutState.media.currentTime);
        const playBtn = document.getElementById('cut-play-btn');
        playBtn.textContent = cutState.media.paused ? '▶' : '⏸';
        if (cutState.media.currentTime >= cutState.endPct * cutState.duration) {
            cutState.media.pause();
            cutState.media.currentTime = cutState.endPct * cutState.duration;
        }
    }

    function updateCutLabels() {
        const d = cutState.duration || 0;
        const startSec = cutState.startPct * d;
        const endSec = cutState.endPct * d;
        document.getElementById('cut-label-start').textContent = fmtTimeInput(startSec);
        document.getElementById('cut-label-end').textContent = fmtTimeInput(endSec);
        const dur = endSec - startSec;
        document.getElementById('cut-label-duration').textContent = dur > 0 ? 'Selection: ' + fmtTimeInput(dur) : '';
    }

    function updateCutRange() {
        const selected = document.getElementById('cut-range-selected');
        const handleStart = document.getElementById('cut-handle-start');
        const handleEnd = document.getElementById('cut-handle-end');
        selected.style.left = (cutState.startPct * 100) + '%';
        selected.style.right = ((1 - cutState.endPct) * 100) + '%';
        handleStart.style.left = (cutState.startPct * 100) + '%';
        handleEnd.style.left = (cutState.endPct * 100) + '%';
    }

    let _cutRangeCleanup = null;

    function setupCutRangeHandles() {
        if (_cutRangeCleanup) _cutRangeCleanup();

        const track = document.getElementById('cut-range-track');
        const handleStart = document.getElementById('cut-handle-start');
        const handleEnd = document.getElementById('cut-handle-end');

        function pctFromEvent(e) {
            const rect = track.getBoundingClientRect();
            const x = (e.touches ? e.touches[0].clientX : e.clientX);
            return Math.max(0, Math.min(1, (x - rect.left) / rect.width));
        }

        let dragging = null;

        function onDown(handle, e) {
            e.preventDefault();
            dragging = handle;
            document.getElementById('cut-handle-' + handle).classList.add('dragging');
        }

        const onStartMouse = (e) => onDown('start', e);
        const onEndMouse = (e) => onDown('end', e);
        const onStartTouch = (e) => onDown('start', e);
        const onEndTouch = (e) => onDown('end', e);

        handleStart.addEventListener('mousedown', onStartMouse);
        handleEnd.addEventListener('mousedown', onEndMouse);
        handleStart.addEventListener('touchstart', onStartTouch, { passive: false });
        handleEnd.addEventListener('touchstart', onEndTouch, { passive: false });

        function onMove(e) {
            if (!dragging) return;
            const pct = pctFromEvent(e);
            if (dragging === 'start') {
                cutState.startPct = Math.min(pct, cutState.endPct - 0.005);
            } else {
                cutState.endPct = Math.max(pct, cutState.startPct + 0.005);
            }
            updateCutRange();
            updateCutLabels();
        }

        function onUp() {
            if (!dragging) return;
            document.getElementById('cut-handle-' + dragging).classList.remove('dragging');
            if (cutState.media && dragging === 'start') {
                cutState.media.currentTime = cutState.startPct * cutState.duration;
            }
            dragging = null;
        }

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onMove, { passive: true });
        document.addEventListener('touchend', onUp);

        function onTrackClick(e) {
            if (e.target === handleStart || e.target === handleEnd) return;
            const pct = pctFromEvent(e);
            if (cutState.media) {
                cutState.media.currentTime = pct * cutState.duration;
                updateCutPlayhead();
            }
        }
        track.addEventListener('click', onTrackClick);

        _cutRangeCleanup = () => {
            handleStart.removeEventListener('mousedown', onStartMouse);
            handleEnd.removeEventListener('mousedown', onEndMouse);
            handleStart.removeEventListener('touchstart', onStartTouch);
            handleEnd.removeEventListener('touchstart', onEndTouch);
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onUp);
            track.removeEventListener('click', onTrackClick);
            _cutRangeCleanup = null;
        };
    }

    document.getElementById('cut-do-btn').addEventListener('click', async () => {
        if (!cutState.tempName || !cutState.duration) return;
        const start = cutState.startPct * cutState.duration;
        const end = cutState.endPct * cutState.duration;
        if (end <= start) return;

        const btn = document.getElementById('cut-do-btn');
        const statusEl = document.getElementById('cut-status');
        const progressBar = document.getElementById('cut-progress-bar');
        btn.disabled = true;
        btn.textContent = 'Decoupe...';
        statusEl.textContent = 'Decoupe en cours... 0%';
        statusEl.className = 'trim-status loading';
        statusEl.classList.remove('hidden');

        try {
            const resp = await fetch('/cut-uploaded', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    temp_name: cutState.tempName,
                    original_name: cutState.originalName,
                    start: start,
                    end: end,
                }),
            });
            const data = await resp.json();
            if (data.cut_id) {
                followCutProgress(data.cut_id, statusEl, progressBar, btn, 'Couper');
            } else {
                statusEl.textContent = data.error || 'Erreur inconnue';
                statusEl.className = 'trim-status error';
                btn.disabled = false;
                btn.textContent = 'Couper';
            }
        } catch (err) {
            statusEl.textContent = 'Erreur: ' + err.message;
            statusEl.className = 'trim-status error';
            btn.disabled = false;
            btn.textContent = 'Couper';
        }
    });
});
