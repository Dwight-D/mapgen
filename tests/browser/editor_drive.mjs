// Drives the flowgraph editor in headless Edge/Chrome over the DevTools protocol with real mouse and key input events.
// Usage: node editor_drive.mjs <browser exe> <editor url> <graph file written by Save> <screenshot dir>
// Needs Node >= 22 (global WebSocket). No packages.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const [exe, url, graphFile, shotDir] = process.argv.slice(2);
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
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } };
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
  const click = async (x, y, extra = {}) => {
    await mouse('mouseMoved', x, y, { buttons: 0 });
    await mouse('mousePressed', x, y, extra);
    await mouse('mouseReleased', x, y, extra);
    await sleep(250);
  };
  const center = (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;var r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2];})()`);
  const clickSel = async (sel) => { const c = await center(sel); if (!c) throw new Error('no element ' + sel); await click(c[0], c[1]); };
  const nodeC = (nid) => center(`.node[data-id="${nid}"] rect, .node[data-id="${nid}"] polygon, .node[data-id="${nid}"] circle:not(.halo)`);
  const key = async (k, code, vk) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, windowsVirtualKeyCode: vk });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk });
  };
  const typeInto = async (sel, text) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});e.focus();if(e.select)e.select();})()`);
    await send('Input.insertText', { text });
    await key('Tab', 'Tab', 9);
    await sleep(200);
  };
  const pick = (sel, val) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});e.value=${JSON.stringify(val)};e.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const counts = () => ev(`({n:document.querySelectorAll('.node').length,e:document.querySelectorAll('.edge').length,save:!document.getElementById('b-save').disabled,head:document.getElementById('errhead').textContent,errs:[].map.call(document.querySelectorAll('#errors li'),function(l){return l.textContent;})})`);
  const empty = (fx, fy) => ev(`(function(){var s=document.getElementById('graph'),r=s.getBoundingClientRect();return [r.x+r.width*${fx},r.y+r.height*${fy}];})()`);
  const settle = () => sleep(500);

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  await sleep(1800);

  let c = await counts();
  check('initial: 2 nodes 1 edge, valid, save enabled', c.n === 2 && c.e === 1 && c.save, c);
  await shot('editor_initial.png');

  // add node O (objective, place center) by clicking empty canvas
  await clickSel('#t-node');
  let p = await empty(0.5, 0.3);
  await click(p[0], p[1]);
  check('node form opens', await ev(`!!document.getElementById('n-add')`));
  await typeInto('#f-id', 'O');
  await pick('#f-kind', 'objective');
  await pick('#f-place', 'center');
  await typeInto('#f-name', 'Town square');
  await clickSel('#n-add');
  await settle();
  // add node C (choke)
  p = await empty(0.3, 0.6);
  await click(p[0], p[1]);
  await typeInto('#f-id', 'C');
  await pick('#f-kind', 'choke');
  await typeInto('#f-name', 'Gate');
  await clickSel('#n-add');
  await settle();
  c = await counts();
  check('two nodes added by click + form', c.n === 4, c);
  check('disconnected -> invalid, save disabled, errors listed', !c.save && /not connected/.test(c.errs.join(' ')), c);
  await shot('editor_invalid.png');

  // add edges via the edge tool: A-C, C-O, O-B
  await clickSel('#t-edge');
  for (const [a, b] of [['A', 'C'], ['C', 'O'], ['O', 'B']]) {
    const ca = await nodeC(a); await click(ca[0], ca[1]);
    const cb = await nodeC(b); await click(cb[0], cb[1]);
  }
  await settle();
  c = await counts();
  check('three edges added by clicking node pairs, valid again', c.e === 4 && c.save && /OK/.test(c.head), c);

  // shift-click shortcut with the select tool: click A, shift-click O
  await clickSel('#t-select');
  const ca = await nodeC('A'); await click(ca[0], ca[1]);
  let co = await nodeC('O');
  await click(co[0], co[1], { modifiers: 8 });
  await settle();
  c = await counts();
  check('shift-click adds edge A-O', c.e === 5, c);

  // edit fields in the side panel
  await clickSel('.node[data-id="C"] polygon');
  await typeInto('#f-name', 'Team A gate');
  check('panel edit changes the label', await ev(`document.querySelector('.node[data-id="C"]').textContent.indexOf('Team A gate')>=0`));
  await typeInto('#f-id', 'AO');
  await settle();
  c = await counts();
  check('rename id cascades into edges', (await ev(`!!document.querySelector('.edge[data-a="A"][data-b="AO"]')`)) && c.save, c);

  // drag O with the mouse
  const before = await ev(`JSON.stringify(document.querySelector('.node[data-id="O"]').getBoundingClientRect())`);
  co = await nodeC('O');
  await mouse('mouseMoved', co[0], co[1], { buttons: 0 });
  await mouse('mousePressed', co[0], co[1]);
  for (let i = 1; i <= 8; i++) await mouse('mouseMoved', co[0] + i * 12, co[1] - i * 8, { buttons: 1 });
  await mouse('mouseReleased', co[0] + 96, co[1] - 64);
  await sleep(300);
  const after = await ev(`JSON.stringify(document.querySelector('.node[data-id="O"]').getBoundingClientRect())`);
  check('drag moves the node', before !== after, { before, after });
  check('drag does not change the graph', (await counts()).e === 5);

  // delete the A-O edge: click its hit line midpoint, press Delete
  await ev(`document.activeElement&&document.activeElement.blur&&document.activeElement.blur()`);
  const mid = await center('.edge[data-a="A"][data-b="O"] .hit');
  await click(mid[0], mid[1]);
  check('edge click shows the edge panel', (await ev(`document.querySelector('#panel h2').textContent`)) === 'Edge A - O');
  await key('Delete', 'Delete', 46);
  await sleep(300);
  check('Delete key removes the edge', (await counts()).e === 4);
  await clickSel('#b-undo');
  check('undo restores the edge', (await counts()).e === 5);
  await clickSel('#b-redo');
  check('redo removes it again', (await counts()).e === 4);

  // node delete cascades
  await clickSel('.node[data-id="O"] polygon');
  await clickSel('#b-delete');
  c = await counts();
  check('node delete cascades its edges', c.n === 3 && c.e === 2, c);
  await clickSel('#b-undo');
  c = await counts();
  check('undo restores node and edges', c.n === 4 && c.e === 4 && c.save, c);

  // re-layout through the server
  const xs = () => ev(`JSON.stringify([].map.call(document.querySelectorAll('.node'),function(n){return Math.round(n.getBoundingClientRect().x);}))`);
  const x0 = await xs();
  await clickSel('#b-layout');
  await sleep(900);
  check('re-layout moves nodes (server layout)', x0 !== (await xs()) && /Layout applied/.test(await ev(`document.getElementById('status').textContent`)));
  await shot('editor_valid_layout.png');

  // save
  await clickSel('#b-save');
  await sleep(900);
  const msg = await ev(`document.getElementById('status').textContent`);
  check('save reports success', /^Saved /.test(msg), msg);
  const text = fs.readFileSync(graphFile, 'utf8');
  check('file written in canonical form', /^format_version: 1\nname: /.test(text) && /- A - AO/.test(text) && /place: center/.test(text), text);
  ws.close();
}
main().then(() => {
  proc.kill();
  const bad = results.filter((r) => !r.ok);
  console.log(bad.length ? 'FAILED ' + bad.length : 'ALL PASSED ' + results.length);
  process.exit(bad.length ? 1 : 0);
}).catch((e) => { console.error('ERROR', e); proc.kill(); process.exit(2); });
