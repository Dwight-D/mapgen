// Drives the coarse analysis preview and the build panel of the spec editor (GDU-56) in headless Edge/Chrome with real mouse input.
// Usage: node analysis_drive.mjs <exe> <url> <spec> <orig> <shot dir>. Needs Node >= 22 (global WebSocket). No packages.
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
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  check('page loads', await waitFor(`document.querySelectorAll('.handle').length>100`, 20000));
  await waitFor(`S.imgVersion===S.st.version`);
  check('analysis layers listed, off by default', await ev(`!!document.getElementById('l-vis-an-territory') && !document.getElementById('l-vis-an-territory').checked && !document.getElementById('an-layer')`));

  // ---- Analyze: real click on the button
  const t0 = Date.now();
  await clickSel('#b-analyze');
  check('analysis ready for the current version', await waitFor(`S.an && S.an.version===S.st.version && S.anState==='ready'`, 20000));
  console.log('INFO click-to-ready ms: ' + (Date.now() - t0));
  check('labelled coarse preview with timing', await ev(`/COARSE PREVIEW/.test(document.getElementById('an-status').textContent) && /cell 20 m, \\d+ ms/.test(document.getElementById('an-status').textContent)`));
  check('territory image drawn (blob URL) and routes drawn', await waitFor(`!!document.getElementById('an-img') && /^blob:/.test(document.getElementById('an-img').getAttribute('href')) && document.querySelectorAll('.an-route').length>=3`, 5000));
  check('territory image decodes and carries the team patterns (dark ink and light cells)', await ev(`new Promise(function(res){var im=new Image();im.onload=function(){var c=document.createElement('canvas');c.width=im.width;c.height=im.height;var x=c.getContext('2d');x.drawImage(im,0,0);var d=x.getImageData(0,0,c.width,c.height).data,dark=0,light=0;for(var i=0;i<d.length;i+=4){if(d[i+3]>100){if(d[i]<60)dark++;else if(d[i]>200)light++;}}res(dark>200&&light>200&&im.width>=100);};im.onerror=function(){res(false);};im.src=document.getElementById('an-img').getAttribute('href');})`));
  check('pattern legend swatches (one per team + contested + unreachable)', await ev(`document.querySelectorAll('.anleg canvas').length===4`));
  check('table lists objectives with times, spread and flag', await ev(`document.querySelectorAll('#an-obj tr').length>=2 && /spread/.test(document.getElementById('an-obj').textContent)`));
  check('verdict with tolerance', await ev(`/tolerance 20%/.test(document.getElementById('an-verdict').textContent)`));
  console.log('INFO verdict: ' + (await ev(`document.getElementById('an-verdict').textContent`)));
  await shot('analysis_overlay.png');

  // ---- layer toggles with real clicks
  await clickSel('#l-vis-an-territory');
  check('territory toggled off, routes stay', await ev(`!document.getElementById('an-img') && document.querySelectorAll('.an-route').length>=3`));
  await shot('analysis_routes_only.png');
  await clickSel('#l-vis-an-territory');
  check('territory toggled back on', await ev(`!!document.getElementById('an-img')`));

  // ---- probe tool: real click on the map
  await clickSel('#t-probe');
  const mc = await ev(`(function(){var r=document.getElementById('map').getBoundingClientRect(),v=S.view,sz=S.st.size;var mx=400,my=400;return [r.left+r.width/2+(mx-v.cx)/v.mpp, r.top+r.height/2+((sz[1]-my)-v.cy)/v.mpp];})()`);
  await click(mc[0], mc[1]);
  check('probe marker and readout', await waitFor(`!!document.getElementById('an-probe-mark') && /probe at/.test(document.getElementById('an-probe').textContent)`, 5000));
  const pr = await ev(`document.getElementById('an-probe').textContent`);
  console.log('INFO probe: ' + pr);
  check('probe shows every team, spread and cost', /A \d+:\d\d/.test(pr) && /B \d+:\d\d/.test(pr) && /spread/.test(pr) && /cost/.test(pr));
  await shot('analysis_probe.png');
  await clickSel('#t-select');

  // ---- an edit marks the preview outdated, then it recomputes (latest wins)
  const v = await version();
  const c = await edge('pois.fa.at');
  await drag(c, 60, -40);
  check('edit commits', await waitVersion(v));
  check('preview follows the buffer (up to date for the new version)', await waitFor(`S.an && S.an.version===S.st.version && S.anState==='ready'`, 20000));
  check('probe marked outdated after the edit', await ev(`/outdated/.test(document.getElementById('an-probe').textContent)`));

  // ---- build: real click
  await ev(`document.getElementById('buildwrap').scrollIntoView()`);
  await clickSel('#b-build');
  check('build starts; the button is disabled while it runs', await waitFor(`S.build && S.build.job && S.build.job.status==='running' && document.getElementById('b-build').disabled`, 5000));
  check('stage list shows a running stage', await waitFor(`!!document.querySelector('#bd-stages li[data-state=running]')`, 5000));
  await shot('build_running.png');
  // editing stays possible during the build
  const v2 = await version();
  await ev(`document.getElementById('map').focus()`);
  await key('z', 'KeyZ', 90, 2);
  check('undo works while the build runs', await waitFor(`S.st.version>${v2}`, 5000));
  check('build completes with exit code 0', await waitFor(`S.build && S.build.job && S.build.job.status==='done' && !!document.getElementById('bd-result')`, 240000));
  check('exit code and stages shown', await ev(`/exit code 0/.test(document.getElementById('bd-head').textContent) && document.querySelectorAll('#bd-stages li[data-state=done]').length===6`));
  check('report warnings list and balance table in the panel', await ev(`!!document.getElementById('bd-obj') && /warning/.test(document.getElementById('bd-result').textContent)`));
  check('image buttons for map and overlays', await ev(`document.querySelectorAll('#bd-files button').length===7 && !!document.querySelector('#bd-files [data-file="map.png"]')`));
  await ev(`document.getElementById('buildwrap').scrollIntoView()`);
  await shot('build_done_panel.png');
  await clickSel('#bd-files [data-file="map.png"]');
  check('viewer shows map.png at full size', await waitFor(`(function(){var i=document.getElementById('v-img');return !!i && i.complete && i.naturalWidth>1000 && document.getElementById('viewer').style.display!=='none';})()`, 10000));
  await sleep(300);
  await shot('build_viewer_map.png');
  await clickSel('[data-vfile="overlay_territory.png"]');
  check('viewer switches to overlay_territory.png', await waitFor(`document.getElementById('v-title').textContent.indexOf('overlay_territory.png')===0 && document.getElementById('v-img').complete`, 10000));
  await sleep(300);
  await shot('build_viewer_territory.png');
  await key('Escape', 'Escape', 27);
  check('Esc closes the viewer', await ev(`document.getElementById('viewer').style.display==='none'`));
  check('page raised no JS exceptions', jsErrors.length === 0, jsErrors);
  ws.close();
}
main().then(() => {
  proc.kill();
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? 'FAILED ' + bad.length : 'ALL PASSED ' + results.length);
  process.exit(bad.length ? 1 : 0);
}).catch((e) => { console.error('ERROR', e); proc.kill(); process.exit(2); });
