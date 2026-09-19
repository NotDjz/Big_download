// Interface smoke test, driven through the Chrome DevTools Protocol.
//
// Zero dependencies: WebSocket has been native since Node 22, and Chrome
// exposes CDP with --remote-debugging-port. Launch it through
// tests/run_ui_smoke.py, which starts Flask and Chrome and then calls this.
//
// It covers what a server-side test cannot see: are the player's handlers
// wired, does the CSP block anything, does the rAF loop advance while playing
// and freeze when paused, and is a running cut really stopped when you change
// files.

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
      const timer = setTimeout(() => { pending.delete(i); rej(new Error(`${method}: no answer within 20 s`)); }, 20000);
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
    const timer = setTimeout(() => rej(new Error('CDP: could not connect within 15 s')), 15000);
    ws.addEventListener('open', () => { clearTimeout(timer); res(); });
    ws.addEventListener('error', () => { clearTimeout(timer); rej(new Error('CDP: WebSocket error')); });
    ws.addEventListener('close', () => { clearTimeout(timer); rej(new Error('CDP: connection closed')); });
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
  const check = (name, ok, detail) => results.push({ name, ok: Boolean(ok), detail });
  const until = async (expr, ms) => {
    const stop = Date.now() + ms;
    while (Date.now() < stop) {
      if ((await js(expr)).val) return true;
      await sleep(250);
    }
    return false;
  };
  const MEDIA = "document.querySelector('#player-container video, #player-container audio')";
  // The list is sorted newest first and may start with a photo, which opens no
  // media element. So aim for the first playable entry; the JSON order and the
  // DOM order match.
  const pickable = await js(
    "fetch('/list-downloads').then(r=>r.json()).then(f=>f.findIndex(x=>x.media_type!=='photo'))");
  // Number.isInteger and not 'idx < 0': when the in-page evaluation throws,
  // pickable.val is undefined, and 'undefined < 0' is false, so the guard was
  // bypassed and we clicked on [undefined].
  const idx = pickable.val;
  if (!Number.isInteger(idx) || idx < 0) {
    console.log('  [SKIP ] downloads/ holds photos only: the player needs a media file');
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
    console.log('  [SKIP ] no file in downloads/: the test needs a video or an audio file');
    ws.close();
    return null;  // sentinel: an empty array would exit 0, i.e. green
  }
  check('download list is populated', rows > 0, `${rows} rows`);

  await openPlayer();
  check('clicking a row opens the player',
    (await js("!document.getElementById('player-modal').classList.contains('hidden')")).val === true, '');
  check('media element created', (await js(`!!${MEDIA}`)).val === true, '');

  // The four handlers rewired in JS (there is no onclick attribute left).
  await js(`${MEDIA}.pause()`);
  await sleep(200);
  await js("document.getElementById('player-play-btn').click()");
  await sleep(700);
  const playing = (await js(`${MEDIA}.paused`)).val === false;
  check('PLAY button responds', playing, `paused = ${!playing}`);

  await js("document.getElementById('player-play-btn').click()");
  await sleep(500);
  const paused = (await js(`${MEDIA}.paused`)).val === true;
  // Conditioned on playback: without it, "paused" would be true by accident
  // and the assertion would pass even with the button unplugged.
  check('PAUSE button responds', playing && paused, playing ? `paused = ${paused}` : 'inconclusive (playback never started)');

  const mBefore = (await js(`${MEDIA}.muted`)).val;
  await js("document.getElementById('player-vol-icon').click()");
  await sleep(300);
  const mAfter = (await js(`${MEDIA}.muted`)).val;
  check('MUTE button responds', mAfter !== mBefore, `${mBefore} -> ${mAfter}`);

  await js("document.getElementById('player-close-btn').click()");
  await sleep(500);
  check('CLOSE button responds',
    (await js("document.getElementById('player-modal').classList.contains('hidden')")).val === true, '');

  await openPlayer();
  await js("document.getElementById('player-backdrop').click()");
  await sleep(500);
  check('clicking the BACKDROP closes',
    (await js("document.getElementById('player-modal').classList.contains('hidden')")).val === true, '');

  // The rAF loop only runs while playing: it must advance, then freeze.
  await openPlayer();
  await js(`${MEDIA}.currentTime=0; ${MEDIA}.play()`);
  await sleep(1400);
  const w1 = (await js("document.getElementById('player-seek-fill').style.width")).val;
  await sleep(1400);
  const w2 = (await js("document.getElementById('player-seek-fill').style.width")).val;
  check('progress bar advances while playing', w1 !== w2, `${w1} -> ${w2}`);

  // Count rAF callbacks rather than reading the clock: #player-time derives
  // from currentTime, which cannot advance while paused. Reading its text would
  // therefore pass even with the loop still running at 60 fps, which is exactly
  // the regression this test exists to catch.
  await js("window.__raf=0; if(!window.__rafPatched){const o=window.requestAnimationFrame;"
         + "window.requestAnimationFrame=function(cb){window.__raf++;return o.call(window,cb)};"
         + "window.__rafPatched=1;}");
  await sleep(1200);
  const rafPlaying = (await js('window.__raf')).val;
  check('the rAF loop runs while playing', rafPlaying > 10, `${rafPlaying} callbacks in 1.2 s`);

  await js(`${MEDIA}.pause()`);
  await sleep(400);
  await js('window.__raf=0');
  await sleep(1200);
  const rafPaused = (await js('window.__raf')).val;
  check('the rAF loop stops when paused', rafPaused < 3, `${rafPaused} callbacks in 1.2 s (expected ~0)`);

  await js(`${MEDIA}.muted=false`);
  await js("document.getElementById('player-backdrop').click()");

  // ---- Cancellation: "Change file" during a cut.
  //
  // This is the only one of the two flows drivable here. A download needs the
  // network, and an SSE forged through CDP ends with its body: EventSource
  // treats that ending as a dropped connection and fires onerror, hence
  // resetJob(), destroying the very state we want to observe before the first
  // assertion.
  //
  // What this test catches: endCut() lived inside followCutProgress for a long
  // time, out of reach of "Change file", which is never disabled during a cut.
  // So you changed files with a cut still running, Stop staying on screen and
  // pointing at the old job.
  await js("document.querySelector('.tab[data-tab=\"cut\"]').click()");
  await sleep(300);

  const uploaded = await js(`(async () => {
    const files = await (await fetch('/list-downloads')).json();
    // The largest video: the cut covers its whole duration, and it has to last
    // long enough for there to be time to click.
    const v = files.filter((x) => x.media_type === 'video').sort((a, b) => b.size - a.size)[0];
    if (!v) return null;
    const blob = await (await fetch('/stream/' + v.category + '/' + encodeURIComponent(v.name))).blob();
    const dt = new DataTransfer();
    dt.items.add(new File([blob], v.name, { type: 'video/mp4' }));
    const inp = document.getElementById('cut-file-input');
    inp.files = dt.files;
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    return v.name + ' - ' + Math.round(v.size / 1048576) + ' MB';
  })()`);

  if (!uploaded.val) {
    console.log('  [SKIP ] no video in the library to test cutting with');
    return null;
  }

  const ready = await until("!document.getElementById('cut-editor').classList.contains('hidden')", 60000);
  check('cut editor ready after upload', ready, uploaded.val);

  if (ready) {
    const before = (await js('fetch("/list-downloads").then(r=>r.json()).then(f=>f.length)')).val;
    await js("document.getElementById('cut-do-btn').click()");
    // Wait until the cut has really started: otherwise we would be testing the
    // "nothing to cancel" path, which passes on its own.
    const started = await until("!document.getElementById('cut-cancel-btn').classList.contains('hidden')", 20000);
    check('the cut starts and Stop appears', started, `button visible = ${started}`);

    // The gesture under test: change files while the cut is running.
    await js("document.getElementById('cut-reset-btn').click()");
    await sleep(1200);

    const cancelled = c.events.some(
      (e) => e.method === 'Network.requestWillBeSent'
        && e.params.request.method === 'POST'
        && e.params.request.url.includes('/cancel/'));
    check('changing files cancels the running cut', cancelled,
      `POST /cancel observed = ${cancelled}`);

    const btnHidden = (await js("document.getElementById('cut-cancel-btn').classList.contains('hidden')")).val;
    check('Stop disappears with the cut', btnHidden, `hidden = ${btnHidden}`);
    const cutEnabled = (await js("!document.getElementById('cut-do-btn').disabled")).val;
    check('Cut becomes usable again', cutEnabled, `enabled = ${cutEnabled}`);

    await sleep(1500);
    const after = (await js('fetch("/list-downloads").then(r=>r.json()).then(f=>f.length)')).val;
    check('no file left by the cancelled cut', after === before,
      `${before} before, ${after} after`);
  }

  // ---- The result panel, for a video and for a playlist.
  //
  // /get-info is intercepted: unlike an SSE stream, a JSON response is a
  // complete body, so Fetch.fulfillRequest serves it without the client seeing
  // a dropped connection. No network, no real video.
  //
  // What this test catches: the five format buttons were built by hand at five
  // sites, two of which forgot the inner <span>s and so rendered without the
  // weight the others have.
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

  const askInfo = async (payload) => {
    infoPayload = payload;
    await js(`(() => {
      const i = document.getElementById('url-input');
      i.value = 'https://www.youtube.com/watch?v=TESTTEST123';
      i.dispatchEvent(new Event('input', { bubbles: true }));
      document.getElementById('go-btn').click();
    })()`);
    return until("!document.getElementById('result-zone').classList.contains('hidden')", 15000);
  };

  const seen = await askInfo({
    title: 'A test video', uploader: 'Nobody', duration: 100,
    platform: 'youtube', available_qualities: [1080, 720], has_audio: true,
  });
  check('the result panel opens', seen, `visible = ${seen}`);

  if (seen) {
    const buttons = (await js(
      "JSON.stringify([...document.querySelectorAll('#result-formats .format-btn')]"
      + ".map(b => ({t: b.textContent, sel: b.classList.contains('selected'),"
      + " lab: !!b.querySelector('.format-label')})))")).val;
    const list = JSON.parse(buttons || '[]');
    check('three formats offered', list.length === 3,
      list.map((b) => b.t).join(' | '));
    // The point of the test: no button left without its .format-label.
    check('every format has its label', list.every((b) => b.lab),
      `${list.filter((b) => b.lab).length}/${list.length}`);
    check('the best quality is preselected',
      list.length > 0 && list[0].sel && !list.slice(1).some((b) => b.sel),
      list.map((b) => (b.sel ? '[' + b.t + ']' : b.t)).join(' '));
  }

  // The case with no qualities listed: THAT is the button that was built
  // without a <span>, and so without the weight the others have. With
  // qualities, all of them already had one and the assertion would discriminate
  // nothing.
  const seenBare = await askInfo({
    title: 'Video with no listed qualities', platform: 'tiktok', height: 720, has_audio: false,
  });
  check('the no-qualities panel opens', seenBare, `visible = ${seenBare}`);
  if (seenBare) {
    const bare = JSON.parse((await js(
      "JSON.stringify([...document.querySelectorAll('#result-formats .format-btn')]"
      + ".map(b => ({t: b.textContent, lab: !!b.querySelector('.format-label')})))")).val || '[]');
    check('format with no quality: label present', bare.length === 1 && bare[0].lab,
      bare.map((b) => `${b.t}(label=${b.lab})`).join(' '));
  }

  const seenPhoto = await askInfo({
    title: 'Un post photo', platform: 'instagram', _is_photo: true,
    has_audio: true, available_qualities: [],
  });
  check('the photo panel opens', seenPhoto, `visible = ${seenPhoto}`);
  if (seenPhoto) {
    const photo = JSON.parse((await js(
      "JSON.stringify([...document.querySelectorAll('#result-formats .format-btn')]"
      + ".map(b => ({t: b.textContent, sel: b.classList.contains('selected')})))")).val || '[]');
    const chosen = photo.find((b) => b.sel);
    check('the photo stays selected, not the MP3',
      !!chosen && chosen.t.startsWith('Photo'),
      photo.map((b) => (b.sel ? '[' + b.t + ']' : b.t)).join(' '));
  }

  const seenPl = await askInfo({
    is_playlist: true, title: 'My playlist', uploader: 'Nobody', video_count: 1593,
  });
  check('the playlist panel opens', seenPl, `visible = ${seenPl}`);

  if (seenPl) {
    const pl = JSON.parse((await js(
      "JSON.stringify({n: document.querySelectorAll('#result-formats .format-btn').length,"
      + " sel: !!document.querySelector('#result-formats .format-btn.selected'),"
      + " lab: !!document.querySelector('#result-formats .format-label'),"
      + " meta: document.getElementById('result-meta').textContent})")).val || '{}');
    check('a single format for a playlist', pl.n === 1, `${pl.n} button(s)`);
    check('it is selected and labelled', pl.sel && pl.lab, `sel=${pl.sel} label=${pl.lab}`);
    check('the total count is shown', (pl.meta || '').includes('1593'), pl.meta);
  }

  await c.send('Fetch.disable');

  const errs = c.events.filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
    .map((e) => e.params.entry.text);
  const excs = c.events.filter((e) => e.method === 'Runtime.exceptionThrown')
    .map((e) => e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text);
  check('no console error (CSP included)', errs.length === 0, `${errs.length} error(s)`);
  check('no JS exception', excs.length === 0, `${excs.length} exception(s)`);

  for (const r of results) console.log(`  [${r.ok ? 'OK  ' : 'FAIL '}] ${r.name.padEnd(42)} ${r.detail}`);
  for (const e of [...errs, ...excs]) console.log(`         ! ${e}`);
  ws.close();
  return results;
}

main()
  .then((results) => {
    // Exit 3 for a skip: exiting 0 would make a run where no assertion ever
    // ran look like a success.
    if (results === null) process.exit(3);
    process.exit(results.every((r) => r.ok) ? 0 : 1);
  })
  .catch((e) => { console.log(`  [FAIL ] harness: ${e}`); process.exit(2); });
