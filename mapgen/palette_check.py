"""Palette legibility check: region fills must differ by >= MIN_DELTA_L in CIE L* or by pattern."""
from __future__ import annotations

from typing import Dict, List, Tuple

from .schema import Palette

MIN_DELTA_L = 12.0


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lightness(hexstr: str) -> float:
    """CIE L* (0..100) of an sRGB hex colour (D65)."""
    s = hexstr.lstrip("#")
    r, g, b = (_lin(int(s[i:i + 2], 16)) for i in (0, 2, 4))
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return 116.0 * y ** (1.0 / 3.0) - 16.0 if y > 0.008856 else 903.3 * y


def fills(pal: Palette) -> List[Tuple[str, str, str]]:
    """(name, colour, pattern) of the ground and every region type."""
    out = [("ground", pal.base, "none")]
    for name, col in pal.regions.items():
        out.append((name, col, pal.patterns.get(name, "none")))
    return out


def check(pal: Palette):
    """Returns (items, matrix, failures): matrix[i][j] = |dL|; failures = pairs below MIN_DELTA_L with equal patterns."""
    items = fills(pal)
    ls = [lightness(c) for _, c, _ in items]
    n = len(items)
    matrix = [[abs(ls[i] - ls[j]) for j in range(n)] for i in range(n)]
    fails = [(items[i][0], items[j][0], matrix[i][j]) for i in range(n) for j in range(i + 1, n)
             if matrix[i][j] < MIN_DELTA_L and items[i][2] == items[j][2]]
    return items, matrix, fails


def report(pal: Palette) -> int:
    items, m, fails = check(pal)
    n = len(items)
    names = [it[0] for it in items]
    print("Region fill lightness (CIE L*) and pattern:")
    for name, col, pat in items:
        print("  %-9s %s  L*=%5.1f  %s" % (name, col, lightness(col), pat))
    print("\n|dL*| matrix (. = pair differs by pattern instead):")
    print("          " + " ".join("%6s" % nm[:6] for nm in names))
    for i in range(n):
        cells = []
        for j in range(n):
            if i == j:
                cells.append("%6s" % "-")
            elif m[i][j] < MIN_DELTA_L and items[i][2] != items[j][2]:
                cells.append("%5.1f." % m[i][j])
            else:
                cells.append("%6.1f" % m[i][j])
        print("%-9s " % names[i][:9] + " ".join(cells))
    allmin = min(m[i][j] for i in range(n) for j in range(i + 1, n))
    plain = [m[i][j] for i in range(n) for j in range(i + 1, n) if items[i][2] == items[j][2]]
    by_pat = [(names[i], names[j], m[i][j]) for i in range(n) for j in range(i + 1, n)
              if m[i][j] < MIN_DELTA_L and items[i][2] != items[j][2]]
    print("\nminimum |dL*| over all pairs: %.1f" % allmin)
    if plain:
        print("minimum |dL*| over pairs without a pattern difference: %.1f (required >= %g)" % (min(plain), MIN_DELTA_L))
    print("pairs separated by pattern only: %s" % (", ".join("%s/%s (%.1f)" % p for p in by_pat) or "none"))
    print("teams: " + ", ".join("%s L*=%.1f" % (c, lightness(c)) for c in pal.teams))
    if fails:
        print("FAIL: " + ", ".join("%s/%s (%.1f)" % f for f in fails))
        return 1
    print("OK")
    return 0
