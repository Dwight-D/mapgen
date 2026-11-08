"""Deterministic value noise (no global RNG state). All functions are vectorised over numpy arrays."""
from __future__ import annotations

import numpy as np

_M32 = 0xFFFFFFFF


def _u32(a) -> np.ndarray:
    """Integer array -> uint32 with two's-complement wraparound."""
    return np.asarray(a).astype(np.int64).astype(np.uint32)


def hash_lattice(ix, iy, seed: int) -> np.ndarray:
    """Integer hash of (ix, iy, seed) -> float64 in [-1, 1]. Same shape as the broadcast of ix, iy."""
    ix, iy = np.broadcast_arrays(np.asarray(ix), np.asarray(iy))
    shape = ix.shape
    with np.errstate(over="ignore"):
        h = _u32(ix.reshape(-1)) * np.uint32(374761393)
        h = h ^ (_u32(iy.reshape(-1)) * np.uint32(668265263))
        h = h ^ np.uint32((int(seed) * 2246822519 + 3266489917) & _M32)
        h = h ^ (h >> np.uint32(15))
        h = h * np.uint32(0x2C1B3C6D)
        h = h ^ (h >> np.uint32(12))
        h = h * np.uint32(0x297A2D39)
        h = h ^ (h >> np.uint32(15))
    out = h.astype(np.float64) / float(_M32) * 2.0 - 1.0
    return out.reshape(shape)


def _smooth(t):
    return t * t * (3.0 - 2.0 * t)


def value_noise2(x, y, seed: int = 0) -> np.ndarray:
    """2D value noise in [-1, 1]; lattice spacing 1."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = _smooth(x - x0)
    fy = _smooth(y - y0)
    ix = x0.astype(np.int64)
    iy = y0.astype(np.int64)
    v00 = hash_lattice(ix, iy, seed)
    v10 = hash_lattice(ix + 1, iy, seed)
    v01 = hash_lattice(ix, iy + 1, seed)
    v11 = hash_lattice(ix + 1, iy + 1, seed)
    a = v00 + (v10 - v00) * fx
    b = v01 + (v11 - v01) * fx
    return a + (b - a) * fy


def fbm2(x, y, seed: int = 0, octaves: int = 5) -> np.ndarray:
    """2D fractal value noise, lacunarity 2, gain 0.5, normalised to [-1, 1]."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    total = np.zeros(np.broadcast(x, y).shape, dtype=np.float64)
    amp = 1.0
    norm = 0.0
    freq = 1.0
    for o in range(octaves):
        total = total + amp * value_noise2(x * freq + o * 17.31, y * freq - o * 9.17, seed + o * 1013)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def ridged_fbm2(x, y, seed: int = 0, octaves: int = 5) -> np.ndarray:
    """Ridged fbm normalised to [0, 1] (1 at ridge crests): 1 - |fbm|."""
    return 1.0 - np.abs(fbm2(x, y, seed, octaves))


def value_noise1(t, seed: int = 0, period: int = 0) -> np.ndarray:
    """1D value noise in [-1, 1], lattice spacing 1. If period > 0 the lattice wraps with that period."""
    t = np.asarray(t, dtype=np.float64)
    t0 = np.floor(t)
    f = _smooth(t - t0)
    i0 = t0.astype(np.int64)
    i1 = i0 + 1
    if period > 0:
        i0 = np.mod(i0, period)
        i1 = np.mod(i1, period)
    v0 = hash_lattice(i0, np.zeros_like(i0), seed)
    v1 = hash_lattice(i1, np.zeros_like(i1), seed)
    return v0 + (v1 - v0) * f


def fbm1(t, seed: int = 0, octaves: int = 3) -> np.ndarray:
    """Open 1D fbm in [-1, 1]; t in lattice units of the first octave."""
    t = np.asarray(t, dtype=np.float64)
    total = np.zeros(t.shape, dtype=np.float64)
    amp, norm, freq = 1.0, 0.0, 1.0
    for o in range(octaves):
        total = total + amp * value_noise1(t * freq + o * 5.7, seed + o * 1013)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def fbm1_periodic(u, cells: int, seed: int = 0, octaves: int = 3) -> np.ndarray:
    """Periodic 1D fbm in [-1, 1]. u is the fraction around the loop (period 1); the first octave has
    `cells` lattice cells around the loop, each further octave doubles that."""
    u = np.asarray(u, dtype=np.float64)
    cells = max(1, int(cells))
    total = np.zeros(u.shape, dtype=np.float64)
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        n = cells * (2 ** o)
        total = total + amp * value_noise1(u * n, seed + o * 1013, period=n)
        norm += amp
        amp *= 0.5
    return total / norm


def value_noise2_grid(xs, ys, seed: int = 0) -> np.ndarray:
    """value_noise2 over the grid of 1D axes: result[j, i] == value_noise2(xs[i], ys[j], seed), shape
    (len(ys), len(xs)). The lattice hash is evaluated once per lattice point instead of four times per cell."""
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    x0, y0 = np.floor(xs), np.floor(ys)
    fx = _smooth(xs - x0)[None, :]
    fy = _smooth(ys - y0)[:, None]
    ix, iy = x0.astype(np.int64), y0.astype(np.int64)
    ix0, iy0 = int(ix.min()), int(iy.min())
    lat = hash_lattice((np.arange(int(ix.max()) - ix0 + 2) + ix0)[None, :],
                       (np.arange(int(iy.max()) - iy0 + 2) + iy0)[:, None], seed)
    a, b = ix - ix0, iy - iy0
    v0, v1 = lat[:, a], lat[:, a + 1]
    row = v0 + (v1 - v0) * fx                      # lattice rows interpolated along x
    r0, r1 = row[b], row[b + 1]
    return r0 + (r1 - r0) * fy


def fbm2_grid(xs, ys, seed: int = 0, octaves: int = 5) -> np.ndarray:
    """fbm2 over the grid of 1D axes (same values as fbm2 on their meshgrid, much faster)."""
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    total = np.zeros((len(ys), len(xs)), dtype=np.float64)
    amp, norm, freq = 1.0, 0.0, 1.0
    for o in range(octaves):
        total += amp * value_noise2_grid(xs * freq + o * 17.31, ys * freq - o * 9.17, seed + o * 1013)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def ridged_fbm2_grid(xs, ys, seed: int = 0, octaves: int = 5) -> np.ndarray:
    return 1.0 - np.abs(fbm2_grid(xs, ys, seed, octaves))
