"""Flowgraph preview: one self-contained HTML file (inline SVG + vanilla JS) drawing a graph (design: mapgen/FLOWGRAPH_SPEC.md section 5)."""
from __future__ import annotations

import math
from html import escape
from typing import Dict, List, Tuple

from .flowgraph import KINDS, Flowgraph
from .flowgraph_layout import layout, solved_extent

R = 28  # node radius in px
# kind -> (shape, fill, stroke dash). Fills differ in lightness (dark -> light), shapes differ; hue is never the cue.
STYLE = {
    "spawn": ("square", "#14213d", ""),
    "objective": ("star", "#3a4f7a", ""),
    "zone": ("hexagon", "#7d8fb5", ""),
    "choke": ("diamond", "#bcc6dc", ""),
    "area": ("circle", "#f2f4f8", "5 3"),
}
LABELS = {"spawn": "Spawn", "objective": "Objective", "zone": "Control zone", "choke": "Chokepoint", "area": "Area"}
_TEXT_ON = {"spawn": "#ffffff", "objective": "#ffffff", "zone": "#0b1020", "choke": "#0b1020", "area": "#0b1020"}


def _pts(points: List[Tuple[float, float]]) -> str:
    return " ".join("%.1f,%.1f" % p for p in points)


def shape_svg(kind: str, cx: float, cy: float, r: float = R) -> str:
    """Marker element for a node kind, centred at (cx, cy)."""
    shape, fill, dash = STYLE[kind]
    attrs = 'fill="%s" stroke="#0b1020" stroke-width="2.5"%s' % (fill, ' stroke-dasharray="%s"' % dash if dash else "")
    if shape == "square":
        return '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" %s/>' % (cx - r * 0.9, cy - r * 0.9, r * 1.8, r * 1.8, attrs)
    if shape == "circle":
        return '<circle cx="%.1f" cy="%.1f" r="%.1f" %s/>' % (cx, cy, r, attrs)
    if shape == "diamond":
        k = r * 1.2
        return '<polygon points="%s" %s/>' % (_pts([(cx, cy - k), (cx + k, cy), (cx, cy + k), (cx - k, cy)]), attrs)
    if shape == "hexagon":
        k = r * 1.1
        return '<polygon points="%s" %s/>' % (_pts([(cx + k * math.cos(math.pi / 3 * i), cy + k * math.sin(math.pi / 3 * i)) for i in range(6)]), attrs)
    k = r * 1.3  # star
    return '<polygon points="%s" %s/>' % (_pts([(cx + (k if i % 2 == 0 else k * 0.45) * math.sin(math.pi / 5 * i),
                                                  cy - (k if i % 2 == 0 else k * 0.45) * math.cos(math.pi / 5 * i)) for i in range(10)]), attrs)


def _canvas(g: Flowgraph) -> Tuple[int, int, Dict[str, Tuple[float, float]]]:
    """Canvas size from the stress solution's extent (EDGE_PX per edge length, 10 % margins), and the layout on it."""
    ex, ey = solved_extent(g)
    w = int(max(900, EDGE_PX * ex / 0.8))
    h = int(max(560, EDGE_PX * ey / 0.8))
    return w, h, {k: (x, h - y) for k, (x, y) in layout(g, w, h).items()}  # layout y points north; the screen's y points down


EDGE_PX = 300  # drawn length of one graph edge, pixels


PAD = 70
LINE_H = 18
CHAR_W = 8.4


def _label_box(n, x: float, y: float, side: str) -> Tuple[float, float, float, float]:
    """Estimated label rectangle (x0, y0, x1, y1) for a node label on one side of its marker."""
    lines = 1 + (1 if n.name else 0) + (1 if n.team else 0)
    w = max([len(n.id)] + [len(n.name)] + ([len("team " + n.team)] if n.team else [0])) * CHAR_W + 8
    h = LINE_H * lines + 4
    rr = R * 1.3 + 8
    if side == "below":
        return (x - w / 2, y + rr, x + w / 2, y + rr + h)
    if side == "above":
        return (x - w / 2, y - rr - h, x + w / 2, y - rr)
    if side == "right":
        return (x + rr, y - h / 2, x + rr + w, y + h / 2)
    return (x - rr - w, y - h / 2, x - rr, y + h / 2)


