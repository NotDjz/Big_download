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
                    deleteFile(file);
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

    async function deleteFile(file) {
        if (!confirm('Supprimer "' + file.name + '" ?')) return;
        try {
            const resp = await fetch('/delete/' + file.category + '/' + encodeURIComponent(file.name), {
                method: 'DELETE',
            });
            const data = await resp.json();
            if (data.success) {
                loadDownloadsList();
                loadStats();
            } else {
                showStatus(data.error || 'Erreur suppression', 'error');
            }
        } catch (err) {
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
        playBtn.innerHTML = media.paused ? '&#9654;' : '&#9646;&#9646;';

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
});
