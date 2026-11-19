"""Spec editor page: HTML + CSS + vanilla JS, one inline SVG over the preview image, no external URLs.
Design: docs/investigations/2026-11-19-map-editor-design.md (slice 2). Handles differ by shape and lightness, never by hue."""
from __future__ import annotations

import json
from typing import Any, Dict


def render_page(boot: Dict[str, Any], file_name: str) -> str:
    data = json.dumps(boot).replace("<", "\\u003c")
    title = "%s - spec editor" % file_name.replace("&", "&amp;").replace("<", "&lt;")
    return _HTML.replace("@@TITLE@@", title).replace("@@CSS@@", _CSS).replace("@@JS@@", _JS).replace("@@BOOT@@", data)


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>@@TITLE@@</title>
@@CSS@@
</head>
<body>
<div id="bar">
  <span class="grp">
    <button id="b-undo" title="Undo (Ctrl+Z)">Undo</button>
    <button id="b-redo" title="Redo (Ctrl+Y)">Redo</button>
    <button id="b-save" class="primary" title="Write the buffer over the file (Ctrl+S)">Save</button>
    <span id="dirty" role="status"></span>
  </span>
  <span class="grp">
    <button id="b-fit" title="Fit the map (0)">Fit</button>
    <button id="b-zin" title="Zoom in (+)">+</button>
    <button id="b-zout" title="Zoom out (-)">-</button>
    <label><input type="checkbox" id="c-outlines" checked> region outlines</label>
    <label><input type="checkbox" id="c-image" checked> preview image</label>
  </span>
  <span id="path" title="the file being edited"></span>
</div>
<div id="main">
  <div id="mapwrap"><svg id="map" xmlns="http://www.w3.org/2000/svg" role="application" aria-label="map editor"
       tabindex="0"><g id="bgimg"><image id="bg" x="0" y="0" preserveAspectRatio="none"/><rect id="frame" fill="none"/></g><g id="ov"></g></svg>
    <div id="preview" aria-live="polite"></div></div>
  <div id="side">
    <div id="status" aria-live="polite"></div>
    <div id="panel"></div>
    <div id="errwrap"><h2 id="errhead">Loader</h2><ul id="errors"></ul></div>
    <div id="pickwrap"><h2><label for="pick">Features</label></h2><select id="pick" size="9"></select></div>
    <div id="legend"></div>
    <div id="help">Drag a handle to move it (1 m steps). Alt-drag a link triangle detaches it to a literal. Double-click a
      waypoint line to add a waypoint; Delete removes the selected waypoint. Arrow keys nudge the selection (Shift: 10 m).
      Wheel zooms, drag the background pans. Ctrl+Z / Ctrl+Y undo / redo, Ctrl+S saves.</div>
  </div>
