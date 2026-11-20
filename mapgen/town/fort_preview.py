"""Preview of a settlement with fort add-on: the standard render with the view widened to outlying forts and wall
towers drawn as dark squares (additive to render.py, which stays unchanged)."""
from __future__ import annotations

import copy

from PIL import ImageDraw
from shapely.geometry import MultiPoint

from .model import Settlement
from .render import SS, _View, render_png


def render_fort(s: Settlement, px: int = 900, title: str = ""):
    pts = [tuple(p) for p in s.footprint] + [tuple(p) for d in s.districts for p in d.polygon]
    hull = MultiPoint(pts).convex_hull
    v = copy.copy(s)
    v.footprint = [list(c) for c in hull.exterior.coords][:-1] if hull.geom_type == "Polygon" else s.footprint
    img = render_png(v, px, title or "%s %s seed %d fort=%s" % (s.generator, s.id, s.seed, s.params.get("fort", "none")))
    view = _View(v, px)
    d = ImageDraw.Draw(img)
    for w in s.walls:
        for t in w.towers:
            x, y = view(t)
            x, y, q = x / SS, y / SS, 5
            d.rectangle([x - q, y - q, x + q, y + q], fill=(20, 20, 20), outline=(255, 255, 255))
    return img
