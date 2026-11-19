// Drives the spec editor (mapgen spec-edit) in headless Edge/Chrome over the DevTools protocol with real mouse and key input events.
// Usage: node spec_editor_drive.mjs <browser exe> <editor url> <spec file the editor saves to> <original spec copy> <screenshot dir>
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
  check('page loads with handles drawn', await waitFor(`document.querySelectorAll('.handle').length>100`, 20000));
  check('preview image arrives (blob URL into the SVG image)', await waitFor(`S.imgVersion===S.st.version && /^blob:/.test(document.getElementById('bg').getAttribute('href')||'')`));
  check('outlines and centrelines drawn from the geometry API', await ev(`S.geo && S.geo.lines.length>10 && S.geo.regions.length>10 && document.querySelectorAll('#ov path').length>20`));
  check('starts clean', /saved/.test(await dirty()) && (await ev(`document.getElementById('b-save').disabled`)));
  check('all handle kinds present as distinct shapes', await ev(`['poi','base','point','waypoint','center'].every(function(k){return document.querySelector('.handle.k-'+k);}) && !!document.querySelector('.handle.k-waypoint polygon') && !!document.querySelector('.handle.k-waypoint circle:not(.ring)')`));
  await shot('spec_editor_initial.png');
  const original = fs.readFileSync(origFile, 'utf8');
  check('file on disk equals the original', fs.readFileSync(specFile, 'utf8') === original);

  // ---- drag a POI with real mouse input
  let h0 = await handle('pois.fa.at');
  let c = await edge('pois.fa.at');
  let v = await version();
  const t0 = Date.now();
  await drag(c, 42, -31);
  check('POI drag commits to the server', await waitVersion(v));
  const commitMs = Date.now() - t0;
  console.log('INFO drag-and-commit round trip (8 mouse moves incl.) ms: ' + commitMs);
  let h1 = await handle('pois.fa.at');
  check('POI moved on integer coordinates', h1.at[0] !== h0.at[0] && h1.at[1] !== h0.at[1] && Number.isInteger(h1.at[0]) && Number.isInteger(h1.at[1]), { h0: h0.at, h1: h1.at });
  check('moved to roughly where the mouse went (north is up)', Math.abs(h1.at[0] - h0.at[0]) > 20 && h1.at[1] > h0.at[1], { h0: h0.at, h1: h1.at });
  check('dirty marker shows unsaved changes', /UNSAVED/.test(await dirty()) && !(await ev(`document.getElementById('b-save').disabled`)));
  check('the file is untouched before Save', fs.readFileSync(specFile, 'utf8') === original);
  await waitFor(`S.imgVersion===S.st.version`);

  // ---- undo / redo via buttons and keyboard
  await clickSel('#b-undo');
  await sleep(300);
  let h2 = await handle('pois.fa.at');
  check('Undo button restores the POI', JSON.stringify(h2.at) === JSON.stringify(h0.at) && /saved/.test(await dirty()), h2);
  await key('y', 'KeyY', 89, 2);
  await sleep(400);
  check('Ctrl+Y redoes the move', JSON.stringify((await handle('pois.fa.at')).at) === JSON.stringify(h1.at));
  await key('z', 'KeyZ', 90, 2);
  await sleep(400);
  check('Ctrl+Z undoes it', JSON.stringify((await handle('pois.fa.at')).at) === JSON.stringify(h0.at));
  await clickSel('#b-redo');
  await sleep(300);

  // ---- save: exactly the edited span changes
  await clickSel('#b-save');
  check('Save reports success and clears the dirty marker', await waitFor(`/Saved /.test(document.getElementById('status').textContent) && /saved/.test(document.getElementById('dirty').textContent)`, 5000), await status());
  let saved = fs.readFileSync(specFile, 'utf8');
  const [o1, n1] = span(original, saved);
  check('Save wrote exactly one changed line', changedLines(original, saved) === 1);
  check('the changed span is just the coordinates', o1 === '395, 265' && n1 === h1.at[0] + ', ' + h1.at[1], { o1, n1 });

  // ---- plain drag of a link triangle moves the referenced definition (every referrer follows)
  const bo0 = await handle('points.bo');
  c = await hc('lines.road_bo_o.path[0]');
  v = await version();
  await drag(c, -28, 20);
  check('reference waypoint drag commits', await waitVersion(v));
  const bo1 = await handle('points.bo');
  const refs = await ev(`JSON.stringify(S.st.handles.filter(function(h){return h.ref==='bo';}).map(function(h){return h.at;}))`);
  check('the definition (points.bo) moved', bo1.at[0] !== bo0.at[0] && bo1.at[1] !== bo0.at[1], { bo0: bo0.at, bo1: bo1.at });
  check('every referrer follows', JSON.parse(refs).length === 2 && JSON.parse(refs).every((p) => p[0] === bo1.at[0] && p[1] === bo1.at[1]), refs);
  await waitFor(`S.imgVersion===S.st.version`);
  await shot('spec_editor_ref_drag.png');

  // ---- Alt-drag detaches the waypoint to a literal; the definition stays
  const ao0 = await handle('points.ao');
  await ev(`select('lines.road_ao_o.path[0]')`);   // a selected handle in a stack is the one that moves
  c = await hc('lines.road_ao_o.path[0]');
  v = await version();
  await drag(c, 30, 26, { modifiers: 1 });
  check('Alt-drag commits', await waitVersion(v));
  const wp = await handle('lines.road_ao_o.path[0]');
  const ao1 = await handle('points.ao');
  check('waypoint became a literal at the dropped position', wp.ref === null && (wp.at[0] !== ao0.at[0]) && Number.isInteger(wp.at[0]), wp);
  check('points.ao unchanged and still referenced by the other line', JSON.stringify(ao1.at) === JSON.stringify(ao0.at) && (await handle('lines.road_a_ao.path[2]')).ref === 'ao');
  await clickSel('#b-save');
  await waitFor(`/Saved /.test(document.getElementById('status').textContent) && /saved/.test(document.getElementById('dirty').textContent)`, 5000);
  saved = fs.readFileSync(specFile, 'utf8');
  check('saved file: the points.bo line and the road_ao_o line changed besides the POI line', changedLines(original, saved) === 3, changedLines(original, saved));
  check('the literal waypoint is written as [x, y] in the road line', saved.includes('path: [[' + wp.at[0] + ', ' + wp.at[1] + '], [330, 398], o]'));
  check('the saved file still loads (the editor validates every edit)', (await ev(`S.errors.length`)) === 0);

  // ---- double-click a segment inserts a waypoint; Delete removes it
  const a = await hc('lines.river_main.path[3]'), b = await hc('lines.river_main.path[4]');
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const n0 = await ev(`S.st.handles.filter(function(h){return h.line==='river_main';}).length`);
  v = await version();
  await mouse('mouseMoved', mid[0], mid[1], { buttons: 0 });
  await mouse('mousePressed', mid[0], mid[1]);
  await mouse('mouseReleased', mid[0], mid[1]);
  await mouse('mousePressed', mid[0], mid[1], { clickCount: 2 });
  await mouse('mouseReleased', mid[0], mid[1], { clickCount: 2 });
  check('double-click on a segment inserts a waypoint', await waitVersion(v) && (await ev(`S.st.handles.filter(function(h){return h.line==='river_main';}).length`)) === n0 + 1);
  check('the new waypoint is selected and literal', (await ev(`S.sel`)) === 'lines.river_main.path[4]' && (await handle('lines.river_main.path[4]')).ref === null);
  v = await version();
  await key('Delete', 'Delete', 46);
  check('Delete key removes the selected waypoint', await waitVersion(v) && (await ev(`S.st.handles.filter(function(h){return h.line==='river_main';}).length`)) === n0);

  // ---- keyboard: select a POI, nudge with arrow keys
  c = await edge('pois.g.at');
  await click(c[0], c[1]);
  check('click selects and fills the panel', (await ev(`S.sel`)) === 'pois.g.at' && /poi g/.test(await ev(`document.querySelector('#panel h2').textContent`)));
  const g0 = await handle('pois.g.at');
  v = await version();
  await key('ArrowRight', 'ArrowRight', 39);
  check('ArrowRight nudges by 1 m', await waitVersion(v) && (await handle('pois.g.at')).at[0] === g0.at[0] + 1);

  // ---- invalid edit through the panel is refused with the loader error list
  v = await version();
  await typeInto('#in-x', '-50');
  await sleep(400);
  const eh = await ev(`document.getElementById('errhead').textContent`);
  check('out-of-bounds edit refused, error listed, buffer version unchanged', /refused/.test(eh) && (await ev(`document.querySelectorAll('#errors li').length`)) >= 1 && (await version()) === v, { eh, v });
  check('the handle did not move', (await handle('pois.g.at')).at[0] === g0.at[0] + 1);

  // ---- slice 3: stacked handles, shape tools, delete with referrers, type change, rename, layers, error highlight, undo to the original
  const m2c = (x, y) => ev(`(function(){var r=document.getElementById('map').getBoundingClientRect(),v=S.view,sz=S.st.size;return [r.left+r.width/2+(${x}-v.cx)/v.mpp, r.top+r.height/2+(sz[1]-${y}-v.cy)/v.mpp];})()`);
  const features = () => ev(`JSON.stringify(S.st.features.map(function(f){return [f.id,f.kind,f.type,f.shape];}))`).then((s) => JSON.parse(s));
  const featureIds = async (kind) => (await features()).filter((f) => f[1] === kind).map((f) => f[0]);
  const props = (id) => ev(`JSON.stringify(S.st.features.filter(function(f){return f.id===${JSON.stringify(id)};})[0].props)`).then((s) => JSON.parse(s));
  const nHandles = (kind, feat) => ev(`S.st.handles.filter(function(h){return h.kind===${JSON.stringify(kind)}&&h.feature===${JSON.stringify(feat)};}).length`);
  const setValue = (sel, val) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});e.value=${JSON.stringify(val)};e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const pickTool = async (tool, type) => { await clickSel('#t-' + tool); if (type) await setValue('#tool-type', type); };
  const placed = async (v0) => { const ok = await waitVersion(v0); await sleep(150); return ok; };
  const saveAndWait = async () => { await clickSel('#b-save'); await waitFor(`/saved/.test(document.getElementById('dirty').textContent)`, 5000); await sleep(200); };
  await clickSel('#b-fit');
  await key('Escape', 'Escape', 27);
  // click cycling: POI o has the link triangles of four roads and the town square centre under it
  c = await hc('pois.o.at');
  await click(c[0], c[1]);
  const first = await ev(`S.sel`);
  await click(c[0], c[1]);
  const second = await ev(`S.sel`);
  check('first click on a stack selects the topmost handle (the POI)', first === 'pois.o.at', first);
  check('clicking the selected handle again cycles to the next one underneath', second !== first && !!(await handle(second)), second);
  await key('Escape', 'Escape', 27);

  // add a blob region by click
  const before = await featureIds('region');
  v = await version();
  await pickTool('blob', 'forest');
  let p = await m2c(640, 640);
  await click(p[0], p[1]);
  check('blob tool: a click adds a forest region', await placed(v));
  const blobId = await ev(`S.feat`);
  check('the new region has a generated id forest_<n> and is selected', /^forest_\d+$/.test(blobId) && !before.includes(blobId), blobId);
  check('tool returns to Select after placing', (await ev(`S.tool`)) === 'select');
  const rh0 = await handle('regions.' + blobId + '.r');
  check('the blob has a radius grip', !!rh0 && rh0.kind === 'radius');
  // drag the radius grip outwards
  c = await hc('regions.' + blobId + '.r');
  v = await version();
  await drag(c, 30, 0);
  check('dragging the radius grip commits a new r', await waitVersion(v));
  const rh1 = await handle('regions.' + blobId + '.r');
  const bp = await props(blobId);
  check('r grew by the drag distance in metres (integer)', rh1.at[0] - rh1.center[0] > rh0.at[0] - rh0.center[0] + 5 && Number.isInteger(bp.r), bp);
  // rough via the slider/number
  await setValue('#p-rough', '0.6');
  await sleep(500);
  check('rough edited in the panel', (await props(blobId)).rough === 0.6);
  // change the region type
  await setValue('#in-type', 'marsh');
  await sleep(500);
  check('type change through the panel (type table names listed)', (await features()).some((f) => f[0] === blobId && f[2] === 'marsh') &&
    (await ev(`Array.prototype.map.call(document.querySelectorAll('#in-type option'),function(o){return o.value;}).join(',')`)).includes('forest,marsh'));
  // add a river with four clicks, finished with Enter
  const lines0 = await featureIds('line');
  v = await version();
  await pickTool('line', 'river');
  for (const [x, y] of [[560, 560], [600, 500], [660, 450], [700, 380]]) { const q = await m2c(x, y); await click(q[0], q[1]); }
  check('line tool: four clicks draw a draft with four vertices', (await ev(`S.draft ? S.draft.pts.length : -1`)) === 4);
  await shot('spec_editor_drafting.png');
  await key('Enter', 'Enter', 13);
  check('Enter adds the river', await placed(v));
  const riverId = await ev(`S.feat`);
  check('river id is river_<n>, not a reuse', /^river_\d+$/.test(riverId) && !lines0.includes(riverId), riverId);
  check('river has 4 vertices', (await nHandles('waypoint', riverId)) === 4);
  // insert a vertex with the + handle and delete it again
  check('midpoint + handles are drawn for the selected line', (await ev(`document.querySelectorAll('.mid').length`)) === 3);
  v = await version();
  await clickSel('.mid');
  check('clicking + inserts a vertex', await waitVersion(v) && (await nHandles('waypoint', riverId)) === 5);
  v = await version();
  await key('Delete', 'Delete', 46);
  check('Delete removes that vertex again', await waitVersion(v) && (await nHandles('waypoint', riverId)) === 4);
  // line width through its grip
  const wg = await handle('lines.' + riverId + '.width');
  check('the river has a width grip', !!wg && wg.kind === 'width');
  c = await hc('lines.' + riverId + '.width');
  v = await version();
  await drag(c, 18, 0);
  check('width grip drag writes width', await waitVersion(v) && (await props(riverId)).width > 0);
  await shot('spec_editor_shapes.png');
  // delete the river (no referrers)
  v = await version();
  await clickSel('#b-del-feature');
  check('Delete feature removes the river', await waitVersion(v) && !(await featureIds('line')).includes(riverId));
  // poly: two clicks and a double-click finish; + inserts a vertex; the panel deletes it
  v = await version();
  await pickTool('poly', 'hills');
  for (const [x, y] of [[90, 600], [190, 640]]) { const q = await m2c(x, y); await click(q[0], q[1]); }
  p = await m2c(150, 540);
  await mouse('mouseMoved', p[0], p[1], { buttons: 0 });
  await mouse('mousePressed', p[0], p[1]); await mouse('mouseReleased', p[0], p[1]);
  await mouse('mousePressed', p[0], p[1], { clickCount: 2 }); await mouse('mouseReleased', p[0], p[1], { clickCount: 2 });
  check('poly tool: double-click finishes a 3-vertex polygon', await placed(v));
  const polyId = await ev(`S.feat`);
  check('poly id hills_<n> with 3 vertices', /^hills_\d+$/.test(polyId) && (await nHandles('vertex', polyId)) === 3, polyId);
  check('the delete-vertex button is disabled at the minimum of 3', await ev(`document.getElementById('b-del') ? document.getElementById('b-del').disabled : true`));
  v = await version();
  await clickSel('.mid');
  check('a + handle also sits on the closing edge: polygon grows to 4 vertices', await waitVersion(v) && (await nHandles('vertex', polyId)) === 4);
  v = await version();
  await clickSel('#b-del');
  check('panel deletes the vertex again', await waitVersion(v) && (await nHandles('vertex', polyId)) === 3);
  // band: two clicks and Enter
  v = await version();
  await pickTool('band', 'field');
  for (const [x, y] of [[300, 700], [420, 740]]) { const q = await m2c(x, y); await click(q[0], q[1]); }
  await key('Enter', 'Enter', 13);
  check('band tool adds a band with a width', await placed(v) && /^field_\d+$/.test(await ev(`S.feat`)) && (await props(await ev(`S.feat`))).width === 30);
  // POI and crossing tools
  v = await version();
  await pickTool('poi', 'supply');
  p = await m2c(700, 700); await click(p[0], p[1]);
  check('POI tool adds poi_<n>', await placed(v) && /^poi_\d+$/.test(await ev(`S.feat`)));
  v = await version();
  await pickTool('crossing', 'ford');
  p = await m2c(100, 100); await click(p[0], p[1]);
  check('crossing tool adds ford_<n>', await placed(v) && /^ford_\d+$/.test(await ev(`S.feat`)));
  // rename a referenced POI: every reference follows
  await ev(`selectFeature('g')`);
  const gRefs = await ev(`(S.st.referrers.g||[]).length`);
  v = await version();
  await typeInto('#in-id', 'oak');
  await clickSel('#b-rename');
  check('rename commits', await waitVersion(v));
  check('the id changed and all ' + gRefs + ' references followed', gRefs > 0 && (await featureIds('poi')).includes('oak') && !(await featureIds('poi')).includes('g') &&
    (await ev(`(S.st.referrers.oak||[]).length`)) === gRefs && !(await ev(`S.st.referrers.g`)), gRefs);
  // delete a referenced POI: the prompt lists the referrers; cancel keeps it
  await ev(`selectFeature('o')`);
  v = await version();
  await clickSel('#b-del-feature');
  await sleep(500);
  check('deleting a referenced POI asks first and lists the referrers', await ev(`!!document.getElementById('prompt') && document.querySelectorAll('#prompt li').length>=5`) && (await version()) === v);
  await shot('spec_editor_delete_prompt.png');
  await clickSel('#b-cancel-delete');
  check('Cancel keeps the POI', (await featureIds('poi')).includes('o') && (await version()) === v);
  await clickSel('#b-del-feature');
  await sleep(500);
  await clickSel('#b-detach-delete');
  check('Detach and delete removes it and writes literal coordinates', await waitVersion(v) && !(await featureIds('poi')).includes('o') && (await ev(`S.st.handles.filter(function(h){return h.ref==='o';}).length`)) === 0);
  // layers: hide regions, lock POIs
  await clickSel('#l-vis-regions');
  check('hiding the regions layer hides its handles', (await ev(`document.querySelectorAll('.handle.k-center').length`)) === 0);
  await clickSel('#l-vis-regions');
  await clickSel('#l-lock-pois');
  check('locking the POI layer makes its handles non-interactive', await ev(`document.querySelectorAll('.handle.k-poi.locked').length>0 && getComputedStyle(document.querySelector('.handle.k-poi.locked')).pointerEvents==='none'`));
  await clickSel('#l-lock-pois');
  // an invalid property edit is refused and the error highlights the selected feature
  await ev(`selectFeature(${JSON.stringify(blobId)})`);
  v = await version();
  await typeInto('#p-r', '-5');
  await sleep(500);
  check('refused edit keeps the buffer and maps the error to the selected feature', (await version()) === v && (await ev(`S.errRefs.length>0 && S.errRefs[0].feature===${JSON.stringify(blobId)} && S.errRefs[0].key==='r'`)));
  check('the error row is marked for the selected feature and the field is flagged invalid', await ev(`!!document.querySelector('#errors li.forsel') && document.getElementById('p-r').getAttribute('aria-invalid')==='true'`));
  await shot('spec_editor_error_highlight.png');
  await ev(`selectFeature(${JSON.stringify(blobId)})`);
  await waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`);
  await shot('spec_editor_session.png');
  // save a valid spec the runner validates with `mapgen validate`
  await saveAndWait();
  fs.copyFileSync(specFile, path.join(shotDir, 'session_saved.yaml'));
  const savedText = fs.readFileSync(specFile, 'utf8');
  check('the saved session file contains the new features', ['id: ' + blobId, 'id: oak', 'id: ' + polyId].every((t) => savedText.includes(t)));
  // undo all the way back: the original bytes
  let nUndo = 0;
  while (await ev(`!document.getElementById('b-undo').disabled`) && nUndo++ < 200) { await clickSel('#b-undo'); await sleep(60); }
  await sleep(300);
  check('undo reaches the start of the session (' + nUndo + ' steps)', nUndo > 20 && (await ev(`S.st.can_undo`)) === false, nUndo);
  await clickSel('#b-save');
  await waitFor(`/saved/.test(document.getElementById('dirty').textContent)`, 5000);
  check('after undoing everything, Save writes the original bytes back', fs.readFileSync(specFile, 'utf8') === original);

  // ---- wheel zoom and pan
  const w0 = await ev(`S.view.mpp`);
  const mc = await ev(`(function(){var r=document.getElementById('map').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})()`);
  await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: mc[0], y: mc[1], deltaX: 0, deltaY: -240 });
  await sleep(200);
  const w1 = await ev(`S.view.mpp`);
  check('wheel zooms in', w1 < w0 * 0.8, { w0, w1 });
  const cx0 = await ev(`S.view.cx`);
  const mr = await ev(`(function(){var r=document.getElementById('map').getBoundingClientRect();return [r.x,r.y];})()`);
  await drag([mr[0] + 15, mr[1] + 15], 60, 40);   // the grey margin around the map: a background drag pans
  await sleep(200);
  check('background drag pans', Math.abs((await ev(`S.view.cx`)) - cx0) > 1, { cx0 });
  await clickSel('#b-fit');
  check('Fit restores the full view', Math.abs((await ev(`S.view.mpp`)) - w0) < 1e-6);

  await waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`);
  c = await edge('pois.o.at');
  await click(c[0], c[1]);
  await shot('spec_editor_final.png');
  check('page raised no JS exceptions', jsErrors.length === 0, jsErrors);
  ws.close();
}
main().then(() => {
  proc.kill();
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? 'FAILED ' + bad.length : 'ALL PASSED ' + results.length);
  process.exit(bad.length ? 1 : 0);
}).catch((e) => { console.error('ERROR', e); proc.kill(); process.exit(2); });