def _hits(box, a, b) -> bool:
    """True when segment ab passes through the box (sampled every few px)."""
    n = max(int(math.hypot(b[0] - a[0], b[1] - a[1]) / 4), 1)
    for i in range(n + 1):
        px, py = a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n
        if box[0] <= px <= box[2] and box[1] <= py <= box[3]:
            return True
    return False


def place_labels(g: Flowgraph, pos: Dict[str, Tuple[float, float]]) -> Dict[str, Tuple[str, Tuple[float, float, float, float]]]:
    """Per node, the label side (below, above, right, left) with the fewest edge and label collisions, in node order."""
    chosen: Dict[str, Tuple[str, Tuple[float, float, float, float]]] = {}
    markers = {n.id: (pos[n.id][0] - R * 1.3, pos[n.id][1] - R * 1.3, pos[n.id][0] + R * 1.3, pos[n.id][1] + R * 1.3) for n in g.nodes}
    for n in g.nodes:
        x, y = pos[n.id]
        best = None
        for rank, side in enumerate(("below", "above", "right", "left")):
            box = _label_box(n, x, y, side)
            score = rank * 0.1
            for e in g.edges:
                if _hits(box, pos[e.a], pos[e.b]):
                    score += 1
            for m, (_, other) in chosen.items():
                if box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]:
                    score += 3
            for m, mk in markers.items():
                if m != n.id and box[0] < mk[2] and mk[0] < box[2] and box[1] < mk[3] and mk[1] < box[3]:
                    score += 3
            if best is None or score < best[0]:
                best = (score, side, box)
        chosen[n.id] = (best[1], best[2])
    return chosen


def _tip(title: str, desc: str) -> str:
    return "<title>%s</title>" % escape(title + ("\n" + desc if desc else ""), quote=False)