</div>
<script id="boot" type="application/json">@@BOOT@@</script>
<script>
@@JS@@
</script>
</body>
</html>
"""

_CSS = """<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:10px;background:#fff;color:#0b1020}
#bar{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;margin-bottom:8px}
.grp{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:4px 10px;border:2px solid #0b1020;background:#fff;color:#0b1020;border-radius:4px;cursor:pointer}
button.primary{background:#0b1020;color:#fff}
button:disabled{opacity:.4;cursor:default}
button:focus-visible,select:focus-visible,input:focus-visible,#map:focus-visible{outline:3px solid #0b1020;outline-offset:2px}
#dirty{font-weight:700;min-width:11em}
#path{font-family:Consolas,monospace;font-size:12px;color:#333;overflow-wrap:anywhere}
#main{display:flex;gap:12px;align-items:stretch}
#mapwrap{position:relative;flex:1 1 auto;min-width:0;height:calc(100vh - 70px);border:2px solid #0b1020;background:#cfd3da}
#map{width:100%;height:100%;display:block;user-select:none;cursor:grab}
#map.panning{cursor:grabbing}
#preview{position:absolute;left:6px;bottom:6px;background:#fff;border:1px solid #0b1020;padding:2px 6px;font-size:12px}
#side{flex:0 0 330px;display:flex;flex-direction:column;gap:8px;overflow:auto;height:calc(100vh - 70px)}
#status{min-height:1.3em;font-size:13px}
h2{font-size:14px;margin:6px 0 4px}
#panel{border:1px solid #0b1020;padding:6px 8px;font-size:13px}
#panel table{border-collapse:collapse}
#panel td{padding:1px 6px 1px 0}
#panel input[type=number]{width:6em;font:inherit}
#panel .refs{font-size:12px;color:#222;margin-top:4px}
#errors{margin:0;padding-left:18px;font-size:12px;overflow-wrap:anywhere}
#errors li.refused{font-weight:700}
#errhead.bad:before{content:"\\2716  "}
#errhead.ok:before{content:"\\2714  "}
#pick{width:100%;font-family:Consolas,monospace;font-size:12px}
#legend{font-size:12px}
#legend div{display:flex;align-items:center;gap:6px;margin:2px 0}
#help{font-size:12px;color:#222}
.handle{cursor:move}
.handle .ring{display:none}
.handle.sel .ring,.handle:focus .ring{display:inline}
.handle:focus{outline:none}
.lbl{font-size:12px;paint-order:stroke;stroke:#fff;stroke-width:3px;fill:#000;pointer-events:none}
.seg{stroke:transparent;stroke-width:11;fill:none;cursor:copy}
.stale{opacity:.35}
</style>"""

_JS = r"""
var BOOT = JSON.parse(document.getElementById('boot').textContent);
var NS = 'http://www.w3.org/2000/svg';
var S = {token: BOOT.token, st: BOOT.state, sel: null, geo: null, geoVersion: 0, imgVersion: 0, view: null, drag: null, pan: null,
         chain: Promise.resolve(), errors: [], refused: false, abort: null, timer: null, url: null};
var $ = function (id) { return document.getElementById(id); };
var RANK = {center: 0, vertex: 0, point: 1, poi: 2, base: 3, waypoint: 4};
var DASH = ['none', '7 4', '2 4', '10 4 2 4', '5 3 1 3', '14 5'];
var LINE_DASH = {road: 'none', trail: '8 4', river: '14 3 2 3', cliff: '2 3'};

function api(path, body, signal) {
  return fetch(path, {method: 'POST', headers: {'X-Token': S.token, 'Content-Type': 'application/json'},
                      body: JSON.stringify(body || {}), signal: signal});
}
function mk(tag, attrs, parent, text) {
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function el(tag, attrs, parent, text) {
  var e = document.createElement(tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function size() { return S.st.size; }
function isDef(h) { return h.kind === 'base' || h.kind === 'poi' || h.kind === 'point'; }
function byKey(k) { for (var i = 0; i < S.st.handles.length; i++) if (S.st.handles[i].path === k) return S.st.handles[i]; return null; }
function setStatus(t) { $('status').textContent = t; }
function setPrev(t) { $('preview').textContent = t; }

// ---- view ----------------------------------------------------------------------------------------------------------
function fit() {
  var r = $('map').getBoundingClientRect(), sz = size();
  S.view = {cx: sz[0] / 2, cy: sz[1] / 2, mpp: Math.max(sz[0] * 1.04 / r.width, sz[1] * 1.04 / r.height)};
}
function applyView() {
  var r = $('map').getBoundingClientRect(), v = S.view;
  $('map').setAttribute('viewBox', [v.cx - r.width * v.mpp / 2, v.cy - r.height * v.mpp / 2, r.width * v.mpp, r.height * v.mpp].join(' '));
}
function toMetres(ev) {   // client px -> map metres (y north)
  var r = $('map').getBoundingClientRect(), v = S.view, sz = size();
  return [v.cx + (ev.clientX - r.left - r.width / 2) * v.mpp, sz[1] - (v.cy + (ev.clientY - r.top - r.height / 2) * v.mpp)];
}
function zoomAt(ev, f) {
  var r = $('map').getBoundingClientRect(), v = S.view;
  var px = ev ? ev.clientX - r.left - r.width / 2 : 0, py = ev ? ev.clientY - r.top - r.height / 2 : 0;
  var nm = Math.min(Math.max(v.mpp * f, 0.05), 40);
  v.cx += px * (v.mpp - nm); v.cy += py * (v.mpp - nm); v.mpp = nm;
  render();
}

// ---- drawing -------------------------------------------------------------------------------------------------------
function effPos(h) {
  var d = S.drag;
  if (!d || !d.moved) return h.at;
  if (h.path === d.h.path) return d.to;
  if (d.moveDef && d.target !== null && (h.ref === d.target || (isDef(h) && h.feature === d.target))) return d.to;
  return h.at;
}
function describe(h) {
  var t = {base: 'base', poi: 'POI', point: 'named point', waypoint: 'waypoint ' + h.index + ' of ' + h.line, center: 'region centre',
           vertex: 'region vertex ' + h.index}[h.kind];
  return t + ' ' + (h.kind === 'waypoint' ? '' : h.feature) + (h.ref ? ' (reference to ' + h.ref + ')' : '');
}
function shape(g, h) {
  var k = h.kind;
  if (k === 'poi') mk('circle', {r: 8, fill: '#fff', stroke: '#000', 'stroke-width': 3}, g);
  else if (k === 'base') mk('rect', {x: -9, y: -9, width: 18, height: 18, fill: '#000', stroke: '#fff', 'stroke-width': 2.5}, g);
  else if (k === 'point') mk('polygon', {points: '0,-10 10,0 0,10 -10,0', fill: '#bbb', stroke: '#000', 'stroke-width': 2.5}, g);
  else if (k === 'waypoint' && h.ref) mk('polygon', {points: '0,-7 7,5 -7,5', fill: '#fff', stroke: '#000', 'stroke-width': 2.5}, g);
  else if (k === 'waypoint') mk('circle', {r: 5, fill: '#000', stroke: '#fff', 'stroke-width': 2}, g);
  else if (k === 'center') {
    mk('circle', {r: 7, fill: '#fff', 'fill-opacity': .6, stroke: '#000', 'stroke-width': 2.5}, g);
    mk('path', {d: 'M-7 0H7M0 -7V7', stroke: '#000', 'stroke-width': 2}, g);
  } else mk('rect', {x: -5, y: -5, width: 10, height: 10, fill: '#fff', stroke: '#000', 'stroke-width': 2}, g);
}
function render() {
  if (!S.view) return;
  applyView();
  var sz = size(), mpp = S.view.mpp, ov = $('ov'), dragging = !!(S.drag && S.drag.moved);
  var active = document.activeElement && document.activeElement.getAttribute ? document.activeElement.getAttribute('data-key') : null;
  while (ov.firstChild) ov.removeChild(ov.firstChild);
  var fr = $('frame'); fr.setAttribute('width', sz[0]); fr.setAttribute('height', sz[1]);
  fr.setAttribute('stroke', '#000'); fr.setAttribute('stroke-width', 2 * mpp);
  var bg = $('bg'); bg.setAttribute('width', sz[0]); bg.setAttribute('height', sz[1]);
  bg.style.display = $('c-image').checked && S.url ? '' : 'none';
  var stale = S.geoVersion !== S.st.version || dragging;
  var gw = mk('g', {'stroke-linejoin': 'round', 'fill': 'none'}, ov);
  if (S.geo && $('c-outlines').checked) {
    var types = {}, order = [];
    S.geo.regions.forEach(function (r) { if (!(r.type in types)) { types[r.type] = order.length; order.push(r.type); } });
    var gr = mk('g', {'class': stale ? 'stale' : '', 'pointer-events': 'none'}, gw);
    S.geo.regions.forEach(function (r) {
      var d = r.rings.map(function (ring) { return 'M' + ring.map(function (p) { return p[0] + ' ' + (sz[1] - p[1]); }).join('L') + 'Z'; }).join('');
      var dash = DASH[types[r.type] % DASH.length];
      mk('path', {d: d, stroke: '#fff', 'stroke-width': 4 * mpp, 'stroke-opacity': .8}, gr);
      mk('path', {d: d, stroke: '#000', 'stroke-width': 1.6 * mpp, 'stroke-dasharray': dash === 'none' ? 'none' : dash.split(' ').map(function (x) { return x * mpp; }).join(' ')}, gr);
    });
  }
  if (S.geo) {
    var gl = mk('g', {'class': stale ? 'stale' : '', 'pointer-events': 'none'}, gw);
    S.geo.lines.forEach(function (l) {
      var d = 'M' + l.pts.map(function (p) { return p[0] + ' ' + (sz[1] - p[1]); }).join('L');
      var dash = LINE_DASH[l.type] || '6 4';
      mk('path', {d: d, stroke: '#fff', 'stroke-width': 5 * mpp, 'stroke-opacity': .85}, gl);
      mk('path', {d: d, stroke: '#000', 'stroke-width': 2 * mpp, 'stroke-dasharray': dash === 'none' ? 'none' : dash.split(' ').map(function (x) { return x * mpp; }).join(' ')}, gl);
    });
  }
  // waypoint polylines (the author's values) with hit segments for double-click insertion
  var lines = {};
  S.st.handles.forEach(function (h) { if (h.kind === 'waypoint') (lines[h.line] = lines[h.line] || []).push(h); });
  var gs = mk('g', {}, ov);
  Object.keys(lines).forEach(function (id) {
    var hs = lines[id].sort(function (a, b) { return a.index - b.index; });
    var pts = hs.map(function (h) { var p = effPos(h); return [p[0], sz[1] - p[1]]; });
    mk('polyline', {points: pts.map(function (p) { return p.join(','); }).join(' '), fill: 'none', stroke: '#fff', 'stroke-width': 3 * mpp,
                    'stroke-dasharray': (6 * mpp) + ' ' + (4 * mpp), 'pointer-events': 'none'}, gs);
    mk('polyline', {points: pts.map(function (p) { return p.join(','); }).join(' '), fill: 'none', stroke: '#000', 'stroke-width': 1.2 * mpp,
                    'stroke-dasharray': (6 * mpp) + ' ' + (4 * mpp), 'pointer-events': 'none'}, gs);
    for (var i = 0; i + 1 < pts.length; i++)
      mk('line', {'class': 'seg', x1: pts[i][0], y1: pts[i][1], x2: pts[i + 1][0], y2: pts[i + 1][1], 'stroke-width': 11 * mpp,
                  'data-line': id, 'data-seg': i}, gs);
  });
  var gh = mk('g', {}, ov);
  S.st.handles.slice().sort(function (a, b) { return RANK[a.kind] - RANK[b.kind]; }).forEach(function (h) {
    var p = effPos(h), sel = S.sel === h.path;
    var g = mk('g', {'class': 'handle k-' + h.kind + (sel ? ' sel' : ''), 'data-key': h.path, tabindex: 0, role: 'button',
                     'aria-label': describe(h) + ' at ' + p[0] + ', ' + p[1],
                     transform: 'translate(' + p[0] + ' ' + (sz[1] - p[1]) + ') scale(' + mpp + ')'}, gh);
    mk('circle', {'class': 'ring', r: 15, fill: 'none', stroke: '#fff', 'stroke-width': 5}, g);
    mk('circle', {'class': 'ring', r: 15, fill: 'none', stroke: '#000', 'stroke-width': 2.2, 'stroke-dasharray': '4 3'}, g);
    shape(g, h);
    if (isDef(h) || sel) mk('text', {'class': 'lbl', x: 12, y: -10}, g, h.kind === 'waypoint' ? h.line + '[' + h.index + ']' : h.feature);
  });
  if (active) {
    var e = ov.querySelector('[data-key="' + active.replace(/"/g, '\\"') + '"]');
    if (e && document.activeElement !== e) e.focus({preventScroll: true});
  }
}

// ---- panel ---------------------------------------------------------------------------------------------------------
function refsOf(h) {
  var name = h.ref || (isDef(h) ? h.feature : null);
  return name === null ? [] : (S.st.referrers[name] || []).filter(function (r) { return r.path !== h.path; }).map(function (r) { return r.path; });
}
function updateAll() {
  var st = S.st;
  $('dirty').textContent = st.dirty ? '● UNSAVED CHANGES' : '○ saved';
  document.title = (st.dirty ? '* ' : '') + st.path.split(/[\\/]/).pop() + ' - spec editor';
  $('path').textContent = st.path;
  $('b-undo').disabled = !st.can_undo; $('b-redo').disabled = !st.can_redo; $('b-save').disabled = !st.dirty;
  if (S.sel && !byKey(S.sel)) S.sel = null;
  var pick = $('pick'), keep = S.sel;
  while (pick.firstChild) pick.removeChild(pick.firstChild);
  st.handles.forEach(function (h) {
    var o = el('option', {value: h.path}, pick, h.path + (h.ref ? '  -> ' + h.ref : ''));
    if (h.path === keep) o.selected = true;
  });
  var eh = $('errhead'), ul = $('errors');
  while (ul.firstChild) ul.removeChild(ul.firstChild);
  S.errors.forEach(function (m, i) { el('li', {'class': i === 0 && S.refused ? 'refused' : ''}, ul, m); });
  eh.textContent = S.errors.length ? 'Loader: last edit refused (' + S.errors.length + ' error' + (S.errors.length > 1 ? 's' : '') + ')' : 'Loader: no errors';
  eh.className = S.errors.length ? 'bad' : 'ok';
  var p = $('panel');
  while (p.firstChild) p.removeChild(p.firstChild);
  var h = S.sel ? byKey(S.sel) : null;
  if (!h) { el('div', {}, p, 'Nothing selected. Click a handle.'); return; }
  el('h2', {}, p, describe(h));
  el('div', {}, p, 'type: ' + h.ftype + '   path: ' + h.path);
  var tb = el('table', {}, p), tr = el('tr', {}, tb);
  el('td', {}, tr, 'x');
  var ix = el('input', {type: 'number', step: 1, id: 'in-x', value: h.at[0], 'aria-label': 'x metres'}, el('td', {}, tr));
  el('td', {}, tr, 'y');
  var iy = el('input', {type: 'number', step: 1, id: 'in-y', value: h.at[1], 'aria-label': 'y metres'}, el('td', {}, tr));
  var go = function () {
    var x = Math.round(Number(ix.value)), y = Math.round(Number(iy.value));
    if (isFinite(x) && isFinite(y) && (x !== h.at[0] || y !== h.at[1])) commit([{op: 'set', path: h.path, value: [x, y], detach: false}], h.path);
  };
  ix.addEventListener('change', go); iy.addEventListener('change', go);
  var refs = refsOf(h);
  if (h.ref) el('div', {'class': 'refs'}, p, 'References point ' + h.ref + ': moving it moves that definition and ' + refs.length +
                ' other referrer' + (refs.length === 1 ? '' : 's') + '. Alt-drag detaches it to a literal.');
  else if (isDef(h)) el('div', {'class': 'refs'}, p, 'Referenced by ' + refs.length + (refs.length ? ': ' + refs.join(', ') : '') + '.');
  if (h.ref) {
    var bd = el('button', {id: 'b-detach'}, p, 'Detach to literal');
    bd.addEventListener('click', function () { commit([{op: 'set', path: h.path, value: h.at, detach: true}], h.path); });
  }
  if (h.kind === 'waypoint') {
    var bx = el('button', {id: 'b-del'}, p, 'Delete waypoint');
    bx.addEventListener('click', delSel);
  }
}

// ---- edits ---------------------------------------------------------------------------------------------------------
function commit(ops, selAfter) {
  S.chain = S.chain.then(function () {
    return api('/api/apply', {version: S.st.version, ops: ops}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { S.st = j.state; S.errors = []; S.refused = false; setStatus('Edit applied (version ' + S.st.version + ').'); }
      else { S.errors = j.errors || ['request failed']; S.refused = true; if (j.state) S.st = j.state; setStatus('Edit refused: ' + S.errors[0]); }
      if (selAfter !== undefined) S.sel = selAfter;
      changed();
    });
  }).catch(function (e) { setStatus('Request failed: ' + e); });
  return S.chain;
}
function simple(path) {
  S.chain = S.chain.then(function () {
    return api(path, {}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { S.st = j.state; S.errors = []; S.refused = false; if (path === '/api/save') setStatus('Saved ' + j.bytes + ' bytes to ' + j.path); else setStatus(path.slice(5) + ' done.'); }
      else { S.errors = j.errors || []; S.refused = true; if (j.state) S.st = j.state; setStatus(path.slice(5) + ' refused: ' + S.errors[0]); }
      changed();
    });
  }).catch(function (e) { setStatus('Request failed: ' + e); });
}
function changed() {
  updateAll(); render();
  if (S.geoVersion !== S.st.version) schedule();
}
function delSel() {
  var h = S.sel ? byKey(S.sel) : null;
  if (h && h.kind === 'waypoint') commit([{op: 'delete', path: h.path}], null);
}
function nudge(dx, dy) {
  var h = S.sel ? byKey(S.sel) : null;
  if (!h) return false;
  commit([{op: 'set', path: h.path, value: [h.at[0] + dx, h.at[1] + dy], detach: false}], h.path);
  return true;
}

// ---- preview refresh (debounced, latest wins) ----------------------------------------------------------------------
function schedule() {
  if (S.timer) clearTimeout(S.timer);
  setPrev('preview image: stale, updating...');
  S.timer = setTimeout(refresh, 300);
}
function refresh() {
  S.timer = null;
  var v = S.st.version;
  if (S.abort) S.abort.abort();
  var ac = new AbortController(); S.abort = ac;
  api('/api/geometry', {version: v}, ac.signal).then(function (r) { return r.json(); }).then(function (j) {
    if (!j.ok || S.st.version !== v) return null;
    S.geo = j.geometry; S.geoVersion = v; render();
    setPrev('shapes ' + j.ms + ' ms; rendering ' + BOOT.preview_px + ' px preview image...');
    return api('/api/image', {version: v}, ac.signal).then(function (r) {
      if ((r.headers.get('Content-Type') || '').indexOf('image/png') !== 0) return null;
      var ms = r.headers.get('X-Render-Ms');
      return r.blob().then(function (b) { return [b, ms]; });
    }).then(function (x) {
      if (!x || S.st.version !== v) return;
      var old = S.url; S.url = URL.createObjectURL(x[0]);
      $('bg').setAttribute('href', S.url); S.imgVersion = v;
      if (old) URL.revokeObjectURL(old);
      setPrev('preview image ' + BOOT.preview_px + ' px, ' + x[1] + ' ms (reduced resolution, not a build)');
      render();
    });
  }).catch(function (e) { if (e.name !== 'AbortError') setPrev('preview failed: ' + e); });
}

// ---- mouse and keyboard --------------------------------------------------------------------------------------------
function clampPt(p) { var sz = size(); return [Math.min(Math.max(Math.round(p[0]), 0), sz[0]), Math.min(Math.max(Math.round(p[1]), 0), sz[1])]; }
function select(key) {
  S.sel = key; updateAll(); render();
}
$('map').addEventListener('mousedown', function (ev) {
  if (ev.button !== 0 && ev.button !== 1) return;
  var t = ev.target.closest ? ev.target.closest('.handle') : null;
  if (t) {
    var h = byKey(t.getAttribute('data-key'));
    if (!h) return;
    ev.preventDefault();
    if (S.sel !== h.path) select(h.path);
    var m = toMetres(ev);
    S.drag = {h: h, startX: ev.clientX, startY: ev.clientY, off: [h.at[0] - m[0], h.at[1] - m[1]], moved: false, to: h.at, alt: false,
              target: h.ref !== null ? h.ref : (isDef(h) ? h.feature : null), moveDef: false};
    var e = $('ov').querySelector('[data-key="' + h.path.replace(/"/g, '\\"') + '"]');
    if (e) e.focus({preventScroll: true});
    return;
  }
  if (ev.target.classList && ev.target.classList.contains('seg')) return;
  ev.preventDefault();
  S.pan = {x: ev.clientX, y: ev.clientY, cx: S.view.cx, cy: S.view.cy, moved: false};
  $('map').classList.add('panning');
  $('map').focus({preventScroll: true});
});
window.addEventListener('mousemove', function (ev) {
  var d = S.drag;
  if (d) {
    if (!d.moved && Math.abs(ev.clientX - d.startX) + Math.abs(ev.clientY - d.startY) < 4) return;
    d.moved = true; d.alt = ev.altKey;
    d.moveDef = d.target !== null && !(d.alt && d.h.ref !== null);
    var m = toMetres(ev);
    d.to = clampPt([m[0] + d.off[0], m[1] + d.off[1]]);
    var what = d.h.ref && d.alt ? 'detaching' : (d.target !== null ? 'moving definition of ' + d.target : 'moving');
    setStatus(what + ' to ' + d.to[0] + ', ' + d.to[1]);
    render();
  } else if (S.pan) {
    var p = S.pan;
    if (Math.abs(ev.clientX - p.x) + Math.abs(ev.clientY - p.y) > 3) p.moved = true;
    S.view.cx = p.cx - (ev.clientX - p.x) * S.view.mpp; S.view.cy = p.cy - (ev.clientY - p.y) * S.view.mpp;
    render();
  }
});
window.addEventListener('mouseup', function (ev) {
  var d = S.drag;
  if (d) {
    S.drag = null;
    if (d.moved && (d.to[0] !== d.h.at[0] || d.to[1] !== d.h.at[1])) {
      commit([{op: 'set', path: d.h.path, value: d.to, detach: !!(ev.altKey && d.h.ref !== null)}], d.h.path);
    } else { render(); }
  } else if (S.pan) {
    var moved = S.pan.moved; S.pan = null;
    $('map').classList.remove('panning');
    if (!moved) select(null);
  }
});
$('map').addEventListener('wheel', function (ev) { ev.preventDefault(); zoomAt(ev, Math.exp(Math.max(-400, Math.min(400, ev.deltaY)) * 0.0015)); }, {passive: false});
$('map').addEventListener('dblclick', function (ev) {
  var t = ev.target;
  if (!t.classList || !t.classList.contains('seg')) return;
  var id = t.getAttribute('data-line'), i = Number(t.getAttribute('data-seg'));
  var m = toMetres(ev), a = byKey('lines.' + id + '.path[' + i + ']'), b = byKey('lines.' + id + '.path[' + (i + 1) + ']');
  var q = m;
  if (a && b) {   // project onto the segment
    var dx = b.at[0] - a.at[0], dy = b.at[1] - a.at[1], L = dx * dx + dy * dy;
    var u = L > 0 ? Math.min(1, Math.max(0, ((m[0] - a.at[0]) * dx + (m[1] - a.at[1]) * dy) / L)) : 0;
    q = [a.at[0] + u * dx, a.at[1] + u * dy];
  }
  q = clampPt(q);
  commit([{op: 'insert', path: 'lines.' + id + '.path', index: i + 1, value: q}], 'lines.' + id + '.path[' + (i + 1) + ']');
});
$('map').addEventListener('focusin', function (ev) {
  var t = ev.target.closest ? ev.target.closest('.handle') : null;
  if (t && !S.drag && S.sel !== t.getAttribute('data-key')) { S.sel = t.getAttribute('data-key'); updateAll(); render(); }
});
$('pick').addEventListener('change', function () {
  var h = byKey($('pick').value);
  if (!h) return;
  S.sel = h.path;
  var r = $('map').getBoundingClientRect(), v = S.view, sz = size();
  var X = h.at[0], Y = sz[1] - h.at[1];
  if (Math.abs(X - v.cx) > r.width * v.mpp * 0.45 || Math.abs(Y - v.cy) > r.height * v.mpp * 0.45) { v.cx = X; v.cy = Y; }
  updateAll(); render();
});
document.addEventListener('keydown', function (ev) {
  var tag = ev.target && ev.target.tagName;
  var inField = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
  var k = ev.key;
  if ((ev.ctrlKey || ev.metaKey) && (k === 'z' || k === 'Z') && !ev.shiftKey) { if (!inField) { ev.preventDefault(); simple('/api/undo'); } return; }
  if ((ev.ctrlKey || ev.metaKey) && (k === 'y' || k === 'Y' || ((k === 'z' || k === 'Z') && ev.shiftKey))) { if (!inField) { ev.preventDefault(); simple('/api/redo'); } return; }
  if ((ev.ctrlKey || ev.metaKey) && (k === 's' || k === 'S')) { ev.preventDefault(); simple('/api/save'); return; }
  if (inField) return;
  if (k === 'Escape') { S.drag = null; S.sel = null; updateAll(); render(); return; }
  if (k === 'Delete' || k === 'Backspace') { if (S.sel) { ev.preventDefault(); delSel(); } return; }
  if (k === '+' || k === '=') { zoomAt(null, 1 / 1.25); return; }
  if (k === '-' || k === '_') { zoomAt(null, 1.25); return; }
  if (k === '0') { fit(); render(); return; }
  var step = ev.shiftKey ? 10 : 1, dx = 0, dy = 0;
  if (k === 'ArrowLeft') dx = -step; else if (k === 'ArrowRight') dx = step; else if (k === 'ArrowUp') dy = step; else if (k === 'ArrowDown') dy = -step; else return;
  ev.preventDefault();
  if (!nudge(dx, dy)) { S.view.cx += dx * S.view.mpp * 20; S.view.cy -= dy * S.view.mpp * 20; render(); }
});
$('b-undo').addEventListener('click', function () { simple('/api/undo'); });
$('b-redo').addEventListener('click', function () { simple('/api/redo'); });
$('b-save').addEventListener('click', function () { simple('/api/save'); });
$('b-fit').addEventListener('click', function () { fit(); render(); });
$('b-zin').addEventListener('click', function () { zoomAt(null, 1 / 1.25); });
$('b-zout').addEventListener('click', function () { zoomAt(null, 1.25); });
$('c-outlines').addEventListener('change', render);
$('c-image').addEventListener('change', render);
window.addEventListener('resize', render);
window.addEventListener('beforeunload', function (ev) { if (S.st.dirty) { ev.preventDefault(); ev.returnValue = ''; } });

function legend() {
  var lg = $('legend');
  el('h2', {}, lg, 'Handles');
  [['poi', 'POI (circle)'], ['base', 'team base (square)'], ['point', 'named point (diamond)'], ['waypoint', 'literal waypoint (dot)'],
   ['wpref', 'waypoint that references a point (triangle)'], ['center', 'region centre (cross)'], ['vertex', 'region vertex (hollow square)']].forEach(function (x) {
    var d = el('div', {}, lg), svg = mk('svg', {width: 24, height: 24, viewBox: '-12 -12 24 24'}, null);
    d.appendChild(svg);
    var g = mk('g', {}, svg);
    shape(g, x[0] === 'wpref' ? {kind: 'waypoint', ref: 'x'} : {kind: x[0], ref: null});
    el('span', {}, d, x[1]);
  });
}
legend();
fit(); updateAll(); render(); refresh();
setStatus('Loaded ' + S.st.path);
"""
