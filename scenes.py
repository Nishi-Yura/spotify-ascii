"""Animated ASCII scenes. Each scene draws into a Frame given time + audio."""
import math
import time
import numpy as np

from noise import fbm1, fbm2, vnoise1, hash01
import sprites

RAMP_SOFT = np.array(list(" .`-':;=+*#%@"))
RAMP_HATCH = np.array(list(" .,-~=+#%@"))
RAMP_DOTS = np.array(list(" .:*oO@"))
RAMP_CLOUD = np.array(list(" .,:;!|/%"))
RAMP_STAR = np.array(list(" .+*"))
GRASS = np.array(list("|'`,;:"))

_BAYER = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21],
]) / 64.0
_cache = {}

# Shared per-frame environment, set by main.py before each draw:
#   p     playback progress 0..1 (drives the time of day)
#   beats audio beat counter (for shooting stars)
#   front glyphs drawn in front of a walking creature's feet
ENV = {"p": 0.0, "beats": 0, "front": None,
       "pos": 0.0, "dur": 0.0,      # playback position / length in seconds
       "sleep": 0.0,                # 0 awake .. 1 asleep (paused)
       "now": 0.0,                  # wall clock (animations that run while paused)
       "cam": 0.0}                  # camera pan in cells; layers scroll by cam * depth
ENTER_EXIT = 5.0                    # seconds to walk in / out at a song's start / end
STAR_COL = (236, 232, 214)


PAN_SPEED = 0.6       # cells per second for the nearest ground layer (depth 1.0)


def cam(k):
    """Horizontal scroll for a layer at depth k (0 = sky, 1 = near ground)."""
    return ENV["cam"] * k


def smoothstep(a, b, x):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def time_of_day(p=None):
    """-> (dusk, night) weights for playback progress p.
    Day for the first part of the song, sunset around the middle-late part,
    night (stars, moon, shooting stars) towards the final chorus."""
    p = ENV["p"] if p is None else p
    night = smoothstep(0.66, 0.84, p)
    dusk = smoothstep(0.38, 0.62, p) * (1.0 - night)
    return dusk, night


def grade(fr, strength=1.0):
    """Colour-grade a flattened frame for the time of day (cool and dark at
    night, slightly warm at sunset). Call after flatten, before the HUD."""
    dusk, night = time_of_day()
    k = night * 0.5 * strength
    wk = dusk * 0.12 * strength
    if k <= 0.001 and wk <= 0.001:
        return
    navy = np.array([8.0, 12.0, 34.0], np.float32)
    warm = np.array([255.0, 140.0, 70.0], np.float32)
    for arr in (fr.fg, fr.bg):
        a = arr.astype(np.float32)
        a = a * (1 - k) + navy * k
        a = a * (1 - wk) + warm * wk
        arr[:] = np.clip(a, 0, 255).astype(np.uint8)


_shots = []
_last_beats = [0]


def grid(fr):
    """Coordinate grids in *cell units* at the frame's drawing resolution
    (sub-cell steps in fine mode), so scene maths is resolution independent."""
    k = ("g", fr.W, fr.H, fr.sx, fr.sy)
    if k not in _cache:
        X, Y = np.meshgrid(np.arange(fr.W, dtype=np.float64) / fr.sx,
                           np.arange(fr.H, dtype=np.float64) / fr.sy)
        _cache[k] = (X, Y)
    return _cache[k]


