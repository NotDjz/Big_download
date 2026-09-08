// Test de fumee de l'interface, pilote via le Chrome DevTools Protocol.
//
// Zero dependance : WebSocket est natif depuis Node 22, et Chrome expose CDP
// avec --remote-debugging-port. Lancer par tests/run_ui_smoke.py, qui demarre
// Flask et Chrome puis appelle ce script.
//
// Couvre ce qu'un test cote serveur ne peut pas voir : les handlers du player
// sont-ils cables, la CSP bloque-t-elle quelque chose, la boucle rAF avance-t-elle
// en lecture et se fige-t-elle en pause, et une coupe en cours est-elle bien
// arretee quand on change de fichier.

const PORT = Number(process.env.CDP_PORT || 9223);
const APP = process.env.APP_URL || 'http://localhost:5555';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rpc(ws) {
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method) events.push(m);
  });
  return {
    events,
    send: (method, params = {}) => new Promise((res, rej) => {
      const i = ++id;
      const timer = setTimeout(() => { pending.delete(i); rej(new Error(`${method}: pas de reponse en 20 s`)); }, 20000);
      pending.set(i, (m) => {
        clearTimeout(timer);
        return m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result);
      });
      ws.send(JSON.stringify({ id: i, method, params }));
    }),
  };
}

