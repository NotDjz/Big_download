// Test de fumee de l'interface, pilote via le Chrome DevTools Protocol.
//
// Zero dependance : WebSocket est natif depuis Node 22, et Chrome expose CDP
// avec --remote-debugging-port. Lancer par tests/run_ui_smoke.py, qui demarre
// Flask et Chrome puis appelle ce script.
//
// Couvre ce qu'un test cote serveur ne peut pas voir : les handlers du player
// sont-ils cables, la CSP bloque-t-elle quelque chose, la boucle rAF avance-t-elle
// en lecture et se fige-t-elle en pause.

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
  const MEDIA = "document.querySelector('#player-container video, #player-container audio')";
  // La liste est triee du plus recent au plus ancien et peut commencer par une
  // photo, qui n'ouvre pas d'element media. On vise donc la premiere entree
  // lisible ; l'ordre du JSON et celui du DOM coincident.
  const pickable = await js(
    "fetch('/list-downloads').then(r=>r.json()).then(f=>f.findIndex(x=>x.media_type!=='photo'))");
  const idx = pickable.val;
  const openPlayer = async () => {
    await js(`document.querySelectorAll('.download-row')[${idx}].click()`);
    await sleep(1400);
  };

  await js("new Promise(r=>{const t=setInterval(()=>{if(document.querySelectorAll('.download-row').length){clearInterval(t);r(1)}},100);setTimeout(()=>{clearInterval(t);r(0)},8000)})");
  const rows = (await js("document.querySelectorAll('.download-row').length")).val;
  if (!rows) {
    console.log('  [SAUTE] aucun fichier dans downloads/ : le test a besoin d une video ou d un audio');
    ws.close();
    return { skipped: true, results: [] };
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
  .then((results) => process.exit(results.every((r) => r.ok) ? 0 : 1))
  .catch((e) => { console.log(`  [ECHEC] harnais : ${e}`); process.exit(2); });