def render(g: Flowgraph) -> str:
    """The preview page as text. Same graph => byte-identical output."""
    w, h, pos = _canvas(g)
    labels = place_labels(g, pos)
    out: List[str] = []
    out.append("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">")
    out.append("<title>%s - flowgraph preview</title>" % escape(g.name, quote=False))
    out.append(_CSS)
    out.append("</head>\n<body>")
    out.append("<h1>%s</h1>" % escape(g.name, quote=False))
    if g.brief:
        out.append("<p class=\"brief\">%s</p>" % escape(g.brief, quote=False))
    out.append("<div class=\"legend\" aria-label=\"legend\">")
    for kind in KINDS:
        out.append("<span class=\"key\"><svg width=\"54\" height=\"54\" viewBox=\"0 0 54 54\" aria-hidden=\"true\">%s</svg>%s</span>"
                   % (shape_svg(kind, 27, 27, 17), LABELS[kind]))
    out.append("<span class=\"key\"><svg width=\"54\" height=\"54\" viewBox=\"0 0 54 54\" aria-hidden=\"true\">"
               "<line x1=\"4\" y1=\"27\" x2=\"50\" y2=\"27\" stroke=\"#0b1020\" stroke-width=\"3\"/></svg>Connection</span>")
    out.append("</div>")
    out.append("<svg id=\"graph\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 -%d %d %d\" width=\"%d\" height=\"%d\" role=\"img\" "
               "aria-label=\"%s flowgraph\">" % (PAD, w, h + 2 * PAD, w, h + 2 * PAD, escape(g.name)))
    out.append("<g id=\"edges\">")
    for e in g.edges:
        (ax, ay), (bx, by) = pos[e.a], pos[e.b]
        out.append("<g class=\"edge\" data-a=\"%s\" data-b=\"%s\" data-desc=\"%s\">%s<line x1=\"%g\" y1=\"%g\" x2=\"%g\" y2=\"%g\" class=\"vis\"/>"
                   "<line x1=\"%g\" y1=\"%g\" x2=\"%g\" y2=\"%g\" class=\"hit\"/></g>" % (
                       escape(e.a), escape(e.b), escape(e.desc), _tip("%s - %s%s" % (e.a, e.b, ": " + e.name if e.name else ""), e.desc),
                       ax, ay, bx, by, ax, ay, bx, by))
    out.append("</g>\n<g id=\"edge-labels\">")
    for e in g.edges:
        if e.name:
            (ax, ay), (bx, by) = pos[e.a], pos[e.b]
            out.append("<text class=\"elabel\" x=\"%g\" y=\"%g\" text-anchor=\"middle\">%s</text>" % ((ax + bx) / 2, (ay + by) / 2 - 6, escape(e.name, quote=False)))
    out.append("</g>\n<g id=\"nodes\">")
    for n in g.nodes:
        x, y = pos[n.id]
        label = "%s - %s" % (n.id, n.name) if n.name else n.id
        out.append("<g class=\"node\" data-id=\"%s\" data-kind=\"%s\" data-desc=\"%s\">%s%s" % (
            escape(n.id), n.kind, escape(n.desc), _tip("%s (%s%s)" % (label, LABELS[n.kind], ", team " + n.team if n.team else ""), n.desc),
            shape_svg(n.kind, x, y)))
        if n.team:
            out.append("<text class=\"team\" x=\"%g\" y=\"%g\" text-anchor=\"middle\" fill=\"%s\">%s</text>" % (x, y + 7, _TEXT_ON[n.kind], escape(n.team, quote=False)))
        bx0, by0, bx1, by1 = labels[n.id][1]
        out.append("<rect class=\"lbg\" x=\"%g\" y=\"%g\" width=\"%g\" height=\"%g\" rx=\"4\"/>" % (bx0, by0, bx1 - bx0, by1 - by0))
        tx = (bx0 + bx1) / 2
        out.append("<text class=\"nlabel\" x=\"%g\" y=\"%g\" text-anchor=\"middle\"><tspan class=\"nid\">%s</tspan>" % (tx, by0 + 16, escape(n.id, quote=False)))
        if n.name:
            out.append("<tspan x=\"%g\" dy=\"%d\">%s</tspan>" % (tx, LINE_H, escape(n.name, quote=False)))
        if n.team:
            out.append("<tspan class=\"nteam\" x=\"%g\" dy=\"%d\">team %s</tspan>" % (tx, LINE_H, escape(n.team, quote=False)))
        out.append("</text></g>")
    out.append("</g>\n</svg>")
    out.append("<div id=\"detail\" aria-live=\"polite\">Hover a node or connection for its description.</div>")
    out.append("<script>\n%s\n</script>" % _JS)
    out.append("</body>\n</html>\n")
    return "\n".join(out)


_CSS = """<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:16px;background:#fff;color:#0b1020}
h1{margin:0 0 4px;font-size:22px}
.brief{margin:0 0 12px;max-width:110ch;white-space:pre-line}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;margin:8px 0}
.key{display:inline-flex;align-items:center;gap:6px;font-size:14px}
#graph{max-width:100%;height:auto;border:1px solid #9aa3b8}
.edge .vis{stroke:#0b1020;stroke-width:3}
.edge .hit{stroke:transparent;stroke-width:16}
.edge.on .vis{stroke-width:6}
.elabel{font-size:14px;font-style:italic;fill:#0b1020;paint-order:stroke;stroke:#fff;stroke-width:5px}
.nlabel{font-size:15px;fill:#0b1020;paint-order:stroke;stroke:#fff;stroke-width:4px}
.lbg{fill:#fff;fill-opacity:.88}
.nid{font-weight:700}.nteam{font-size:13px}
.team{font-size:20px;font-weight:700}
.node.on polygon,.node.on rect,.node.on circle{stroke-width:5}
#detail{margin-top:8px;min-height:3em;font-size:15px;max-width:80ch}
</style>"""

_JS = """(function(){
var d=document.getElementById('detail');
function nodeText(n){return n.getAttribute('data-id')+' ('+n.getAttribute('data-kind')+')'+(n.getAttribute('data-desc')?': '+n.getAttribute('data-desc'):'');}
function edgeText(e){return e.getAttribute('data-a')+' - '+e.getAttribute('data-b')+(e.getAttribute('data-desc')?': '+e.getAttribute('data-desc'):'');}
var nodes=document.querySelectorAll('.node'),edges=document.querySelectorAll('.edge');
function clear(){nodes.forEach(function(n){n.classList.remove('on');});edges.forEach(function(e){e.classList.remove('on');});}
nodes.forEach(function(n){
  n.addEventListener('mouseenter',function(){clear();n.classList.add('on');var id=n.getAttribute('data-id');
    edges.forEach(function(e){if(e.getAttribute('data-a')===id||e.getAttribute('data-b')===id)e.classList.add('on');});d.textContent=nodeText(n);});
  n.addEventListener('mouseleave',clear);
});
edges.forEach(function(e){
  e.addEventListener('mouseenter',function(){clear();e.classList.add('on');d.textContent=edgeText(e);});
  e.addEventListener('mouseleave',clear);
});
})();"""
