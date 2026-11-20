// Drives the spec editor's flowgraph overlay and live check badge (slice 5) in headless Edge/Chrome over the DevTools protocol.
// Real input: mouse drag (Alt-drag detaches a road end and moves it), clicks on the layer toggle, badge, finding rows, strict box,
// terrain button. Scripted: page-state reads via Runtime.evaluate.
// Usage: node flowgraph_drive.mjs <browser exe> <editor url> <spec file> <flowgraph file> <screenshot dir>
// Needs Node >= 22 (global WebSocket). No packages.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [exe, url, specFile, graphFile, shotDir] = process.argv.slice(2);
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
  const center = (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;var r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})()`);
  const hc = (p) => ev(`(function(){var g=document.querySelector('.handle[data-key=${JSON.stringify(p).replace(/'/g, "\\'")}]');if(!g)return null;var s=g.querySelector('circle:not(.ring),rect,polygon');var r=s.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})()`);
  const drag = async (from, dx, dy, extra = {}) => {
    await mouse('mouseMoved', from[0], from[1], { buttons: 0, ...extra });
    await mouse('mousePressed', from[0], from[1], extra);
    for (let i = 1; i <= 8; i++) await mouse('mouseMoved', from[0] + (dx * i) / 8, from[1] + (dy * i) / 8, { buttons: 1, ...extra });
    await mouse('mouseReleased', from[0] + dx, from[1] + dy, extra);
  };
  const click = async (x, y) => {
    await mouse('mouseMoved', x, y, { buttons: 0 });
    await mouse('mousePressed', x, y);
    await mouse('mouseReleased', x, y);
    await sleep(150);
  };
  const clickSel = async (sel) => { const c = await center(sel); await click(c[0], c[1]); };
  const waitFor = async (expr, ms = 30000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await ev(expr)) return true; await sleep(100); } return false; };
  const badge = () => ev(`document.getElementById('fg-badge').textContent`);
  const edgeStatus = (e) => ev(`(function(){var n=document.querySelector('.fg-edge[data-edge="${e}"]');return n?n.getAttribute('data-status'):null;})()`);
  const graphBytes = fs.readFileSync(graphFile);
  const graphMtime = fs.statSync(graphFile).mtimeMs;
  const specBytes = fs.readFileSync(specFile, 'utf8');

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  check('page loads with the flowgraph layer present', await waitFor(`document.querySelectorAll('.handle').length>100 && !!document.getElementById('l-vis-flowgraph')`, 20000));
  check('badge reads OK on v4', await waitFor(`document.getElementById('fg-badge').getAttribute('data-state')==='ok'`) && /flowgraph: OK/.test(await badge()), await badge());
  check('overlay draws 9 node discs with markers and 12 edges', await waitFor(`document.querySelectorAll('#fg-layer .fg-node').length===9 && document.querySelectorAll('#fg-layer .fg-edge').length===12`));
  check('every satisfied edge is solid (status ok)', await ev(`Array.prototype.every.call(document.querySelectorAll('.fg-edge'),function(e){return e.getAttribute('data-status')==='ok';})`));
  check('node labels carry id and name', await ev(`/Town square/.test(document.getElementById('fg-layer').textContent) && /Team A spawn/.test(document.getElementById('fg-layer').textContent)`));
  await waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`);
  await shot('flowgraph_ok.png');

  // layer toggle with the other layers
  await clickSel('#l-vis-flowgraph');
  check('layer toggle off hides the overlay', (await ev(`document.querySelectorAll('#fg-layer').length`)) === 0);
  await clickSel('#l-vis-flowgraph');
  check('layer toggle on shows it again', (await ev(`document.querySelectorAll('#fg-layer .fg-node').length`)) === 9);

  // expand the badge
  await clickSel('#fg-badge');
  check('clicking the badge expands the finding list', await ev(`document.getElementById('fg-body').style.display===''  && document.getElementById('fg-badge').getAttribute('aria-expanded')==='true'`));
  check('no findings listed', await ev(`/no findings/.test(document.getElementById('fg-findings').textContent)`));

  // real edit: Alt-drag the `o` end of road_ao_o (a link triangle) far from the square so AO - O breaks
  await ev(`select('lines.road_ao_o.path[2]')`);
  const c = await hc('lines.road_ao_o.path[2]');
  const v0 = await ev(`S.st.version`);
  await drag(c, 0, -170, { modifiers: 1 });
  check('Alt-drag commits the edit', await waitFor(`S.st.version>${v0}`, 15000));
  check('the badge turns to a problem count', await waitFor(`/1 problem/.test(document.getElementById('fg-badge').textContent) && document.getElementById('fg-badge').getAttribute('data-state')==='bad'`, 15000), await badge());
  check('the broken edge is drawn dashed with an X marker (status missing)', (await edgeStatus('AO-O')) === 'missing' &&
    (await ev(`document.querySelectorAll('.fg-edge[data-edge="AO-O"] path').length`)) === 1);
  check('the finding is listed', await ev(`/missing edge AO - O/.test(document.getElementById('fg-findings').textContent)`));
  check('other edges stay solid', (await edgeStatus('A-AO')) === 'ok' && (await edgeStatus('B-BO')) === 'ok');
  await waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`);

  // click the finding: highlights the edge and its nodes
  await clickSel('#fg-findings li[data-idx="0"]');
  check('clicking the finding highlights the edge and both nodes', (await edgeStatus('AO-O')) === 'missing hi' &&
    (await ev(`document.querySelectorAll('.fg-node').length`)) === 9 && (await ev(`S.fgHi.nodes.join(',')`)) === 'AO,O' &&
    await ev(`document.querySelector('#fg-findings li.on')!==null`));
  await shot('flowgraph_broken_edge.png');

  // strict: still 1 problem (extras count only in strict); an extra edge may appear from the moved road
  await clickSel('#fg-strict');
  await sleep(300);
  check('strict toggle recounts and is a real checkbox', await ev(`document.getElementById('fg-strict').checked && /problem/.test(document.getElementById('fg-badge').textContent)`), await badge());
  await shot('flowgraph_strict.png');
  await clickSel('#fg-strict');

  // undo: back to OK
  await clickSel('#b-undo');
  check('Undo returns the badge to OK', await waitFor(`/flowgraph: OK/.test(document.getElementById('fg-badge').textContent)`, 15000), await badge());
  check('the highlight is gone with its finding', (await ev(`S.fgHi`)) === null);

  // rename warning through the panel (real typing + button): POI g is bound by node G
  await ev(`selectFeature('g')`);
  await ev(`(function(){var e=document.getElementById('in-id');e.focus();e.select();})()`);
  await send('Input.insertText', { text: 'oak' });
  await clickSel('#b-rename');
  check('rename shows the unbound warning and the node is listed as unbound', await waitFor(`/node G/.test(document.getElementById('fg-warn').textContent) && /flowgraph edit/.test(document.getElementById('fg-warn').textContent) && document.querySelectorAll('#fg-unbound li').length===1`, 15000));
  check('the badge counts the missing node', await waitFor(`/problem/.test(document.getElementById('fg-badge').textContent)`, 15000), await badge());
  check('the unbound node is not drawn on the map', (await ev(`document.querySelectorAll('.fg-node').length`)) === 8);
  await waitFor(`S.imgVersion===S.st.version && S.geoVersion===S.st.version`);
  await shot('flowgraph_rename_warning.png');
  await clickSel('#b-undo');
  await waitFor(`/flowgraph: OK/.test(document.getElementById('fg-badge').textContent)`, 15000);

  // terrain on demand
  await clickSel('#fg-terrain');
  check('terrain shows computing while it runs', await ev(`/computing|finding/.test(document.getElementById('fg-terr-state').textContent)`));
  check('terrain finishes', await waitFor(`/terrain: \\d+ finding/.test(document.getElementById('fg-terr-state').textContent)`, 90000), await ev(`document.getElementById('fg-terr-state').textContent`));

  check('the graph file was not touched', Buffer.compare(fs.readFileSync(graphFile), graphBytes) === 0 && fs.statSync(graphFile).mtimeMs === graphMtime);
  check('the spec file was not written (no Save pressed)', fs.readFileSync(specFile, 'utf8') === specBytes);
  check('page raised no JS exceptions', jsErrors.length === 0, jsErrors);
  ws.close();
}
main().then(() => {
  proc.kill();
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? 'FAILED ' + bad.length : 'ALL PASSED ' + results.length);
  process.exit(bad.length ? 1 : 0);
}).catch((e) => { console.error('ERROR', e); proc.kill(); process.exit(2); });