async function main() {
  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(APP)}`, { method: 'PUT' })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    const timer = setTimeout(() => rej(new Error('CDP : connexion impossible en 15 s')), 15000);
    ws.addEventListener('open', () => { clearTimeout(timer); res(); });
    ws.addEventListener('error', () => { clearTimeout(timer); rej(new Error('CDP : erreur WebSocket')); });
    ws.addEventListener('close', () => { clearTimeout(timer); rej(new Error('CDP : connexion fermee')); });
  });
  const c = rpc(ws);

  await c.send('Network.enable');
  await c.send('Network.setCacheDisabled', { cacheDisabled: true });
  await c.send('Runtime.enable');
  await c.send('Log.enable');
  await c.send('Page.enable');
  await c.send('Page.navigate', { url: APP });
  await sleep(2500);

  const js = async (expr) => {
    const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    return r.exceptionDetails ? { err: r.exceptionDetails.exception?.description } : { val: r.result.value };
  };

  const results = [];
  const check = (nom, ok, detail) => results.push({ nom, ok: Boolean(ok), detail });
  const until = async (expr, ms) => {
    const stop = Date.now() + ms;
    while (Date.now() < stop) {
      if ((await js(expr)).val) return true;
      await sleep(250);
    }
    return false;
  };
  const MEDIA = "document.querySelector('#player-container video, #player-container audio')";
  // La liste est triee du plus recent au plus ancien et peut commencer par une
  // photo, qui n'ouvre pas d'element media. On vise donc la premiere entree
  // lisible ; l'ordre du JSON et celui du DOM coincident.
  const pickable = await js(
    "fetch('/list-downloads').then(r=>r.json()).then(f=>f.findIndex(x=>x.media_type!=='photo'))");
  // Number.isInteger et non 'idx < 0' : quand l'evaluation dans la page
  // leve, pickable.val vaut undefined, et 'undefined < 0' est faux — la garde
  // etait donc contournee et on cliquait sur [undefined].
  const idx = pickable.val;
  if (!Number.isInteger(idx) || idx < 0) {
    console.log('  [SAUTE] downloads/ ne contient que des photos : le player a besoin d un media');
    ws.close();
    return null;
  }
  const openPlayer = async () => {
    await js(`document.querySelectorAll('.download-row')[${idx}].click()`);
    await sleep(1400);
  };

  await js("new Promise(r=>{const t=setInterval(()=>{if(document.querySelectorAll('.download-row').length){clearInterval(t);r(1)}},100);setTimeout(()=>{clearInterval(t);r(0)},8000)})");
  const rows = (await js("document.querySelectorAll('.download-row').length")).val;
  if (!rows) {
    console.log('  [SAUTE] aucun fichier dans downloads/ : le test a besoin d une video ou d un audio');
    ws.close();
    return null;  // sentinelle : un tableau vide sortirait en 0, donc vert
  }
  check('liste des telechargements remplie', rows > 0, `${rows} lignes`);

  await openPlayer();
  check('le player s ouvre au clic sur une ligne',
    (await js("!document.getElementById('player-modal').classList.contains('hidden')")).val === true, '');
  check('element media cree', (await js(`!!${MEDIA}`)).val === true, '');

  // Les quatre handlers recables en JS (il n y a plus d attribut onclick).
  await js(`${MEDIA}.pause()`);
  await sleep(200);
  await js("document.getElementById('player-play-btn').click()");
  await sleep(700);
  const playing = (await js(`${MEDIA}.paused`)).val === false;
  check('bouton LECTURE repond', playing, `paused = ${!playing}`);

  await js("document.getElementById('player-play-btn').click()");
  await sleep(500);
  const paused = (await js(`${MEDIA}.paused`)).val === true;
  // Conditionne a la lecture : sans elle, "en pause" serait vrai par accident
  // et l assertion passerait alors meme que le bouton est debranche.
  check('bouton PAUSE repond', playing && paused, playing ? `paused = ${paused}` : 'non concluant (lecture jamais partie)');

  const mBefore = (await js(`${MEDIA}.muted`)).val;
  await js("document.getElementById('player-vol-icon').click()");
  await sleep(300);
  const mAfter = (await js(`${MEDIA}.muted`)).val;
  check('bouton MUET repond', mAfter !== mBefore, `${mBefore} -> ${mAfter}`);

  await js("document.getElementById('player-close-btn').click()");
  await sleep(500);
  check('bouton FERMER repond',
    (await js("document.getElementById('player-modal').classList.contains('hidden')")).val === true, '');

  await openPlayer();
  await js("document.getElementById('player-backdrop').click()");
  await sleep(500);
  check('clic sur le FOND ferme',
    (await js("document.getElementById('player-modal').classList.contains('hidden')")).val === true, '');

  // La boucle rAF ne tourne qu en lecture : elle doit avancer, puis se figer.
  await openPlayer();
  await js(`${MEDIA}.currentTime=0; ${MEDIA}.play()`);
  await sleep(1400);
  const w1 = (await js("document.getElementById('player-seek-fill').style.width")).val;
  await sleep(1400);
  const w2 = (await js("document.getElementById('player-seek-fill').style.width")).val;
  check('barre de progression avance en lecture', w1 !== w2, `${w1} -> ${w2}`);

  // Compter les rappels rAF, pas lire l'horloge : #player-time derive de
  // currentTime, qui ne peut pas avancer en pause. Lire son texte passerait
  // donc meme si la boucle tournait toujours a 60 fps - soit exactement la
  // regression que ce test doit attraper.
  await js("window.__raf=0; if(!window.__rafPatched){const o=window.requestAnimationFrame;"
         + "window.requestAnimationFrame=function(cb){window.__raf++;return o.call(window,cb)};"
         + "window.__rafPatched=1;}");
  await sleep(1200);
  const rafPlaying = (await js('window.__raf')).val;
  check('la boucle rAF tourne en lecture', rafPlaying > 10, `${rafPlaying} rappels en 1,2 s`);

  await js(`${MEDIA}.pause()`);
  await sleep(400);
  await js('window.__raf=0');
  await sleep(1200);
  const rafPaused = (await js('window.__raf')).val;
  check('la boucle rAF s arrete en pause', rafPaused < 3, `${rafPaused} rappels en 1,2 s (attendu ~0)`);

  await js(`${MEDIA}.muted=false`);
  await js("document.getElementById('player-backdrop').click()");

  // ---- Annulation : « Changer de fichier » pendant une decoupe.
  //
  // C'est le seul des deux flux pilotable ici. Un telechargement demande le
  // reseau, et un SSE fabrique par CDP se termine avec son corps : EventSource
  // traite cette fin comme une coupure et declenche onerror, donc resetJob() --
  // l'etat qu'on veut observer serait detruit avant la premiere assertion.
  //
  // Ce que ce test attrape : endCut() a longtemps vecu dans followCutProgress,
  // hors de portee de « Changer de fichier », qui n'est jamais desactive
  // pendant une coupe. On changeait donc de fichier avec une coupe toujours en
  // cours, « Arreter » restant visible et pointe sur l'ancien job.
  await js("document.querySelector('.tab[data-tab=\"cut\"]').click()");
  await sleep(300);

  const uploaded = await js(`(async () => {
    const files = await (await fetch('/list-downloads')).json();
    // La plus grosse video : la coupe porte sur toute la duree, il faut qu'elle
    // dure assez pour qu'on ait le temps de cliquer.
    const v = files.filter((x) => x.media_type === 'video').sort((a, b) => b.size - a.size)[0];
    if (!v) return null;
    const blob = await (await fetch('/stream/' + v.category + '/' + encodeURIComponent(v.name))).blob();
    const dt = new DataTransfer();
    dt.items.add(new File([blob], v.name, { type: 'video/mp4' }));
    const inp = document.getElementById('cut-file-input');
    inp.files = dt.files;
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    return v.name + ' - ' + Math.round(v.size / 1048576) + ' Mo';
  })()`);

  if (!uploaded.val) {
    console.log('  [SAUT ] aucune video dans la bibliotheque pour tester la decoupe');
    return null;
  }

  const pret = await until("!document.getElementById('cut-editor').classList.contains('hidden')", 60000);
  check('editeur de decoupe pret apres upload', pret, uploaded.val);

  if (pret) {
    const avant = (await js('fetch("/list-downloads").then(r=>r.json()).then(f=>f.length)')).val;
    await js("document.getElementById('cut-do-btn').click()");
    // Attendre que la coupe soit reellement lancee : sinon on testerait le
    // chemin « rien a annuler », qui passe tout seul.
    const lancee = await until("!document.getElementById('cut-cancel-btn').classList.contains('hidden')", 20000);
    check('la coupe demarre et « Arreter » apparait', lancee, `bouton visible = ${lancee}`);

    // Le geste teste : changer de fichier alors que la coupe tourne.
    await js("document.getElementById('cut-reset-btn').click()");
    await sleep(1200);

    const annule = c.events.some(
      (e) => e.method === 'Network.requestWillBeSent'
        && e.params.request.method === 'POST'
        && e.params.request.url.includes('/cancel/'));
    check('changer de fichier annule la coupe en cours', annule,
      `POST /cancel observe = ${annule}`);

    const btnCache = (await js("document.getElementById('cut-cancel-btn').classList.contains('hidden')")).val;
    check('« Arreter » disparait avec la coupe', btnCache, `cache = ${btnCache}`);
    const coupeDispo = (await js("!document.getElementById('cut-do-btn').disabled")).val;
    check('« Couper » redevient utilisable', coupeDispo, `actif = ${coupeDispo}`);

    await sleep(1500);
    const apres = (await js('fetch("/list-downloads").then(r=>r.json()).then(f=>f.length)')).val;
    check('aucun fichier laisse par la coupe annulee', apres === avant,
      `${avant} avant, ${apres} apres`);
  }

  // ---- Le panneau de resultat, video et playlist.
  //
  // /get-info est intercepte : contrairement au SSE, une reponse JSON est un
  // corps complet, donc Fetch.fulfillRequest la sert sans que le client y voie
  // une connexion coupee. Aucun reseau, aucune vraie video.
  //
  // Ce que ce test attrape : les cinq boutons de format etaient construits a la
  // main sur cinq sites, dont deux qui oubliaient les <span> internes et
  // s'affichaient donc sans le gras des autres.
  const b64 = (t) => Buffer.from(t, 'utf8').toString('base64');
  let infoPayload = null;
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.method !== 'Fetch.requestPaused') return;
    const { requestId, request } = m.params;
    if (request.url.includes('/get-info') && infoPayload) {
      c.send('Fetch.fulfillRequest', {
        requestId, responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: b64(JSON.stringify(infoPayload)),
      });
    } else {
      c.send('Fetch.continueRequest', { requestId });
    }
  });
  await c.send('Fetch.enable', { patterns: [{ urlPattern: '*/get-info', requestStage: 'Request' }] });

  await js("document.querySelector('.tab[data-tab=\"download\"]').click()");
  await sleep(200);

  const demandeInfo = async (payload) => {
    infoPayload = payload;
    await js(`(() => {
      const i = document.getElementById('url-input');
      i.value = 'https://www.youtube.com/watch?v=TESTTEST123';
      i.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('go-btn').click();
    })()`);
    return until("!document.getElementById('result-zone').classList.contains('hidden')", 15000);
  };

  const vu = await demandeInfo({
    title: 'Une video de test', uploader: 'Personne', duration: 100,
    platform: 'youtube', available_qualities: [1080, 720], has_audio: true,
  });
  check('le panneau de resultat s ouvre', vu, `visible = ${vu}`);

  if (vu) {
    const boutons = (await js(
      "JSON.stringify([...document.querySelectorAll('#result-formats .format-btn')]"
      + ".map(b => ({t: b.textContent, sel: b.classList.contains('selected'),"
      + " lab: !!b.querySelector('.format-label')})))")).val;
    const liste = JSON.parse(boutons || '[]');
    check('trois formats proposes', liste.length === 3,
      liste.map((b) => b.t).join(' | '));
    // Le point du test : plus aucun bouton sans son .format-label.
    check('chaque format a son libelle', liste.every((b) => b.lab),
      `${liste.filter((b) => b.lab).length}/${liste.length}`);
    check('la meilleure qualite est preselectionnee',
      liste.length > 0 && liste[0].sel && !liste.slice(1).some((b) => b.sel),
      liste.map((b) => (b.sel ? '[' + b.t + ']' : b.t)).join(' '));
  }

  // Le cas sans qualites listees : c'est CE bouton-la qui etait construit sans
  // <span>, donc sans le gras des autres. Avec des qualites, tous en avaient
  // deja un et l'assertion ne discriminerait rien.
  const vuNu = await demandeInfo({
    title: 'Video sans qualites', platform: 'tiktok', height: 720, has_audio: false,
  });
  check('le panneau sans qualites s ouvre', vuNu, `visible = ${vuNu}`);
  if (vuNu) {
    const nu = JSON.parse((await js(
      "JSON.stringify([...document.querySelectorAll('#result-formats .format-btn')]"
      + ".map(b => ({t: b.textContent, lab: !!b.querySelector('.format-label')})))")).val || '[]');
    check('format sans qualite : libelle present', nu.length === 1 && nu[0].lab,
      nu.map((b) => `${b.t}(label=${b.lab})`).join(' '));
  }

  const vuPhoto = await demandeInfo({
    title: 'Un post photo', platform: 'instagram', _is_photo: true,
    has_audio: true, available_qualities: [],
  });
  check('le panneau photo s ouvre', vuPhoto, `visible = ${vuPhoto}`);
  if (vuPhoto) {
    const ph = JSON.parse((await js(
      "JSON.stringify([...document.querySelectorAll('#result-formats .format-btn')]"
      + ".map(b => ({t: b.textContent, sel: b.classList.contains('selected')})))")).val || '[]');
    const choisi = ph.find((b) => b.sel);
    check('la photo reste selectionnee, pas le MP3',
      !!choisi && choisi.t.startsWith('Photo'),
      ph.map((b) => (b.sel ? '[' + b.t + ']' : b.t)).join(' '));
  }

  const vuPl = await demandeInfo({
    is_playlist: true, title: 'Ma playlist', uploader: 'Personne', video_count: 1593,
  });
  check('le panneau de playlist s ouvre', vuPl, `visible = ${vuPl}`);

  if (vuPl) {
    const pl = JSON.parse((await js(
      "JSON.stringify({n: document.querySelectorAll('#result-formats .format-btn').length,"
      + " sel: !!document.querySelector('#result-formats .format-btn.selected'),"
      + " lab: !!document.querySelector('#result-formats .format-label'),"
      + " meta: document.getElementById('result-meta').textContent})")).val || '{}');
    check('un seul format pour une playlist', pl.n === 1, `${pl.n} bouton(s)`);
    check('il est selectionne et libelle', pl.sel && pl.lab, `sel=${pl.sel} label=${pl.lab}`);
    check('le compte total est affiche', (pl.meta || '').includes('1593'), pl.meta);
  }

  await c.send('Fetch.disable');

  const errs = c.events.filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
    .map((e) => e.params.entry.text);
  const excs = c.events.filter((e) => e.method === 'Runtime.exceptionThrown')
    .map((e) => e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text);
  check('aucune erreur console (CSP incluse)', errs.length === 0, `${errs.length} erreur(s)`);
  check('aucune exception JS', excs.length === 0, `${excs.length} exception(s)`);

  for (const r of results) console.log(`  [${r.ok ? 'OK  ' : 'ECHEC'}] ${r.nom.padEnd(42)} ${r.detail}`);
  for (const e of [...errs, ...excs]) console.log(`         ! ${e}`);
  ws.close();
  return results;
}

main()
  .then((results) => {
    // Code 3 pour un saut : sortir en 0 ferait passer pour un succes une
    // execution ou aucune assertion n'a tourne.
    if (results === null) process.exit(3);
    process.exit(results.every((r) => r.ok) ? 0 : 1);
  })
  .catch((e) => { console.log(`  [ECHEC] harnais : ${e}`); process.exit(2); });
