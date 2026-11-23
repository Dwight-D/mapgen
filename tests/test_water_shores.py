"""Water shores (MAPGEN_SPEC 4.3 water union, 6.2 step 9): shelving lake shores and island beaches, river banks
without carved walls, river centrelines ending just inside the lake they end in, river beds running on into the lake
bed. Built on maps/outdoor/outdoor_sandbox.yaml
(Still Lake with Heron Isle, Fern Creek ending in the lake)."""
import os

import numpy as np
import pytest
from scipy import ndimage
from shapely.geometry import LineString, Point, box

from mapgen import geometry, height, raster, schema
from mapgen.geometry import (RIVER_LAKE_OVERLAP, RIVER_MOUTH_CHANNEL, RIVER_MOUTH_STEP, _cap_depth, _resample,
                             mouth_channels, trim_in_lakes, water_line_reach)

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


def test_trim_in_lakes_oblique_mouth_covers_the_strip():
    # a line entering the lake at 45 degrees: the end cap of half width 3 lies RIVER_LAKE_OVERLAP m inside the lake
    # at both corners, and is the first such cap along the line (to the walk step)
    lake = box(100.0, -50.0, 200.0, 50.0)
    cl = LineString([(50.0, -50.0), (150.0, 50.0)])
    reach = 3.0
    t = trim_in_lakes(cl, lake, reach)
    shore = lake.boundary
    assert _cap_depth(cl, t.length, reach, lake, shore) >= RIVER_LAKE_OVERLAP - 1e-9
    assert _cap_depth(cl, t.length - RIVER_MOUTH_STEP, reach, lake, shore) < RIVER_LAKE_OVERLAP
    # corner at reach 3 across a 45-degree shore: the centre runs (3 + 0.5 * sqrt 2) m past the shore crossing
    assert t.length - cl.project(Point(100.0, 0.0)) == pytest.approx(3.0 + 0.5 * 2 ** 0.5, abs=RIVER_MOUTH_STEP)


def test_mouth_channels():
    lake = box(100.0, -50.0, 200.0, 50.0)
    (m,) = mouth_channels(LineString([(0.0, 0.0), (150.0, 0.0)]), lake, 2.0)
    assert m.coords[0] == pytest.approx((100.0, 0.0))
    assert m.length == pytest.approx(RIVER_MOUTH_CHANNEL)
    (m,) = mouth_channels(LineString([(150.0, 0.0), (300.0, 0.0)]), lake, 2.0)
    assert m.coords[0] == pytest.approx((200.0, 0.0))
    assert m.coords[-1] == pytest.approx((200.0 - RIVER_MOUTH_CHANNEL, 0.0))
    assert mouth_channels(LineString([(0.0, 0.0), (300.0, 0.0)]), lake) == []


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
    # the margin is shallow: water within 2 m of the shoreline is less than half a metre deep (away from the mouth
    # channel, where the creek's bed runs on into the lake bed)
    d_in = ndimage.distance_transform_edt(hard, sampling=hm.grid.cell_size_x)
    mouth = np.zeros(hard.shape, dtype=bool)
    for ln in spec.lines:
        for mc in geoms.lines[ln.index].mouths:
            mouth |= raster.hard(raster.mask(mc.buffer(ln.width), hm.grid))
    margin = hard & (d_in <= 2.0) & ~mouth
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
    reach = water_line_reach(creek)
    # the end cap of the widest strip lies just inside the lake: no gap between river and lake water, no long stub
    depth = _cap_depth(cl, cl.length, reach, lake_g, lake_g.boundary)
    assert RIVER_LAKE_OVERLAP - 1e-6 <= depth <= RIVER_LAKE_OVERLAP + 1.0
    assert cl.intersection(lake_g).length <= 2.0 * reach + RIVER_LAKE_OVERLAP
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


def test_creek_bed_runs_into_the_lake_bed(sandbox):
    # along the creek's last 20 m and its mouth channel the bed stays well under the water (no bar at the mouth)
    # and the water is the lake's at the mouth
    spec, geoms, hm, lake, creek = sandbox
    level = hm.lake_surface[lake.index]
    cl = geoms.lines[creek.index].centreline
    ws = hm.river_surface[creek.index]
    pts, s = _resample(np.asarray(cl.coords), 10.0)
    tail = np.linspace(max(0.0, cl.length - 20.0), cl.length, 41)
    xy = np.array([cl.interpolate(t).coords[0] for t in tail])
    water = np.interp(tail, s, ws)
    (mc,) = geoms.lines[creek.index].mouths
    mxy = np.array([mc.interpolate(t).coords[0] for t in np.linspace(0.0, 0.5 * mc.length, 11)])
    depth_river = water - height.sample(hm, xy[:, 0], xy[:, 1])
    depth_mouth = level - height.sample(hm, mxy[:, 0], mxy[:, 1])
    assert water[-1] == pytest.approx(level, abs=1e-4)
    assert depth_river.min() > 0.5
    assert depth_mouth.min() > 0.5


def test_creek_banks_have_no_terraces(sandbox):
    # along Fern Creek's upper run the banks follow the sloping water smoothly: height profiles parallel to the
    # centreline deviate little from their 4 m running mean (a staircase of bank steps every 1-3 m left 0.19 m)
    spec, geoms, hm, _, creek = sandbox
    cl = geoms.lines[creek.index].centreline
    s = np.arange(5.0, cl.length, 0.25)
    p = np.array([cl.interpolate(t).coords[0] for t in s])
    t = np.gradient(p, axis=0)
    t /= np.hypot(t[:, 0], t[:, 1])[:, None]
    n = np.column_stack([-t[:, 1], t[:, 0]])
    sel = (p[:, 0] > 10.0) & (p[:, 0] < 105.0)
    sel[:16] = sel[-16:] = False
    for off in (-12.0, -9.0, -6.0, -4.0, 4.0, 6.0, 9.0, 12.0):
        q = p + n * off
        z = height.sample(hm, q[:, 0], q[:, 1])
        r = z - np.convolve(z, np.ones(16) / 16, mode="same")
        assert np.abs(r[sel]).max() < 0.15, off
