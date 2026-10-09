"""Silhouette creatures and props, built from simple shapes and rasterised at
sub-cell (braille dot) resolution.

A creature is a function f(phase) -> (width_px, height_px, shapes) where
phase in [0,1) drives the walk / flap cycle. Pixels are roughly square
(one braille dot = half a cell wide, a quarter of a cell tall).

Each shape is (layer, geometry):
  BODY   - the solid silhouette (drawn as a block of colour)
  HOLE   - cut-out inside the silhouette (scene background shows through)
  DETAIL - inside the silhouette, drawn in a second (ink) colour
Shapes are painted in order, so a later BODY can fill part of an earlier HOLE.

Geometries:
  ("e", cx, cy, rx, ry)          ellipse
  ("c", x1, y1, x2, y2, r)       capsule (thick line with round ends)
  ("p", [(x, y), ...])           convex polygon
  ("r", x0, y0, x1, y1)          rectangle
  ("ring", cx, cy, r1, r2)       circular ring
"""
import math
import numpy as np

BODY, HOLE, DETAIL = 1, 2, 3


def S(x):
    return math.sin(2 * math.pi * x)


# --- rasteriser --------------------------------------------------------------
def _mask(g, X, Y):
    k = g[0]
    if k == "e":
        _, cx, cy, rx, ry = g
        return ((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2 <= 1.0
    if k == "c":
        _, x1, y1, x2, y2, r = g
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy or 1e-9
        t = np.clip(((X - x1) * dx + (Y - y1) * dy) / L, 0, 1)
        return (X - x1 - t * dx) ** 2 + (Y - y1 - t * dy) ** 2 <= r * r
    if k == "p":
        pts = g[1]
        pos = np.ones(X.shape, bool)
        neg = np.ones(X.shape, bool)
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            cr = (bx - ax) * (Y - ay) - (by - ay) * (X - ax)
            pos &= cr >= 0
            neg &= cr <= 0
        return pos | neg
    if k == "r":
        _, x0, y0, x1, y1 = g
        return (X >= x0) & (X < x1) & (Y >= y0) & (Y < y1)
    if k == "ring":
        _, cx, cy, r1, r2 = g
        d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        return (d >= r1) & (d <= r2)
    raise ValueError(k)


_cache = {}


def raster(fn, phase, scale=1.0, flip=False, steps=16):
    """-> uint8 array (H, W) of 0 / BODY / HOLE / DETAIL. Cached per phase step."""
    phase = (round(phase * steps) % steps) / steps
    scale = round(scale * 10) / 10
    key = (fn.__name__, phase, scale, flip)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    w, h, shapes = fn(phase)
    W, H = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    Y, X = np.mgrid[0:H, 0:W].astype(np.float64)
    X = (X + 0.5) / scale
    Y = (Y + 0.5) / scale
    out = np.zeros((H, W), np.uint8)
    for layer, g in shapes:
        m = _mask(g, X, Y)
        if layer == BODY:
            out[m] = BODY
        else:
            out[m & (out > 0)] = layer
    if flip:
        out = out[:, ::-1].copy()
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = out
    return out


def _legs(xs, top, foot, p, r=1.2, swing=1.6, offs=(0, 0.5, 0.5, 0)):
    out = []
    for x, o in zip(xs, offs):
        sw = swing * S(p + o)
        out.append((BODY, ("c", x, top, x + sw, foot, r)))
    return out


# --- land --------------------------------------------------------------------
def pig(p):
    b = 0.6 * abs(S(p))
    sh = [
        (BODY, ("e", 15, 10 - b, 11, 6.5)),
        (BODY, ("e", 25, 8 - b, 5.5, 5)),
        (BODY, ("e", 29.5, 9.5 - b, 2.5, 2.3)),
        (BODY, ("p", [(21, 5 - b), (23, 0.6 - b), (26.5, 4 - b)])),
        (BODY, ("c", 4.5, 8 - b, 2, 5 + S(p * 2) - b, 1.0)),
    ]
    sh += _legs([8, 11.5, 18, 21.5], 13 - b, 18.6, p, r=1.3)
    sh += [
        (HOLE, ("e", 25.6, 6.6 - b, 0.9, 1.0)),
        (HOLE, ("e", 30.6, 9.2 - b, 0.5, 0.7)),
        (HOLE, ("c", 9, 12.5 - b, 17, 13.2 - b, 0.35)),
    ]
    return 33, 20, sh


def cat(p):
    b = 0.4 * abs(S(p))
    tw = S(p * 0.5)
    sh = [
        (BODY, ("e", 13, 12 - b, 9, 4.2)),
        (BODY, ("e", 24, 8 - b, 4.6, 4.2)),
        (BODY, ("p", [(20.4, 6 - b), (21, 0.4 - b), (24, 4.2 - b)])),
        (BODY, ("p", [(24.6, 4.2 - b), (27.6, 0.6 - b), (28.2, 6.4 - b)])),
        (BODY, ("c", 5, 11 - b, 2 + tw, 6 - b, 1.1)),
        (BODY, ("c", 2 + tw, 6 - b, 3.5 + tw * 1.5, 1.5 - b, 1.1)),
    ]
    sh += _legs([7, 10, 16, 19], 14 - b, 19.2, p, r=1.0, swing=1.5)
    sh += [
        (HOLE, ("e", 25.8, 7.4 - b, 0.8, 1.1)),
        (HOLE, ("c", 11, 8.6 - b, 12, 11 - b, 0.45)),
        (HOLE, ("c", 14, 8.4 - b, 15, 11 - b, 0.45)),
        (HOLE, ("c", 17, 8.8 - b, 17.8, 11 - b, 0.45)),
    ]
    return 31, 20, sh


def fox(p):
    b = 0.4 * abs(S(p))
    sw = 1.2 * S(p * 0.5)
    sh = [
        (BODY, ("e", 17, 11 - b, 9, 4)),
        (BODY, ("e", 28, 8 - b, 4, 3.6)),
        (BODY, ("p", [(30, 6.4 - b), (36, 9 - b), (30, 11 - b)])),
        (BODY, ("p", [(25, 6 - b), (25.6, 0.4 - b), (28, 5 - b)])),
        (BODY, ("p", [(28, 5 - b), (30, 0.4 - b), (30.6, 6.6 - b)])),
        (BODY, ("e", 6, 8.5 + sw - b, 6, 2.8)),
    ]
    sh += _legs([11, 14, 20, 23], 13 - b, 19.2, p, r=1.0, swing=1.6)
    sh += [
        (DETAIL, ("e", 1.6, 8.5 + sw - b, 1.8, 1.9)),
        (HOLE, ("e", 29, 7 - b, 0.7, 0.9)),
        (DETAIL, ("e", 22, 13 - b, 4, 1.4)),
    ]
    return 36, 20, sh


def bear(p):
    b = 0.6 * abs(S(p))
    sh = [
        (BODY, ("e", 15, 12 - b, 11, 7)),
        (BODY, ("e", 27, 9 - b, 5.5, 5)),
        (BODY, ("e", 24.4, 4.4 - b, 1.9, 1.9)),
        (BODY, ("e", 29.6, 4.4 - b, 1.9, 1.9)),
        (BODY, ("e", 31.6, 10.6 - b, 2.5, 2.1)),
    ]
    sh += _legs([8, 12.5, 19, 23], 16 - b, 20.2, p, r=1.8, swing=1.3)
    sh += [
        (HOLE, ("e", 24.4, 4.4 - b, 0.7, 0.7)),
        (HOLE, ("e", 29.6, 4.4 - b, 0.7, 0.7)),
        (HOLE, ("e", 28.4, 7.8 - b, 0.8, 0.9)),
        (DETAIL, ("e", 33.6, 9.8 - b, 0.8, 0.7)),
    ]
    return 35, 22, sh


def rabbit(p):
    j = max(0.0, S(p)) * 4.0
    sh = [
        (BODY, ("e", 11, 15 - j, 7, 4.5)),
        (BODY, ("e", 18, 11 - j, 3.8, 3.4)),
        (BODY, ("e", 16.6, 4.4 - j, 1.3, 4.4)),
        (BODY, ("e", 19.4, 4.8 - j, 1.3, 4.4)),
        (BODY, ("e", 9, 19.6 - j * 0.6, 4.2, 1.4)),
        (BODY, ("c", 15, 17 - j, 16.5, 20 - j * 0.6, 1.0)),
        (BODY, ("e", 3.8, 13.6 - j, 1.9, 1.9)),
    ]
    sh += [
        (DETAIL, ("e", 3.8, 13.6 - j, 1.2, 1.2)),
        (HOLE, ("e", 16.6, 4.6 - j, 0.4, 3.0)),
        (HOLE, ("e", 19.2, 10.4 - j, 0.7, 0.8)),
    ]
    return 23, 22, sh


def duck(p):
    t = 0.6 * S(p)
    sh = [
        (BODY, ("e", 12, 13, 9, 5)),
        (BODY, ("p", [(2, 9), (6, 11), (4, 14)])),
        (BODY, ("c", 18, 11, 18.5 + t * 0.3, 7, 2.4)),
        (BODY, ("e", 19.5 + t * 0.3, 5.5, 3.8, 3.6)),
        (BODY, ("p", [(22.5 + t * 0.3, 4.6), (27.5 + t * 0.3, 6.3), (22.5 + t * 0.3, 7.6)])),
        (BODY, ("c", 10 + t, 17, 11 + t * 2, 19.4, 0.8)),
        (BODY, ("c", 14 - t, 17, 15 - t * 2, 19.4, 0.8)),
    ]
    sh += [
        (HOLE, ("e", 20.5 + t * 0.3, 4.6, 0.7, 0.8)),
        (HOLE, ("c", 7, 11.5, 15, 13, 0.45)),
        (HOLE, ("c", 9, 13.5, 15.5, 13.8, 0.45)),
    ]
    return 28, 20, sh


def snail(p):
    br = 0.8 * S(p)
    sh = [
        (BODY, ("c", 2, 15.5, 25, 15.5, 2.0)),
        (BODY, ("c", 23, 15, 26, 10.5, 1.7)),
        (BODY, ("c", 25, 11, 24.5 - br * 0.3, 5 + br, 0.5)),
        (BODY, ("c", 26.4, 11, 28.4, 5.6 + br, 0.5)),
        (BODY, ("e", 24.5 - br * 0.3, 5 + br, 1.1, 1.1)),
        (BODY, ("e", 28.4, 5.6 + br, 1.1, 1.1)),
        (BODY, ("e", 13, 8, 7.2, 7.2)),
    ]
    sh += [
        (HOLE, ("ring", 13, 8, 4.4, 5.1)),
        (HOLE, ("ring", 13.6, 7.4, 1.6, 2.2)),
        (HOLE, ("c", 4, 16.8, 22, 16.8, 0.35)),
    ]
    return 30, 18, sh


# --- sea ---------------------------------------------------------------------
def whale(p):
    f = 2.0 * S(p)
    sh = [
        (BODY, ("e", 28, 12, 19, 7.5)),
        (BODY, ("c", 11, 12, 5, 10 + f * 0.4, 2.6)),
        (BODY, ("p", [(6, 10 + f * 0.4), (0.5, 3 + f), (3, 11 + f * 0.4)])),
        (BODY, ("p", [(6, 10.5 + f * 0.4), (0.5, 18 + f), (3, 10 + f * 0.4)])),
        (BODY, ("p", [(26, 17), (31, 21), (33, 17)])),
    ]
    sh += [
        (HOLE, ("e", 41, 11, 1.0, 1.1)),
        (HOLE, ("c", 36, 14.6, 47, 13.4, 0.4)),
        (HOLE, ("c", 18, 16.6, 38, 17.6, 0.35)),
        (HOLE, ("c", 20, 15.2, 39, 16.2, 0.35)),
        (DETAIL, ("e", 33, 6.4, 1.2, 0.8)),
    ]
    return 48, 22, sh


def boat(p):
    fl = 1.0 * S(p)
    sh = [
        (BODY, ("p", [(0, 20), (40, 20), (34, 28), (6, 28)])),
        (BODY, ("r", 19, 2, 21, 20)),
        (BODY, ("p", [(21.5, 3), (21.5, 18.5), (34, 18.5)])),
        (BODY, ("p", [(18.5, 5), (18.5, 18.5), (8, 18.5)])),
        (BODY, ("p", [(21, 0.5), (27, 1.8 + fl), (21, 3.5)])),
    ]
    sh += [
        (DETAIL, ("e", 12, 24, 1.3, 1.3)),
        (DETAIL, ("e", 20, 24, 1.3, 1.3)),
        (DETAIL, ("e", 28, 24, 1.3, 1.3)),
        (HOLE, ("c", 2, 21.4, 38, 21.4, 0.35)),
        (HOLE, ("c", 22.5, 10, 27, 15.4, 0.35)),
    ]
    return 40, 28, sh


def fish(p):
    f = 1.4 * S(p)
    sh = [
        (BODY, ("e", 13, 6, 8, 4.5)),
        (BODY, ("p", [(6, 6), (0.5, 1.5 + f), (0.5, 10.5 + f)])),
        (BODY, ("p", [(10, 2), (14, 0.4), (16, 2.5)])),
    ]
    sh += [
        (HOLE, ("e", 17.4, 5, 0.9, 0.9)),
        (HOLE, ("c", 14.4, 3.2, 14.4, 8.8, 0.4)),
        (DETAIL, ("c", 6.5, 6, 12, 6.4, 0.5)),
    ]
    return 22, 12, sh


# --- sky ---------------------------------------------------------------------
def bird(p):
    f = S(p)
    sh = [
        (BODY, ("e", 12, 9, 5, 2.5)),
        (BODY, ("e", 17, 8, 2.3, 2.2)),
        (BODY, ("p", [(19, 7.4), (22.5, 8.4), (19, 9.3)])),
        (BODY, ("p", [(7.5, 8.5), (1.5, 6.5), (2, 11)])),
        (BODY, ("p", [(8.5, 9), (14.5, 9), (11, 9 - 7 * f)])),
    ]
    sh += [(HOLE, ("e", 17.6, 7.4, 0.6, 0.6))]
    return 23, 17, sh


def balloon(p):
    s = 0.4 * S(p)
    sh = [
        (BODY, ("e", 12, 11, 11, 11)),
        (BODY, ("p", [(3, 15), (21, 15), (14.5, 24), (9.5, 24)])),
        (HOLE, ("e", 12, 11.5, 6, 11.2)),
        (BODY, ("e", 12, 11.5, 4.8, 11.2)),
        (BODY, ("c", 10, 24, 10.4 + s, 28, 0.45)),
        (BODY, ("c", 14, 24, 13.6 + s, 28, 0.45)),
        (BODY, ("r", 9 + s, 28, 15 + s, 33)),
    ]
    sh += [
        (HOLE, ("c", 2, 12, 22, 12, 0.4)),
        (DETAIL, ("r", 9.5 + s, 30, 14.5 + s, 30.8)),
    ]
    return 24, 34, sh


# --- props -------------------------------------------------------------------
def cactus(p):
    sh = [
        (BODY, ("c", 7, 3, 7, 23, 2.6)),
        (BODY, ("c", 7, 14, 2, 14, 1.6)),
        (BODY, ("c", 2, 14, 2, 8, 1.6)),
        (BODY, ("c", 7, 11, 12, 11, 1.6)),
        (BODY, ("c", 12, 11, 12, 5, 1.6)),
    ]
    sh += [
        (HOLE, ("c", 7, 2, 7, 22, 0.35)),
        (HOLE, ("c", 5.4, 4, 5.4, 22, 0.3)),
        (HOLE, ("c", 8.6, 4, 8.6, 22, 0.3)),
    ]
    return 14, 26, sh


def tumbleweed(p):
    a = p * math.pi
    sh = [(BODY, ("ring", 6, 6, 4.0, 5.6))]
    for k in range(3):
        ang = a + k * math.pi / 3
        dx, dy = math.cos(ang) * 5, math.sin(ang) * 5
        sh.append((BODY, ("c", 6 - dx, 6 - dy, 6 + dx, 6 + dy, 0.5)))
    sh.append((HOLE, ("ring", 6, 6, 2.2, 2.7)))
    return 12, 12, sh


def car(p):
    sh = [
        (BODY, ("r", 1, 3, 20, 7)),
        (BODY, ("p", [(5, 3.2), (7.5, 0.4), (13.5, 0.4), (16, 3.2)])),
        (BODY, ("e", 5, 7, 2.1, 2.1)),
        (BODY, ("e", 16, 7, 2.1, 2.1)),
    ]
    sh += [
        (HOLE, ("p", [(6.6, 3), (8.2, 1.3), (10.1, 1.3), (10.1, 3)])),
        (HOLE, ("p", [(10.9, 1.3), (12.8, 1.3), (14.4, 3), (10.9, 3)])),
        (HOLE, ("e", 5, 7, 0.8, 0.8)),
        (HOLE, ("e", 16, 7, 0.8, 0.8)),
        (DETAIL, ("e", 19.4, 4.4, 0.8, 0.9)),
    ]
    return 21, 10, sh


LAND = {f.__name__: f for f in (pig, cat, fox, bear, rabbit, duck, snail)}
SEA = {f.__name__: f for f in (whale, boat, fish)}
SKY = {f.__name__: f for f in (bird, balloon)}


def pick(table, seed):
    names = sorted(table)
    name = names[seed % len(names)]
    return name, table[name]


# --- foreground props (near layer, pass in front of everything) -------------
def fg_tree(p):
    sh = [
        (BODY, ("r", 13, 22, 17, 48)),
        (BODY, ("e", 15, 14, 12, 10)),
        (BODY, ("e", 8, 20, 7, 6)),
        (BODY, ("e", 22, 19, 8, 6.5)),
        (BODY, ("c", 15, 30, 7, 25, 1.2)),
    ]
    sh += [(HOLE, ("e", 11, 12, 1.2, 0.8)), (HOLE, ("e", 19, 16, 1.0, 0.7)), (HOLE, ("e", 14, 20, 0.9, 0.6))]
    return 30, 48, sh


def fg_pine(p):
    sh = [(BODY, ("r", 9, 34, 12, 48))]
    for i, (y, wd) in enumerate([(2, 3), (9, 6), (17, 8), (25, 10)]):
        sh.append((BODY, ("p", [(10.5, y), (10.5 - wd, y + 11), (10.5 + wd, y + 11)])))
    return 21, 48, sh


def fg_pole(p):
    sh = [
        (BODY, ("r", 5, 2, 7, 48)),
        (BODY, ("r", 0, 6, 12, 7.2)),
        (BODY, ("r", 1, 11, 11, 12)),
        (BODY, ("e", 1, 5.4, 0.8, 1.0)), (BODY, ("e", 11, 5.4, 0.8, 1.0)),
    ]
    return 12, 48, sh


def fg_fence(p):
    sh = [(BODY, ("r", 0, 30, 40, 32)), (BODY, ("r", 0, 38, 40, 40))]
    for x in (2, 15, 28):
        sh.append((BODY, ("p", [(x, 28), (x + 2, 25), (x + 4, 28), (x + 4, 48), (x, 48)])))
    return 40, 48, sh


def fg_tuft(p):
    sh = []
    for i, (x, h, lean) in enumerate([(3, 14, -2), (6, 20, 1), (9, 16, 3), (12, 22, -1), (15, 13, 2), (18, 18, 0)]):
        sh.append((BODY, ("c", x, 48, x + lean, 48 - h, 0.8)))
    return 21, 48, sh


def sit_raster(fn, scale, amount):
    """Sitting / lying pose for a creature: the legs fold under the body
    (bottom part of the silhouette is removed and the body lowered), and
    small cut-outs (eyes, nostrils) close as `amount` approaches 1."""
    code = raster(fn, 0.0, scale)
    if amount <= 0.01:
        return code
    H, W = code.shape
    fold = int(round(H * 0.28 * amount))
    out = np.zeros_like(code)
    if fold > 0:
        out[fold:] = code[:H - fold]
        # keep the feet line: whatever reached the bottom stays as a flat base
        base = code[H - 1] > 0
        out[H - 1][base] = BODY
    else:
        out[:] = code
    if amount > 0.6:
        out = _close_small_holes(out)
    return out


_sleep_cache = {}


def _close_small_holes(code, max_px=10):
    key = code.tobytes() + bytes(code.shape)
    hit = _sleep_cache.get(key)
    if hit is not None:
        return hit
    out = code.copy()
    H, W = code.shape
    seen = np.zeros(code.shape, bool)
    for y in range(H):
        for x in range(W):
            if code[y, x] == HOLE and not seen[y, x]:
                stack, comp = [(y, x)], []
                seen[y, x] = True
                while stack:
                    cy, cx = stack.pop()
                    comp.append((cy, cx))
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < H and 0 <= nx < W and not seen[ny, nx] and code[ny, nx] == HOLE:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
                if len(comp) <= max_px:
                    for cy, cx in comp:
                        out[cy, cx] = BODY
    if len(_sleep_cache) > 300:
        _sleep_cache.clear()
    _sleep_cache[key] = out
    return out


FOREGROUND = {
    "hills": [fg_tree, fg_fence, fg_tuft],
    "forest": [fg_tree, fg_pine, fg_tuft],
    "rain": [fg_pole, fg_tree, fg_tuft],
    "snow": [fg_pine, fg_fence],
    "desert": [fg_tuft, fg_pole],
    "city": [fg_pole, fg_fence],
    "night": [fg_pine, fg_tree],
    "aurora": [fg_pine],
}
