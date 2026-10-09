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
    # same lattice values as _lat2, sharing the row lookups between corners
    bx = ix.astype(np.int64) + seed * 7
    iy = iy.astype(np.int64) + seed * 13
    p0 = bx + _PERM[iy & 255]
    p1 = bx + _PERM[(iy + 1) & 255]
    a = _PERMF[p0 & 255]
    b = _PERMF[(p0 + 1) & 255]
    c = _PERMF[p1 & 255]
    d = _PERMF[(p1 + 1) & 255]
    top = a + (b - a) * ux
    bot = c + (d - c) * ux
    return top + (bot - top) * uy


def _upsample2(small, shape):
    """Half-resolution field -> full resolution (bilinear)."""
    H, W = shape
    h, w = small.shape
    r = np.empty((h, 2 * w))
    r[:, 0::2] = small
    r[:, 1:-1:2] = (small[:, :-1] + small[:, 1:]) * 0.5
    r[:, -1] = small[:, -1]
    big = np.empty((2 * h, 2 * w))
    big[0::2] = r
    big[1:-1:2] = (r[:-1] + r[1:]) * 0.5
    big[-1] = r[-1]
    return big[:H, :W]


def fbm2(x, y, seed=0, octaves=4, lac=2.0, gain=0.5):
    """Fractal value noise. Large 2-D fields are evaluated on every other
    sample and interpolated: the noise is smooth, so this looks the same and
    costs about a quarter."""
    x, y = np.broadcast_arrays(np.asarray(x, np.float64), np.asarray(y, np.float64))
    if x.ndim == 2 and x.shape[0] >= 16 and x.shape[1] >= 16:
        small = _fbm2_full(x[::2, ::2], y[::2, ::2], seed, octaves, lac, gain)
        return _upsample2(small, x.shape)
    return _fbm2_full(x, y, seed, octaves, lac, gain)


def _fbm2_full(x, y, seed=0, octaves=4, lac=2.0, gain=0.5):
    out = np.zeros(np.broadcast(x, y).shape, dtype=np.float64)
    amp, freq, norm = 1.0, 1.0, 0.0
    for k in range(octaves):
        out += amp * vnoise2(x * freq, y * freq, seed + k * 101)
        norm += amp
        amp *= gain
        freq *= lac
    return out / norm
