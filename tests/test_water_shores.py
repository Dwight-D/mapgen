"""Water shores (MAPGEN_SPEC 4.3 water union, 6.2 step 9): shelving lake shores and island beaches, river banks
without carved walls, river centrelines trimmed at the lake they end in. Built on maps/outdoor/outdoor_sandbox.yaml
(Still Lake with Heron Isle, Fern Creek ending in the lake)."""
import os

import numpy as np
import pytest
from scipy import ndimage
from shapely.geometry import LineString, Point, box

from mapgen import geometry, height, raster, schema
from mapgen.geometry import RIVER_LAKE_OVERLAP, _resample, trim_in_lakes

SANDBOX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "maps", "outdoor", "outdoor_sandbox.yaml")


@pytest.fixture(scope="module")
def sandbox():
    spec = schema.load_spec(SANDBOX)
    geoms = geometry.build_geometries(spec)
    hm = height.synthesize(spec, geoms)
    lake = next(r for r in spec.regions if r.id == "still_lake")
    creek = next(ln for ln in spec.lines if ln.id == "forest_creek")
    return spec, geoms, hm, lake, creek


def _slope_deg(hm):
    return np.degrees(np.arctan(height.slope_grid(hm.h, hm.grid)))


def test_shore_cap_profile():
    d = np.linspace(0.0, 40.0, 4001)
    cap = height.shore_cap(d)
    assert cap[0] == 0.0
    step = np.diff(cap) / np.diff(d)
    assert step.min() >= height.SHORE_SLOPE - 1e-6
    assert step.max() <= height.SHORE_STEEP + 1e-6
    # the 0..SHORE_RISE band rises at the gentle slope only
    band = cap[1:] <= height.SHORE_RISE
    assert step[band].max() <= height.SHORE_SLOPE + 1e-6


def test_trim_in_lakes():
    lake = box(100.0, -50.0, 200.0, 50.0)
    ends_in = LineString([(0.0, 0.0), (150.0, 0.0)])
    t = trim_in_lakes(ends_in, lake)
    assert t.coords[0] == (0.0, 0.0)
    assert t.coords[-1][0] == pytest.approx(100.0 + RIVER_LAKE_OVERLAP)
    starts_in = LineString([(150.0, 0.0), (300.0, 0.0)])
    assert trim_in_lakes(starts_in, lake).coords[0][0] == pytest.approx(200.0 - RIVER_LAKE_OVERLAP)
    through = LineString([(0.0, 0.0), (300.0, 0.0)])
    assert trim_in_lakes(through, lake).length == pytest.approx(300.0)
    dry = LineString([(0.0, 80.0), (300.0, 80.0)])
    assert trim_in_lakes(dry, lake).equals(dry)


def test_no_vertical_lake_shore_or_island_rim(sandbox):
    spec, geoms, hm, lake, _ = sandbox
    level = hm.lake_surface[lake.index]
    hard = raster.hard(raster.mask(geoms.regions[lake.index], hm.grid))
    island = ndimage.binary_fill_holes(hard) & ~hard
    assert island.any()
    d_out = ndimage.distance_transform_edt(~hard, sampling=hm.grid.cell_size_x)
    slope = _slope_deg(hm)
    h = hm.h
    for zone in (island, ~ndimage.binary_fill_holes(hard)):
        band = zone & (d_out > 0) & (d_out <= 12.0) & (h >= level) & (h <= level + 3.0)
        assert band.any()
        assert slope[band].max() <= 32.0
    # the margin is shallow: water within 2 m of the shoreline is less than half a metre deep
    d_in = ndimage.distance_transform_edt(hard, sampling=hm.grid.cell_size_x)
    margin = hard & (d_in <= 2.0)
    assert (level - h[margin]).max() < 0.5
    assert (h[hard] < level).all()
    assert h[island].max() > level + 0.5


def test_lake_level_below_its_shore(sandbox):
    spec, geoms, hm, lake, creek = sandbox
    level = hm.lake_surface[lake.index]
    hard = raster.hard(raster.mask(geoms.regions[lake.index], hm.grid))
    creek_m = raster.hard(raster.mask(geoms.lines[creek.index].area, hm.grid))
    ring = ndimage.binary_dilation(hard, iterations=4) & ~hard & ~creek_m
    assert hm.h[ring].min() >= level - 1e-3


def test_creek_ends_at_the_shore(sandbox):
    spec, geoms, hm, lake, creek = sandbox
    cl = geoms.lines[creek.index].centreline
    lake_g = geoms.regions[lake.index]
    assert cl.intersection(lake_g).length <= RIVER_LAKE_OVERLAP + 1e-6
    assert lake_g.contains(Point(cl.coords[-1]))
    ws = hm.river_surface[creek.index]
    pts, _ = _resample(np.asarray(cl.coords), 10.0)
    assert len(ws) == len(pts)
    assert float(ws[-1]) == pytest.approx(hm.lake_surface[lake.index], abs=1e-4)


def test_river_water_above_bed(sandbox):
    spec, geoms, hm, _, _ = sandbox
    for ln in spec.lines:
        ws = hm.river_surface.get(ln.index)
        if ws is None:
            continue
        pts, _ = _resample(np.asarray(geoms.lines[ln.index].centreline.coords), 10.0)
        assert (ws - height.sample(hm, pts[:, 0], pts[:, 1])).min() > 0.05