def bayer(w, h):
    k = ("b", w, h)
    if k not in _cache:
        _cache[k] = np.tile(_BAYER, (h // 8 + 1, w // 8 + 1))[:h, :w]
    return _cache[k]


def shade(v, ramp=RAMP_SOFT, dither=0.35):
    h, w = v.shape
    n = len(ramp)
    idx = np.floor((v + (bayer(w, h) - 0.5) * dither) * (n - 1) + 0.5)
    idx = np.where(v <= 0.015, 0, idx)   # dither never invents ink in empty areas
    return ramp[np.clip(idx, 0, n - 1).astype(np.int32)]


def sil(pal):
    """Intensity tint for a silhouette (dark mass in dark mode, ink on paper)."""
    return 0.9 if pal.paper else 0.3


def paint(fr, v, pal, mask=None, ramp=RAMP_SOFT, dither=0.35, tint=None, color=None,
          opaque=False, literal=False, fgmap=None):
    """v: intensity field (H,W) in 0..1 (0 = empty, 1 = densest ink).
    fgmap: explicit per-cell colours (H,W,3). literal: always use ramp
    characters (one per cell) even in fine mode - for stars, glyphs etc."""
    v = np.asarray(v, np.float64)
    if v.ndim == 0:
        v = np.full((fr.H, fr.W), float(v))
    if color is None and fgmap is None and tint is not None and np.ndim(tint) == 0:
        color = pal.tone(float(tint))

    if fr.fine and not literal:
        on = v > bayer(fr.W, fr.H) * (1.0 - 0.02)
        if opaque:
            m = mask if mask is not None else np.ones(v.shape, bool)
            fr.dots[m] = on[m]
            fr.chars[m] = " "
        else:
            m = on
            if mask is not None:
                m = m & mask
            fr.dots[m] = True
        # colours only for the pixels actually touched
        if fgmap is not None:
            fr.fg[m] = fgmap[m]
        elif color is not None:
            fr.fg[m] = color
        else:
            src = v if tint is None else tint
            fr.fg[m] = pal.grad(src[m] if np.ndim(src) else src)
        return

    if fgmap is not None:
        fg = fgmap
    elif color is not None:
        fg = np.empty(v.shape + (3,), np.uint8)
        fg[:] = color
    else:
        fg = pal.grad(v if tint is None else tint)

    if fr.fine:  # literal glyphs in fine mode: one char per cell
        h, w, sy, sx = fr.h, fr.w, fr.sy, fr.sx
        vc = v.reshape(h, sy, w, sx).max(axis=(1, 3))
        fgc = fg[::sy, ::sx]
        if mask is not None:
            mask = mask.reshape(h, sy, w, sx).any(axis=(1, 3))
        ch = shade(vc, ramp, dither)
        m = ch != " "
        if mask is not None:
            m = m & mask
        sub_c = fr.chars[::sy, ::sx]
        sub_f = fr.fg[::sy, ::sx]
        sub_c[m] = ch[m]
        sub_f[m] = fgc[m]
        return

    ch = shade(v, ramp, dither)
    if opaque:
        m = mask if mask is not None else np.ones(v.shape, bool)
    else:
        m = ch != " "
        if mask is not None:
            m = m & mask
    fr.chars[m] = ch[m]
    fr.fg[m] = fg[m]


def sky(fr, pal, horizon, t, seed, clouds=0.5, stars=0.0, sun=None, moon=None):
    h, w = fr.h, fr.w
    X, Y = grid(fr)
    dusk, night = time_of_day()
    warm = np.array([250.0, 120.0, 70.0]) * 0.75 + np.asarray(pal.accent, float) * 0.25
    for y in range(h):
        tt = min(1.0, y / max(1, horizon))
        c = np.asarray(pal.sky(tt), float)
        c = c + (warm - c) * dusk * (0.15 + 0.6 * tt ** 1.5)
        c = c + (np.array([6.0, 8.0, 26.0]) - c) * night * 0.55
        fr.bg[y] = np.clip(c, 0, 255).astype(np.uint8)
    above = Y < horizon
    stars = max(stars, night * 1.4)
    if stars > 0:
        key = ("stars", fr.W, fr.H, seed)
        r = _cache.get(key)
        if r is None:                       # star positions never change: hash once
            r = _cache[key] = hash01(X, Y, seed + 7)
        m = (r < stars * 0.03) & above
        rm = r[m]
        v = np.zeros(r.shape)
        v[m] = 0.4 + 0.6 * (0.5 + 0.5 * np.sin(t * (1.5 + (rm * 400) % 3) + rm * 1000))
        if night > 0.3 or not pal.paper:
            paint(fr, v, pal, mask=m, ramp=RAMP_STAR, dither=0.0, color=STAR_COL, literal=True)
        else:
            paint(fr, v, pal, mask=m, ramp=RAMP_STAR, dither=0.0, tint=np.clip(v + 0.2, 0, 1), literal=True)
    # shooting stars on the beat once it is night
    if night > 0.5 and ENV["beats"] != _last_beats[0]:
        _last_beats[0] = ENV["beats"]
        if len(_shots) < 3 and np.random.random() < 0.45:
            _shots.append([np.random.uniform(0.1, 0.9) * w, np.random.uniform(0, 0.35) * horizon,
                           np.random.choice([-1, 1]) * np.random.uniform(35, 55), np.random.uniform(7, 11), 0.0])
    keep = []
    for sh in _shots:
        sh[0] += sh[2] / 30.0
        sh[1] += sh[3] / 30.0
        sh[4] += 1 / 30.0
        if sh[4] < 0.9 and 0 <= sh[1] < horizon and night > 0.3:
            for k in range(8):
                c = "*" if k == 0 else ("\\" if sh[2] > 0 else "/")
                fr.put(int(sh[0] - k * sh[2] * 0.025), int(sh[1] - k * sh[3] * 0.025), c,
                       STAR_COL if k < 3 else pal.tone(0.6))
            keep.append(sh)
    _shots[:] = keep
    if sun is not None:
        sx, sy, r = sun
        sink = smoothstep(0.25, 0.72, ENV["p"])
        sy = sy + (horizon + r - sy) * sink
        if sy - r < horizon and night < 0.6:
            col = tuple(int(v) for v in np.asarray(pal.accent, float) * (1 - dusk) + warm * dusk)
            d = np.sqrt(((X - sx) / 2.0) ** 2 + (Y - sy) ** 2)
            paint(fr, np.clip(r + 0.5 - d, 0, 1), pal, mask=above, ramp=RAMP_SOFT, dither=0.15, color=col)
        if moon is None and night > 0.35:
            moon = (w - sx, int(horizon * 0.25), max(2, r))
    if clouds > 0:
        n = fbm2(X * 0.045 + t * 0.03 + seed % 100, Y * 0.16 + seed % 37, seed + 3, 3)
        v = np.clip((n - (0.62 - clouds * 0.22)) * 3.2, 0, 1)
        v *= np.clip(1.2 - Y / max(1, horizon), 0, 1)
        paint(fr, v * 0.75, pal, mask=above, ramp=RAMP_CLOUD, dither=0.5, tint=0.55)
    if moon is not None:
        sx, sy, r = moon
        d = np.sqrt(((X - sx) / 2.0) ** 2 + (Y - sy) ** 2)
        d2 = np.sqrt(((X - sx - r * 1.1) / 2.0) ** 2 + (Y - sy + r * 0.3) ** 2)
        v = np.clip(r + 0.5 - d, 0, 1) * (d2 > r * 0.95)
        paint(fr, v, pal, mask=above, ramp=RAMP_SOFT, dither=0.1,
              color=STAR_COL if (night > 0.3 or pal.paper) else pal.ink)


def sprite_scale(fr):
    return float(np.clip(fr.h / 34.0, 0.8, 2.0))


def creature(fr, fn, phase, x, bottom, pal, scale=None, flip=False, lift=0.0, col=None, code=None):
    """Draw silhouette `fn` with its left edge at cell x and its feet on
    cell row `bottom` (both may be fractional, sub-cell smooth)."""
    if code is None:
        code = sprites.raster(fn, phase, scale or sprite_scale(fr), flip)
    px = int(round(x * fr.sx))
    py = int(round((bottom - lift) * fr.sy)) - code.shape[0]
    # colour of the scenery behind the creature (cell background)
    cy0 = max(0, min(fr.h - 1, py // fr.sy))
    cy1 = max(cy0 + 1, min(fr.h, (py + code.shape[0]) // fr.sy))
    cx0 = max(0, min(fr.w - 1, px // fr.sx))
    cx1 = max(cx0 + 1, min(fr.w, (px + code.shape[1]) // fr.sx))
    behind = fr.bg[cy0:cy1, cx0:cx1].reshape(-1, 3).mean(axis=0)
    if col is None:
        # body colour sits between the scenery and the accent so it belongs
        # to the picture instead of floating on top of it
        col = tuple(int(v) for v in np.asarray(pal.tone(0.62), float) * 0.45
                    + np.asarray(pal.accent, float) * 0.35 + behind * 0.2)
    fr.silhouette(px, py, code, col, pal.ink)
    return code.shape[1] / fr.sx


def creature_width(fr, fn, scale=None):
    return sprites.raster(fn, 0.0, scale or sprite_scale(fr)).shape[1] / fr.sx


def _ease(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def seek_x(fr, width):
    """Left edge for an object of `width` cells whose centre sits above the
    seek bar's playhead (the bar spans roughly columns 7 .. w-7). In the
    first / last few seconds of a song it walks in from the left edge /
    off the right edge, so creatures hand over from one song to the next."""
    x0, x1 = 7, fr.w - 7
    x = x0 + (x1 - x0) * min(1.0, max(0.0, ENV["p"])) - width / 2.0
    pos, dur = ENV["pos"], ENV["dur"]
    if dur > ENTER_EXIT * 3:
        if pos < ENTER_EXIT:
            x = -width - 1 + (x + width + 1) * _ease(pos / ENTER_EXIT)
        rem = dur - pos
        if rem < ENTER_EXIT:
            x = x + (fr.w + 1 - x) * _ease(1 - rem / ENTER_EXIT)
    return x


def zzz(fr, x, y, pal):
    """Little z's floating up from a sleeping creature (cell coords)."""
    now = ENV["now"]
    for k in range(3):
        ph = (now * 0.45 + k / 3.0) % 1.0
        cx = int(round(x + ph * 3.0))
        cy = int(round(y - ph * 3.5))
        c = "z" if ph < 0.4 else "Z"
        col = pal.tone(0.4 + 0.5 * (1 - ph)) if not pal.paper else pal.tone(0.9 - 0.5 * ph)
        fr.overlay(cx, cy, c, col)


def cast_shadow(fr, code, px, py):
    """Shadow with the creature's own shape, cast on the ground below it,
    drawn with the same braille dots as the rest of the picture.
    The silhouette is flipped at the feet, squashed and slanted; how dark and
    how long it is follows the time of day: crisp and short in the day,
    long and slanted at sunset, gone at night."""
    dusk, night = time_of_day()
    alpha = 0.6 * (1.0 - night) * (1.0 - 0.25 * dusk)
    if alpha < 0.03 or not fr.fine:
        return
    k = 0.32 + 0.7 * dusk                  # vertical squash (longer at sunset)
    shear = 0.25 + 1.6 * dusk              # sideways slant per shadow row
    H, W = code.shape
    filled = code > 0
    hs = max(1, int(round(H * k)))
    j = np.arange(hs)
    src = np.clip(H - 1 - (j / k).astype(int), 0, H - 1)   # flip at the feet
    rows = filled[src]
    shift = np.round(j * shear).astype(int)
    pad = int(shift.max()) if hs else 0
    m = np.zeros((hs, W + pad), bool)
    for r in range(hs):
        m[r, shift[r]:shift[r] + W] = rows[r]
    y0, x0 = py + H, px
    ys0, xs0 = max(0, y0), max(0, x0)
    ys1, xs1 = min(fr.H, y0 + hs), min(fr.W, x0 + W + pad)
    if ys0 >= ys1 or xs0 >= xs1:
        return
    sub = m[ys0 - y0:ys1 - y0, xs0 - x0:xs1 - x0]
    # density fades towards the far end; ordered dither keeps it smooth
    fade = 1.0 - 0.6 * (np.arange(ys0 - y0, ys1 - y0) / max(1, hs - 1))
    dens = alpha * 1.4 * fade[:, None]
    on = sub & (bayer(xs1 - xs0, ys1 - ys0) < dens)
    # shadow colour: the ground's background, darkened
    bgc = fr.bg[ys0 // fr.sy:(ys1 - 1) // fr.sy + 1, xs0 // fr.sx:(xs1 - 1) // fr.sx + 1]
    base = bgc.reshape(-1, 3).mean(axis=0)
    col = (base * (1 - alpha) * 0.55).astype(np.uint8)
    reg_c = fr.chars[ys0:ys1, xs0:xs1]
    reg_d = fr.dots[ys0:ys1, xs0:xs1]
    reg_f = fr.fg[ys0:ys1, xs0:xs1]
    # inside the shadow: existing ground marks get darker ...
    reg_f[sub] = (reg_f[sub].astype(np.float32) * (1 - alpha * 0.8)).astype(np.uint8)
    # ... and the shape itself is filled with dark dots
    reg_c[on] = " "
    reg_d[on] = True
    reg_f[on] = col


def walker(fr, t, au, pal, fn, ground_y, speed=5.0):
    """Creature walking with its feet on row ground_y. It travels across the
    screen with the song (its centre follows the seek bar's playhead), while
    the legs keep time with the music: one stride every two beats."""
    if fn is None:
        return
    sw = creature_width(fr, fn)
    x = seek_x(fr, sw)
    gy = int(ground_y)
    phase = (au.beat_pos * 0.5) % 1.0
    sleep = ENV["sleep"]
    if sleep > 0.01:
        code = sprites.sit_raster(fn, sprite_scale(fr), sleep)
    else:
        code = sprites.raster(fn, phase, sprite_scale(fr))
    cast_shadow(fr, code, int(round(x * fr.sx)), int(round(ground_y * fr.sy)) - code.shape[0])
    creature(fr, fn, phase, x, ground_y, pal, code=code)
    if sleep > 0.8:
        zzz(fr, x + sw * 0.8, ground_y - code.shape[0] / fr.sy - 0.5, pal)


def flyer(fr, t, au, pal, fn, y0, speed=4.0):
    """Flies across with the song (follows the seek bar); wings flap once per beat."""
    if fn is None:
        return
    sw = creature_width(fr, fn)
    x = seek_x(fr, sw)
    y = y0 + math.sin(t * 1.2) * 2
    sleep = ENV["sleep"]
    phase = 0.0 if sleep > 0.5 else au.beat_pos % 1.0
    creature(fr, fn, phase, x, y, pal)
    if sleep > 0.8:
        h = sprites.raster(fn, 0.0, sprite_scale(fr)).shape[0] / fr.sy
        zzz(fr, x + sw * 0.8, y - h - 0.5, pal)


def foreground(fr, scene, t, pal):
    """Near-layer props (trees, poles, fences, grass) sliding past in front
    of everything, slower than nothing and faster than the far hills, which
    gives the picture depth."""
    kinds = sprites.FOREGROUND.get(scene.name)
    if not kinds:
        return
    scale = sprite_scale(fr) * 0.85
    if pal.paper:
        col = tuple(int(v) for v in np.asarray(pal.ink, float) * 0.85 + np.asarray(pal.bg, float) * 0.15)
    else:
        col = tuple(int(v) for v in np.asarray(pal.bg, float) * 0.45)
    period = fr.w + 120
    speed = 1.6                                  # cells per second (scene time)
    rng = np.random.default_rng(scene.seed)
    base = rng.uniform(0, period)
    for i in range(2):
        fn = kinds[int(rng.integers(0, len(kinds)))]
        off = base + i * period / 2 + rng.uniform(-10, 10)   # always well apart
        x = (off - t * speed) % period - 40
        code = sprites.raster(fn, 0.0, scale)
        px = int(round(x * fr.sx))
        py = (fr.h + 2) * fr.sy - code.shape[0]
        fr.silhouette(px, py, code, col, col)


# glyphs drawn in front of a walking creature's feet, per scene
FRONT_FOR = {"hills": ",;'|`", "forest": ".,'`", "rain": ".,'", "snow": ".:'",
             "desert": ".,~", "city": None}


class Scene:
    name = "base"
    kind = "land"   # land | sea | sky | none

    def __init__(self, seed, art=None, media=None, creature_seed=None):
        self.seed = int(seed) & 0xFFFFFFFF
        self.creature_seed = self.seed if creature_seed is None else int(creature_seed) & 0xFFFFFFFF
        self.art = art        # album art bytes (may be None)
        self.media = media    # local clip path for this track (may be None)
        self.rng = np.random.default_rng(self.seed)
        self.front = FRONT_FOR.get(self.name)
        table = {"land": sprites.LAND, "sea": sprites.SEA, "sky": sprites.SKY}.get(self.kind)
        if table:
            self.sprite_name, self.sprite = sprites.pick(table, self.creature_seed >> 8)
        else:
            self.sprite_name, self.sprite = "", None
        self.setup()

    def setup(self):
        pass

    def draw(self, fr, t, dt, au, pal):
        pass


# ---------------------------------------------------------------------------
class Hills(Scene):
    name = "hills"
    kind = "land"

    def setup(self):
        r = self.rng
        self.layers = int(r.integers(3, 5))
        self.cloud = float(r.uniform(0.3, 0.9))
        self.sunx = float(r.uniform(0.15, 0.85))
        self.drift = float(r.uniform(0.5, 1.5))
        self.off = r.uniform(0, 100, 8)
        self.speed = float(r.uniform(3, 7))

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        horizon = int(h * 0.5)
        sky(fr, pal, horizon, t, self.seed, clouds=self.cloud, sun=(int(self.sunx * w), int(h * 0.14), 2))
        x = X[0]
        tex0 = fbm2(X * 0.09, Y * 0.45, self.seed + 10, 3)
        for i in range(self.layers):
            depth = i / max(1, self.layers - 1)
            amp = h * (0.05 + 0.10 * depth)
            base = horizon + (h - horizon) * (0.1 + 0.6 * depth)
            n = fbm1(x * (0.012 + 0.006 * i) + self.off[i] + t * 0.015 * (i + 1) * self.drift, self.seed + i, 4)
            ridge = base - amp * (2 * n - 1)
            mask = Y >= ridge[None, :]
            tex = np.roll(tex0, (int(self.off[i] * 7) - int(cam(0.3 + 0.7 * depth) * fr.sx)) % fr.W, axis=1)
            edge = np.clip(1 - (Y - ridge[None, :]) / 2.5, 0, 1)
            v = 0.22 + 0.5 * depth + (tex - 0.5) * 0.35 + edge * 0.2
            paint(fr, np.clip(v, 0, 1), pal, mask=mask, ramp=RAMP_HATCH, dither=0.45, tint=0.35 + 0.6 * depth, opaque=True)
        g0 = h - 5
        sway = np.round(np.sin(t * 2.5 + X * 0.25) * (0.6 + 2.0 * au.bass)).astype(np.int64)
        r = hash01(X + sway + cam(1.0), Y, self.seed + 99)
        gm = (Y >= g0) & (r < 0.5 + 0.1 * (Y - g0))
        gc = GRASS[(r * 6).astype(int) % 6]
        fr.chars[gm] = gc[gm]
        fr.fg[gm] = pal.tone(0.9)
        walker(fr, t, au, pal, self.sprite, g0 + 1, self.speed)


class Ocean(Scene):
    name = "ocean"
    kind = "sea"

    def setup(self):
        r = self.rng
        self.night = bool(r.random() < 0.4)
        self.k = r.uniform(0.08, 0.2, 3)
        self.s = r.uniform(0.8, 2.5, 3)
        self.a = np.array([0.5, 0.3, 0.2])
        self.sunx = float(r.uniform(0.2, 0.8))
        self.speed = float(r.uniform(1, 3))

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        horizon = int(h * 0.42)
        sx = int(self.sunx * w)
        sky(fr, pal, horizon, t, self.seed, clouds=0.35, stars=0.8 if self.night else 0.0,
            sun=None if self.night else (sx, horizon - 4, 2), moon=(sx, horizon - 5, 2) if self.night else None)
        d = np.clip((Y - horizon) / max(1, h - horizon), 0, 1)
        amp = 0.6 + 0.9 * au.bass
        wave = np.zeros_like(X)
        for i in range(3):
            wave += self.a[i] * np.sin(X * self.k[i] * (0.6 + d) + t * self.s[i] + Y * 0.8 * (i + 1) + i)
        foam = fbm2(X * 0.05 - t * 0.25, Y * 0.6, self.seed, 3)
        v = 0.15 + 0.38 * d + wave * 0.22 * amp + (foam - 0.5) * 0.15
        mask = Y >= horizon
        paint(fr, np.clip(v, 0, 1), pal, mask=mask, ramp=RAMP_SOFT, dither=0.45,
              tint=np.clip(0.25 + 0.6 * d + wave * 0.15, 0, 1), opaque=True)
        crest = mask & (wave * amp > 0.62)
        cc = np.where(hash01(X, Y, self.seed + 5) < 0.5, "~", "^")
        fr.chars[crest] = cc[crest]
        fr.fg[crest] = pal.ink
        glit = mask & (np.abs(X - sx) < 2 + 8 * d) & (hash01(X + np.floor(t * 8), Y, self.seed + 9) < 0.08 + 0.3 * au.high)
        gc = np.where(hash01(X, Y + np.floor(t * 8), self.seed) < 0.5, "*", "+")
        fr.chars[glit] = gc[glit]
        fr.fg[glit] = pal.accent
        if self.sprite:
            sw = creature_width(fr, self.sprite)
            x = seek_x(fr, sw)
            bob = math.sin(t * 1.5) * 0.6
            if self.sprite_name == "boat":
                bottom = horizon + 2.5 + bob
            else:
                bottom = horizon + (h - horizon) * 0.6 + bob * 2
            creature(fr, self.sprite, (au.beat_pos * 0.5) % 1.0, x, bottom, pal)
            if ENV["sleep"] > 0.8:
                hh = sprites.raster(self.sprite, 0.0, sprite_scale(fr)).shape[0] / fr.sy
                zzz(fr, x + sw * 0.8, bottom - hh - 0.5, pal)


class Rain(Scene):
    name = "rain"
    kind = "land"

    def setup(self):
        r = self.rng
        n = 700
        self.px = r.uniform(0, 1, n)
        self.py = r.uniform(0, 1, n)
        self.ps = r.uniform(0.7, 1.3, n)
        self.wind = float(r.uniform(-0.6, 0.6))
        self.flash = 0
        self.bolt = None
        self.ripples = []

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        horizon = int(h * 0.55)
        ground = h - 3
        sky(fr, pal, horizon, t, self.seed, clouds=0.95)
        x = X[0]
        xr = x + cam(0.3)
        ridge = horizon - h * 0.12 * (2 * fbm1(xr * 0.02 + 3, self.seed, 4) - 1)
        v = 0.3 + (fbm2((X + cam(0.3)) * 0.1, Y * 0.4, self.seed + 1, 3) - 0.5) * 0.2
        paint(fr, np.clip(v, 0, 1), pal, mask=Y >= ridge[None, :], ramp=RAMP_HATCH, dither=0.4, tint=0.4 if pal.paper else 0.25, opaque=True)
        xt = x + cam(0.75)
        trees = ground - 2 - np.abs(np.sin(xt * 0.9 + fbm1(xt * 0.3, self.seed + 2, 2) * 6)) * h * 0.18 * (0.5 + fbm1(xt * 0.05, self.seed + 3, 2))
        paint(fr, 0.7, pal, mask=(Y >= trees[None, :]) & (Y < ground), ramp=RAMP_HATCH, dither=0.5, tint=sil(pal), opaque=True)
        fr.bg[ground:] = pal.tone(0.15)
        # rain
        n = int(150 + 500 * min(1.0, au.high * 1.5 + au.level * 0.5))
        speed = 25 + 15 * au.level
        self.py[:n] += self.ps[:n] * speed * dt / h
        self.px[:n] += self.wind * dt * 0.08
        done = self.py >= ground / h
        for i in np.flatnonzero(done[:n])[:6]:
            self.ripples.append([self.px[i] * w, 0.0])
        self.py[done] = self.rng.uniform(0, 0.05, int(done.sum()))
        self.px %= 1.0
        xs = (self.px[:n] * w).astype(int)
        ys = (self.py[:n] * h).astype(int)
        ok = ys < ground
        ch = "|" if abs(self.wind) < 0.25 else ("/" if self.wind < 0 else "\\")
        fr.put_many(xs[ok], ys[ok], ch, pal.tone(0.55))
        keep = []
        for rp in self.ripples:
            rp[1] += dt
            if rp[1] < 0.5:
                rad = int(rp[1] * 6)
                s = "o" if rad == 0 else "(" + " " * (2 * rad - 1) + ")"
                fr.text(int(rp[0]) - rad, ground, s, pal.tone(0.6))
                keep.append(rp)
        self.ripples = keep[-40:]
        # lightning
        if self.flash <= 0 and au.bass > 0.8 and au.pulse() > 0.7 and self.rng.random() < 0.15:
            self.flash = 3
            bx = float(self.rng.uniform(0.1, 0.9) * w)
            pts = [(bx, 0.0)]
            yy = 0.0
            while yy < horizon:
                yy += float(self.rng.integers(2, 5))
                bx += float(self.rng.uniform(-4, 4))
                pts.append((bx, yy))
            self.bolt = pts
        if self.flash > 0 and self.bolt:
            self.flash -= 1
            lit = pal.tone(0.35)
            for y in range(horizon):
                fr.bg[y] = lit
            for (x0, y0), (x1, y1) in zip(self.bolt, self.bolt[1:]):
                steps = max(1, int(y1 - y0))
                for k in range(steps):
                    c = "|" if abs(x1 - x0) < 1.5 else ("\\" if x1 > x0 else "/")
                    fr.put(int(x0 + (x1 - x0) * k / steps), int(y0 + k), c, pal.ink)
        walker(fr, t, au, pal, self.sprite, ground, 2.5)


class Night(Scene):
    name = "night"
    kind = "sky"

    def setup(self):
        r = self.rng
        self.tilt = float(r.uniform(-0.3, 0.3))
        self.moonx = float(r.uniform(0.15, 0.85))
        self.shots = []
        self.lastbeat = 0

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        horizon = int(h * 0.78)
        sky(fr, pal, horizon, t, self.seed, clouds=0, stars=1.2, moon=(int(self.moonx * w), int(h * 0.18), 3))
        band = np.exp(-((Y - (h * 0.35 + (X - w / 2) * self.tilt * 0.5)) / (h * 0.13)) ** 2)
        n = fbm2(X * 0.07 + t * 0.01, Y * 0.3, self.seed + 4, 4)
        v = band * np.clip((n - 0.35) * 1.6, 0, 1) * (0.6 + 0.3 * au.mid)
        paint(fr, v * 0.8, pal, mask=Y < horizon, ramp=RAMP_DOTS, dither=0.6, tint=0.5)
        if au.beat_count != self.lastbeat and len(self.shots) < 4 and self.rng.random() < 0.5:
            self.lastbeat = au.beat_count
            self.shots.append([float(self.rng.uniform(0.1, 0.9) * w), float(self.rng.uniform(0, 0.3) * h),
                               float(self.rng.choice([-1, 1]) * self.rng.uniform(30, 50)), float(self.rng.uniform(6, 10)), 0.0])
        keep = []
        for s in self.shots:
            s[0] += s[2] * dt
            s[1] += s[3] * dt
            s[4] += dt
            if s[4] < 1.0 and 0 <= s[1] < horizon:
                for k in range(7):
                    c = "*" if k == 0 else ("\\" if s[2] > 0 else "/")
                    fr.put(int(s[0] - k * s[2] * 0.03), int(s[1] - k * s[3] * 0.03), c, pal.ink if k < 2 else pal.tone(0.6))
                keep.append(s)
        self.shots = keep
        x = X[0]
        ridge = horizon - h * 0.22 * fbm1((x + cam(0.4)) * 0.02 + 9, self.seed + 8, 4)
        v = 0.7 + (fbm2((X + cam(0.4)) * 0.1, Y * 0.4, self.seed + 2, 2) - 0.5) * 0.3
        paint(fr, np.clip(v, 0, 1), pal, mask=Y >= ridge[None, :], ramp=RAMP_HATCH, dither=0.35, tint=sil(pal), opaque=True)
        flyer(fr, t, au, pal, self.sprite, int(h * 0.5), 4)


class Forest(Scene):
    name = "forest"
    kind = "land"

    def setup(self):
        r = self.rng
        self.nff = int(r.integers(15, 30))
        self.fx = r.uniform(0, 1, 80)
        self.fy = r.uniform(0.3, 0.9, 80)
        self.fph = r.uniform(0, 6.28, 80)
        self.trees = r.uniform(0, 1, 14)
        self.tw = r.integers(1, 3, 14)
        self.th = r.uniform(0.25, 0.5, 14)

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        ground = h - 3
        cy = Y / h
        n = fbm2((X + cam(0.45)) * 0.06 + self.seed % 50, Y * 0.22, self.seed, 4)
        v = np.clip((n - 0.36) * 2.4, 0, 1) * np.clip(1 - (cy - 0.3) / 0.3, 0, 1)
        paint(fr, v * 0.75, pal, ramp=RAMP_DOTS, dither=0.45, tint=0.6)
        fog = fbm2(X * 0.03 + t * 0.12, Y * 0.3 + t * 0.03, self.seed + 5, 3) * np.exp(-((Y - h * 0.68) / (h * 0.12)) ** 2)
        paint(fr, np.clip(fog * 0.6 * (0.6 + 0.5 * au.mid), 0, 1), pal, ramp=RAMP_SOFT, dither=0.4, tint=0.3)
        for i in range(14):
            tx = int((self.trees[i] * (w + 6) - cam(0.85)) % (w + 6)) - 3
            top = int(h * (0.55 - self.th[i] * 0.5))
            m = (X >= tx) & (X < tx + int(self.tw[i])) & (Y >= top) & (Y < ground)
            fr.chars[m] = "|"
            fr.fg[m] = pal.tone(0.9)
        Xg = X + cam(1.0)
        gm = (Y >= ground) & (hash01(Xg, Y, self.seed + 1) < 0.5)
        gc = np.array(list(".,'`"))[(hash01(Xg, Y, self.seed + 2) * 4).astype(int)]
        fr.chars[gm] = gc[gm]
        fr.fg[gm] = pal.tone(0.7)
        n = min(80, int(self.nff + 45 * au.energy))
        ph = self.fph[:n]
        xs = (((self.fx[:n] + 0.04 * np.sin(t * 0.5 + ph)) % 1.0) * w).astype(int)
        ys = (((self.fy[:n] + 0.03 * np.sin(t * 0.8 + ph * 2)) % 1.0) * ground).astype(int)
        b = 0.5 + 0.5 * np.sin(t * 3 + ph) + au.pulse() * 0.5
        for i in range(n):
            c = "*" if b[i] > 1.0 else ("o" if b[i] > 0.6 else ".")
            fr.put(int(xs[i]), int(ys[i]), c, pal.accent)
        walker(fr, t, au, pal, self.sprite, ground + 1, 3)


class Aurora(Scene):
    name = "aurora"
    kind = "sky"

    def setup(self):
        r = self.rng
        self.strength = r.uniform(0.6, 1.0, 3)
        self.sp = r.uniform(0.6, 1.4, 3)

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        lake = int(h * 0.78)
        mount = int(h * 0.68)
        sky(fr, pal, mount, t, self.seed, clouds=0, stars=0.9)
        V = np.zeros((fr.H, fr.W))
        x = X[0]
        for k in range(3):
            center = h * (0.12 + 0.13 * k) + (2 * fbm1(x * 0.013 + t * 0.06 * self.sp[k] + k * 31, self.seed + k, 3) - 1) * h * 0.2
            thick = h * (0.05 + 0.03 * k) * (1 + 0.6 * au.bass)
            streak = vnoise1(x * 0.45 + t * 0.5 * self.sp[k] + k * 7, self.seed + 30 + k)
            dy = Y - center[None, :]
            e = np.exp(-(dy / np.where(dy < 0, thick, thick * 2.2)) ** 2)
            V += e * (0.2 + 0.8 * streak[None, :] ** 2) * self.strength[k] * (0.6 + 0.5 * au.bass)
        V = np.clip(V, 0, 1) ** 1.3
        paint(fr, V * 0.85, pal, mask=Y < mount, ramp=RAMP_SOFT, dither=0.3, tint=np.clip(V * 0.8 + 0.2, 0, 1))
        hot = (V > 0.8) & (Y < mount)
        fr.fg[hot] = pal.accent
        ridge = mount - h * 0.22 * fbm1((x + cam(0.25)) * 0.025 + 5, self.seed + 8, 4)
        mv = 0.7 + (fbm2((X + cam(0.25)) * 0.1, Y * 0.4, self.seed + 2, 2) - 0.5) * 0.3
        paint(fr, np.clip(mv, 0, 1), pal, mask=(Y >= ridge[None, :]) & (Y < lake), ramp=RAMP_HATCH, dither=0.3, tint=sil(pal), opaque=True)
        # lake reflection (mirror the hi-res buffers above the lake line)
        sy, sx = fr.sy, fr.sx
        L = lake * sy
        rows = np.arange(L, fr.H)
        src = np.clip(2 * L - rows - 1, 0, fr.H - 1)
        shift = (np.sin(rows / sy * 0.8 + t * 2.0) * (1 + 2 * au.level) * sx).astype(int)
        Xs = (np.arange(fr.W)[None, :] + shift[:, None]) % fr.W
        dim = hash01(Xs // sx, rows[:, None] // sy, self.seed + 77) < 0.6
        fr.chars[L:] = np.where(dim, fr.chars[src[:, None], Xs], " ")
        fr.fg[L:] = (fr.fg[src[:, None], Xs] * 0.6 + np.asarray(pal.bg) * 0.4).astype(np.uint8)
        if fr.fine:
            fr.dots[L:] = fr.dots[src[:, None], Xs] & dim
        fr.bg[lake:] = pal.tone(0.08)
        flyer(fr, t, au, pal, self.sprite, int(h * 0.45), 3)


class Desert(Scene):
    name = "desert"
    kind = "land"

    def setup(self):
        r = self.rng
        self.sunx = float(r.uniform(0.55, 0.85))
        self.off = r.uniform(0, 100, 3)
        self.cacti = r.uniform(0.05, 0.95, 3)
        self.tumble_x = 0.0

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        horizon = int(h * 0.5)
        sx, sy = int(self.sunx * w), int(h * 0.2)
        sky(fr, pal, horizon, t, self.seed, clouds=0.15, sun=(sx, sy, 4))
        L = 2 + 4 * au.bass
        for a in np.linspace(0, 2 * math.pi, 12, endpoint=False):
            for k in range(6, int(6 + L)):
                yy = int(sy + math.sin(a) * k)
                if yy < horizon - 1:
                    c = "-" if abs(math.sin(a)) < 0.3 else ("|" if abs(math.cos(a)) < 0.3 else ("\\" if math.cos(a) * math.sin(a) > 0 else "/"))
                    fr.put(int(sx + math.cos(a) * k * 2.0), yy, c, pal.accent)
        band = np.exp(-((Y - horizon) / (h * 0.08)) ** 2)
        Xs = X + np.sin(Y * 0.9 + t * 6) * (0.8 + 2.5 * au.level) * band
        for i, depth in enumerate([0.0, 1.0]):
            amp = h * (0.08 + 0.08 * depth)
            base = horizon + (h - horizon) * (0.12 + 0.5 * depth)
            Xl = Xs + cam(0.35 + 0.6 * depth)
            ridge = base - amp * (2 * fbm1(Xl * (0.012 + 0.008 * i) + self.off[i], self.seed + i, 3) - 1)
            mask = Y >= ridge
            stripes = ((Y - ridge) * 0.6 + Xl * 0.12 + self.off[2]) % 4.0 < 1.2
            v = 0.2 + 0.35 * depth + stripes * 0.2 + (fbm2(Xl * 0.08, Y * 0.3, self.seed + 5 + i, 2) - 0.5) * 0.15
            paint(fr, np.clip(v, 0, 1), pal, mask=mask, ramp=RAMP_HATCH, dither=0.4, tint=0.35 + 0.5 * depth, opaque=True)
        for cx in self.cacti:
            creature(fr, sprites.cactus, 0.0, (cx * (w + 16) - cam(1.0)) % (w + 16) - 8, h - 1.5, pal,
                     col=pal.tone(0.95 if pal.paper else 0.3))
        self.tumble_x = (self.tumble_x + dt * (4 + 8 * au.level)) % (w + 10)
        creature(fr, sprites.tumbleweed, (t * 1.5) % 1.0, self.tumble_x - 5, h - 2, pal,
                 scale=sprite_scale(fr) * 0.8, lift=abs(math.sin(t * 4)) * 1.5)
        walker(fr, t, au, pal, self.sprite, h - 2, 3)


class Snow(Scene):
    name = "snow"
    kind = "land"

    def setup(self):
        r = self.rng
        n = 500
        self.px = r.uniform(0, 1, n)
        self.py = r.uniform(0, 1, n)
        self.sz = r.integers(0, 4, n)
        self.ph = r.uniform(0, 6.28, n)
        self.trees = [(float(r.uniform(0, 1)), float(r.uniform(0.3, 0.6)), bool(r.random() < 0.5)) for _ in range(16)]
        self.moonx = float(r.uniform(0.1, 0.9))

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        ground = h - 4
        sky(fr, pal, ground, t, self.seed, clouds=0.2, stars=0.5, moon=(int(self.moonx * w), int(h * 0.15), 2))
        key = (fr.W, fr.H)
        if getattr(self, "_tree_key", None) != key:     # trees are static: build once per size
            layers = []
            for near in (False, True):
                M = np.zeros((fr.H, fr.W), bool)
                V = np.zeros((fr.H, fr.W))
                for (tx, th, nr) in self.trees:
                    if nr != near:
                        continue
                    cx = int(tx * w)
                    top = int(ground - th * h * (1.0 if near else 0.6))
                    m = (Y >= top) & (Y < ground) & (np.abs(X - cx) <= (Y - top) * 0.5 * (1.3 if near else 0.9))
                    tier = ((Y - top) % 4) < 3
                    M |= m
                    V = np.where(m, np.where(tier, 0.75 if near else 0.4, 0.0), V)
                layers.append((near, M, V))
            self._tree_key, self._trees_cached = key, layers
        for near, M, V in self._trees_cached:
            sh = -int(cam(0.8 if near else 0.4) * fr.sx) % fr.W
            M, V = np.roll(M, sh, axis=1), np.roll(V, sh, axis=1)
            paint(fr, V, pal, mask=M, ramp=RAMP_HATCH, dither=0.4, tint=0.85 if near else 0.45, opaque=True)
        fr.bg[ground:] = pal.tone(0.05) if pal.paper else pal.tone(0.35)
        gm = (Y >= ground) & (hash01(X + cam(1.0), Y, self.seed) < 0.15)
        fr.chars[gm] = "."
        fr.fg[gm] = pal.ink
        n = int(120 + 300 * au.level)
        wind = math.sin(t * 0.2) * 0.5 + au.bass * 1.5
        self.py[:n] += (3 + self.sz[:n] * 2) * dt / h
        self.px[:n] += (np.sin(t * 0.8 + self.ph[:n]) * 0.4 + wind) * dt * 2 / w
        self.py %= 1.0
        self.px %= 1.0
        xs = (self.px[:n] * w).astype(int)
        ys = (self.py[:n] * ground).astype(int)
        fr.put_many(xs, ys, np.array(list(".*+o"))[self.sz[:n]], pal.ink)
        walker(fr, t, au, pal, self.sprite, ground, 2)


class City(Scene):
    name = "city"
    kind = "land"

    def setup(self):
        r = self.rng
        self.b = []
        x = 0
        while x < 500:
            wd = int(r.integers(5, 14))
            self.b.append((x, wd, float(r.uniform(0.2, 0.6))))
            x += wd + int(r.integers(0, 3))
        self.rainy = bool(r.random() < 0.4)
        self.moonx = float(r.uniform(0.1, 0.9))
        self.cars = r.uniform(0, 1, 8)
        self.cs = r.uniform(8, 20, 8) * np.where(r.random(8) < 0.5, -1, 1)
        self.px = r.uniform(0, 1, 250)
        self.py = r.uniform(0, 1, 250)

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        ground = h - 3
        sky(fr, pal, ground, t, self.seed, clouds=0.3, stars=0.6, moon=(int(self.moonx * w), int(h * 0.12), 2))
        lit = hash01(X, Y, self.seed + int(t * 0.4) + au.beat_count) < 0.5 + 0.2 * au.level
        span = float(self.b[-1][0] + self.b[-1][1])
        x = (X[0] + cam(0.6)) % span
        Xc = (X + cam(0.6)) % span
        top_of = np.full(fr.W, float(ground))      # roof height per column
        x0_of = np.zeros(fr.W)
        wd_of = np.ones(fr.W)
        for (x0, wd, ht) in self.b:
            cols = (x >= x0) & (x < x0 + wd)
            if not cols.any():
                continue
            top_of[cols] = int(ground - ht * h)
            x0_of[cols] = x0
            wd_of[cols] = wd
        body = (Y >= top_of[None, :]) & (Y < ground)
        paint(fr, 0.45, pal, mask=body, ramp=RAMP_HATCH, dither=0.25, tint=sil(pal), opaque=True)
        roof = body & (Y == top_of[None, :])
        fr.chars[roof] = "="
        fr.fg[roof] = pal.tone(0.6)
        win = body & ((Xc - x0_of[None, :]) % 3 == 1) & ((Y - top_of[None, :]) % 2 == 1) & (Xc < (x0_of + wd_of - 1)[None, :])
        fr.chars[win & lit] = "#"
        fr.fg[win & lit] = pal.accent
        fr.chars[win & ~lit] = "."
        fr.fg[win & ~lit] = pal.tone(0.45)
        fr.bg[ground:] = pal.tone(0.1)
        if ground + 1 < h:
            fr.put_many(np.arange(0, w, 4), np.full((w + 3) // 4, ground + 1), "-", pal.tone(0.6))
        for i in range(8):
            x = (self.cars[i] * w + t * self.cs[i]) % (w + 12) - 6
            y = ground if self.cs[i] > 0 else min(h - 1, ground + 2)
            creature(fr, sprites.car, 0.0, x, y + 1, pal, scale=sprite_scale(fr) * 0.6, flip=self.cs[i] < 0)
        if self.rainy:
            n = 250
            self.py[:n] += 30 * dt / h
            self.py %= 1.0
            fr.put_many((self.px * w).astype(int), (self.py * ground).astype(int), "|", pal.tone(0.5))
        walker(fr, t, au, pal, self.sprite, ground, 3)


class Spectrum(Scene):
    name = "spectrum"
    kind = "none"

    def setup(self):
        self.hist = [np.zeros(32) for _ in range(20)]
        self.acc = 0.0

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        self.acc += dt
        if self.acc > 0.08:
            self.acc = 0.0
            self.hist.insert(0, np.array(au.spectrum, dtype=np.float64))
            self.hist = self.hist[:20]
        xs = X[0]
        cx = w / 2.0
        base0 = h * 0.9
        nh = len(self.hist)
        for k in range(nh - 1, -1, -1):
            z = k / max(1, nh - 1)
            sp = self.hist[k]
            mir = np.concatenate([sp[::-1], sp])
            scale = 1 - 0.55 * z
            px = cx + (np.arange(64) - 31.5) / 31.5 * (w * 0.48) * scale
            ridge_h = np.interp(xs, px, mir, left=0, right=0) * (h * 0.35) * scale * (0.6 + 0.5 * au.level)
            base = base0 - z * h * 0.45
            ridge = base - ridge_h
            m = (Y >= ridge[None, :]) & (Y < base + 1) & (np.abs(X - cx) < w * 0.49 * scale)
            v = 0.25 + 0.6 * (1 - z) + (Y - ridge[None, :]) * 0.02
            paint(fr, np.clip(v, 0, 1), pal, mask=m, ramp=RAMP_SOFT, dither=0.35, tint=0.3 + 0.7 * (1 - z), opaque=True)
            if k == 0:
                lm = m & (Y < ridge[None, :] + 1)
                fr.fg[lm] = pal.accent
        r = 3 + 5 * au.pulse() + 2 * au.bass
        cy = h * 0.28
        d = np.sqrt(((X - cx) / 2.0) ** 2 + (Y - cy) ** 2)
        ring = np.abs(d - r) < 0.7
        fr.chars[ring] = "*"
        fr.fg[ring] = pal.accent
        inner = np.abs(d - r * 0.5) < 0.6
        fr.chars[inner] = "."
        fr.fg[inner] = pal.tone(0.6)


class Plasma(Scene):
    name = "plasma"
    kind = "none"

    def setup(self):
        self.scale = float(self.rng.uniform(0.7, 1.4))
        self.cycle = float(self.rng.uniform(0.02, 0.06))

    def draw(self, fr, t, dt, au, pal):
        h, w = fr.h, fr.w
        X, Y = grid(fr)
        fr.clear(pal.bg)
        cx, cy = w / 2.0, h / 2.0
        a = fbm2(X * 0.04 * self.scale + t * 0.12, Y * 0.08 * self.scale + t * 0.05, self.seed, 3)
        b = np.sin(X * 0.07 * self.scale + t * 0.7 + a * 5)
        d = np.sqrt(((X - cx) / 2.0) ** 2 + (Y - cy) ** 2)
        c = np.sin(d * 0.35 - t * (1.5 + 2 * au.bass))
        v = (a * 1.3 + (b + 1) * 0.25 + (c + 1) * 0.3) / 2.0 - 0.15
        r = (time.time() - au.beat_time) * 22
        ring = np.exp(-((d - r) ** 2) / 6) * au.pulse(4)
        v = np.clip(v + ring * 0.7, 0, 1)
        paint(fr, v, pal, ramp=RAMP_SOFT, dither=0.3, tint=(v + t * self.cycle) % 1.0, opaque=True)


ALL = {}
for _cls in (Hills, Ocean, Rain, Night, Forest, Aurora, Desert, Snow, City, Spectrum, Plasma):
    ALL[_cls.name] = _cls
