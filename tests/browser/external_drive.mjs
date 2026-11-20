// Drives the external-change safety of the spec editor (GDU-55) in headless Edge/Chrome with real mouse input: drag a POI, change the
// file from outside, see the banner, Replay, Save; then a clean-buffer reload notice. Usage: node external_drive.mjs <exe> <url> <spec> <orig> <shot dir>
// Needs Node >= 22 (global WebSocket). No packages.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [exe, url, specFile, origFile, shotDir] = process.argv.slice(2);
const port = 9300 + Math.floor(Math.random() * 500);
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'edge-prof-'));
const proc = spawn(exe, ['--headless=new', '--disable-gpu', '--no-first-run', '--window-size=1500,950', '--remote-debugging-port=' + port,
  '--user-data-dir=' + prof, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function check(name, ok, extra) {
  results.push({ name, ok: !!ok });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (!ok && extra !== undefined ? ' ' + JSON.stringify(extra) : ''));
}
const lines = (t) => t.split('\n');
function changedLines(a, b) {
  const la = lines(a), lb = lines(b);
  if (la.length !== lb.length) return -1;
  let n = 0;
  for (let i = 0; i < la.length; i++) if (la[i] !== lb[i]) n++;
  return n;
}
function span(a, b) {
  let i = 0;
  while (i < Math.min(a.length, b.length) && a[i] === b[i]) i++;
  let j = 0;
  while (j < Math.min(a.length, b.length) - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++;
  return [a.slice(i, a.length - j), b.slice(i, b.length - j)];
}

async function main() {
  let targets = [];
  for (let i = 0; i < 60; i++) {
    try { targets = await (await fetch('http://127.0.0.1:' + port + '/json')).json(); if (targets.some((t) => t.type === 'page')) break; } catch (e) { /* retry */ }
    await sleep(250);
  }
  const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const pend = new Map();
  const jsErrors = [];
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } else if (d.method === 'Runtime.exceptionThrown') jsErrors.push(JSON.stringify(d.params.exceptionDetails).slice(0, 300)); };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails));
    return r.result.result.value;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.mkdirSync(shotDir, { recursive: true });
    fs.writeFileSync(path.join(shotDir, name), Buffer.from(r.result.data, 'base64'));
  };
  const mouse = (type, x, y, extra = {}) => send('Input.dispatchMouseEvent', Object.assign({ type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1 }, extra));
  const key = async (k, code, vk, modifiers = 0) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, windowsVirtualKeyCode: vk, modifiers });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, modifiers });
  };
  // centre of a handle's shape (not its label), client px
  const hc = (p) => ev(`(function(){var g=document.querySelector('.handle[data-key=${JSON.stringify(p).replace(/'/g, "\\'")}]');if(!g)return null;var s=g.querySelector('circle:not(.ring),rect,polygon');var r=s.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})()`);
  // a POI's disc edge (its centre is covered by the link triangles of the waypoints that reference it)
  const edge = async (p) => { const c = await hc(p); return [c[0] + 6.5, c[1]]; };
  const handle = (p) => ev(`JSON.stringify(S.st.handles.filter(function(h){return h.path===${JSON.stringify(p)};})[0]||null)`).then((s) => JSON.parse(s));
  const drag = async (from, dx, dy, extra = {}) => {
    await mouse('mouseMoved', from[0], from[1], { buttons: 0, ...extra });
    await mouse('mousePressed', from[0], from[1], extra);
    for (let i = 1; i <= 8; i++) await mouse('mouseMoved', from[0] + (dx * i) / 8, from[1] + (dy * i) / 8, { buttons: 1, ...extra });
    await mouse('mouseReleased', from[0] + dx, from[1] + dy, extra);
  };
  const click = async (x, y, extra = {}) => {
    await mouse('mouseMoved', x, y, { buttons: 0 });
    await mouse('mousePressed', x, y, extra);
    await mouse('mouseReleased', x, y, extra);
    await sleep(150);
  };
  const clickSel = async (sel) => { const c = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});var r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})()`); await click(c[0], c[1]); };
  const waitFor = async (expr, ms = 60000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(100); } return false; };
  const version = () => ev('S.st.version');
  const waitVersion = async (v) => waitFor(`S.st.version>${v}`, 15000);
  const status = () => ev(`document.getElementById('status').textContent`);
  const dirty = () => ev(`document.getElementById('dirty').textContent`);
  const typeInto = async (sel, text) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});e.focus();e.select();})()`);
    await send('Input.insertText', { text });
    await key('Tab', 'Tab', 9);
    await sleep(400);
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  check('page loads', await waitFor(`document.querySelectorAll('.handle').length>100`, 20000));
  await waitFor(`S.imgVersion===S.st.version`);
  const original = fs.readFileSync(origFile, 'utf8');
  const vis = (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});return !!e && e.style.display!=='none' && e.getBoundingClientRect().height>0;})()`);
  check('no banner at start', !(await vis('#ext')) && !(await vis('#info')));

  // ---- drag a POI with the real mouse, then change the file from outside
  const h0 = await handle('pois.fa.at');
  const c = await edge('pois.fa.at');
  const v = await version();
  await drag(c, 42, -31);
  check('POI drag commits', await waitVersion(v));
  const h1 = await handle('pois.fa.at');
  check('POI moved', h1.at[0] !== h0.at[0] && h1.at[1] !== h0.at[1], { h0: h0.at, h1: h1.at });
  fs.writeFileSync(specFile, original.replace('label: Attic }', 'label: Attic2 }'));
  check('banner appears (the page polls the server watcher)', await waitFor(`document.getElementById('ext').style.display!=='none' && !!document.getElementById('ext-replay')`, 15000));
  const msg = await ev(`document.getElementById('ext').textContent`);
  check('banner names the change, the unsaved op count and offers the three actions + Cancel', /File changed on disk/.test(msg) && /Replay my edits/.test(msg) && /Discard my edits/.test(msg) && /Save as copy/.test(msg) && /Cancel/.test(msg) && /1 unsaved edit op/.test(msg), msg);
  await shot('external_banner.png');
  await clickSel('#b-save');
  await sleep(500);
  check('Save is refused while the change is unresolved', /refused/.test(await status()) && /Attic2/.test(fs.readFileSync(specFile, 'utf8')) && !/Attic }/.test(fs.readFileSync(specFile, 'utf8')), await status());
  await clickSel('#ext-cancel');
  check('Cancel collapses the banner to a persistent strip', await ev(`document.getElementById('ext').className==='compact' && !!document.getElementById('ext-open') && !document.getElementById('ext-replay')`));
  await shot('external_cancelled.png');
  await clickSel('#ext-open');
  check('Resolve... reopens it', await ev(`!!document.getElementById('ext-replay')`));

  // ---- Replay
  const v2 = await version();
  await clickSel('#ext-replay');
  check('Replay commits', await waitVersion(v2));
  check('banner gone, replay result shown', !(await vis('#ext')) && /Replayed 1 op/.test(await ev(`document.getElementById('info').textContent`)));
  const h3 = await handle('pois.fa.at');
  check('buffer keeps my moved POI', JSON.stringify(h3.at) === JSON.stringify(h1.at), { h3: h3.at, h1: h1.at });
  check('dirty and undoable', /UNSAVED/.test(await dirty()) && (await ev(`!document.getElementById('b-undo').disabled`)));
  await waitFor(`S.imgVersion===S.st.version`);
  await shot('external_replayed.png');
  await clickSel('#b-save');
  await sleep(600);
  const saved = fs.readFileSync(specFile, 'utf8');
  check('Save wrote disk change + my edit', /Attic2/.test(saved) && !/\[395, 265\]/.test(saved) && changedLines(original, saved) === 2, changedLines(original, saved));
  check('saved marker', /saved/.test(await dirty()) && !(await vis('#ext')));

  // ---- clean buffer: automatic reload notice
  const v3 = await version();
  fs.writeFileSync(specFile, saved.replace('label: Attic2 }', 'label: Attic3 }'));
  check('clean buffer reloads', await waitVersion(v3));
  check('reload notice shown', await waitFor(`/reloaded/.test(document.getElementById('info').textContent)`, 5000) && !(await vis('#ext')));
  await waitFor(`S.imgVersion===S.st.version`);
  await shot('external_reloaded.png');
  check('page raised no JS exceptions', jsErrors.length === 0, jsErrors);
  ws.close();
}
main().then(() => {
  proc.kill();
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? 'FAILED ' + bad.length : 'ALL PASSED ' + results.length);
  process.exit(bad.length ? 1 : 0);
}).catch((e) => { console.error('ERROR', e); proc.kill(); process.exit(2); });
