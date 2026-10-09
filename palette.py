"""Colour palettes: extracted from album art, or synthesised from a seed."""
import io
import colorsys
import numpy as np

try:
    from PIL import Image
except Exception:  # pillow missing -> seed palettes only
    Image = None


PAPER = (226, 222, 208)      # base "paper" colour for the light look
PAPER_MAX_LUM = 0.84         # cap so the light look is never glaring white


def lum(c):
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0


def mix(a, b, t):
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    return a + (b - a) * t


def sat(c):
    r, g, b = [v / 255.0 for v in c]
    return colorsys.rgb_to_hsv(r, g, b)[1]


def _t(c):
    return tuple(int(v) for v in c)


class Palette:
    def __init__(self, colors, accent, paper, seed=0):
        colors = [_t(c) for c in colors]
        colors.sort(key=lum)
        self.colors = colors
        self.paper = paper
        self.seed = seed
        dark, light = colors[0], colors[-1]
        if paper:
            # off-white paper: a muted cream tinted by the artwork, never glaring white
            paper_bg = mix(light, PAPER, 0.72)
            l = lum(paper_bg)
            if l > PAPER_MAX_LUM:
                paper_bg = paper_bg * (PAPER_MAX_LUM / l)
            self.bg = _t(paper_bg)
            self.ink = _t(mix(dark, (0, 0, 0), 0.45))
            stops = [self.bg] + colors[::-1] + [self.ink]
        else:
            self.bg = _t(mix(dark, (0, 0, 0), 0.72))
            ink = np.asarray(light, np.float64)
            if lum(ink) < 0.72:
                ink = mix(ink, (255, 255, 255), 0.55)
            self.ink = _t(ink)
            stops = [self.bg] + colors + [self.ink]
        acc = np.asarray(accent, np.float64)
        # keep the accent readable on the background
        if paper and lum(acc) > 0.6:
            acc = mix(acc, (0, 0, 0), 0.35)
        if not paper and lum(acc) < 0.35:
            acc = mix(acc, (255, 255, 255), 0.45)
        self.accent = _t(acc)
        self.mid = colors[len(colors) // 2]
        self._stops = np.asarray(stops, np.float64)
        self._pos = np.linspace(0, 1, len(stops))

    def c(self, i):
        return self.colors[i % len(self.colors)]

    def grad(self, t, steps=12):
        """Intensity field (0=background .. 1=ink) -> (..., 3) uint8 colours."""
        t = np.clip(np.asarray(t, np.float64), 0, 1)
        if steps == 12:                       # common case: 13-colour lookup table
            lut = getattr(self, "_lut", None)
            if lut is None:
                lut = self._lut = self.grad(np.linspace(0, 1, 13), steps=0)
            return lut[np.rint(t * 12).astype(np.intp)]
        if steps:
            t = np.round(t * steps) / steps
        out = np.empty(t.shape + (3,), np.uint8)
        for ch in range(3):
            out[..., ch] = np.interp(t, self._pos, self._stops[:, ch]).astype(np.uint8)
        return out

    def sky(self, t):
        """Background colour for sky rows, t in 0..1 (top -> horizon)."""
        return _t(mix(self.bg, self.mid, 0.04 + 0.14 * t))

    def tone(self, t):
        """Single colour from the gradient."""
        return _t(self.grad(np.asarray(float(t)), steps=0))

    def toggled(self):
        return Palette(self.colors, self.accent, not self.paper, self.seed)


def palette_from_seed(seed, paper=None):
    rng = np.random.default_rng(seed)
    h = rng.random()
    drift = rng.uniform(-0.08, 0.08)
    cols = []
    for i in range(6):
        hh = (h + drift * i / 5) % 1.0
        l = 0.12 + 0.13 * i
        s = rng.uniform(0.35, 0.75)
        r, g, b = colorsys.hls_to_rgb(hh, l, s)
        cols.append((int(r * 255), int(g * 255), int(b * 255)))
    ar, ag, ab = colorsys.hls_to_rgb((h + rng.uniform(0.35, 0.65)) % 1.0, 0.6, 0.85)
    if paper is None:
        paper = rng.random() < 0.3
    return Palette(cols, (int(ar * 255), int(ag * 255), int(ab * 255)), paper, seed)


def palette_from_art(data, seed, paper=None):
    if Image is None or not data:
        return palette_from_seed(seed, paper)
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((48, 48))
        q = im.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[: 8 * 3]
        counts = sorted(q.getcolors(), reverse=True)
        total = float(sum(c for c, _ in counts))
        cols, weights = [], []
        for cnt, idx in counts:
            col = tuple(pal[idx * 3: idx * 3 + 3])
            if cnt / total < 0.015:
                continue
            cols.append(col)
            weights.append(cnt / total)
    except Exception:
        return palette_from_seed(seed, paper)
    if len(cols) < 3:
        return palette_from_seed(seed, paper)
    lums = [lum(c) for c in cols]
    mean_l = float(np.average(lums, weights=weights))
    # make sure the ramp spans enough brightness
    if max(lums) - min(lums) < 0.4:
        cols.append(_t(mix(cols[int(np.argmin(lums))], (0, 0, 0), 0.5)))
        cols.append(_t(mix(cols[int(np.argmax(lums))], (255, 255, 255), 0.5)))
    accent = max(cols, key=lambda c: sat(c) * (0.4 + 0.6 * min(1.0, lum(c) * 2)))
    if sat(accent) < 0.25:
        rng = np.random.default_rng(seed)
        r, g, b = colorsys.hls_to_rgb(rng.random(), 0.6, 0.8)
        accent = (int(r * 255), int(g * 255), int(b * 255))
    if paper is None:
        rng = np.random.default_rng(seed)
        paper = mean_l > 0.55 or rng.random() < 0.22
    return Palette(cols, accent, paper, seed)


def art_scenes(data):
    """Scene names that fit the album art's colours (best first), or [] if
    there is no usable artwork. Used to pick each track's landscape scene."""
    if Image is None or not data:
        return []
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((48, 48))
        q = im.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[: 8 * 3]
        counts = q.getcolors()
    except Exception:
        return []
    total = float(sum(c for c, _ in counts))
    L = S = 0.0
    hues = np.zeros(12)
    for cnt, idx in counts:
        r, g, b = [v / 255.0 for v in pal[idx * 3: idx * 3 + 3]]
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        w = cnt / total
        L += w * lum((r * 255, g * 255, b * 255))
        S += w * s * min(1.0, v * 1.5)
        if s > 0.25 and v > 0.2:
            hues[int(h * 12) % 12] += w
    strong = int((hues > 0.15).sum())
    if S < 0.15 or hues.sum() < 0.12:            # mostly grey / black / white
        if L > 0.6:
            return ["snow", "hills"]
        if L > 0.3:
            return ["rain", "city"]
        return ["night", "city"]
    if strong >= 3:                               # very colourful
        return ["plasma", "spectrum"]
    h = (int(np.argmax(hues)) + 0.5) / 12.0
    if L < 0.22:                                  # dark artwork
        if 0.45 <= h < 0.93:
            return ["night", "aurora"]
        if 0.18 <= h < 0.45:
            return ["aurora", "forest"]
        return ["city", "night"]
    if h < 0.1 or h >= 0.93:
        return ["desert", "city"] if L < 0.5 else ["desert", "hills"]
    if h < 0.18:
        return ["desert", "hills"]
    if h < 0.45:
        return ["forest", "hills"]
    if h < 0.7:
        return ["ocean", "snow"] if L > 0.55 else ["ocean", "rain"]
    return ["aurora", "night", "plasma"]
