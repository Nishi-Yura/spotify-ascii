"""Scenes built from images/video: the album art, or a local clip for the
track (media/<artist> - <title>.mp4|gif|png ...), rendered as colour ASCII."""
import io
import os
import re
import math
import time
import threading
import numpy as np
from PIL import Image, ImageSequence

from scenes import Scene, grid, paint, RAMP_SOFT, hash01, fbm2, RAMP_DOTS, ENV

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
    """Images and GIFs -> (list of RGB uint8 arrays, seconds per frame).
    (Videos are streamed instead, see VideoStream.)"""
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
    return frames, spf


class VideoStream(threading.Thread):
    """Decodes a video in the background, following the song: `target` is
    the playback position in seconds, `frame` the picture for it. Skipping
    in Spotify seeks the video; nothing is loaded up front, so the clip
    starts right away and stays in step with the song (and its lyrics)."""

    def __init__(self, path, max_w=480):
        super().__init__(daemon=True)
        self.path, self.max_w = path, max_w
        self.target = 0.0           # song position (s) at time `stamp`
        self.stamp = None           # None: paused, the position stays put
        self.frame = None           # RGB uint8, the frame at (about) target
        self.err = ""
        self._quit = threading.Event()
        self.start()

    def follow(self, pos, playing):
        """Called on every draw with the song position."""
        self.target = pos
        self.stamp = time.time() if playing else None

    def now(self):
        st = self.stamp
        return self.target + (time.time() - st if st is not None else 0.0)

    def close(self):
        self._quit.set()

    def _small(self, f):
        h, w = f.shape[:2]
        if w > self.max_w:
            f = cv2.resize(f, (self.max_w, max(1, int(h * self.max_w / w))), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(f, cv2.COLOR_BGR2RGB)

    def run(self):
        try:
            cap = cv2.VideoCapture(self.path)
            if not cap.isOpened():
                self.err = "could not open the video"
                return
            fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
            count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            length = count / fps if count > 0 else 0.0
            nxt = 0                                     # index of the next frame to decode
            while not self._quit.is_set():
                t = self.now()                          # runs on between draws
                if length > 0:
                    t %= length                         # shorter clip than the song: loop
                want = int(t * fps)
                if want < nxt - 1 or want > nxt + 2 * fps:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, want)     # skipped / far behind: seek
                    nxt = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                if want < nxt and self.frame is not None:
                    self._quit.wait(0.01)               # up to date: wait for the song
                    continue
                while nxt < want and cap.grab():        # catch up without converting
                    nxt += 1
                ok, f = cap.read()
                if not ok:                              # past the end (or unreadable)
                    if nxt == 0:
                        self.err = "could not read the video"
                        return
                    self._quit.wait(0.05)
                    if length <= 0:
                        length = nxt / fps              # unknown length: learn it, then loop
                    continue
                nxt += 1
                self.frame = self._small(f)
            cap.release()
        except Exception as e:
            self.err = str(e)


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
    """Plays media/<track>.mp4|gif|png as colour ascii, in step with the song
    (videos stream in the background; the album art shows until ready)."""
    name = "video"
    kind = "none"

    def setup(self):
        self.frames, self.spf = [], 0.05
        self.err = ""
        self.stream = None
        self._loader = None
        self.art_rgb = None
        self.set_art(self.art)
        ext =os.path.splitext(self.media or "")[1].lower()
        if ext in VID_EXT:
            if cv2 is None:
                self.err = "mp4 などの動画を再生するには video-support.bat を一度実行してください"
            else:
                self.stream = VideoStream(self.media)
        elif self.media:
            # images / gifs: load in the background too (big gifs take a while)
            self._loader = threading.Thread(target=self._load, daemon=True)
            self._loader.start()

    def set_art(self, art):
        """Album art, shown until the clip's first frame is ready."""
        self.art = art
        if art:
            try:
                self.art_rgb = np.asarray(Image.open(io.BytesIO(art)).convert("RGB"))
            except Exception:
                pass

    def _load(self):
        try:
            frames, spf = load_clip(self.media)
            self.spf = spf
            self.frames = frames
            if not frames:
                self.err = "could not load clip"
        except Exception as e:
            self.err = str(e)

    def close(self):
        if self.stream is not None:
            self.stream.close()

    def draw(self, fr, t, dt, au, pal):
        fr.clear(pal.bg)
        pos = ENV.get("pos", t)
        rgb = None
        if self.stream is not None:
            self.stream.follow(pos, ENV.get("playing", True))
            rgb = self.stream.frame
            self.err = self.stream.err
        elif self.frames:
            rgb = self.frames[int(pos / self.spf) % len(self.frames)]
        if rgb is not None:
            draw_image(fr, rgb, pal, t, au, self.seed)
        elif self.err:
            fr.text(2, fr.h // 2, self.err, pal.tone(0.7))
        elif self.art_rgb is not None:                 # still loading: the album art
            draw_image(fr, self.art_rgb, pal, t, au, self.seed)
