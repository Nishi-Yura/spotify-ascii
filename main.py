"""Spotify ASCII terminal: animated, colour ASCII scenery that follows what
Spotify is playing.

- each track has two scenes: a landscape picked from the album art's colours,
  and the album cover itself (n toggles, remembered per track)
- the time of day follows the song: day -> sunset -> night with stars
- creatures walk / flap in time with the detected tempo (BPM)
- creatures cross the screen in step with the seek bar, walk in / out
  between songs and fall asleep while paused
- songs from the same album share the creature, each song gets its own scenery
- the scenery scrolls slowly (far layers slower), near props slide past in front

keys:  q quit   n scene <-> cover   w white/dark
       [ ]  shift beat timing earlier / later (to match what you hear)
"""
import os
import sys
import json
import time
import zlib
import argparse
import numpy as np

from render import Terminal, Frame, is_wide
from palette import palette_from_art, palette_from_seed, art_scenes, mix
from audio import Audio
from nowplaying import NowPlaying
from noise import hash01
import scenes as S
import media_scenes as M

SCENES = sorted(S.ALL)          # landscape / motion scenes (cover is separate)
HELP = [
    ("n", "シーン ⇄ ジャケット 切替（曲ごとに記憶）"),
    ("[ ]", "ビートのタイミングを 早く / 遅く"),
    ("w", "ダーク / ホワイト 切替"),
    ("?", "この操作一覧を表示 / 閉じる"),
    ("q", "終了"),
]

try:
    import msvcrt
except ImportError:  # not windows
    msvcrt = None

HERE = os.path.dirname(os.path.abspath(__file__))
OVERRIDES = os.path.join(HERE, "track_scenes.json")
SETTINGS = os.path.join(HERE, "app_settings.json")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(path, d):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fmt_time(s):
    m, s = divmod(int(max(0, s)), 60)
    return "%d:%02d" % (m, s)


def dwidth(s):
    return sum(2 if is_wide(c) else 1 for c in s)


def clip_text(s, maxw):
    if dwidth(s) <= maxw:
        return s
    out, wsum = "", 0
    for c in s:
        cw = 2 if is_wide(c) else 1
        if wsum + cw > maxw - 1:
            break
        out += c
        wsum += cw
    return out + "…"


# --- HUD ----------------------------------------------------------------------
def draw_hud(fr, tr, pal, info):
    icon = "♫" if tr.status == "playing" else "||"
    label = " %s — %s  %s " % (icon, tr.title, tr.artist)
    label = clip_text(label, fr.w - 6)
    bw = dwidth(label) + 2
    x = fr.w - bw - 2
    fr.box(x, 1, bw, 3, pal.bg, pal.ink)
    fr.text(x + 1, 2, label, pal.bg, pal.ink)
    if info:
        fr.text(1, fr.h - 2, clip_text(info, fr.w - 2), pal.tone(0.45))
    if tr.duration > 0:
        y = fr.h - 1
        pos = tr.pos_now()
        left = " %s " % fmt_time(pos)
        right = " %s " % fmt_time(tr.duration)
        fr.text(1, y, left, pal.ink)
        fr.text(fr.w - 1 - len(right), y, right, pal.ink)
        x0 = 1 + len(left)
        n = fr.w - 1 - len(right) - x0
        if n > 0:
            fr.text(x0, y, "─" * n, pal.tone(0.35))
            fr.text(x0, y, "━" * int(n * min(1.0, pos / tr.duration)), pal.accent)


