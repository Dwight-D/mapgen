"""Settlement preview PNG (design: mapgen/TOWN_SPEC.md section 10).

Colour-blind safe: district kinds differ by grey level and pattern, never hue. Roads are white on a dark casing, the wall
is a thick dark band with a light core, gates are light diamonds, the plaza is a ring with a cross.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .model import Settlement
from .water import wall_runs

# kind -> (grey level, pattern)
KIND_STYLE = {
    "core": (250, "none"), "market": (215, "dots"), "residential": (190, "none"), "craft": (150, "hatch"),
    "harbour": (125, "grid"), "citadel": (70, "cross"), "temple": (235, "dots"), "slum": (105, "hatch"),
    "outskirt": (170, "none"),
}
BG = 238
INK = (20, 20, 20)
SS = 2                    # supersampling of the working canvas


def _pattern(draw: ImageDraw.ImageDraw, kind: str, box, dark: int, density: float = 1.0) -> None:
    x0, y0, x1, y1 = [int(v) for v in box]
    step = int(round((9 + 14 * (1.0 - density)) * SS))
    col = (max(dark - 70, 0),) * 3
    if kind == "hatch":
        for k in range(x0 - (y1 - y0), x1, step):
            draw.line([(k, y1), (k + (y1 - y0), y0)], fill=col, width=SS)
    elif kind == "cross":
        for k in range(x0 - (y1 - y0), x1, step):
            draw.line([(k, y1), (k + (y1 - y0), y0)], fill=col, width=SS)
        for k in range(x0, x1 + (y1 - y0), step):
            draw.line([(k, y1), (k - (y1 - y0), y0)], fill=col, width=SS)
    elif kind == "dots":
        for yy in range(y0, y1, step):
            for xx in range(x0 + ((yy - y0) // step % 2) * step // 2, x1, step):
                draw.ellipse([xx - SS, yy - SS, xx + SS, yy + SS], fill=col)
    elif kind == "grid":
        for k in range(x0, x1, step):
            draw.line([(k, y0), (k, y1)], fill=col, width=SS)
        for k in range(y0, y1, step):
            draw.line([(x0, k), (x1, k)], fill=col, width=SS)


def _parity(s: Settlement):
    """Lightness offset (+-10) of each leaf by the index parity among its siblings, so adjacent children separate."""
    nodes = {n.id: n for n in s.layers}
    out = {}
    for n in s.layers:
        for i, c in enumerate(n.children):
            out[c] = 10 if i % 2 == 0 else -10
    return out


class _View:
    def __init__(self, s: Settlement, px: int):
        pts = list(s.footprint)
        for d in s.districts:
            if d.kind == "outskirt":
                pts.extend(d.polygon)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0 * 1.12
        self.scale = px * SS / (2.0 * half)
        self.cx, self.cy, self.px = cx, cy, px * SS
        self.mpp = 1.0 / (self.scale / SS)

    def __call__(self, p) -> Tuple[float, float]:
        return ((p[0] - self.cx) * self.scale + self.px / 2.0, self.px / 2.0 - (p[1] - self.cy) * self.scale)


def render_png(s: Settlement, px: int = 900, title: str = "") -> Image.Image:
    v = _View(s, px)
    img = Image.new("RGB", (v.px, v.px), (BG,) * 3)
    d = ImageDraw.Draw(img)
    for ring in [r["polygon"] for r in s.water.get("rivers", [])] + [b["polygon"] for b in s.water.get("bays", [])]:
        wpts = [v(p) for p in ring]
        d.polygon(wpts, fill=(48, 48, 48))
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).polygon(wpts, fill=255)
        layer = Image.new("RGB", img.size, (48, 48, 48))
        ld = ImageDraw.Draw(layer)
        for yy in range(0, img.size[1], 8 * SS):
            ld.line([(0, yy), (img.size[0], yy)], fill=(120, 120, 120), width=SS)
        img.paste(layer, (0, 0), mask)
        d = ImageDraw.Draw(img)
    for isl in s.water.get("islands", []):
        d.polygon([v(p) for p in isl["polygon"]], fill=(200, 200, 200), outline=INK)
    layered = bool(s.layers)
    parity = _parity(s) if layered else {}
    for dist in s.districts:
        pts = [v(p) for p in dist.polygon]
        grey, pat = KIND_STYLE.get(dist.kind, (200, "none"))
        if dist.kind == "outskirt":     # lighter the thinner the density; denser dots the denser the buildings
            grey, pat = int(round(232 - 40 * dist.density)), "dots"
        if layered:
            grey = max(60, min(250, grey + parity.get(dist.id, 0)))
        d.polygon(pts, fill=(grey,) * 3)
        if pat != "none":
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).polygon(pts, fill=255)
            layer = Image.new("RGB", img.size, (grey,) * 3)
            _pattern(ImageDraw.Draw(layer), pat, (min(p[0] for p in pts), min(p[1] for p in pts),
                                                  max(p[0] for p in pts), max(p[1] for p in pts)), grey, dist.density)
            img.paste(layer, (0, 0), mask)
            d = ImageDraw.Draw(img)
        d.line(pts + [pts[0]], fill=(150, 150, 150) if dist.depth else (90, 90, 90), width=SS)
    for n in s.layers:     # hierarchy: the outline of a subdivided district, heavier the shallower it is
        pts = [v(p) for p in n.polygon]
        d.line(pts + [pts[0]], fill=(40, 40, 40) if n.depth == 0 else (90, 90, 90), width=(3 if n.depth == 0 else 2) * SS)
    for r in s.roads:
        pts = [v(p) for p in r.polyline]
        w = max(3.0, r.width * v.scale * 0.6)
        if r.cls == "street":
            w = max(2.0 * SS, r.width * v.scale * 0.4)
        d.line(pts, fill=INK, width=int(w + 2 * SS), joint="curve")
        d.line(pts, fill=(255, 255, 255), width=int(w), joint="curve")
    for wl in s.walls:
        w = max(4.0 * SS, wl.thickness * v.scale * 0.6)
        for run in wall_runs(wl):
            pts = [v(p) for p in run]
            d.line(pts, fill=INK, width=int(w + 3 * SS), joint="curve")
            d.line(pts, fill=(235, 225, 195), width=max(1, int(w - 3 * SS)), joint="curve")
    for g in s.gates:
        x, y = v(g.at)
        r = 8 * SS
        d.polygon([(x, y - r), (x + r, y), (x, y + r), (x - r, y)], fill=(255, 255, 255), outline=INK)
        d.line([(x - r, y), (x + r, y)], fill=INK, width=SS)
    for l in s.landmarks:
        x, y = v(l.at)
        r = max(l.r * v.scale, 6 * SS)
        if l.kind == "square":
            d.ellipse([x - r, y - r, x + r, y + r], outline=INK, width=2 * SS)
            d.line([(x - r * 0.6, y), (x + r * 0.6, y)], fill=INK, width=2 * SS)
            d.line([(x, y - r * 0.6), (x, y + r * 0.6)], fill=INK, width=2 * SS)
        else:
            q = 9 * SS
            d.rectangle([x - q, y - q, x + q, y + q], fill=(255, 255, 255), outline=INK, width=2 * SS)
            d.rectangle([x - q / 3, y - q / 3, x + q / 3, y + q / 3], fill=INK)
    font = ImageFont.load_default()
    for dist in s.districts:
        if dist.kind == "core" or (dist.depth and len(s.districts) > 120):
            continue
        poly_x = [p[0] for p in dist.polygon]
        poly_y = [p[1] for p in dist.polygon]
        x, y = v((sum(poly_x) / len(poly_x), sum(poly_y) / len(poly_y)))
        d.text((x - 8 * SS, y - 5 * SS), dist.key, fill=INK, font=font)
    if s.layers and len(s.layers) <= 20:
        for n in s.layers:
            xs = [p[0] for p in n.polygon]
            ys = [p[1] for p in n.polygon]
            x, y = v((sum(xs) / len(xs), sum(ys) / len(ys)))
            d.text((x - 8 * SS, y + 5 * SS), n.id.split("_d_", 1)[-1].upper(), fill=INK, font=font)
    img = img.resize((px, px), Image.LANCZOS)
    t = ImageDraw.Draw(img)
    if not title and s.layers:
        title = "%s %s seed %d  (%d districts, %d subdivided, depth %d)" % (
            s.generator, s.id, s.seed, len(s.districts), len(s.layers), max(d.depth for d in s.districts))
    t.text((8, 6), title or "%s %s seed %d  (%d districts, %d gates)" % (s.generator, s.id, s.seed, len(s.districts), len(s.gates)),
           fill=INK, font=font)
    return img


def render_sheet(items: Sequence[Settlement], px: int = 700) -> Image.Image:
    """Side-by-side contact sheet of several settlements."""
    tiles = [render_png(s, px) for s in items]
    sheet = Image.new("RGB", (px * len(tiles) + 4 * (len(tiles) - 1), px), (0, 0, 0))
    for i, t in enumerate(tiles):
        sheet.paste(t, (i * (px + 4), 0))
    return sheet
