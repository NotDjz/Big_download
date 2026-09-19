document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('url-input');
    const goBtn = document.getElementById('go-btn');
    const platformBadge = document.getElementById('platform-badge');
    const resultZone = document.getElementById('result-zone');
    const progressZone = document.getElementById('progress-zone');
    const cancelBtn = document.getElementById('cancel-btn');
    // The job in flight: { id, es } or null. The stop button reads it, and
    // hideProgress() clears it so it never points at nothing.
    //
    // One phrase, two sites: the SSE fallback and the local path used to carry
    // two different French sentences and now say the same thing. Rewording one
    // and not the other would go unnoticed.
    const STOPPED = 'Download stopped';
    let currentJob = null;
    const statusMsg = document.getElementById('status-msg');

    let currentInfo = null;
    let selectedFormat = null;

    urlInput.addEventListener('input', onUrlChange);
    urlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') goBtn.click();
    });
    goBtn.addEventListener('click', onGo);

    loadDownloadsList();
    setInterval(() => {
        // The result card and the editor hide the library (a :has() rule):
        // without this guard, two requests and two server-side folder walks ran
        // to produce no pixels at all.
        if (!document.getElementById('library').offsetParent) return;
        loadDownloadsList();
    }, 15000);

    document.getElementById('open-folder-btn').addEventListener('click', () => {
        fetch('/open-folder', { method: 'POST' }).catch(() => {});
    });

    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
            document.getElementById('tab-' + tab.dataset.tab).classList.remove('hidden');
            // The card and the editor occupy the workspace: switching tabs
            // has to hand it back to the library, otherwise you keep staring at
            // the panel of the tab you just left.
            hideResult();
            // Hide without destroying: resetCutEditor() also releases
            // cutState, the temporary file and the selection. A round trip
            // between tabs therefore threw away a finished upload.
            const goingToCut = tab.dataset.tab === 'cut';
            const hasFile = Boolean(cutState.tempName);
            // Pause it: hiding without stopping left the audio playing behind
            // an invisible editor, with no button anywhere to stop it.
            if (!goingToCut && cutState.media) cutState.media.pause();
            setCutView(goingToCut && hasFile);
        });
    });

    // One icon set. The rows used to mix geometric glyphs with emoji, which
    // share neither weight nor colour nor alignment.
    // One table for the enum the server sends: the CSS class, the label and
    // the default were each expressed in a different place.
    const MEDIA_KIND = { video: 'Video', audio: 'Audio', photo: 'Photo' };

    // One span per fact, separated by a text node. Without the node, copying
    // the row produced one run-on word; without the spans, the CSS gap had
    // nothing to space out and the middle dot stayed.
    // The three action buttons were three copies of the same block, including
    // the stopPropagation each needs so it does not open the player.
    // Remember which icon is up, so nothing is rebuilt when it has not changed.
    function setBtnIcon(btn, name, filled) {
        if (btn.dataset.icon === name) return;
        btn.dataset.icon = name;
        btn.textContent = '';
        btn.appendChild(svgIcon(name, filled));
    }

    function actionBtn(icon, title, filled, onClick, extraClass) {
        const btn = document.createElement('button');
        btn.className = 'dl-action-btn' + (extraClass ? ' ' + extraClass : '');
        btn.title = title;
        btn.appendChild(svgIcon(icon, filled));
        btn.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
        return btn;
    }

    // POST /cancel BEFORE closing the SSE stream. Closing first fires the
    // server generator's finally, which drops the entry from the registry:
    // /cancel then found nothing to cancel, answered success, and the job ran
    // to completion while the interface said it had stopped. The route only
    // raises a flag, so the wait is on the order of a millisecond; the race
    // bounds it in case the server does not answer at all.
    async function postCancel(id) {
        try {
            await Promise.race([
                fetch('/cancel/' + id, { method: 'POST' }),
                new Promise((resolve) => setTimeout(resolve, 1500)),
            ]);
        } catch (err) {
            // The server did not answer: hand control back anyway.
        }
    }

    // The cut has its own button, in the Cut tab. Like currentJob, it holds
    // everything needed to undo itself: "Change file" is never disabled during
    // a cut and has to be able to stop one.
    let currentCut = null;   // { id, es, btn, bar } or null

    const cutCancelBtn = document.getElementById('cut-cancel-btn');

    // The counterpart of resetJob() for cuts, and for the same reason: one
    // place that returns the tab to rest, reachable from anywhere. While it
    // lived inside followCutProgress, changing files mid-cut left Stop on
    // screen pointing at the old job, whose terminal event then re-enabled Cut
    // underneath the new one.
    function endCut(delay) {
        const cut = currentCut;
        if (!cut) return;
        currentCut = null;
        cut.es.close();
        cutCancelBtn.classList.add('hidden');
        cutCancelBtn.disabled = false;
        cut.btn.disabled = false;
        cut.btn.textContent = 'Cut';
        if (delay) setTimeout(() => cut.bar.classList.add('hidden'), delay);
        else cut.bar.classList.add('hidden');
    }

    async function cancelCurrentCut() {
        // Capture before the await: the 'cancelled' event can land during it.
        const cut = currentCut;
        if (!cut) return;
        cutCancelBtn.disabled = true;
        await postCancel(cut.id);
        // FFmpeg is a subprocess: it is killed outright and the 'cancelled'
        // event follows immediately, returning the tab to rest. But if the
        // entry had already been dropped from the registry, that event never
        // comes, and the button has to re-enable itself.
        cutCancelBtn.disabled = false;
    }

    cutCancelBtn.addEventListener('click', cancelCurrentCut);

    function metaSpans(el, parts) {
        el.textContent = '';
        parts.filter(Boolean).forEach((t, i) => {
            if (i > 0) el.appendChild(document.createTextNode(' '));
            const span = document.createElement('span');
            span.textContent = t;
            el.appendChild(span);
        });
    }

    const SPEAKER = 'M3.5 6.2h2.3L8.5 3.8v8.4L5.8 9.8H3.5z';
    const ICONS = {
        play:   'M5 3.5v9l8-4.5z',
        folder: 'M2 4.5h4l1.2 1.6H14v6.4H2z',
        close:  'M4 4l8 8M12 4l-8 8',
        pause:  'M5.5 3.5h2v9h-2zM8.5 3.5h2v9h-2z',
        vol1:   SPEAKER + 'M10.6 6.4a2.6 2.6 0 010 3.2',
        vol2:   SPEAKER + 'M10.6 6.4a2.6 2.6 0 010 3.2M12.4 4.8a5 5 0 010 6.4',
        mute:   SPEAKER + 'M11 6.5l3 3M14 6.5l-3 3',
    };
    function svgIcon(name, filled) {
        const NS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('viewBox', '0 0 16 16');
        svg.setAttribute('aria-hidden', 'true');
        const path = document.createElementNS(NS, 'path');
        path.setAttribute('d', ICONS[name]);
        if (filled) { path.setAttribute('fill', 'currentColor'); path.setAttribute('stroke', 'none'); }
        svg.appendChild(path);
        return svg;
    }

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

    function onUrlChange() {
        const url = urlInput.value.trim();
        const platform = detectPlatform(url);

        if (platform) {
            // The CSS uppercases it: the ternary existed only for the 'x'.
            platformBadge.textContent = platform;
            platformBadge.className = 'platform-badge';
            platformBadge.classList.remove('hidden');
        } else {
            platformBadge.classList.add('hidden');
        }

        hideResult();
        // Do not hide it while a job is alive: Stop lives in that zone, and a
        // single keystroke in the URL field made a running download invisible,
        // and so impossible to stop.
        if (!currentJob) hideProgress();
        hideStatus();
    }

    function onGo() {
        const url = urlInput.value.trim();
        if (!url) return;

        // No filtering here any more: URL semantics belong to the server, and
        // its list differed from this one (vimeo was known to only one side).
        // detectPlatform now only feeds the badge while you type.
        fetchInfo(url);
    }

    async function fetchInfo(url) {
        goBtn.disabled = true;
        goBtn.textContent = '...';
        showStatus('Reading the link...', 'loading');
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
            // The server says whether this is a playlist. The client used to
            // pick the endpoint from its own detection, which could contradict
            // the server's.
            if (data.is_playlist) showPlaylistResult(data);
            else showResult(data);
            hideStatus();
        } catch (err) {
            showStatus('Error: ' + err.message, 'error');
        } finally {
            goBtn.disabled = false;
            goBtn.textContent = 'Download';
        }
    }

    // The three helpers below fetch #result-formats themselves, as
    // selectFormat already does: passing it from hand to hand only added one
    // more variable to follow.
    function formatsZone() {
        return document.getElementById('result-formats');
    }

    // The frame shared by both result panels: the same gestures were written
    // out twice.
    function resultFrame(title, parts, thumbnail) {
        const thumbEl = document.getElementById('result-thumb');
        thumbEl.textContent = '';
        if (thumbnail) {
            const img = document.createElement('img');
            img.src = thumbnail;
            img.alt = '';
            thumbEl.appendChild(img);
        }
        // Added in both cases: the two branches of the old if/else appended
        // this same chevron, which left only one of them doing any work.
        const play = document.createElement('span');
        play.className = 'thumb-play';
        play.textContent = '\u25B6';
        thumbEl.appendChild(play);

        document.getElementById('result-title').textContent = title;
        metaSpans(document.getElementById('result-meta'), parts);
        formatsZone().textContent = '';
    }

    // And the closing act, identical as well.
    function finishResult() {
        const dlBtn = document.createElement('button');
        dlBtn.className = 'go-btn';
        dlBtn.style.marginLeft = 'auto';
        dlBtn.textContent = 'Download';
        dlBtn.addEventListener('click', startDownload);
        formatsZone().appendChild(dlBtn);
        resultZone.classList.remove('hidden');
    }

    // Five sites built this button by hand, and two of them did it without the
    // inner <span>s: lacking .format-label, those two rendered without the
    // weight every other one has.
    //
    // It appends and selects itself. Without that, each call site had to repeat
    // `type` and `quality` to call selectFormat back on the button it had just
    // created.
    function formatBtn({ label, detail, type, quality = null, selected = false }) {
        const btn = document.createElement('button');
        btn.className = 'format-btn';
        const l = document.createElement('span');
        l.className = 'format-label';
        l.textContent = label;
        btn.appendChild(l);
        if (detail) {
            const d = document.createElement('span');
            d.className = 'format-detail';
            d.textContent = detail;
            btn.appendChild(d);
        }
        btn.addEventListener('click', () => selectFormat(btn, type, quality));
        formatsZone().appendChild(btn);
        if (selected) selectFormat(btn, type, quality);
        return btn;
    }

    const QUALITY_LABELS = { 2160: '4K', 1440: '1440p', 1080: '1080p', 720: '720p', 480: '480p', 360: '360p' };

    function showResult(data) {
        const parts = [];
        if (data.uploader && data.uploader !== 'N/A') parts.push(data.uploader);
        if (data.duration) parts.push(fmtTime(data.duration));
        if (data.platform) parts.push(data.platform.toUpperCase());
        resultFrame(data.title || 'Untitled', parts, data.thumbnail);
        selectedFormat = null;

        const qualities = data.available_qualities || [];
        const hasVideo = qualities.length > 0 || (data.height && data.height > 0);
        const hasAudio = data.has_audio !== false;
        const isPhoto = data._is_photo || (!hasVideo && !hasAudio && data.platform === 'instagram');

        if (isPhoto) {
            formatBtn({ label: 'Photo', detail: 'Image', type: 'photo', selected: true });
        } else if (hasVideo) {
            if (qualities.length > 0) {
                qualities.forEach((q, i) => formatBtn({
                    label: QUALITY_LABELS[q] || (q + 'p'), detail: 'MP4',
                    type: 'video', quality: q, selected: i === 0,
                }));
            } else {
                formatBtn({ label: 'MP4', type: 'video', selected: true });
            }
        }

        // MP3 only takes the selection in the absence of video AND of a
        // photo. Without the second test, a photo post whose extraction yields
        // no formats was offered an MP3, added after the Photo button and so
        // selected in its place. The server no longer claims audio there, but
        // the rule "an image outranks an assumed sound" reads better here.
        if (hasAudio) {
            formatBtn({ label: 'MP3', detail: 'Audio', type: 'mp3',
                selected: !hasVideo && !isPhoto });
        }

        finishResult();
    }

    function showPlaylistResult(data) {
        // "50+" when the server could not get an exact count: it enumerates
        // only the first videos, and that floor must not pass itself off as a
        // total.
        const count = data.video_count + (data.video_count_partial ? '+' : '');
        resultFrame(data.title || 'Playlist', [data.uploader, count + ' videos'], null);
        // The button carried its own handler, redoing by hand the .selected
        // bookkeeping selectFormat already does.
        formatBtn({ label: 'MP4 (all)', type: 'playlist', selected: true });
        finishResult();
    }


    function selectFormat(btn, type, quality) {
        const formatsEl = document.getElementById('result-formats');
        formatsEl.querySelectorAll('.format-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');

        // currentInfo.platform comes from the server; re-deriving it here made
        // the two detections diverge (vimeo was recognised on one side only).
        const platform = currentInfo.platform;
        if (type === 'playlist') {
            // This case was missing: without it a playlist's button fell into
            // the youtube branch below and asked for a single video.
            selectedFormat = { type: 'playlist', quality: null };
        } else if (type === 'mp3' || type === 'photo') {
            selectedFormat = { type: type, quality: null };
        } else if (platform === 'youtube') {
            selectedFormat = { type: 'youtube', quality: quality };
        } else {
            // quality was set to null here: picking 480p still downloaded the
            // heaviest stream the platform offers.
            selectedFormat = { type: 'social', quality: quality };
        }
    }

    function startDownload() {
        if (!currentInfo || !selectedFormat) return;
        const url = currentInfo._url;
        const type = selectedFormat.type;
        const quality = selectedFormat.quality;
        downloadWithProgress(url, type, quality);
    }

    async function downloadWithProgress(url, type, quality) {
        // Undo the previous one before opening another. Otherwise its
        // EventSource stayed open, its handlers called resetJob() and closed the
        // new one's stream; and the abandoned download carried on server-side
        // with nothing left that could stop it.
        if (currentJob) {
            postCancel(currentJob.id);
            resetJob();
        }
        hideResult();
        showProgress();
        updateProgress(0, 'Starting...', '', '');

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
            currentJob = { id: data.download_id, es: es };

            es.onmessage = (event) => {
                const msg = JSON.parse(event.data);

                if (msg.status === 'downloading') {
                    let status = 'Downloading...';
                    if (msg.current_video) {
                        status = 'Video ' + msg.current_video + '/' + msg.total_videos;
                    }
                    updateProgress(msg.percent || 0, status, msg.speed || '', msg.eta ? 'ETA: ' + msg.eta : '');
                } else if (msg.status === 'processing') {
                    updateProgress(100, msg.message || 'Working...', '', '');
                } else if (msg.status === 'playlist_start') {
                    updateProgress(0, 'Playlist: ' + msg.title + ' (' + msg.total_videos + ' videos)', '', '');
                } else if (msg.status === 'playlist_video_start') {
                    updateProgress(0, 'Video ' + msg.current_video + '/' + msg.total_videos + ': ' + msg.video_title, '', '');
                } else if (msg.status === 'playlist_video_error') {
                    updateProgress(0, 'Video ' + msg.current_video + '/' + msg.total_videos + ' failed', '', '');
                } else if (msg.status === 'complete') {
                    resetJob();
                    let message = 'Downloaded: ' + msg.title;
                    if (msg.is_playlist) {
                        message = 'Playlist finished: ' + msg.title + ' (' + msg.total_videos + ' videos)';
                    } else if (msg.resolution) {
                        message += ' (' + msg.resolution + ')';
                    }
                    showStatus(message, 'success');
                    loadDownloadsList();
                } else if (msg.status === 'cancelled') {
                    resetJob();
                    showStatus(msg.message || STOPPED, 'error');
                    loadDownloadsList();
                } else if (msg.status === 'error') {
                    resetJob();
                    showStatus(msg.message || 'Unknown error', 'error');
                }
            };

            es.onerror = () => {
                resetJob();
                showStatus('Connection lost', 'error');
            };

        } catch (err) {
            hideProgress();
            showStatus('Error: ' + err.message, 'error');
        }
    }

    // Frees the interface immediately, without waiting for the worker to
    // acknowledge: a download stuck where no yt-dlp hook runs will never answer,
    // and the user has to be able to start again regardless.
    async function cancelCurrentJob() {
        const job = currentJob;
        if (!job) return;
        cancelBtn.disabled = true;
        await postCancel(job.id);
        cancelBtn.disabled = false;
        // The job may have finished during the round trip: resetJob() will
        // already have run, and overwriting its success message with "stopped"
        // would be a lie.
        if (currentJob !== job) return;
        resetJob();
        showStatus(STOPPED, 'error');
    }

    cancelBtn.addEventListener('click', cancelCurrentJob);

    function showProgress() {
        progressZone.classList.remove('hidden');
    }

    // One place that undoes a job: close the stream, forget the reference,
    // hide the zone. This was spread over four sites and three calls to
    // hideProgress() forgot the reference.
    function resetJob() {
        if (currentJob && currentJob.es) currentJob.es.close();
        currentJob = null;
        hideProgress();
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

    // Formatters hoisted: passing an options object to toLocaleString bypasses
    // V8's cache and rebuilds an Intl.NumberFormat on every call. Measured at
    // 86x the cost per call, on a list that redraws often.
    const NUM0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
    const NUM1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

    function formatSize(bytes) {
        if (!bytes) return '0 B';
        const k = 1024;
        const units = ['B', 'KB', 'MB', 'GB'];
        const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
        const n = bytes / Math.pow(k, i);
        return (i === 0 ? NUM0 : NUM1).format(n) + ' ' + units[i];
    }

    function timeAgo(timestamp) {
        const diff = Math.floor(Date.now() / 1000 - timestamp);
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
        if (diff < 86400) return Math.floor(diff / 3600) + ' h ago';
        return Math.floor(diff / 86400) + ' d ago';
    }

    async function loadDownloadsList() {
        const listEl = document.getElementById('downloads-list');
        const countEl = document.getElementById('downloads-count');

        try {
            const resp = await fetch('/list-downloads');
            const files = await resp.json();
            // Before the early return on an empty list: otherwise deleting the
            // last file left the footer showing its old numbers.
            showStats(files);

            countEl.textContent = files.length > 0 ? files.length : '';

            if (files.length === 0) {
                listEl.textContent = '';
                const empty = document.createElement('div');
                empty.className = 'downloads-empty';
                empty.textContent = 'Nothing here yet';
                listEl.appendChild(empty);
                return;
            }

            listEl.textContent = '';

            files.forEach(file => {
                const row = document.createElement('div');
                row.className = 'download-row';

                const kind = MEDIA_KIND[file.media_type] ? file.media_type : 'video';
                row.dataset.type = kind;

                const info = document.createElement('div');
                info.className = 'dl-info';

                const name = document.createElement('div');
                name.className = 'dl-name';
                name.textContent = file.name;
                info.appendChild(name);

                const meta = document.createElement('div');
                meta.className = 'dl-meta';
                const parts = [formatSize(file.size), MEDIA_KIND[kind]];
                if (file.timestamp) parts.push(timeAgo(file.timestamp));
                metaSpans(meta, parts);
                info.appendChild(meta);

                row.appendChild(info);

                const actions = document.createElement('div');
                actions.className = 'dl-actions';

                actions.appendChild(actionBtn('play', 'Play', true, () => openPlayer(file)));
                actions.appendChild(actionBtn('folder', 'Show in Explorer', false,
                    () => fetch('/open-file/' + file.category + '/' + encodeURIComponent(file.name), { method: 'POST' })));
                actions.appendChild(actionBtn('close', 'Delete', false,
                    () => confirmDelete(file, row), 'danger'));

                row.appendChild(actions);

                row.addEventListener('click', () => openPlayer(file));

                listEl.appendChild(row);
            });

        } catch (err) {
            listEl.textContent = '';
            const empty = document.createElement('div');
            empty.className = 'downloads-empty';
            empty.textContent = 'Could not load';
            listEl.appendChild(empty);
        }
    }

    function confirmDelete(file, rowEl) {
        const overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        const box = document.createElement('div');
        box.className = 'confirm-box';
        box.innerHTML = '<div class="confirm-msg">Delete <strong>' +
            file.name.replace(/</g, '&lt;') + '</strong>?</div>';
        const btns = document.createElement('div');
        btns.className = 'confirm-btns';
        const dismissBtn = document.createElement('button');
        dismissBtn.className = 'confirm-btn cancel';
        dismissBtn.textContent = 'Cancel';
        const okBtn = document.createElement('button');
        okBtn.className = 'confirm-btn ok';
        okBtn.textContent = 'Delete';
        btns.appendChild(dismissBtn);
        btns.appendChild(okBtn);
        box.appendChild(btns);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        dismissBtn.addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
        okBtn.addEventListener('click', () => { overlay.remove(); deleteFile(file, rowEl); });
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
                // A single reload: it refreshes the list, the counter and the
                // footer. This used to be an optimistic removal plus a call to
                // /get-stats, which meant two folder walks.
                loadDownloadsList();
            } else {
                rowEl.style.opacity = '1';
                showStatus(data.error || 'Could not delete', 'error');
            }
        } catch (err) {
            rowEl.style.opacity = '1';
            showStatus('Error: ' + err.message, 'error');
        }
    }

    // Derived from the list already loaded. /get-stats walked all three server
    // folders again for two numbers that 'files' already holds.
    function showStats(files) {
        const total = files.reduce((n, f) => n + (f.size || 0), 0);
        const n = files.length;
        document.getElementById('footer-stats').textContent =
            n + (n === 1 ? ' file, ' : ' files, ') + formatSize(total);
    }

    // Player modal + custom controls
    let seekAnimFrame = null;

    // Player elements, resolved once. updatePlayerUI runs at 60 fps and was
    // redoing six lookups per frame (two querySelector inside getMedia, four
    // getElementById) for nodes that do not move for the whole session.
    const PL = {};
    ['player-container', 'player-seek-fill', 'player-time', 'player-play-btn',
     'player-vol-icon', 'player-volume', 'player-modal'].forEach(id => {
        PL[id.replace('player-', '')] = document.getElementById(id);
    });

    let _media = null;
    function getMedia() {
        // Stay on the cache while the element is still in the container;
        // closePlayer empties it, which invalidates naturally.
        if (_media && _media.parentNode === PL.container) return _media;
        _media = PL.container.querySelector('video, audio');
        return _media;
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
        if (seekAnimFrame) {
            cancelAnimationFrame(seekAnimFrame);
            seekAnimFrame = null;
        }

        const fill = PL['seek-fill'];
        const timeEl = PL.time;
        const playBtn = PL['play-btn'];

        const pct = media.duration ? (media.currentTime / media.duration) * 100 : 0;
        fill.style.width = pct + '%';
        timeEl.textContent = fmtTime(media.currentTime) + ' / ' + fmtTime(media.duration);
        // Only redraw on change: called from the rAF loop, this block rebuilt
        // two SVG nodes every frame, 60 a second, for an icon that moves only on
        // play and pause.
        setBtnIcon(playBtn, media.paused ? 'play' : 'pause', true);

        // Only reschedule while playing: the function used to reschedule from
        // each of its callers, stacking chains that seekAnimFrame could no
        // longer cancel, so a paused video kept repainting at 60 fps.
        if (!media.paused && !media.ended) {
            seekAnimFrame = requestAnimationFrame(updatePlayerUI);
        }
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
        const volSlider = PL.volume;
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
        const icon = PL['vol-icon'];
        if (!media) return;
        // The same icon set as the rows. Emoji 128264/5/6 were the only
        // coloured glyphs left in the interface, sitting beside monochrome
        // strokes.
        const name = (media.muted || media.volume === 0) ? 'mute'
                   : media.volume < 0.5 ? 'vol1' : 'vol2';
        setBtnIcon(icon, name, false);
    }

    function togglePlay() {
        const media = getMedia();
        if (!media) return;
        if (media.paused) media.play(); else media.pause();
    }

    function toggleMute() {
        const media = getMedia();
        if (!media) return;
        media.muted = !media.muted;
        const volSlider = PL.volume;
        if (media.muted) {
            volSlider.value = 0;
        } else {
            volSlider.value = media.volume;
        }
        updateVolIcon();
    };

    setupSeekBar();
    setupVolume();

    function openPlayer(file) {
        const modal = PL.modal;
        const container = PL.container;
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
            container.appendChild(img);
            controls.style.display = 'none';
        } else {
            const el = document.createElement(isAudio ? 'audio' : 'video');
            el.src = streamUrl;
            el.autoplay = true;
            container.appendChild(el);
            controls.style.display = '';

            const volSlider = PL.volume;
            el.volume = parseFloat(volSlider.value);
            // Draw the icon on open: it was only drawn on the first volume
            // change, with an HTML entity providing the initial state. Once that
            // moved to SVG, the button stayed empty until the first click.
            updateVolIcon();

            // 'seeked' covers scrubbing a paused video, which the rAF loop no
            // longer repaints. No 'timeupdate': it adds no smoothness and keeps
            // firing at 4 Hz in a hidden window, which is exactly where rAF is
            // throttled to nothing.
            ['play', 'pause', 'ended', 'loadedmetadata', 'seeked']
                .forEach(ev => el.addEventListener(ev, updatePlayerUI));

            updatePlayerUI();
        }

        titleEl.textContent = file.name;
        // MEDIA_KIND and not file.category: the row said "Video" where the
        // player said "Videos" for the same file.
        metaSpans(metaEl, [formatSize(file.size), MEDIA_KIND[file.media_type] || 'Video']);

        modal.classList.remove('hidden');
        // Still needed: the 820px media query puts the body back to
        // overflow:auto, and the page then scrolled behind the open player.
        document.body.style.overflow = 'hidden';
    };

    function closePlayer() {
        const modal = PL.modal;
        const container = PL.container;

        if (seekAnimFrame) cancelAnimationFrame(seekAnimFrame);
        const media = getMedia();
        if (media) media.pause();

        container.textContent = '';
        _media = null;  // otherwise the closure keeps the detached element and its buffer
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    }

    // Wired in JS rather than through onclick attributes: those forced four
    // functions onto window and ruled out a CSP without 'unsafe-inline'.
    document.getElementById('player-backdrop').addEventListener('click', closePlayer);
    const closeBtn = document.getElementById('player-close-btn');
    setBtnIcon(closeBtn, 'close', false);
    closeBtn.addEventListener('click', closePlayer);
    document.getElementById('player-play-btn').addEventListener('click', togglePlay);
    PL['vol-icon'].addEventListener('click', toggleMute);

    document.addEventListener('keydown', (e) => {
        // PL.modal: this handler is on document, so it ran on every keystroke
        // in the URL field or the cut editor.
        if (PL.modal.classList.contains('hidden')) return;

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
                PL.volume.value = media.volume;
                updateVolIcon();
            }
        } else if (e.key === 'ArrowDown') {
            const media = getMedia();
            if (media) {
                media.volume = Math.max(0, media.volume - 0.1);
                PL.volume.value = media.volume;
                updateVolIcon();
            }
        }
    });

    function followCutProgress(cutId, statusEl, progressBarEl, btn) {
        const fill = progressBarEl.querySelector('.cut-progress-fill');
        progressBarEl.classList.remove('hidden');
        cutCancelBtn.classList.remove('hidden');
        fill.style.width = '0%';

        const es = new EventSource('/cut-progress/' + cutId);
        // endCut closes the stream: it, and it alone, undoes a cut.
        currentCut = { id: cutId, es: es, btn: btn, bar: progressBarEl };
        es.onmessage = (e) => {
            const ev = JSON.parse(e.data);
            if (ev.status === 'progress') {
                fill.style.width = ev.percent + '%';
                statusEl.textContent = 'Cutting... ' + ev.percent + '%';
            } else if (ev.status === 'complete') {
                fill.style.width = '100%';
                statusEl.textContent = ev.message + ' (' + formatSize(ev.size) + ')';
                statusEl.className = 'trim-status success';
                endCut(1500);
                loadDownloadsList();
            } else if (ev.status === 'cancelled' || ev.status === 'error') {
                // 'cancelled' was missing: a stopped cut left the bar on screen
                // and Cut disabled until the page was reloaded.
                endCut();
                statusEl.textContent = ev.message;
                statusEl.className = 'trim-status error';
            }
        };
        es.onerror = () => {
            endCut();
            statusEl.textContent = 'Connection lost';
            statusEl.className = 'trim-status error';
        };
    }

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
                setTimeout(() => { origText.textContent = 'Drop a file here'; cutDropzone.classList.remove('dragover'); }, 3000);
                return;
            }

            cutState.tempName = data.temp_name;
            cutState.originalName = data.original_name;
            cutState.duration = data.duration;
            cutState.startPct = 0;
            cutState.endPct = 1;

            showCutEditor(data);
        } catch (err) {
            origText.textContent = 'Upload failed: ' + err.message;
            setTimeout(() => { origText.textContent = 'Drop a file here'; cutDropzone.classList.remove('dragover'); }, 3000);
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

    // One place where the invariant "editor visible <=> dropzone hidden" is
    // written down; it used to be written in three, in both directions.
    function setCutView(showEditor) {
        cutEditor.classList.toggle('hidden', !showEditor);
        cutUploadZone.classList.toggle('hidden', showEditor);
    }

    function resetCutEditor() {
        // "Change file" is never disabled during a cut, so the cut has to be
        // stopped for real, not just hidden behind the editor.
        if (currentCut) {
            postCancel(currentCut.id);
            endCut();
        }
        if (_cutRangeCleanup) _cutRangeCleanup();
        setCutView(false);
        if (cutState.media) cutState.media.pause();
        cutState = { tempName: null, originalName: null, duration: 0, startPct: 0, endPct: 1, media: null };
        cutFileInput.value = '';
        document.querySelector('.cut-dropzone-text').textContent = 'Drop a file here';
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
        document.getElementById('cut-current-time').textContent = fmtTime(cutState.media.currentTime);
        const playBtn = document.getElementById('cut-play-btn');
        setBtnIcon(playBtn, cutState.media.paused ? 'play' : 'pause', true);
        if (cutState.media.currentTime >= cutState.endPct * cutState.duration) {
            cutState.media.pause();
            cutState.media.currentTime = cutState.endPct * cutState.duration;
        }
    }

    function updateCutLabels() {
        const d = cutState.duration || 0;
        const startSec = cutState.startPct * d;
        const endSec = cutState.endPct * d;
        document.getElementById('cut-label-start').textContent = fmtTime(startSec);
        document.getElementById('cut-label-end').textContent = fmtTime(endSec);
        const dur = endSec - startSec;
        document.getElementById('cut-label-duration').textContent = dur > 0 ? 'Selection: ' + fmtTime(dur) : '';
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

        // The rect is measured once when the drag starts, not on every move:
        // onMove writes four styles, so re-reading the geometry afterwards
        // forced a synchronous layout on every pointer event. The track cannot
        // move mid-drag.
        let dragRect = null;

        function pctFromEvent(e) {
            const rect = dragRect || track.getBoundingClientRect();
            const x = (e.touches ? e.touches[0].clientX : e.clientX);
            return Math.max(0, Math.min(1, (x - rect.left) / rect.width));
        }

        let dragging = null;

        function onDown(handle, e) {
            e.preventDefault();
            dragging = handle;
            dragRect = track.getBoundingClientRect();
            document.getElementById('cut-handle-' + handle).classList.add('dragging');
        }

        const onStartDown = (e) => onDown('start', e);
        const onEndDown = (e) => onDown('end', e);

        handleStart.addEventListener('mousedown', onStartDown);
        handleEnd.addEventListener('mousedown', onEndDown);
        handleStart.addEventListener('touchstart', onStartDown, { passive: false });
        handleEnd.addEventListener('touchstart', onEndDown, { passive: false });

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
            dragRect = null;
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
            handleStart.removeEventListener('mousedown', onStartDown);
            handleEnd.removeEventListener('mousedown', onEndDown);
            handleStart.removeEventListener('touchstart', onStartDown);
            handleEnd.removeEventListener('touchstart', onEndDown);
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
        btn.textContent = 'Cutting...';
        statusEl.textContent = 'Cutting... 0%';
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
                followCutProgress(data.cut_id, statusEl, progressBar, btn);
            } else {
                statusEl.textContent = data.error || 'Unknown error';
                statusEl.className = 'trim-status error';
                btn.disabled = false;
                btn.textContent = 'Cut';
            }
        } catch (err) {
            statusEl.textContent = 'Error: ' + err.message;
            statusEl.className = 'trim-status error';
            btn.disabled = false;
            btn.textContent = 'Cut';
        }
    });
});
