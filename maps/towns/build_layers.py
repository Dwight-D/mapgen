"""Regenerate the layered-subdivision showcase: python maps/towns/build_layers.py  (run from tools/mapgen).
Registers the layered maps with build.py's pipeline and builds them."""
import os
import sys

sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402

LAYERED = {
    "wardring": dict(gen="radial", seed=7, sid="ward", size=1200, teams=("Northguard", "Southguard"),
                     params=dict(radius_frac=0.6, spokes=5, rings=2, gates=5, citadel="false",
                                 layers="grid;min_split_area=9000")),
}

if __name__ == "__main__":
    for n in (sys.argv[1:] or LAYERED):
        build.build(n, LAYERED[n])
