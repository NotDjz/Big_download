document.addEventListener('DOMContentLoaded', () => {
    const urlInput = document.getElementById('url-input');
    const goBtn = document.getElementById('go-btn');
    const platformBadge = document.getElementById('platform-badge');
    const resultZone = document.getElementById('result-zone');
    const progressZone = document.getElementById('progress-zone');
    const cancelBtn = document.getElementById('cancel-btn');
    // Le job en cours : { id, es } ou null. Le bouton s'en sert, et
    // hideProgress() le remet a zero pour qu'il ne pointe jamais dans le vide.
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
        // La fiche et l'editeur masquent la bibliotheque (regle :has()) : sans
        // cette garde, deux requetes et deux parcours de dossiers cote serveur
        // tournaient pour ne produire aucun pixel.
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
            // La fiche et l'editeur occupent le plan de travail : changer
            // d'onglet doit le rendre a la bibliotheque, sinon on garde sous
            // les yeux le panneau de l'onglet qu'on vient de quitter.
            hideResult();
            // Masquer sans detruire : resetCutEditor() libere aussi cutState,
            // le fichier temporaire et la selection. Un aller-retour entre
            // onglets jetait donc un upload deja termine.
            const goingToCut = tab.dataset.tab === 'cut';
            const hasFile = Boolean(cutState.tempName);
            // Mettre en pause : masquer sans arreter laissait l'audio tourner
            // derriere un editeur invisible, donc sans aucun bouton pour l'arreter.
            if (!goingToCut && cutState.media) cutState.media.pause();
            setCutView(goingToCut && hasFile);
        });
    });

    // Un seul jeu d'icones. Avant, les lignes melangeaient des glyphes
    // geometriques et des emojis, qui n'ont ni le meme poids ni la meme
    // couleur ni le meme alignement.
    // Une seule table pour l'enum que le serveur envoie : la classe CSS, le
    // libelle et la valeur par defaut etaient exprimes a trois endroits.
    const MEDIA_KIND = { video: 'Video', audio: 'Audio', photo: 'Photo' };

    // Un span par fait, separes par un noeud texte. Sans le noeud, copier la
    // ligne donnait un seul mot colle ; sans les spans, le gap CSS n'avait
    // rien a espacer et le point median restait.
    // Les trois boutons d'action etaient trois copies du meme bloc, y compris
    // du stopPropagation que chacun doit faire pour ne pas ouvrir le player.
    // Memorise l'icone posee pour ne rien reconstruire quand elle ne change pas.
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

    // Poster /cancel AVANT de fermer le flux SSE. Fermer d'abord declenche le
    // finally du generateur cote serveur, qui retire l'entree du registre :
    // /cancel ne trouvait alors plus rien a annuler, repondait succes, et le job
    // continuait jusqu'au bout pendant que l'interface affichait « arrete ».
    // La route ne fait que poser un drapeau, donc l'attente est de l'ordre de la
    // milliseconde ; la course la borne si le serveur ne repond pas du tout.
    async function postCancel(id) {
        try {
            await Promise.race([
                fetch('/cancel/' + id, { method: 'POST' }),
                new Promise((resolve) => setTimeout(resolve, 1500)),
            ]);
        } catch (err) {
            // Le serveur n'a pas repondu : on rend la main quand meme.
        }
    }

    // La decoupe a son propre bouton, dans l'onglet Decouper. Comme currentJob,
    // elle retient de quoi se defaire entierement : « Changer de fichier »
    // n'est jamais desactive pendant une coupe et doit pouvoir l'arreter.
    let currentCut = null;   // { id, es, btn, bar } ou null

    const cutCancelBtn = document.getElementById('cut-cancel-btn');

    // Le pendant de resetJob() pour la decoupe, et pour la meme raison : un seul
    // endroit qui rend l'onglet au repos, atteignable de partout. Quand il
    // vivait dans followCutProgress, changer de fichier pendant une coupe
    // laissait « Arreter » visible et pointe sur l'ancien job, dont l'evenement
    // terminal reactivait ensuite « Couper » sous la nouvelle coupe.
    function endCut(delay) {
        const cut = currentCut;
        if (!cut) return;
        currentCut = null;
        cut.es.close();
        cutCancelBtn.classList.add('hidden');
        cutCancelBtn.disabled = false;
        cut.btn.disabled = false;
        cut.btn.textContent = 'Couper';
        if (delay) setTimeout(() => cut.bar.classList.add('hidden'), delay);
        else cut.bar.classList.add('hidden');
    }

    async function cancelCurrentCut() {
        // Capture avant l'await : l'evenement 'cancelled' peut arriver pendant.
        const cut = currentCut;
        if (!cut) return;
        cutCancelBtn.disabled = true;
        await postCancel(cut.id);
        // FFmpeg est un sous-processus : il est tue pour de bon et l'evenement
        // 'cancelled' arrive aussitot, qui remet l'onglet au repos. Mais si
        // l'entree avait deja ete retiree du registre, cet evenement ne vient
        // jamais — le bouton doit alors se reactiver de lui-meme.
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
            // Le CSS met en capitales : le ternaire n'existait que pour le 'x'.
            platformBadge.textContent = platform;
            platformBadge.className = 'platform-badge';
            platformBadge.classList.remove('hidden');
        } else {
            platformBadge.classList.add('hidden');
        }

        hideResult();
        // Pas de masquage tant qu'un job vit : « Arreter » est dans cette zone,
        // et une seule frappe dans le champ URL rendait un telechargement en
        // cours invisible, donc inarretable.
        if (!currentJob) hideProgress();
        hideStatus();
    }

    function onGo() {
        const url = urlInput.value.trim();
        if (!url) return;

        // Plus de filtrage ici : c'est le serveur qui possede la semantique des
        // URL, et sa liste differait de celle-ci (vimeo n'etait connu que d'un
        // cote). detectPlatform ne sert plus qu'au badge pendant la frappe.
        fetchInfo(url);
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
            // C'est le serveur qui dit si c'est une playlist. Le client
            // choisissait l'endpoint sur sa propre detection, qui pouvait
            // contredire la sienne.
            if (data.is_playlist) showPlaylistResult(data);
            else showResult(data);
            hideStatus();
        } catch (err) {
            showStatus('Erreur: ' + err.message, 'error');
        } finally {
            goBtn.disabled = false;
            goBtn.textContent = 'Telecharger';
        }
    }

    // Les trois helpers ci-dessous vont chercher #result-formats eux-memes,
    // comme selectFormat le fait deja : le passer de main en main n'ajoutait
    // qu'une variable a suivre.
    function formatsZone() {
        return document.getElementById('result-formats');
    }

    // Le cadre commun aux deux affichages de resultat : les memes gestes
    // etaient ecrits deux fois.
    function resultFrame(titre, parts, thumbnail) {
        const thumbEl = document.getElementById('result-thumb');
        thumbEl.textContent = '';
        if (thumbnail) {
            const img = document.createElement('img');
            img.src = thumbnail;
            img.alt = '';
            thumbEl.appendChild(img);
        }
        // Pose dans les deux cas : les deux branches de l'ancien if/else
        // ajoutaient ce meme chevron, ce qui n'en laissait qu'une utile.
        const play = document.createElement('span');
        play.className = 'thumb-play';
        play.textContent = '\u25B6';
        thumbEl.appendChild(play);

        document.getElementById('result-title').textContent = titre;
        metaSpans(document.getElementById('result-meta'), parts);
        formatsZone().textContent = '';
    }

    // Et la fermeture, identique elle aussi.
    function finishResult() {
        const dlBtn = document.createElement('button');
        dlBtn.className = 'go-btn';
        dlBtn.style.marginLeft = 'auto';
        dlBtn.textContent = 'Telecharger';
        dlBtn.addEventListener('click', startDownload);
        formatsZone().appendChild(dlBtn);
        resultZone.classList.remove('hidden');
    }

    // Cinq sites construisaient ce bouton a la main, et deux le faisaient sans
    // les <span> internes : faute de .format-label, ces deux-la s'affichaient
    // sans le gras de tous les autres.
    //
    // Il pose et selectionne lui-meme. Sans cela, chaque site d'appel devait
    // repeter `type` et `quality` pour rappeler selectFormat sur le bouton
    // qu'il venait de creer.
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
        resultFrame(data.title || 'Sans titre', parts, data.thumbnail);
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

        // Le MP3 ne prend la main que faute de video ET faute de photo. Sans
        // le second test, un post photo dont l'extraction ne rend aucun format
        // se voyait proposer un MP3 -- pose apres le bouton Photo, donc
        // selectionne a sa place. Le serveur ne l'annonce plus, mais la regle
        // « une image passe avant un son suppose » se lit mieux ici.
        if (hasAudio) {
            formatBtn({ label: 'MP3', detail: 'Audio', type: 'mp3',
                selected: !hasVideo && !isPhoto });
        }

        finishResult();
    }

    function showPlaylistResult(data) {
        // « 50+ » quand le serveur n'a pas pu obtenir le compte exact : il
        // n'enumere que les premieres videos, et ce plancher ne doit pas se
        // faire passer pour un total.
        const compte = data.video_count + (data.video_count_partial ? '+' : '');
        resultFrame(data.title || 'Playlist', [data.uploader, compte + ' videos'], null);
        // Le bouton avait son propre gestionnaire, qui refaisait a la main le
        // menage de la classe .selected que selectFormat fait deja.
        formatBtn({ label: 'MP4 (all)', type: 'playlist', selected: true });
        finishResult();
    }


    function selectFormat(btn, type, quality) {
        const formatsEl = document.getElementById('result-formats');
        formatsEl.querySelectorAll('.format-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');

        // currentInfo.platform vient du serveur ; le re-deduire ici faisait
        // diverger les deux detections (vimeo etait reconnu d'un cote seulement).
        const platform = currentInfo.platform;
        if (type === 'playlist') {
            // Ce cas manquait : sans lui, le bouton d'une playlist retombait
            // dans la branche youtube ci-dessous et demandait une video seule.
            selectedFormat = { type: 'playlist', quality: null };
        } else if (type === 'mp3' || type === 'photo') {
            selectedFormat = { type: type, quality: null };
        } else if (platform === 'youtube') {
            selectedFormat = { type: 'youtube', quality: quality };
        } else {
            // la qualite etait mise a null ici : choisir 480p telechargeait
            // quand meme le flux le plus lourd que la plateforme propose.
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
        // Defaire le precedent avant d'en ouvrir un autre. Sinon son
        // EventSource restait ouvert, ses handlers appelaient resetJob() et
        // fermaient le flux du nouveau ; et le telechargement abandonne
        // continuait cote serveur sans que rien ne puisse plus l'arreter.
        if (currentJob) {
            postCancel(currentJob.id);
            resetJob();
        }
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
            currentJob = { id: data.download_id, es: es };

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
                    resetJob();
                    let message = 'Telecharge: ' + msg.title;
                    if (msg.is_playlist) {
                        message = 'Playlist terminee: ' + msg.title + ' (' + msg.total_videos + ' videos)';
                    } else if (msg.resolution) {
                        message += ' (' + msg.resolution + ')';
                    }
                    showStatus(message, 'success');
                    loadDownloadsList();
                } else if (msg.status === 'cancelled') {
                    resetJob();
                    showStatus(msg.message || 'Telechargement annule', 'error');
                    loadDownloadsList();
                } else if (msg.status === 'error') {
                    resetJob();
                    showStatus(msg.message || 'Erreur inconnue', 'error');
                }
            };

            es.onerror = () => {
                resetJob();
                showStatus('Connexion perdue', 'error');
            };

        } catch (err) {
            hideProgress();
            showStatus('Erreur: ' + err.message, 'error');
        }
    }

    // Libere l'interface tout de suite, sans attendre que le worker accuse
    // reception : un telechargement bloque la ou aucun hook yt-dlp ne passe ne
    // repondra jamais, et l'utilisateur doit pouvoir relancer malgre tout.
    async function cancelCurrentJob() {
        const job = currentJob;
        if (!job) return;
        cancelBtn.disabled = true;
        await postCancel(job.id);
        cancelBtn.disabled = false;
        // Le job a pu se terminer pendant l'aller-retour : resetJob() a alors
        // deja tourne, et ecraser son message de succes par « arrete » mentirait.
        if (currentJob !== job) return;
        resetJob();
        showStatus('Telechargement arrete', 'error');
    }

    cancelBtn.addEventListener('click', cancelCurrentJob);

    function showProgress() {
        progressZone.classList.remove('hidden');
    }

    // Un seul endroit qui defait un job : fermer le flux, oublier la reference,
    // masquer la zone. C'etait eparpille sur quatre sites et trois appels a
    // hideProgress() l'oubliaient.
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

    // Unites et separateur decimal francais : le reste de l'interface est en
    // francais, afficher '37.4 MB' au milieu detonnait.
    // Formateurs hisses : passer un objet d'options a toLocaleString court-circuite
    // le cache de V8 et reconstruit un Intl.NumberFormat a chaque appel.
    const NUM0 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
    const NUM1 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });

    function formatSize(bytes) {
        if (!bytes) return '0 o';
        const k = 1024;
        const units = ['o', 'Ko', 'Mo', 'Go'];
        const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), units.length - 1);
        const n = bytes / Math.pow(k, i);
        return (i === 0 ? NUM0 : NUM1).format(n) + ' ' + units[i];
    }

    function timeAgo(timestamp) {
        const diff = Math.floor(Date.now() / 1000 - timestamp);
        if (diff < 60) return 'a l\'instant';
        if (diff < 3600) return 'il y a ' + Math.floor(diff / 60) + ' min';
        if (diff < 86400) return 'il y a ' + Math.floor(diff / 3600) + ' h';
        return 'il y a ' + Math.floor(diff / 86400) + ' j';
    }

    async function loadDownloadsList() {
        const listEl = document.getElementById('downloads-list');
        const countEl = document.getElementById('downloads-count');

        try {
            const resp = await fetch('/list-downloads');
            const files = await resp.json();
            // Avant le retour anticipe sur liste vide : sinon supprimer le
            // dernier fichier laissait le pied de page sur ses anciens chiffres.
            showStats(files);

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

                actions.appendChild(actionBtn('play', 'Lire', true, () => openPlayer(file)));
                actions.appendChild(actionBtn('folder', "Ouvrir dans l'explorateur", false,
                    () => fetch('/open-file/' + file.category + '/' + encodeURIComponent(file.name), { method: 'POST' })));
                actions.appendChild(actionBtn('close', 'Supprimer', false,
                    () => confirmDelete(file, row), 'danger'));

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

    function confirmDelete(file, rowEl) {
        const overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        const box = document.createElement('div');
        box.className = 'confirm-box';
        box.innerHTML = '<div class="confirm-msg">Supprimer <strong>' +
            file.name.replace(/</g, '&lt;') + '</strong> ?</div>';
        const btns = document.createElement('div');
        btns.className = 'confirm-btns';
        const dismissBtn = document.createElement('button');
        dismissBtn.className = 'confirm-btn cancel';
        dismissBtn.textContent = 'Annuler';
        const okBtn = document.createElement('button');
        okBtn.className = 'confirm-btn ok';
        okBtn.textContent = 'Supprimer';
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
                // Un seul rechargement : il rafraichit la liste, le compteur et
                // le pied de page. Avant, c'etait un retrait optimiste plus un
                // appel a /get-stats, soit deux parcours de dossiers.
                loadDownloadsList();
            } else {
                rowEl.style.opacity = '1';
                showStatus(data.error || 'Erreur suppression', 'error');
            }
        } catch (err) {
            rowEl.style.opacity = '1';
            showStatus('Erreur: ' + err.message, 'error');
        }
    }

    // Derive de la liste deja chargee. /get-stats refaisait un parcours complet
    // des trois dossiers cote serveur pour deux nombres que 'files' contient.
    function showStats(files) {
        const total = files.reduce((n, f) => n + (f.size || 0), 0);
        document.getElementById('footer-stats').textContent =
            files.length + ' fichiers · ' + formatSize(total);
    }

    // Player modal + custom controls
    let seekAnimFrame = null;

    // Elements du player, resolus une fois. updatePlayerUI tourne a 60 fps et
    // refaisait six recherches par image (deux querySelector dans getMedia,
    // quatre getElementById) pour des noeuds qui ne bougent pas de la session.
    const PL = {};
    ['player-container', 'player-seek-fill', 'player-time', 'player-play-btn',
     'player-vol-icon', 'player-volume', 'player-modal'].forEach(id => {
        PL[id.replace('player-', '')] = document.getElementById(id);
    });

    let _media = null;
    function getMedia() {
        // Reste sur le cache tant que l'element est encore dans le conteneur ;
        // closePlayer le vide, ce qui invalide naturellement.
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
        // Ne redessiner qu'au changement : appele depuis la boucle rAF, ce bloc
        // reconstruisait deux noeuds SVG a chaque image (60/s) pour une icone
        // qui ne bouge que sur lecture/pause.
        setBtnIcon(playBtn, media.paused ? 'play' : 'pause', true);

        // Ne se replanifier qu'en lecture : la fonction se replanifiait depuis
        // chacun de ses appelants, empilant des chaines que seekAnimFrame ne
        // pouvait plus annuler - une video en pause repeignait a 60 fps.
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
        // Meme jeu d'icones que les lignes. Les emojis 128264/5/6 restaient les
        // seuls glyphes en couleur de l'interface, a cote de traces monochromes.
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
            // Poser l'icone a l'ouverture : elle n'etait dessinee qu'au premier
            // reglage du volume, l'entite HTML fournissant l'etat initial. En
            // passant au SVG, le bouton restait vide jusqu'au premier clic.
            updateVolIcon();

            // 'seeked' couvre le deplacement sur une video en pause, que la
            // boucle rAF ne repeint plus. Pas de 'timeupdate' : il n'ajoute
            // aucune transition et continue de tourner a 4 Hz dans une fenetre
            // masquee, ou rAF est justement bride a zero.
            ['play', 'pause', 'ended', 'loadedmetadata', 'seeked']
                .forEach(ev => el.addEventListener(ev, updatePlayerUI));

            updatePlayerUI();
        }

        titleEl.textContent = file.name;
        // MEDIA_KIND et non file.category : la ligne disait « Video » quand le
        // player disait « Videos » pour le meme fichier.
        metaSpans(metaEl, [formatSize(file.size), MEDIA_KIND[file.media_type] || 'Video']);

        modal.classList.remove('hidden');
        // Toujours necessaire : la media query a 820px repasse le body en
        // overflow:auto, et la page defilait alors derriere le player ouvert.
        document.body.style.overflow = 'hidden';
    };

    function closePlayer() {
        const modal = PL.modal;
        const container = PL.container;

        if (seekAnimFrame) cancelAnimationFrame(seekAnimFrame);
        const media = getMedia();
        if (media) media.pause();

        container.textContent = '';
        _media = null;  // sinon la closure retient l'element detache et son tampon
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    }

    // Cablage en JS plutot que par des attributs onclick : ceux-ci
    // obligeaient a exposer quatre fonctions sur window et interdisaient une
    // CSP sans 'unsafe-inline'.
    document.getElementById('player-backdrop').addEventListener('click', closePlayer);
    const closeBtn = document.getElementById('player-close-btn');
    setBtnIcon(closeBtn, 'close', false);
    closeBtn.addEventListener('click', closePlayer);
    document.getElementById('player-play-btn').addEventListener('click', togglePlay);
    PL['vol-icon'].addEventListener('click', toggleMute);

    document.addEventListener('keydown', (e) => {
        // PL.modal : ce handler est sur document, il tournait donc a chaque
        // frappe dans le champ d'URL ou l'editeur de decoupe.
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
        // endCut ferme le flux : c'est lui, et lui seul, qui defait une coupe.
        currentCut = { id: cutId, es: es, btn: btn, bar: progressBarEl };
        es.onmessage = (e) => {
            const ev = JSON.parse(e.data);
            if (ev.status === 'progress') {
                fill.style.width = ev.percent + '%';
                statusEl.textContent = 'Decoupe en cours... ' + ev.percent + '%';
            } else if (ev.status === 'complete') {
                fill.style.width = '100%';
                statusEl.textContent = ev.message + ' (' + formatSize(ev.size) + ')';
                statusEl.className = 'trim-status success';
                endCut(1500);
                loadDownloadsList();
            } else if (ev.status === 'cancelled' || ev.status === 'error') {
                // 'cancelled' manquait : une coupe arretee laissait la barre a
                // l'ecran et « Couper » desactive jusqu'au rechargement.
                endCut();
                statusEl.textContent = ev.message;
                statusEl.className = 'trim-status error';
            }
        };
        es.onerror = () => {
            endCut();
            statusEl.textContent = 'Connexion perdue';
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

    // Un seul endroit ou l'invariant « editeur visible <=> depot masque » est
    // ecrit ; il l'etait a trois, dans les deux sens.
    function setCutView(showEditor) {
        cutEditor.classList.toggle('hidden', !showEditor);
        cutUploadZone.classList.toggle('hidden', showEditor);
    }

    function resetCutEditor() {
        // « Changer de fichier » n'est jamais desactive pendant une coupe : il
        // faut donc l'arreter pour de bon, pas seulement masquer l'editeur.
        if (currentCut) {
            postCancel(currentCut.id);
            endCut();
        }
        if (_cutRangeCleanup) _cutRangeCleanup();
        setCutView(false);
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

        // Le rectangle est mesure au debut du glissement, pas a chaque
        // deplacement : onMove ecrit quatre styles, donc relire la geometrie
        // ensuite forcait une mise en page synchrone a chaque evenement de
        // pointeur. La piste ne peut pas bouger pendant un glissement.
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
                followCutProgress(data.cut_id, statusEl, progressBar, btn);
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
