"""Spec editor page: HTML + CSS + vanilla JS, one inline SVG over the preview image, no external URLs.
Design: docs/investigations/2026-11-19-map-editor-design.md (slices 2 and 3). Handles differ by shape and lightness, never by hue."""
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
  <span class="grp" id="tools" role="toolbar" aria-label="tools"></span>
  <span class="grp">
    <select id="tool-type" aria-label="type of the feature to add" style="display:none"></select>
    <button id="finish" style="display:none" title="Finish the shape being drawn (Enter)">Finish (Enter)</button>
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
    <div id="layers"></div>
    <div id="legend"></div>
    <div id="help">Drag a handle to move it (1 m steps). Alt-drag a link triangle detaches it to a literal. Click a handle
      that is already selected to cycle through the handles stacked under it. A "+" at a segment middle (selected shape)
      or a double-click on a segment inserts a vertex; Delete removes the selected vertex, or the feature when the
      handle is not a vertex (a referenced feature asks first). Drag a radius or width grip to resize. Tools (V select,
      P POI, B blob, D band, G poly, L line, X crossing): click to place; shapes with several vertices end with Enter
      or a double-click, Backspace drops the last vertex, Esc cancels. Arrow keys nudge the selection (Shift: 10 m).
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
.handle.locked{pointer-events:none}
.handle.err .ring{display:inline}
.errmark{font-size:16px;font-weight:700;fill:#000;paint-order:stroke;stroke:#fff;stroke-width:3px;pointer-events:none}
.mid{cursor:copy}
button.mini{padding:0 6px;border-width:1px}
button.danger{margin-top:8px;border-style:dashed}
button[aria-pressed=true]{background:#0b1020;color:#fff}
.rowerr{font-size:12px;font-weight:700}
input.bad{outline:3px double #000}
.addrow{margin-top:4px;display:flex;gap:6px}
#prompt{border:3px double #000;padding:6px;margin:-2px}
#prompt ul{margin:4px 0;padding-left:18px;font-family:Consolas,monospace;font-size:12px}
#errors li[data-feature]{cursor:pointer}
#errors li.forsel{text-decoration:underline}
#layers table{border-collapse:collapse;font-size:12px}
#layers td,#layers th{padding:0 8px 0 0;text-align:left}
#panel select{max-width:15em}
#panel input[type=range]{width:8em;vertical-align:middle}
.handle .ring{display:none}
.handle.sel .ring,.handle:focus .ring{display:inline}
.handle:focus{outline:none}
.lbl{font-size:12px;paint-order:stroke;stroke:#fff;stroke-width:3px;fill:#000;pointer-events:none}
.seg{stroke:transparent;stroke-width:11;fill:none;cursor:copy}
.stale{opacity:.35}
</style>"""

_JS = r"""
var BOOT = JSON.parse(document.getElementById('boot').textContent);
var SCH = BOOT.schema;
var NS = 'http://www.w3.org/2000/svg';
var S = {token: BOOT.token, st: BOOT.state, sel: null, feat: null, geo: null, geoVersion: 0, imgVersion: 0, view: null, drag: null, pan: null,
         chain: Promise.resolve(), errors: [], errRefs: [], refused: false, abort: null, timer: null, url: null,
         tool: 'select', toolType: {}, draft: null, cursor: null, prompt: null, layers: loadLayers()};
var $ = function (id) { return document.getElementById(id); };
// drawing order of handle kinds (later = on top); the selected feature's handles are raised above all of them
var RANK = {center: 0, vertex: 0, waypoint: 1, crossing: 1, point: 2, poi: 3, base: 4, radius: 5, width: 5};
var DASH = ['none', '7 4', '2 4', '10 4 2 4', '5 3 1 3', '14 5'];
var LINE_DASH = {road: 'none', trail: '8 4', river: '14 3 2 3', cliff: '2 3'};
var LAYERS = [['regions', 'regions'], ['lines', 'lines'], ['crossings', 'crossings'], ['pois', 'POIs and bases'], ['points', 'named points']];
var TOOLS = [['select', 'Select', 'v'], ['poi', 'POI', 'p'], ['blob', 'Blob', 'b'], ['band', 'Band', 'd'], ['poly', 'Poly', 'g'], ['line', 'Line', 'l'], ['crossing', 'Crossing', 'x']];
var TOOL_TABLE = {poi: 'poi', blob: 'region', band: 'region', poly: 'region', line: 'line', crossing: 'crossing'};
var TOOL_DEFAULT = {poi: 'capture', blob: 'forest', band: 'field', poly: 'forest', line: 'road', crossing: 'bridge'};
var MIN_PTS = {band: 2, poly: 3, line: 2};

function loadLayers() {
  try { var t = localStorage.getItem('mapgen-layers:' + BOOT.state.path); return t ? JSON.parse(t) : {}; } catch (e) { return {}; }
}
function saveLayers() {
  try { localStorage.setItem('mapgen-layers:' + BOOT.state.path, JSON.stringify(S.layers)); } catch (e) { /* storage unavailable */ }
}
function layer(n) { return S.layers[n] || (S.layers[n] = {vis: true, lock: false}); }
function layerOf(h) {
  var p = h.path;
  if (p.indexOf('regions.') === 0) return 'regions';
  if (p.indexOf('lines.') === 0) return 'lines';
  if (p.indexOf('crossings.') === 0) return 'crossings';
  if (p.indexOf('points.') === 0) return 'points';
  return 'pois';
}

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
function featOf(id) { for (var i = 0; i < S.st.features.length; i++) if (S.st.features[i].id === id) return S.st.features[i]; return null; }
function setStatus(t) { $('status').textContent = t; }
function setPrev(t) { $('preview').textContent = t; }
function isVertexLike(h) { return !!h.chain && (h.kind === 'waypoint' || h.kind === 'vertex'); }
function dist(a, b) { return Math.sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1])); }
function fmt(n) { return String(Math.round(n * 100) / 100); }

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
  if (h.path === d.h.path) return d.pos;
  if (d.moveDef && d.target !== null && (h.ref === d.target || (isDef(h) && h.feature === d.target))) return d.to;
  return h.at;
}
function describe(h) {
  var t = {base: 'base', poi: 'POI', point: 'named point', waypoint: 'waypoint ' + h.index + ' of ' + h.line, center: 'region centre',
           vertex: 'vertex ' + h.index + ' of', crossing: 'crossing', radius: 'radius handle of', width: 'width handle of'}[h.kind];
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
  } else if (k === 'crossing') mk('polygon', {points: '-5,-9 5,-9 10,0 5,9 -5,9 -10,0', fill: '#fff', stroke: '#000', 'stroke-width': 2.5}, g);
  else if (k === 'radius') {
    mk('rect', {x: -11, y: -6, width: 22, height: 12, rx: 3, fill: '#fff', stroke: '#000', 'stroke-width': 2.5}, g);
    mk('path', {d: 'M-7 0H7M-7 0l3 -3M-7 0l3 3M7 0l-3 -3M7 0l-3 3', stroke: '#000', 'stroke-width': 1.6, fill: 'none'}, g);
  } else if (k === 'width') {
    mk('rect', {x: -6, y: -11, width: 12, height: 22, rx: 3, fill: '#000', stroke: '#fff', 'stroke-width': 2}, g);
    mk('path', {d: 'M0 -7V7M0 -7l-3 3M0 -7l3 3M0 7l-3 -3M0 7l3 -3', stroke: '#fff', 'stroke-width': 1.6, fill: 'none'}, g);
  } else mk('rect', {x: -5, y: -5, width: 10, height: 10, fill: '#fff', stroke: '#000', 'stroke-width': 2}, g);
}
function dashArr(d, mpp) { return d === 'none' ? 'none' : d.split(' ').map(function (x) { return x * mpp; }).join(' '); }
function chainsOf() {   // chain path -> its vertex handles in order
  var m = {};
  S.st.handles.forEach(function (h) { if (isVertexLike(h)) (m[h.chain] = m[h.chain] || []).push(h); });
  Object.keys(m).forEach(function (k) { m[k].sort(function (a, b) { return a.index - b.index; }); });
  return m;
}
function errFeatures() {
  var m = {};
  S.errRefs.forEach(function (r) { if (r && r.feature) m[r.feature] = true; });
  return m;
}
function render() {
  if (!S.view) return;
  applyView();
  var sz = size(), mpp = S.view.mpp, ov = $('ov'), dragging = !!(S.drag && S.drag.moved);
  var active = document.activeElement && document.activeElement.getAttribute ? document.activeElement.getAttribute('data-key') : null;
  if (active && S.sel && byKey(S.sel)) active = S.sel;   // keyboard focus follows the selection, never the other way round
  while (ov.firstChild) ov.removeChild(ov.firstChild);
  var fr = $('frame'); fr.setAttribute('width', sz[0]); fr.setAttribute('height', sz[1]);
  fr.setAttribute('stroke', '#000'); fr.setAttribute('stroke-width', 2 * mpp);
  var bg = $('bg'); bg.setAttribute('width', sz[0]); bg.setAttribute('height', sz[1]);
  bg.style.display = $('c-image').checked && S.url ? '' : 'none';
  var stale = S.geoVersion !== S.st.version || dragging;
  var gw = mk('g', {'stroke-linejoin': 'round', 'fill': 'none'}, ov);
  if (S.geo && $('c-outlines').checked && layer('regions').vis) {
    var types = {}, order = [];
    S.geo.regions.forEach(function (r) { if (!(r.type in types)) { types[r.type] = order.length; order.push(r.type); } });
    var gr = mk('g', {'class': stale ? 'stale' : '', 'pointer-events': 'none'}, gw);
    S.geo.regions.forEach(function (r) {
      var d = r.rings.map(function (ring) { return 'M' + ring.map(function (p) { return p[0] + ' ' + (sz[1] - p[1]); }).join('L') + 'Z'; }).join('');
      var dash = DASH[types[r.type] % DASH.length], on = r.id === S.feat;
      mk('path', {d: d, stroke: '#fff', 'stroke-width': (on ? 7 : 4) * mpp, 'stroke-opacity': .8}, gr);
      mk('path', {d: d, stroke: '#000', 'stroke-width': (on ? 2.6 : 1.6) * mpp, 'stroke-dasharray': dashArr(dash, mpp)}, gr);
    });
  }
  if (S.geo && layer('lines').vis) {
    var gl = mk('g', {'class': stale ? 'stale' : '', 'pointer-events': 'none'}, gw);
    S.geo.lines.forEach(function (l) {
      var d = 'M' + l.pts.map(function (p) { return p[0] + ' ' + (sz[1] - p[1]); }).join('L'), on = l.id === S.feat;
      mk('path', {d: d, stroke: '#fff', 'stroke-width': (on ? 8 : 5) * mpp, 'stroke-opacity': .85}, gl);
      mk('path', {d: d, stroke: '#000', 'stroke-width': (on ? 3 : 2) * mpp, 'stroke-dasharray': dashArr(LINE_DASH[l.type] || '6 4', mpp)}, gl);
    });
  }
  // control polylines (the author's values): every line, and the selected region/crossing; hit segments insert a vertex on double-click
  var chains = chainsOf(), gs = mk('g', {}, ov), gm = mk('g', {}, ov);
  Object.keys(chains).forEach(function (cp) {
    var hs = chains[cp], f = hs[0].feature, isLine = hs[0].kind === 'waypoint';
    if (!(isLine || f === S.feat) || !layer(layerOf(hs[0])).vis) return;
    var closed = hs[0].closed;
    var pts = hs.map(function (h) { var p = effPos(h); return [p[0], sz[1] - p[1]]; });
    var loop = closed ? pts.concat([pts[0]]) : pts;
    var str = loop.map(function (p) { return p.join(','); }).join(' ');
    mk('polyline', {points: str, fill: 'none', stroke: '#fff', 'stroke-width': 3 * mpp, 'stroke-dasharray': (6 * mpp) + ' ' + (4 * mpp), 'pointer-events': 'none'}, gs);
    mk('polyline', {points: str, fill: 'none', stroke: '#000', 'stroke-width': 1.2 * mpp, 'stroke-dasharray': (6 * mpp) + ' ' + (4 * mpp), 'pointer-events': 'none'}, gs);
    if (layer(layerOf(hs[0])).lock) return;
    for (var i = 0; i + 1 < loop.length; i++) {
      var ins = i + 1;
      mk('line', {'class': 'seg', x1: loop[i][0], y1: loop[i][1], x2: loop[i + 1][0], y2: loop[i + 1][1], 'stroke-width': 11 * mpp,
                  'data-chain': cp, 'data-ins': ins}, gs);
      if (f === S.feat) {   // midpoint "+" handle: one click inserts a vertex there
        var mx = (loop[i][0] + loop[i + 1][0]) / 2, my = (loop[i][1] + loop[i + 1][1]) / 2;
        var g = mk('g', {'class': 'mid', 'data-chain': cp, 'data-ins': ins, 'data-x': mx, 'data-y': sz[1] - my,
                         transform: 'translate(' + mx + ' ' + my + ') scale(' + mpp + ')'}, gm);
        mk('circle', {r: 6, fill: '#fff', stroke: '#000', 'stroke-width': 1.6}, g);
        mk('path', {d: 'M-3.5 0H3.5M0 -3.5V3.5', stroke: '#000', 'stroke-width': 1.8}, g);
        mk('title', {}, g, 'insert a vertex here');
      }
    }
  });
  // live radius / width feedback while dragging
  if (dragging && S.drag.h.kind === 'radius') {
    mk('circle', {cx: S.drag.h.center[0], cy: sz[1] - S.drag.h.center[1], r: S.drag.value, fill: 'none', stroke: '#000',
                  'stroke-width': 2 * mpp, 'stroke-dasharray': (6 * mpp) + ' ' + (4 * mpp), 'pointer-events': 'none'}, ov);
  }
  // the shape being drawn
  if (S.draft) {
    var dp = S.draft.pts.slice();
    var gd = mk('g', {'pointer-events': 'none'}, ov);
    var all = S.cursor ? dp.concat([S.cursor]) : dp;
    if (all.length > 1) {
      var ds = all.map(function (p) { return p[0] + ',' + (sz[1] - p[1]); }).join(' ');
      mk('polyline', {points: ds, fill: 'none', stroke: '#fff', 'stroke-width': 5 * mpp}, gd);
      mk('polyline', {points: ds, fill: 'none', stroke: '#000', 'stroke-width': 2 * mpp, 'stroke-dasharray': (8 * mpp) + ' ' + (4 * mpp)}, gd);
    }
    dp.forEach(function (p, i) {
      var g = mk('g', {transform: 'translate(' + p[0] + ' ' + (sz[1] - p[1]) + ') scale(' + mpp + ')'}, gd);
      mk('circle', {r: 6, fill: '#fff', stroke: '#000', 'stroke-width': 2.5}, g);
      mk('text', {'class': 'lbl', x: 9, y: -8}, g, String(i + 1));
    });
  }
  var gh = mk('g', {}, ov), ef = errFeatures();
  S.st.handles.filter(function (h) {
    if (!layer(layerOf(h)).vis) return false;
    return !((h.kind === 'radius' || h.kind === 'width') && h.feature !== S.feat);
  }).sort(function (a, b) {
    return (RANK[a.kind] + (a.feature === S.feat ? 10 : 0)) - (RANK[b.kind] + (b.feature === S.feat ? 10 : 0));
  }).forEach(function (h) {
    var p = effPos(h), sel = S.sel === h.path, locked = layer(layerOf(h)).lock || S.tool !== 'select';
    var g = mk('g', {'class': 'handle k-' + h.kind + (sel ? ' sel' : '') + (locked ? ' locked' : '') + (ef[h.feature] ? ' err' : ''),
                     'data-key': h.path, tabindex: locked ? -1 : 0, role: 'button',
                     'aria-label': describe(h) + ' at ' + fmt(p[0]) + ', ' + fmt(p[1]),
                     transform: 'translate(' + p[0] + ' ' + (sz[1] - p[1]) + ') scale(' + mpp + ')'}, gh);
    mk('circle', {'class': 'ring', r: 15, fill: 'none', stroke: '#fff', 'stroke-width': 5}, g);
    mk('circle', {'class': 'ring', r: 15, fill: 'none', stroke: '#000', 'stroke-width': 2.2, 'stroke-dasharray': '4 3'}, g);
    shape(g, h);
    if (ef[h.feature] && h.path === (featOf(h.feature) || {}).handle) mk('text', {'class': 'errmark', x: -4, y: -14}, g, '!');
    if (isDef(h) || sel) mk('text', {'class': 'lbl', x: 12, y: -10}, g, h.kind === 'waypoint' ? h.line + '[' + h.index + ']' : h.feature);
  });
  if (active) {
    var e = ov.querySelector('[data-key="' + active.replace(/"/g, '\\"') + '"]');
    if (e && document.activeElement !== e) e.focus({preventScroll: true});
  }
}

// ---- selection -----------------------------------------------------------------------------------------------------
function select(key) {   // by handle path
  S.sel = key;
  var h = key ? byKey(key) : null;
  S.feat = h ? h.feature : null;
  S.prompt = null;
  updateAll(); render();
}
function selectFeature(id) {
  var f = id ? featOf(id) : null;
  S.feat = f ? f.id : null;
  S.sel = f && f.handle ? f.handle : null;
  S.prompt = null;
  updateAll(); render();
}

// ---- panel ---------------------------------------------------------------------------------------------------------
function refsOf(h) {
  var name = h.ref || (isDef(h) ? h.feature : null);
  return name === null ? [] : (S.st.referrers[name] || []).filter(function (r) { return r.path !== h.path; }).map(function (r) { return r.path; });
}
function kindKey(f) { return f.kind === 'region' ? 'region_' + f.shape : f.kind; }
function typeTable(f) { return SCH.types[f.kind === 'base' ? 'poi' : f.kind]; }
function typeLabel(name, row) {
  var bits = [];
  if (row && row.cost !== undefined) bits.push('cost ' + row.cost);
  if (row && row.width !== undefined) bits.push('width ' + row.width);
  if (row && row.r !== undefined) bits.push('r ' + row.r);
  if (row && row.splat) bits.push(row.splat);
  return name + (bits.length ? '  (' + bits.join(', ') + ')' : '');
}
function fillTypeSelect(sel, table, current) {
  Object.keys(table).forEach(function (n) {
    var o = el('option', {value: n}, sel, typeLabel(n, table[n]));
    if (n === current) o.selected = true;
  });
}
function putOp(path, value) { return {op: 'put', path: path, value: value}; }
function propRow(tb, f, key, required) {
  var hint = SCH.props[key] || {t: 'text'}, val = f.props[key], path = f.section + '.' + f.id + '.' + key;
  var tr = el('tr', {}, tb), lab = el('td', {}, tr, key);
  var errs = S.errRefs.map(function (r, i) { return r && r.feature === f.id && r.key === key ? S.errors[i] : null; }).filter(Boolean);
  var cell = el('td', {}, tr), inp;
  if (hint.t === 'bool') {
    inp = el('input', {type: 'checkbox', id: 'p-' + key, 'aria-label': key}, cell);
    inp.checked = val === true;
    inp.addEventListener('change', function () { commit([putOp(path, inp.checked)]); });
  } else if (hint.t === 'enum' || hint.t === 'line' || hint.t === 'team') {
    inp = el('select', {id: 'p-' + key, 'aria-label': key}, cell);
    var opts = hint.t === 'enum' ? hint.values : hint.t === 'team' ? Object.keys(S.st.features.filter(function (x) { return x.kind === 'base'; }).reduce(function (m, x) { m[x.id.slice(5)] = 1; return m; }, {}))
      : S.st.features.filter(function (x) { return x.kind === 'line' && x.id !== f.id; }).map(function (x) { return x.id; });
    opts.forEach(function (o) { var oe = el('option', {value: o}, inp, o); if (o === val) oe.selected = true; });
    inp.addEventListener('change', function () { commit([putOp(path, inp.value)]); });
  } else if (hint.t === 'num' || hint.t === 'int') {
    var attrs = {type: 'number', id: 'p-' + key, step: hint.step || 1, value: val, 'aria-label': key};
    if (hint.min !== undefined) attrs.min = hint.min;
    if (hint.max !== undefined) attrs.max = hint.max;
    if (hint.min !== undefined && hint.max !== undefined && hint.t === 'num') {
      var rng = el('input', {type: 'range', min: hint.min, max: hint.max, step: hint.step || 0.05, value: val, id: 'r-' + key, 'aria-label': key + ' slider'}, cell);
      inp = el('input', attrs, cell);
      rng.addEventListener('input', function () { inp.value = rng.value; });
      rng.addEventListener('change', function () { commit([putOp(path, Number(rng.value))]); });
    } else inp = el('input', attrs, cell);
    inp.addEventListener('change', function () {
      var n = Number(inp.value);
      if (inp.value !== '' && isFinite(n)) commit([putOp(path, hint.t === 'int' ? Math.round(n) : n)]);
    });
  } else if (typeof val === 'object' && val !== null) {
    el('span', {}, cell, JSON.stringify(val) + ' (edit in the file)');
  } else {
    inp = el('input', {type: 'text', id: 'p-' + key, value: val === null || val === undefined ? '' : val, 'aria-label': key}, cell);
    inp.addEventListener('change', function () { commit([putOp(path, inp.value)]); });
  }
  if (errs.length && inp) { inp.setAttribute('aria-invalid', 'true'); inp.className = 'bad'; }
  if (!required) {
    var x = el('button', {'class': 'mini', title: 'remove ' + key + ' (the default applies)', 'aria-label': 'remove ' + key}, el('td', {}, tr), '×');
    x.addEventListener('click', function () { commit([{op: 'delete', path: path}]); });
  }
  errs.forEach(function (m) { el('div', {'class': 'rowerr'}, cell, '✖ ' + m); });
}
function buildPanel() {
  var p = $('panel');
  while (p.firstChild) p.removeChild(p.firstChild);
  if (S.prompt) {
    var pr = el('div', {id: 'prompt', role: 'alertdialog', 'aria-label': 'referenced feature'}, p);
    el('h2', {}, pr, S.prompt.name + ' is referenced');
    el('div', {}, pr, 'These features use it:');
    var ul = el('ul', {}, pr);
    S.prompt.referrers.forEach(function (r) { el('li', {}, ul, r.path); });
    var b1 = el('button', {id: 'b-detach-delete', 'class': 'primary'}, pr, 'Detach references and delete');
    el('div', {'class': 'refs'}, pr, 'Point references become literal coordinates; a `join` is removed.');
    var b2 = el('button', {id: 'b-cancel-delete'}, pr, 'Cancel');
    b1.addEventListener('click', function () {
      var ops = S.prompt.ops.map(function (o) { return o.op === 'delete' ? {op: 'delete', path: o.path, detach_refs: true} : o; });
      S.prompt = null; commit(ops, null);
    });
    b2.addEventListener('click', function () { S.prompt = null; updateAll(); });
    return;
  }
  var f = S.feat ? featOf(S.feat) : null;
  var h = S.sel ? byKey(S.sel) : null;
  if (!f) { el('div', {}, p, 'Nothing selected. Click a handle, pick a feature in the list, or choose a tool to add one.'); return; }
  el('h2', {}, p, f.kind + ' ' + f.id + (f.type && f.kind !== 'point' ? '  (' + f.type + (f.shape ? ', ' + f.shape : '') + ')' : ''));
  var fe = S.errRefs.map(function (r, i) { return r && r.feature === f.id && !r.key ? S.errors[i] : null; }).filter(Boolean);
  fe.forEach(function (m) { el('div', {'class': 'rowerr'}, p, '✖ ' + m); });
  var base = f.kind === 'base';
  // id, type
  var tb0 = el('table', {}, p);
  if (!base) {
    var r1 = el('tr', {}, tb0); el('td', {}, r1, 'id');
    var idin = el('input', {type: 'text', id: 'in-id', value: f.id, 'aria-label': 'feature id', spellcheck: 'false'}, el('td', {}, r1));
    var rb = el('button', {id: 'b-rename', 'class': 'mini'}, el('td', {}, r1), 'Rename');
    var doRename = function () {
      var n = idin.value.trim();
      if (n && n !== f.id) commit([{op: 'rename', from: f.id, to: n}], function () { selectFeature(n); });
    };
    rb.addEventListener('click', doRename);
    idin.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') doRename(); });
  }
  if (f.type && f.kind !== 'point' && f.kind !== 'base') {
    var r2 = el('tr', {}, tb0); el('td', {}, r2, 'type');
    var ts = el('select', {id: 'in-type', 'aria-label': 'type'}, el('td', {colspan: 2}, r2));
    var table = typeTable(f);
    if (!(f.type in table)) el('option', {value: f.type}, ts, f.type);
    fillTypeSelect(ts, table, f.type);
    ts.addEventListener('change', function () { commit([putOp(f.section + '.' + f.id + '.type', ts.value)]); });
  }
  // the selected handle's coordinates
  if (h && !(h.kind === 'radius' || h.kind === 'width')) {
    el('div', {}, p, 'handle: ' + h.path);
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
    if (isVertexLike(h)) {
      var bx = el('button', {id: 'b-del'}, p, 'Delete vertex');
      if (h.count <= h.minimum) { bx.disabled = true; bx.title = 'needs at least ' + h.minimum + ' vertices'; }
      bx.addEventListener('click', delVertex);
    }
  }
  // properties from the schema's allowed keys
  if (f.section !== 'teams' && f.section !== 'points') {
    var sch = SCH.keys[kindKey(f)], skip = {};
    SCH.structural.forEach(function (k) { skip[k] = true; });
    var keys = sch.allowed.filter(function (k) { return !skip[k]; });
    el('h2', {}, p, 'Properties');
    var pt = el('table', {id: 'props'}, p);
    keys.forEach(function (k) { if (k in f.props) propRow(pt, f, k, sch.required.indexOf(k) >= 0); });
    var absent = keys.filter(function (k) { return !(k in f.props) && SCH.props[k]; });
    if (absent.length) {
      var ar = el('div', {'class': 'addrow'}, p);
      var asel = el('select', {id: 'add-prop', 'aria-label': 'property to add'}, ar);
      absent.forEach(function (k) { el('option', {value: k}, asel, k); });
      var ab = el('button', {id: 'b-add-prop', 'class': 'mini'}, ar, 'Add property');
      ab.addEventListener('click', function () {
        var k = asel.value, hint = SCH.props[k], v = hint.default;
        if (hint.t === 'line') { var ls = S.st.features.filter(function (x) { return x.kind === 'line' && x.id !== f.id; }); v = ls.length ? ls[0].id : null; }
        if (hint.t === 'team') { var tm = S.st.features.filter(function (x) { return x.kind === 'base'; }); v = tm.length ? tm[0].id.slice(5) : null; }
        if (v === null || v === undefined) { setStatus('Nothing to choose for ' + k + '.'); return; }
        commit([putOp(f.section + '.' + f.id + '.' + k, v)]);
      });
    }
  }
  if (f.kind !== 'base') {
    var bf = el('button', {id: 'b-del-feature', 'class': 'danger'}, p, 'Delete ' + f.kind);
    bf.addEventListener('click', function () { delFeature(f.id); });
  }
}
function updateAll() {
  var st = S.st;
  $('dirty').textContent = st.dirty ? '● UNSAVED CHANGES' : '○ saved';
  document.title = (st.dirty ? '* ' : '') + st.path.split(/[\\/]/).pop() + ' - spec editor';
  $('path').textContent = st.path;
  $('b-undo').disabled = !st.can_undo; $('b-redo').disabled = !st.can_redo; $('b-save').disabled = !st.dirty;
  if (S.sel && !byKey(S.sel)) S.sel = null;
  if (S.feat && !featOf(S.feat)) { S.feat = null; S.sel = null; }
  var pick = $('pick');
  while (pick.firstChild) pick.removeChild(pick.firstChild);
  st.features.forEach(function (f) {
    var o = el('option', {value: f.id}, pick, f.kind + ' ' + f.id + (f.type && f.kind !== 'point' && f.kind !== 'base' ? '  (' + f.type + ')' : ''));
    if (f.id === S.feat) o.selected = true;
  });
  var eh = $('errhead'), ul = $('errors');
  while (ul.firstChild) ul.removeChild(ul.firstChild);
  S.errors.forEach(function (m, i) {
    var r = S.errRefs[i], cls = (i === 0 && S.refused ? 'refused ' : '') + (r && r.feature && r.feature === S.feat ? 'forsel' : '');
    var li = el('li', {'class': cls}, ul, m);
    if (r && r.feature) {
      li.setAttribute('data-feature', r.feature); li.setAttribute('tabindex', '0'); li.setAttribute('role', 'button');
      li.title = 'select ' + r.feature;
    }
  });
  eh.textContent = S.errors.length ? 'Loader: last edit refused (' + S.errors.length + ' error' + (S.errors.length > 1 ? 's' : '') + ')' : 'Loader: no errors';
  eh.className = S.errors.length ? 'bad' : 'ok';
  buildPanel();
  document.querySelectorAll('#tools button').forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-tool') === S.tool ? 'true' : 'false'); });
  $('tool-type').style.display = S.tool === 'select' ? 'none' : '';
  $('finish').style.display = S.draft ? '' : 'none';
}

// ---- edits ---------------------------------------------------------------------------------------------------------
function commit(ops, after) {
  S.chain = S.chain.then(function () {
    return api('/api/apply', {version: S.st.version, ops: ops}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) {
        S.st = j.state; S.errors = []; S.errRefs = []; S.refused = false; S.prompt = null;
        setStatus('Edit applied (version ' + S.st.version + ').');
        if (typeof after === 'function') after(j);
        else if (after !== undefined) { S.sel = after; var hh = after ? byKey(after) : null; S.feat = hh ? hh.feature : (after === null ? null : S.feat); }
      } else if (j.referenced) {
        if (j.state) S.st = j.state;
        S.prompt = {name: j.referenced, referrers: j.referrers, ops: ops}; S.errors = []; S.errRefs = []; S.refused = false;
        setStatus(j.referenced + ' is referenced by ' + j.referrers.length + ' place' + (j.referrers.length === 1 ? '' : 's') + '.');
      } else {
        S.errors = j.errors || ['request failed']; S.errRefs = j.error_refs || []; S.refused = true; if (j.state) S.st = j.state;
        setStatus('Edit refused: ' + S.errors[0]);
        var first = S.errRefs.filter(function (r) { return r && r.feature && featOf(r.feature); })[0];
        if (first && !(S.feat && S.errRefs.some(function (r) { return r && r.feature === S.feat; }))) { S.feat = first.feature; var ff = featOf(first.feature); S.sel = ff ? ff.handle : null; }
      }
      changed();
    });
  }).catch(function (e) { setStatus('Request failed: ' + e); });
  return S.chain;
}
function simple(path) {
  S.chain = S.chain.then(function () {
    return api(path, {}).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) { S.st = j.state; S.errors = []; S.errRefs = []; S.refused = false; S.prompt = null; if (path === '/api/save') setStatus('Saved ' + j.bytes + ' bytes to ' + j.path); else setStatus(path.slice(5) + ' done.'); }
      else { S.errors = j.errors || []; S.errRefs = []; S.refused = true; if (j.state) S.st = j.state; setStatus(path.slice(5) + ' refused: ' + S.errors[0]); }
      changed();
    });
  }).catch(function (e) { setStatus('Request failed: ' + e); });
}
function changed() {
  updateAll(); render();
  if (S.geoVersion !== S.st.version) schedule();
}
function featPath(f) { return f.section + '.' + f.id; }
function delFeature(id) {
  var f = featOf(id);
  if (!f || f.kind === 'base') return;
  commit([{op: 'delete', path: featPath(f)}], null);
}
function delVertex() {
  var h = S.sel ? byKey(S.sel) : null;
  if (!h || !isVertexLike(h)) return;
  if (h.count <= h.minimum) { setStatus('A ' + h.chain.split('.')[0].replace(/s$/, '') + ' needs at least ' + h.minimum + ' vertices; delete the feature instead.'); return; }
  commit([{op: 'delete', path: h.path}], h.chain + '[' + Math.max(0, h.index - 1) + ']');
}
function deleteSelection() {
  var h = S.sel ? byKey(S.sel) : null;
  if (h && isVertexLike(h)) { delVertex(); return; }
  if (S.feat) delFeature(S.feat);
}
function nudge(dx, dy) {
  var h = S.sel ? byKey(S.sel) : null;
  if (!h || h.kind === 'radius' || h.kind === 'width') return false;
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

// ---- tools: add a feature by clicking ------------------------------------------------------------------------------
function clampPt(p) { var sz = size(); return [Math.min(Math.max(Math.round(p[0]), 0), sz[0]), Math.min(Math.max(Math.round(p[1]), 0), sz[1])]; }
function setTool(t) {
  S.tool = t; S.draft = null; S.cursor = null;
  if (t !== 'select') {
    var tt = $('tool-type');
    while (tt.firstChild) tt.removeChild(tt.firstChild);
    var table = SCH.types[TOOL_TABLE[t]];
    var cur = S.toolType[t] || TOOL_DEFAULT[t];
    if (!(cur in table)) cur = Object.keys(table)[0];
    fillTypeSelect(tt, table, cur);
    S.toolType[t] = cur;
    S.sel = null; S.feat = null;
    setStatus(toolHelp(t));
  } else setStatus('Select tool.');
  updateAll(); render();
}
function toolHelp(t) {
  if (t === 'poi' || t === 'crossing' || t === 'blob') return t + ' tool: click the map to place it.';
  return t + ' tool: click vertices (at least ' + MIN_PTS[t] + '), Enter or double-click finishes, Backspace removes the last, Esc cancels.';
}
function addFeature(value, section) {
  commit([{op: 'add', section: section, value: value}], function (j) {
    var r = j.state.results && j.state.results[0];
    S.tool = 'select'; S.draft = null; S.cursor = null;
    if (r && r.id) { S.st = j.state; selectFeature(r.id); setStatus('Added ' + r.id + '.'); }
  });
}
function toolClick(pt) {
  var t = S.tool, type = $('tool-type').value;
  S.toolType[t] = type;
  var sz = size();
  if (t === 'poi') return addFeature({type: type, at: pt}, 'pois');
  if (t === 'crossing') return addFeature({type: type, at: pt}, 'crossings');
  if (t === 'blob') return addFeature({type: type, shape: 'blob', at: pt, r: Math.max(10, Math.round(Math.min(sz[0], sz[1]) / 20)), rough: 0.3}, 'regions');
  if (!S.draft) S.draft = {pts: []};
  var last = S.draft.pts[S.draft.pts.length - 1];
  if (!last || last[0] !== pt[0] || last[1] !== pt[1]) S.draft.pts.push(pt);
  setStatus(t + ': ' + S.draft.pts.length + ' vertex' + (S.draft.pts.length === 1 ? '' : 'es') + ' (Enter or double-click to finish).');
  updateAll(); render();
}
function finishDraft() {
  var d = S.draft, t = S.tool;
  if (!d) return;
  var pts = d.pts.filter(function (p, i) { return i === 0 || p[0] !== d.pts[i - 1][0] || p[1] !== d.pts[i - 1][1]; });
  if (pts.length < MIN_PTS[t]) { setStatus(t + ' needs at least ' + MIN_PTS[t] + ' vertices (have ' + pts.length + ').'); return; }
  var type = $('tool-type').value;
  if (t === 'line') return addFeature({type: type, path: pts}, 'lines');
  if (t === 'band') return addFeature({type: type, shape: 'band', path: pts, width: 30}, 'regions');
  return addFeature({type: type, shape: 'poly', points: pts}, 'regions');
}

// ---- mouse and keyboard --------------------------------------------------------------------------------------------
function handleStack(ev) {   // the handles under the pointer, topmost first
  var out = [];
  (document.elementsFromPoint ? document.elementsFromPoint(ev.clientX, ev.clientY) : [ev.target]).forEach(function (e) {
    var g = e.closest ? e.closest('.handle') : null;
    if (g && !g.classList.contains('locked') && out.indexOf(g.getAttribute('data-key')) < 0) out.push(g.getAttribute('data-key'));
  });
  return out;
}
$('map').addEventListener('mousedown', function (ev) {
  if (ev.button !== 0 && ev.button !== 1) return;
  var mid = ev.target.closest ? ev.target.closest('.mid') : null;
  if (mid && S.tool === 'select') {
    ev.preventDefault();
    var cp = mid.getAttribute('data-chain'), at = Number(mid.getAttribute('data-ins'));
    var q = clampPt([Number(mid.getAttribute('data-x')), Number(mid.getAttribute('data-y'))]);   // metres
    commit([{op: 'insert', path: cp, index: at, value: q}], cp + '[' + at + ']');
    return;
  }
  var t = S.tool === 'select' && ev.target.closest ? ev.target.closest('.handle') : null;
  if (t && !t.classList.contains('locked')) {
    var stack = handleStack(ev), key = t.getAttribute('data-key');
    if (S.sel && stack.indexOf(S.sel) >= 0) key = S.sel;   // a selected handle in a stack is the one that moves
    var h = byKey(key);
    if (!h) return;
    ev.preventDefault();
    var wasSel = S.sel === h.path;
    if (!wasSel) select(h.path);
    var m = toMetres(ev);
    S.drag = {h: h, startX: ev.clientX, startY: ev.clientY, off: [h.at[0] - m[0], h.at[1] - m[1]], moved: false, to: h.at, pos: h.at, alt: false,
              target: h.ref !== null ? h.ref : (isDef(h) ? h.feature : null), moveDef: false, stack: stack, wasSel: wasSel, value: null};
    var e = $('ov').querySelector('[data-key="' + h.path.replace(/"/g, '\\"') + '"]');
    if (e) e.focus({preventScroll: true});
    return;
  }
  if (S.tool === 'select' && ev.target.classList && ev.target.classList.contains('seg')) return;
  ev.preventDefault();
  S.pan = {x: ev.clientX, y: ev.clientY, cx: S.view.cx, cy: S.view.cy, moved: false};
  $('map').classList.add('panning');
  $('map').focus({preventScroll: true});
});
function dragValue(d, m) {   // radius / width drags
  var h = d.h;
  if (h.kind === 'radius') {
    var r = Math.max(1, Math.round(dist(h.center, m)));
    d.value = r;
    var L = dist(h.center, m) || 1;
    d.pos = [h.center[0] + (m[0] - h.center[0]) / L * r, h.center[1] + (m[1] - h.center[1]) / L * r];
    return 'radius ' + r + ' m';
  }
  var ch = chainsOf()[h.chain] || [], best = Infinity;
  for (var i = 0; i + 1 < ch.length; i++) {
    var a = ch[i].at, b = ch[i + 1].at, dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    var u = L2 > 0 ? Math.min(1, Math.max(0, ((m[0] - a[0]) * dx + (m[1] - a[1]) * dy) / L2)) : 0;
    best = Math.min(best, dist(m, [a[0] + u * dx, a[1] + u * dy]));
  }
  var w = Math.max(1, Math.round(2 * best));
  d.value = w; d.pos = m;
  return 'width ' + w + ' m';
}
window.addEventListener('mousemove', function (ev) {
  var d = S.drag;
  if (d) {
    if (!d.moved && Math.abs(ev.clientX - d.startX) + Math.abs(ev.clientY - d.startY) < 4) return;
    d.moved = true; d.alt = ev.altKey;
    var m = toMetres(ev);
    if (d.h.kind === 'radius' || d.h.kind === 'width') {
      setStatus('dragging ' + dragValue(d, m));
    } else {
      d.moveDef = d.target !== null && !(d.alt && d.h.ref !== null);
      d.to = clampPt([m[0] + d.off[0], m[1] + d.off[1]]); d.pos = d.to;
      var what = d.h.ref && d.alt ? 'detaching' : (d.target !== null ? 'moving definition of ' + d.target : 'moving');
      setStatus(what + ' to ' + d.to[0] + ', ' + d.to[1]);
    }
    render();
  } else if (S.pan) {
    var p = S.pan;
    if (Math.abs(ev.clientX - p.x) + Math.abs(ev.clientY - p.y) > 3) p.moved = true;
    S.view.cx = p.cx - (ev.clientX - p.x) * S.view.mpp; S.view.cy = p.cy - (ev.clientY - p.y) * S.view.mpp;
    render();
  } else if (S.draft) {
    S.cursor = clampPt(toMetres(ev)); render();
  }
});
window.addEventListener('mouseup', function (ev) {
  var d = S.drag;
  if (d) {
    S.drag = null;
    var h = d.h;
    if (d.moved && (h.kind === 'radius' || h.kind === 'width')) {
      var cur = h.kind === 'radius' ? Math.round(dist(h.center, h.at)) : h.value;
      if (d.value !== null && d.value !== cur) commit([putOp(h.path, d.value)], h.path); else render();
    } else if (d.moved && (d.to[0] !== h.at[0] || d.to[1] !== h.at[1])) {
      commit([{op: 'set', path: h.path, value: d.to, detach: !!(ev.altKey && h.ref !== null)}], h.path);
    } else {
      if (!d.moved && d.wasSel && d.stack.length > 1) {   // click again on a selected stack: next handle underneath
        var i = d.stack.indexOf(h.path);
        select(d.stack[(i + 1) % d.stack.length]);
        setStatus('Selected ' + S.sel + ' (' + d.stack.length + ' handles here; click again to cycle).');
      } else render();
    }
  } else if (S.pan) {
    var moved = S.pan.moved; S.pan = null;
    $('map').classList.remove('panning');
    if (!moved) {
      if (S.tool !== 'select') toolClick(clampPt(toMetres(ev)));
      else select(null);
    }
  }
});
$('map').addEventListener('wheel', function (ev) { ev.preventDefault(); zoomAt(ev, Math.exp(Math.max(-400, Math.min(400, ev.deltaY)) * 0.0015)); }, {passive: false});
$('map').addEventListener('dblclick', function (ev) {
  if (S.draft) { finishDraft(); return; }
  var t = ev.target;
  if (S.tool !== 'select' || !t.classList || !t.classList.contains('seg')) return;
  var cp = t.getAttribute('data-chain'), at = Number(t.getAttribute('data-ins'));
  var ch = chainsOf()[cp] || [], n = ch.length;
  var a = ch[at - 1], b = ch[at % n];
  var m = toMetres(ev), q = m;
  if (a && b) {   // project onto the segment
    var dx = b.at[0] - a.at[0], dy = b.at[1] - a.at[1], L = dx * dx + dy * dy;
    var u = L > 0 ? Math.min(1, Math.max(0, ((m[0] - a.at[0]) * dx + (m[1] - a.at[1]) * dy) / L)) : 0;
    q = [a.at[0] + u * dx, a.at[1] + u * dy];
  }
  commit([{op: 'insert', path: cp, index: at, value: clampPt(q)}], cp + '[' + at + ']');
});
$('map').addEventListener('focusin', function (ev) {
  var t = ev.target.closest ? ev.target.closest('.handle') : null;
  if (t && !S.drag && S.sel !== t.getAttribute('data-key')) { S.sel = t.getAttribute('data-key'); var h = byKey(S.sel); S.feat = h ? h.feature : null; updateAll(); render(); }
});
$('pick').addEventListener('change', function () {
  var f = featOf($('pick').value);
  if (!f) return;
  selectFeature(f.id);
  var h = f.handle ? byKey(f.handle) : null;
  if (!h) return;
  var r = $('map').getBoundingClientRect(), v = S.view, sz = size();
  var X = h.at[0], Y = sz[1] - h.at[1];
  if (Math.abs(X - v.cx) > r.width * v.mpp * 0.45 || Math.abs(Y - v.cy) > r.height * v.mpp * 0.45) { v.cx = X; v.cy = Y; render(); }
});
$('errors').addEventListener('click', function (ev) {
  var li = ev.target.closest ? ev.target.closest('li[data-feature]') : null;
  if (li) selectFeature(li.getAttribute('data-feature'));
});
document.addEventListener('keydown', function (ev) {
  var tag = ev.target && ev.target.tagName;
  var inField = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
  var k = ev.key;
  if ((ev.ctrlKey || ev.metaKey) && (k === 'z' || k === 'Z') && !ev.shiftKey) { if (!inField) { ev.preventDefault(); simple('/api/undo'); } return; }
  if ((ev.ctrlKey || ev.metaKey) && (k === 'y' || k === 'Y' || ((k === 'z' || k === 'Z') && ev.shiftKey))) { if (!inField) { ev.preventDefault(); simple('/api/redo'); } return; }
  if ((ev.ctrlKey || ev.metaKey) && (k === 's' || k === 'S')) { ev.preventDefault(); simple('/api/save'); return; }
  if (inField) return;
  if (S.draft) {
    if (k === 'Enter') { ev.preventDefault(); finishDraft(); return; }
    if (k === 'Escape') { S.draft = null; S.cursor = null; setStatus('Drawing cancelled.'); updateAll(); render(); return; }
    if (k === 'Backspace' || k === 'Delete') { ev.preventDefault(); S.draft.pts.pop(); if (!S.draft.pts.length) S.draft = null; updateAll(); render(); return; }
  }
  if (k === 'Escape') {
    if (S.tool !== 'select') { setTool('select'); return; }
    S.drag = null; S.sel = null; S.feat = null; S.prompt = null; updateAll(); render(); return;
  }
  if (k === 'Delete' || k === 'Backspace') { if (S.sel || S.feat) { ev.preventDefault(); deleteSelection(); } return; }
  if (!ev.ctrlKey && !ev.metaKey && !ev.altKey) {
    for (var i = 0; i < TOOLS.length; i++) if (k === TOOLS[i][2]) { setTool(TOOLS[i][0]); return; }
  }
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
$('finish').addEventListener('click', finishDraft);
$('tool-type').addEventListener('change', function () { S.toolType[S.tool] = $('tool-type').value; });
$('c-outlines').addEventListener('change', render);
$('c-image').addEventListener('change', render);
window.addEventListener('resize', render);
window.addEventListener('beforeunload', function (ev) { if (S.st.dirty) { ev.preventDefault(); ev.returnValue = ''; } });

function buildTools() {
  var tl = $('tools');
  TOOLS.forEach(function (t) {
    var b = el('button', {'data-tool': t[0], id: 't-' + t[0], title: t[1] + ' tool (' + t[2].toUpperCase() + ')', 'aria-pressed': t[0] === 'select' ? 'true' : 'false'}, tl, t[1]);
    b.addEventListener('click', function () { setTool(t[0]); });
  });
}
function buildLayers() {
  var lw = $('layers');
  el('h2', {}, lw, 'Layers');
  var tb = el('table', {}, lw);
  var hr = el('tr', {}, tb); el('th', {}, hr, ''); el('th', {}, hr, 'show'); el('th', {}, hr, 'lock');
  LAYERS.forEach(function (l) {
    var tr = el('tr', {}, tb); el('td', {}, tr, l[1]);
    var a = el('input', {type: 'checkbox', id: 'l-vis-' + l[0], 'aria-label': 'show ' + l[1]}, el('td', {}, tr)); a.checked = layer(l[0]).vis;
    var b = el('input', {type: 'checkbox', id: 'l-lock-' + l[0], 'aria-label': 'lock ' + l[1]}, el('td', {}, tr)); b.checked = layer(l[0]).lock;
    a.addEventListener('change', function () { layer(l[0]).vis = a.checked; saveLayers(); render(); });
    b.addEventListener('change', function () { layer(l[0]).lock = b.checked; saveLayers(); render(); });
  });
}
function legend() {
  var lg = $('legend');
  el('h2', {}, lg, 'Handles');
  [['poi', 'POI (circle)'], ['base', 'team base (square)'], ['point', 'named point (diamond)'], ['waypoint', 'literal waypoint (dot)'],
   ['wpref', 'waypoint that references a point (triangle)'], ['center', 'region centre (cross)'], ['vertex', 'region vertex (hollow square)'],
   ['crossing', 'crossing (hexagon)'], ['radius', 'radius grip (wide bar, arrows)'], ['width', 'width grip (tall bar, arrows)']].forEach(function (x) {
    var d = el('div', {}, lg), svg = mk('svg', {width: 28, height: 26, viewBox: '-14 -13 28 26'}, null);
    d.appendChild(svg);
    var g = mk('g', {}, svg);
    shape(g, x[0] === 'wpref' ? {kind: 'waypoint', ref: 'x'} : {kind: x[0], ref: null});
    el('span', {}, d, x[1]);
  });
}
buildTools(); buildLayers(); legend();
fit(); updateAll(); render(); refresh();
setStatus('Loaded ' + S.st.path);
"""
