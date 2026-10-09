"""Scenes built from images/video: the album art, or a local clip for the
track (media/<artist> - <title>.mp4|gif|png ...), rendered as colour ASCII."""
import io
import os
import re
import math
import time
import numpy as np
from PIL import Image, ImageSequence

from scenes import Scene, grid, paint, RAMP_SOFT, hash01, fbm2, RAMP_DOTS

try:
    import cv2
except Exception:
    cv2 = None

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(HERE, "media")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
GIF_EXT = (".gif",)
VID_EXT = (".mp4", ".webm", ".mkv", ".mov", ".avi")


def _norm(s):
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", s.lower())


def find_media(track):
    """Local clip/image for this track, or None."""
    if not os.path.isdir(MEDIA_DIR) or not track.title:
        return None
    wanted = {_norm("%s - %s" % (track.artist, track.title)), _norm(track.title)}
    wanted.discard("")
    for f in sorted(os.listdir(MEDIA_DIR)):
        stem, ext = os.path.splitext(f)
        if ext.lower() in IMG_EXT + GIF_EXT + VID_EXT and _norm(stem) in wanted:
            return os.path.join(MEDIA_DIR, f)
    return None


def load_clip(path, max_frames=900, max_w=480):
    """-> (list of RGB uint8 arrays, seconds per frame). Images give 1 frame."""
    ext = os.path.splitext(path)[1].lower()
    frames, spf = [], 1 / 24
    if ext in IMG_EXT:
        im = Image.open(path).convert("RGB")
        frames.append(np.asarray(_fit(im, max_w)))
    elif ext in GIF_EXT:
        im = Image.open(path)
        durs = []
        for fr in ImageSequence.Iterator(im):
            frames.append(np.asarray(_fit(fr.convert("RGB"), max_w)))
            durs.append(fr.info.get("duration", 80) / 1000.0)
            if len(frames) >= max_frames:
                break
        spf = max(0.02, float(np.mean(durs))) if durs else 0.08
    elif ext in VID_EXT and cv2 is not None:
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24
        spf = 1.0 / fps
        step = max(1, int(round(fps / 15)))  # ~15 fps is plenty for ascii
        i = 0
        while len(frames) < max_frames:
            ok, f = cap.read()
            if not ok:
                break
            if i % step == 0:
                im = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
                frames.append(np.asarray(_fit(im, max_w)))
            i += 1
        cap.release()
        spf *= step
    return frames, spf


def _fit(im, max_w):
    if im.width > max_w:
        im = im.resize((max_w, max(1, int(im.height * max_w / im.width))))
    return im


def draw_image(fr, rgb, pal, t, au, seed, zoom=1.0):
    """Render an RGB array as colour ascii/braille, fitted and centred."""
    h, w, sx, sy = fr.h, fr.w, fr.sx, fr.sy
    ih, iw = rgb.shape[:2]
    a = iw / float(ih)
    cw = int(min(w * 0.92, h * 0.92 * 2 * a))
    ch = max(1, int(cw / (2 * a)))
    cw = max(1, cw)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    im = Image.fromarray(rgb)
    if zoom != 1.0:
        zw, zh = int(iw / zoom), int(ih / zoom)
        l, tp = (iw - zw) // 2, (ih - zh) // 2
        im = im.crop((l, tp, l + zw, tp + zh))
    im = im.resize((cw * sx, ch * sy), Image.BILINEAR)
    px = np.asarray(im).astype(np.float64)
    lum = (0.299 * px[..., 0] + 0.587 * px[..., 1] + 0.114 * px[..., 2]) / 255.0
    v = (1.0 - lum) if pal.paper else lum
    v = np.clip((v - 0.5) * 1.25 + 0.5, 0, 1) * (1.0 + 0.25 * au.pulse())
    # bass wobble + occasional glitch slices
    rows = np.arange(v.shape[0])
    shift = (np.sin(rows / sy * 0.35 + t * 2.0) * au.bass * 1.5 * sx).astype(int)
    if au.pulse() > 0.6:
        g = hash01(np.zeros_like(rows), rows // (3 * sy), seed + int(au.beat_count)) < 0.15
        shift = shift + (g * (hash01(rows, np.zeros_like(rows), seed) * 10 - 5) * sx).astype(int)
    cols = (np.arange(v.shape[1])[None, :] + shift[:, None]) % v.shape[1]
    v = np.take_along_axis(v, cols, axis=1)
    px = np.take_along_axis(px, cols[..., None], axis=1)
    if pal.paper:
        px = px * 0.75
    else:
        px = np.clip(px * 1.1 + 20, 0, 255)
    fgmap = (px.astype(np.uint8) >> 3) << 3
    V = np.zeros((fr.H, fr.W))
    F = np.zeros((fr.H, fr.W, 3), np.uint8)
    ys, xs = slice(y0 * sy, (y0 + ch) * sy), slice(x0 * sx, (x0 + cw) * sx)
    V[ys, xs] = np.clip(v, 0, 1)
    F[ys, xs] = fgmap
    mask = np.zeros((fr.H, fr.W), bool)
    mask[ys, xs] = True
    paint(fr, V, pal, mask=mask, ramp=RAMP_SOFT, dither=0.3, fgmap=F, opaque=True)
    return x0, y0, cw, ch


class Cover(Scene):
    """The album art itself, pulsing with the music, over a drifting field."""
    name = "cover"
    kind = "none"

    def setup(self):
        self.rgb = None
        self.set_art(self.art)

    def set_art(self, art):
        self.art = art
        if art:
            try:
                self.rgb = np.asarray(Image.open(io.BytesIO(art)).convert("RGB"))
            except Exception:
                self.rgb = None

    def draw(self, fr, t, dt, au, pal):
        X, Y = grid(fr)
        fr.clear(pal.bg)
        n = fbm2(X * 0.05 + t * 0.04, Y * 0.12 - t * 0.02, self.seed, 3)
        v = np.clip((n - 0.55) * 2.2, 0, 1) * (0.25 + 0.3 * au.level)
        paint(fr, v, pal, ramp=RAMP_DOTS, dither=0.5, tint=0.4)
        if self.rgb is None:
            fr.text(fr.w // 2 - 6, fr.h // 2, "(no artwork)", pal.tone(0.6))
            return
        zoom = 1.0 + 0.04 * math.sin(t * 0.3) + 0.05 * au.pulse(3)
        x0, y0, cw, ch = draw_image(fr, self.rgb, pal, t, au, self.seed, zoom)
        fr.box(x0 - 1, y0 - 1, cw + 2, ch + 2, pal.tone(0.6), pal.bg, fill=False)


class Video(Scene):
    """Plays media/<track>.mp4|gif|png as colour ascii, looping."""
    name = "video"
    kind = "none"

    def setup(self):
        self.frames, self.spf = [], 0.05
        self.err = ""
        if self.media:
            try:
                self.frames, self.spf = load_clip(self.media)
            except Exception as e:
                self.err = str(e)
        if not self.frames:
            ext = os.path.splitext(self.media or "")[1].lower()
            if ext in VID_EXT and cv2 is None:
                self.err = "mp4 などの動画を再生するには video-support.bat を一度実行してください"
            self.err = self.err or "could not load clip"

    def draw(self, fr, t, dt, au, pal):
        fr.clear(pal.bg)
        if not self.frames:
            fr.text(2, fr.h // 2, self.err, pal.tone(0.7))
            return
        idx = int(t / self.spf) % len(self.frames)
        draw_image(fr, self.frames[idx], pal, t, au, self.seed)