# --- idle screen ----------------------------------------------------------------
def draw_idle(fr, t, pal, line2, keys=True):
    fr.clear((9, 9, 12))
    X, Y = S.grid(fr)
    v = np.clip((S.fbm2(X * 0.04 + t * 0.05, Y * 0.12, 1, 3) - 0.55) * 2, 0, 1) * 0.4
    S.paint(fr, v, pal, ramp=S.RAMP_DOTS, dither=0.5, tint=0.4)
    msg = "Spotify で曲を再生すると始まります"
    sub = "waiting for spotify" + "." * (int(t * 2) % 4)
    cy = fr.h // 2 - 2
    fr.text((fr.w - dwidth(msg)) // 2, cy - 1, msg, (210, 210, 220))
    fr.text((fr.w - len(sub)) // 2, cy + 1, sub, (120, 120, 140))
    if line2:
        fr.text((fr.w - dwidth(line2)) // 2, cy + 3, line2, (90, 90, 110))
    if not keys:            # wallpaper: no keyboard, just a calm message
        return
    # key guide
    bw = max(dwidth(d) for _, d in HELP) + 10
    bx = (fr.w - bw) // 2
    by = cy + 5
    if by + len(HELP) + 2 < fr.h:
        fr.box(bx, by, bw, len(HELP) + 2, (90, 90, 110), (9, 9, 12))
        fr.text(bx + 2, by, " 操作 ", (150, 150, 170), (9, 9, 12))
        for i, (k, d) in enumerate(HELP):
            fr.text(bx + 3, by + 1 + i, k, (230, 200, 120), (9, 9, 12))
            fr.text(bx + 7, by + 1 + i, d, (170, 170, 185), (9, 9, 12))
    tips = ["曲が進むと 昼 → 夕焼け → 夜 に変わり、動物は曲のテンポで歩きます",
            "media/ に「アーティスト - 曲名.mp4」を置くとその動画を再生"]
    for i, tip in enumerate(tips):
        y = by + len(HELP) + 3 + i
        if y < fr.h - 1:
            fr.text((fr.w - dwidth(tip)) // 2, y, tip, (90, 90, 110))


# --- scenes -------------------------------------------------------------------
def album_key(tr):
    return "%s|%s" % (tr.artist, tr.album) if tr.album else ""


def creature_seed(tr):
    """Seed for the creature: per album, so one album keeps one companion."""
    k = album_key(tr)
    return zlib.crc32(k.encode("utf-8")) if k else tr.seed


def draw_help(fr, pal):
    """Key guide overlay (toggled with ?)."""
    bg = tuple(int(v) for v in np.asarray(pal.bg, float) * 0.85)
    fg = pal.ink
    rows = [(k, d) for k, d in HELP]
    bw = max(dwidth(d) for _, d in rows) + 12
    bh = len(rows) + 4
    x0 = (fr.w - bw) // 2
    y0 = (fr.h - bh) // 2
    if x0 < 0 or y0 < 0:
        return
    fr.box(x0, y0, bw, bh, pal.tone(0.6), bg)
    fr.text(x0 + 2, y0, " 操作 ", fg, bg)
    for i, (k, d) in enumerate(rows):
        fr.text(x0 + 3, y0 + 2 + i, k, pal.accent, bg)
        fr.text(x0 + 9, y0 + 2 + i, d, fg, bg)


def track_scene_name(tr, forced=None, avoid=None):
    """The landscape/motion scene for this track, chosen from the album art's
    colours (falling back to the track name). `avoid`: previous scene of the
    same album -> pick a different scenery of the same kind, so the creature
    can stay the same while the background changes."""
    if forced:
        return forced
    cands = [c for c in art_scenes(tr.art) if c in S.ALL]
    if avoid in S.ALL:
        kind = S.ALL[avoid].kind
        same_kind = [n for n in SCENES if S.ALL[n].kind == kind and n != avoid]
        pool = [c for c in cands if c in same_kind] + [n for n in same_kind if n not in cands]
        if pool:
            return pool[tr.seed % len(pool)] if not cands else pool[tr.seed % min(len(pool), 2)]
    if cands:
        return cands[tr.seed % len(cands)]
    return SCENES[tr.seed % len(SCENES)]


def make_scene(tr, show_cover, media, forced=None, avoid=None):
    if show_cover:
        return M.Cover(tr.seed, tr.art)
    if media:
        return M.Video(tr.seed, tr.art, media)
    name = track_scene_name(tr, forced, avoid)
    return S.ALL[name](tr.seed, tr.art, media, creature_seed=creature_seed(tr))


def is_landscape(scene):
    return scene is not None and not isinstance(scene, (M.Cover, M.Video))


class Visualiser:
    """Everything that turns "what is Spotify playing" into a picture.

    frame(w, h) returns one finished, cell-resolution Frame (scene, fades,
    HUD). The terminal front end (main) and the desktop wallpaper
    (wallpaper.py) both drive this; only how the Frame reaches the screen
    differs."""

    def __init__(self, scene=None, any_player=False, no_audio=False, white=None,
                 show_info=True, keys=True):
        self.forced_scene = scene
        self.show_info = show_info
        self.keys = keys
        self.overrides = load_json(OVERRIDES)
        self.settings = load_json(SETTINGS)
        # dark is the standard look; white is the alternative (w toggles, remembered)
        self.paper = bool(self.settings.get("white", False)) if white is None else bool(white)
        self.npl = NowPlaying(any_player=any_player)
        self.npl.start()
        self.au = Audio(not no_audio)
        self.au.latency = float(self.settings.get("latency_ms", 0)) / 1000.0
        self.au.start()
        self.idle_pal = palette_from_seed(1, paper=False)
        self.cur_key = None
        self.scene = None
        self.pal = None
        self.art_seen = None
        self.scene_t = 0.0
        self.show_cover = False
        self.media = None
        self.cur_album = ""
        self.sleep = 0.0
        self.show_help = False
        self.latency_msg_until = 0.0
        self.trans = None
        self.prev = None
        self.idle_since = None
        self.t0 = time.time()
        self.last = self.t0

    # --- input ---------------------------------------------------------------
    def key(self, k):
        now = time.time()
        tr = self.npl.track
        if k in ("?", "/", "h", "H"):
            self.show_help = not self.show_help
        elif k in ("w", "W", "p") and self.pal is not None:
            self.pal = self.pal.toggled()
            self.paper = self.pal.paper
            self.settings["white"] = self.paper
            save_json(SETTINGS, self.settings)
        elif k in ("[", "]"):
            au = self.au
            au.latency = max(-0.5, min(1.0, au.latency + (0.02 if k == "]" else -0.02)))
            self.settings["latency_ms"] = int(round(au.latency * 1000))
            save_json(SETTINGS, self.settings)
            self.latency_msg_until = now + 2.5
        elif k == "n" and self.scene is not None and self.cur_key:
            self.show_cover = not self.show_cover
            self.overrides[tr.key] = "cover" if self.show_cover else "scene"
            save_json(OVERRIDES, self.overrides)
            self.scene = make_scene(tr, self.show_cover, self.media, self.forced_scene)
            if self.prev is not None:
                self.trans = (self.prev, now)

    def sync_settings(self):
        """Pick up choices made in another window (e.g. the terminal app
        while the wallpaper runs): white/dark and per-track scene/cover."""
        s = load_json(SETTINGS)
        white = bool(s.get("white", False))
        if white != self.paper and self.pal is not None:
            self.pal = self.pal.toggled()
        self.paper = white
        self.au.latency = float(s.get("latency_ms", 0)) / 1000.0
        self.settings = s
        o = load_json(OVERRIDES)
        tr = self.npl.track
        if self.cur_key and (o.get(tr.key) == "cover") != self.show_cover:
            self.show_cover = not self.show_cover
            self.scene = make_scene(tr, self.show_cover, self.media, self.forced_scene)
            if self.prev is not None:
                self.trans = (self.prev, time.time())
        self.overrides = o

    @property
    def state(self):
        tr = self.npl.track
        if tr.status == "none" or not tr.title:
            return "idle"
        return tr.status

    # --- one picture -----------------------------------------------------------
    def frame(self, w, h, idle_line2=""):
        now = time.time()
        dt = min(0.1, now - self.last)
        self.last = now
        tr = self.npl.track
        au = self.au

        if tr.status == "none" or not tr.title:
            if self.cur_key is not None or self.idle_since is None:
                self.idle_since = now
            self.cur_key = None
            fr = Frame(w, h)
            draw_idle(fr, now - self.t0, self.idle_pal, idle_line2 or (
                "(%s)" % self.npl.error[:60] if self.npl.error else ""), keys=self.keys)
            fr = fr.flatten()          # keep prev at cell resolution for the fade
            self.prev = fr
            return fr

        if tr.key != self.cur_key:
            self.cur_key = tr.key
            self.media = M.find_media(tr)
            new_cover = self.overrides.get(tr.key) == "cover"
            akey = album_key(tr)
            # same album: keep the creature, but give the song new scenery
            avoid = self.scene.name if (akey and akey == self.cur_album and is_landscape(self.scene)) else None
            self.show_cover = new_cover
            self.pal = palette_from_art(tr.art, tr.seed, self.paper)
            self.art_seen = tr.art
            au.reset_tempo()
            self.scene = make_scene(tr, self.show_cover, self.media, self.forced_scene, avoid)
            self.scene_t = 0.0
            if self.prev is not None:
                self.trans = (self.prev, now)
            self.cur_album = akey
        elif tr.art is not None and tr.art is not self.art_seen:
            # artwork arrived (or changed) after the track started
            self.art_seen = tr.art
            self.pal = palette_from_art(tr.art, tr.seed, self.paper)
            if hasattr(self.scene, "set_art"):
                self.scene.set_art(tr.art)
            elif not self.show_cover and not self.media and not self.forced_scene and self.scene_t < 5:
                nm = track_scene_name(tr)
                if nm != self.scene.name:
                    self.scene = make_scene(tr, False, None)
                    if self.prev is not None:
                        self.trans = (self.prev, now)

        scene, pal = self.scene, self.pal
        speed = 1.0 if tr.status == "playing" else 0.08
        self.scene_t += dt * speed
        target = 1.0 if tr.status == "paused" else 0.0
        self.sleep += (target - self.sleep) * min(1.0, dt * (2.5 if target > self.sleep else 5.0))
        au.tick(now)
        au.advance(dt * speed, now)
        S.ENV["p"] = (tr.pos_now() / tr.duration) if tr.duration > 0 else (self.scene_t / 240.0) % 1.0
        S.ENV["beats"] = au.beat_count
        S.ENV["front"] = getattr(scene, "front", None)
        S.ENV["pos"] = tr.pos_now()
        S.ENV["dur"] = tr.duration
        S.ENV["sleep"] = self.sleep
        S.ENV["now"] = now
        S.ENV["cam"] = self.scene_t * S.PAN_SPEED
        fr = Frame(w, h)
        scene.draw(fr, self.scene_t, dt * speed, au, pal)
        if is_landscape(scene):
            S.foreground(fr, scene, self.scene_t, pal)
        fr = fr.flatten()
        if not isinstance(scene, (M.Cover, M.Video)):
            S.grade(fr, 0.5 if scene.name in ("night", "aurora") else 1.0)

        if self.trans is not None:
            old, ts = self.trans
            p = (now - ts) / 0.9
            if p >= 1.0 or old.chars.shape != fr.chars.shape:
                self.trans = None
            else:
                X, Y = S.grid(fr)
                m = hash01(X, Y, int(ts * 1000) & 0xFFFF) >= p
                fr.chars[m] = old.chars[m]
                fr.fg[m] = old.fg[m]
                fr.bg[m] = old.bg[m]
        clean = fr.chars.copy(), fr.fg.copy(), fr.bg.copy()

        info = ""
        if self.show_info:
            other = "scene" if self.show_cover else "cover"
            info = " %s" % scene.name
            if scene.sprite_name:
                info += " · %s" % scene.sprite_name
            if self.media:
                info += " · " + os.path.basename(self.media)
            if now < self.latency_msg_until:
                info += " · ビート補正 %+dms" % round(au.latency * 1000)
            info += " · n: %s · ?: 操作 " % other
        draw_hud(fr, tr, pal, info)
        if self.show_help:
            draw_help(fr, pal)
        # the fade uses the frame without the HUD, so text never dissolves
        self.prev = Frame(w, h, fine=False)
        self.prev.chars[:], self.prev.fg[:], self.prev.bg[:] = clean
        return fr

    def stop(self):
        self.au.stop()
        self.npl.stop()


def main():
    ap = argparse.ArgumentParser(description="Spotify ASCII terminal visualiser")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--scene", choices=SCENES, help="use this scene for every track")
    ap.add_argument("--no-audio", action="store_true", help="disable system-audio reactivity")
    ap.add_argument("--white", action="store_true", help="start in the white (light) look")
    ap.add_argument("--dark", action="store_true", help="start in the dark look (default)")
    ap.add_argument("--list", action="store_true", help="list scenes and exit")
    ap.add_argument("--any-player", action="store_true", help="also react to other media players (browser etc.)")
    ap.add_argument("--quit-idle", type=float, default=0, help="exit after N seconds without Spotify (0 = never)")
    args = ap.parse_args()
    if args.list:
        print("\n".join(SCENES + ["cover"]))
        return

    white = True if args.white else (False if args.dark else None)
    vis = Visualiser(args.scene, args.any_player, args.no_audio, white)
    term = Terminal()
    work_ema = 0.0           # average time spent per frame (adaptive frame rate)
    was_min = False
    frame_dt = 1.0 / max(5, args.fps)

    term.enter()
    try:
        while True:
            now = time.time()
            if msvcrt:
                while msvcrt.kbhit():
                    k = msvcrt.getwch()
                    if k in ("q", "Q", "\x1b", "\x03"):
                        return
                    vis.key(k)

            # minimised: draw nothing, just keep up with the music state
            if term.minimized():
                was_min = True
                vis.last = time.time()
                time.sleep(0.25)
                continue
            if was_min:
                was_min = False
                term.last_size = (0, 0)      # full redraw after restoring

            w, h = term.size()
            fr = vis.frame(w, h)
            term.draw(fr)
            state = vis.state
            if state == "idle":
                if args.quit_idle and vis.idle_since and now - vis.idle_since > args.quit_idle:
                    return
                time.sleep(0.1)
                continue

            el = time.time() - now
            work_ema = el if work_ema == 0.0 else work_ema * 0.9 + el * 0.1
            # paused: the picture barely moves, so draw far fewer frames
            target_dt = frame_dt if state == "playing" or vis.trans is not None else max(frame_dt, 1 / 10.0)
            # slow machine / huge window: settle on a steady lower frame rate
            # (with some headroom) instead of stuttering at full speed
            target_dt = max(target_dt, min(1 / 12.0, work_ema * 1.25))
            if el < target_dt:
                time.sleep(target_dt - el)
    except KeyboardInterrupt:
        pass
    finally:
        term.exit()
        vis.stop()


if __name__ == "__main__":
    main()
