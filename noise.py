"""Deterministic, vectorised value noise / fbm / hashing (numpy only).

hash01/hash1 use a strong integer hash (for sparse per-cell randomness such as
star placement). The smooth noises use a permutation table, which is several
times faster and plenty for terrain, clouds and water."""
import numpy as np

U = np.uint64
_PERM = np.random.default_rng(1234).permutation(256).astype(np.int64)
_PERM = np.concatenate([_PERM, _PERM])          # 512 entries: wrap without masking twice
_PERMF = _PERM / 255.0


def _h(x, seed):
    x = x.astype(np.uint64)
    x = x * U(0x9E3779B1) + U(seed & 0xFFFFFFFF) * U(0x85EBCA77)
    x ^= x >> U(15)
    x *= U(0x2C1B3C6D)
    x ^= x >> U(12)
    x *= U(0x297A2D39)
    x ^= x >> U(15)
    return (x & U(0xFFFFFF)).astype(np.float64) / 16777216.0


def _i(x):
    return np.floor(x).astype(np.int64).astype(np.uint64)


def hash01(x, y, seed=0):
    """Per-cell pseudo random in [0,1) for integer coords (x, y)."""
    return _h(_i(x) * U(73856093) ^ _i(y) * U(19349663), seed)


def hash1(x, seed=0):
    return _h(_i(x), seed)


def _lat1(ix, seed):
    return _PERMF[(ix + seed * 7) & 255]


def _lat2(ix, iy, seed):
    return _PERMF[(ix + _PERM[(iy + seed * 13) & 255] + seed * 7) & 255]


def vnoise1(x, seed=0):
    i = np.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f)
    ii = i.astype(np.int64)
    a = _lat1(ii, seed)
    b = _lat1(ii + 1, seed)
    return a + (b - a) * u


def fbm1(x, seed=0, octaves=4, lac=2.0, gain=0.5):
    out = np.zeros_like(x, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for k in range(octaves):
        out += amp * vnoise1(x * freq, seed + k * 101)
        norm += amp
        amp *= gain
        freq *= lac
    return out / norm


def vnoise2(x, y, seed=0):
    ix = np.floor(x)
    iy = np.floor(y)
    fx = x - ix
    fy = y - iy
    ux = fx * fx * (3 - 2 * fx)
    uy = fy * fy * (3 - 2 * fy)
    ix = ix.astype(np.int64)
    iy = iy.astype(np.int64)
    a = _lat2(ix, iy, seed)
    b = _lat2(ix + 1, iy, seed)
    c = _lat2(ix, iy + 1, seed)
    d = _lat2(ix + 1, iy + 1, seed)
    top = a + (b - a) * ux
    bot = c + (d - c) * ux
    return top + (bot - top) * uy


def fbm2(x, y, seed=0, octaves=4, lac=2.0, gain=0.5):
    out = np.zeros(np.broadcast(x, y).shape, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for k in range(octaves):
        out += amp * vnoise2(x * freq, y * freq, seed + k * 101)
        norm += amp
        amp *= gain
        freq *= lac
    return out / norm
