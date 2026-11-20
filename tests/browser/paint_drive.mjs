// Drives the editor's paint, erase and scatter-area tools in headless Edge/Chrome over the DevTools protocol with real mouse and key input.
// Usage: node paint_drive.mjs <browser exe> <editor url> <spec file the editor saves to> <original spec copy> <screenshot dir>
// Needs Node >= 22 (global WebSocket). No packages. Writes <screenshot dir>/painted_saved.yaml (the saved session).
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
  check('preview image arrives', await waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`));
  const original = fs.readFileSync(origFile, 'utf8');
  const px = (X, Y) => ev(`(function(){var r=document.getElementById('map').getBoundingClientRect(),v=S.view,h=S.st.size[1];return [r.left+r.width/2+(${X}-v.cx)/v.mpp, r.top+r.height/2+((h-${Y})-v.cy)/v.mpp];})()`);
  // a stroke along metre points with real mouse events; `hold` leaves the button down for a screenshot
  const stroke = async (pts, hold) => {
    const ps = [];
    for (const p of pts) ps.push(await px(p[0], p[1]));
    await mouse('mouseMoved', ps[0][0], ps[0][1], { buttons: 0 });
    await mouse('mousePressed', ps[0][0], ps[0][1]);
    for (let i = 1; i < ps.length; i++) {
      for (let k = 1; k <= 4; k++) await mouse('mouseMoved', ps[i - 1][0] + ((ps[i][0] - ps[i - 1][0]) * k) / 4, ps[i - 1][1] + ((ps[i][1] - ps[i - 1][1]) * k) / 4, { buttons: 1 });
    }
    if (hold) return ps;
    await mouse('mouseReleased', ps[ps.length - 1][0], ps[ps.length - 1][1]);
    return ps;
  };
  const setType = async (t) => { await ev(`(function(){var s=document.getElementById('tool-type');s.value=${JSON.stringify(t)};s.dispatchEvent(new Event('change'));})()`); };
  const feat = (id) => ev(`JSON.stringify(featOf(${JSON.stringify(id)}))`).then((s) => JSON.parse(s));
  const polys = (type) => ev(`JSON.stringify(S.st.features.filter(function(f){return f.shape==='poly'&&f.type===${JSON.stringify(type)};}).map(function(f){return f.id;}))`).then((s) => JSON.parse(s));
  const nverts = (id) => ev(`S.st.handles.filter(function(h){return h.feature===${JSON.stringify(id)}&&h.kind==='vertex';}).length`);
  const settle = async () => waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`);

  // ---- paint tool, radius keys
  await clickSel('#t-paint');
  check('Paint tool shows the brush controls', await ev(`document.getElementById('brush').style.display!=='none' && document.getElementById('tool-type').value==='forest'`));
  const r0 = await ev(`S.brush.r`);
  await key(']', 'BracketRight', 221);
  await key(']', 'BracketRight', 221);
  const r1 = await ev(`S.brush.r`);
  await key('[', 'BracketLeft', 219);
  const r2 = await ev(`S.brush.r`);
  check('[ and ] change the radius (' + r0 + ' -> ' + r1 + ' -> ' + r2 + ')', r1 > r0 && r2 < r1 && (await ev(`document.getElementById('brush-rv').textContent`)) === String(r2));
  await ev(`setRadius(16)`);

  // ---- stroke 1 (forest), held mid-stroke for the screenshot
  let v = await version();
  const s1 = [[110, 150], [190, 175], [270, 160], [340, 200]];
  await stroke(s1, true);
  await sleep(200);
  check('mid-stroke: translucent preview and brush cursor are drawn', await ev(`!!document.getElementById('stroke-preview') && !!document.getElementById('brush-cursor') && S.stroke && S.stroke.pts.length>4`));
  check('mid-stroke: nothing committed yet', (await version()) === v);
  await shot('paint_midstroke.png');
  const last = await px(340, 200);
  await mouse('mouseReleased', last[0], last[1]);
  check('mouse-up commits ONE edit', await waitVersion(v) && (await version()) === v + 1);
  const f1 = await polys('forest');
  check('a new forest poly region was added with a generated id', f1.length === 1 && /^forest_\d+$/.test(f1[0]), f1);
  const forestId = f1[0];
  const n1 = await nverts(forestId);
  console.log('INFO stroke 1 -> ' + forestId + ' with ' + n1 + ' vertices');
  check('vertex count is sane for a ~250 m stroke (<= 120)', n1 >= 8 && n1 <= 120, n1);
  check('the committed region appears in the vector overlay at once (before the image)', await waitFor(`S.geoVersion===S.st.version && S.geo.regions.some(function(r){return r.id===${JSON.stringify(forestId)};})`, 8000));
  check('the preview image refreshes', await settle());
  await shot('paint_after_stroke1.png');

  // ---- stroke 2 overlaps stroke 1: merge, same id
  v = await version();
  await stroke([[200, 120], [215, 200], [225, 260]]);
  check('overlapping stroke commits', await waitVersion(v));
  const f2 = await polys('forest');
  check('merged into the same region (still one forest poly, same id)', f2.length === 1 && f2[0] === forestId, f2);
  const n2 = await nverts(forestId);
  console.log('INFO after merge ' + forestId + ' has ' + n2 + ' vertices');
  check('the merged region has more vertices and the status says merged', n2 > n1 && /merged into/.test(await status()), await status());
  await settle();

  // ---- erase a middle piece: split
  await key('e', 'KeyE', 69);
  check('E selects the Erase tool', (await ev(`S.tool`)) === 'erase' && (await ev(`document.getElementById('tool-type').value`)) === 'any');
  await setType('forest');
  await ev(`setRadius(18)`);
  v = await version();
  await stroke([[230, 150], [232, 215]]);
  await mouse('mouseMoved', 5, 5, { buttons: 0 });
  check('erase commits', await waitVersion(v));
  const f3 = await polys('forest');
  check('the erase split the region in two (original id kept)', f3.length === 2 && f3.includes(forestId), f3);
  for (const i of f3) console.log('INFO   after erase ' + i + ' vertices ' + (await nverts(i)));
  await settle();
  await shot('paint_after_erase.png');

  // ---- mountain, impassable
  await clickSel('#t-paint');
  await setType('mountain');
  await clickSel('#brush-imp');
  check('impassable toggle is on', await ev(`S.brush.imp===true`));
  await ev(`setRadius(24)`);
  v = await version();
  await stroke([[480, 150], [540, 190], [600, 160]]);
  check('mountain stroke commits', await waitVersion(v));
  const mt = await polys('mountain');
  check('a mountain poly region with impassable: true', mt.length === 1 && (await feat(mt[0])).props.impassable === true, mt);
  await settle();

  // ---- click inside a painted region selects it (select tool); restyle and delete
  await key('v', 'KeyV', 86);
  const inside = await px(540, 190);
  await click(inside[0], inside[1]);
  check('click inside the painted mountain selects it', (await ev(`S.feat`)) === mt[0], await ev(`S.feat`));
  // a throwaway dab: select it with a click and delete it
  await clickSel('#t-paint');
  await setType('ruins');
  await clickSel('#brush-imp');
  await ev(`setRadius(14)`);
  v = await version();
  await stroke([[640, 450]]);
  check('single click paints a dab', await waitVersion(v));
  const ru = await polys('ruins');
  await key('v', 'KeyV', 86);
  await settle();
  const dab = await px(640, 450);
  await click(dab[0], dab[1]);
  check('click inside selects the dab', (await ev(`S.feat`)) === ru[0], [ru, await ev(`S.feat`)]);
  v = await version();
  await key('Delete', 'Delete', 46);
  check('Delete removes the painted region', await waitVersion(v) && (await polys('ruins')).length === 0);

  // ---- scatter area: new entry with a polygon, then drag one of its vertices
  await key('s', 'KeyS', 83);
  check('S selects the scatter-area tool with a type/target picker', (await ev(`S.tool`)) === 'scatter' && (await ev(`document.querySelectorAll('#tool-type option').length`)) > 5);
  await setType('new:marsh');
  v = await version();
  for (const p of [[60, 560], [260, 540], [280, 700], [90, 720]]) { const c = await px(p[0], p[1]); await click(c[0], c[1]); }
  check('four vertices are drafted', (await ev(`S.draft && S.draft.pts.length`)) === 4);
  await key('Enter', 'Enter', 13);
  check('Enter commits a new scatter entry', await waitVersion(v));
  const sc = await ev(`JSON.stringify(S.st.features.filter(function(f){return f.kind==='scatter'&&f.props.within&&f.type==='marsh';}).map(function(f){return f.id;}))`).then((s) => JSON.parse(s));
  check('the scatter entry carries within and the default coverage/scale/clump/avoid', sc.length === 1 && (await feat(sc[0])).props.coverage === 0.4 && (await feat(sc[0])).props.scale === 120 && (await feat(sc[0])).props.within.length === 4, sc);
  const wv = 'scatter.' + sc[0] + '.within[1]';
  await settle();
  const c = await hc(wv);
  const before = await handle(wv);
  v = await version();
  await drag(c, 30, -25);
  check('dragging a within vertex commits', await waitVersion(v));
  const after = await handle(wv);
  check('the vertex moved on integer metres', after.at[0] > before.at[0] && after.at[1] > before.at[1] && Number.isInteger(after.at[0]), [before.at, after.at]);
  await settle();
  await shot('paint_session.png');

  // ---- save, then undo everything back to the original bytes
  await clickSel('#b-save');
  await waitFor(`/saved/.test(document.getElementById('dirty').textContent)`, 8000);
  fs.copyFileSync(specFile, path.join(shotDir, 'painted_saved.yaml'));
  const saved = fs.readFileSync(specFile, 'utf8');
  check('the saved file contains the painted regions and the scatter entry', [forestId, mt[0], sc[0]].every((i) => saved.includes('id: ' + i)));
  check('painted regions are flow-style poly entries', saved.includes('id: ' + forestId + ', type: forest, shape: poly, points: [[') && saved.includes('type: mountain, shape: poly, points: [[') && saved.includes('impassable: true }'));
  let nUndo = 0;
  while (await ev(`!document.getElementById('b-undo').disabled`) && nUndo++ < 50) { await clickSel('#b-undo'); await sleep(80); }
  check('undo steps: one per stroke/commit (' + nUndo + ')', nUndo >= 7 && nUndo <= 10 && (await ev(`S.st.can_undo`)) === false, nUndo);
  await clickSel('#b-save');
  await waitFor(`/saved/.test(document.getElementById('dirty').textContent)`, 8000);
  check('after undoing everything, Save writes the original bytes back', fs.readFileSync(specFile, 'utf8') === original);
  check('page raised no JS exceptions', jsErrors.length === 0, jsErrors);
  ws.close();
}
main().then(() => {
  proc.kill();
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? 'FAILED ' + bad.length : 'ALL PASSED ' + results.length);
  process.exit(bad.length ? 1 : 0);
}).catch((e) => { console.error('ERROR', e); proc.kill(); process.exit(2); });
