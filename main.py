"""Spotify ASCII: animated, colour ASCII scenery that follows what Spotify is
playing - as the desktop wallpaper (standard; this terminal then lists the
keys and changes the wallpaper) or drawn in the terminal itself (t switches).

- each track has two scenes: a landscape picked from the album art's colours,
  and the album cover itself (n toggles, remembered per track)
- the time of day follows the song: day -> sunset -> night with stars
- creatures walk / flap in time with the detected tempo (BPM)
- creatures cross the screen in step with the seek bar, walk in / out
  between songs and fall asleep while paused
- songs from the same album share the creature, each song gets its own scenery
- the scenery scrolls slowly (far layers slower), near props slide past in front

keys:  q quit   n scene <-> cover   w white/dark   t wallpaper <-> terminal
       [ ]  shift beat timing earlier / later (to match what you hear)
       wallpaper: + - detail, 1-9 monitor on/off, a all monitors
"""
import os
import json
import time
import zlib
import ctypes
import argparse
import threading
import numpy as np

from render import Terminal, Frame, is_wide, ConsoleFont
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
    ("+ -", "文字の大きさ（Windows Terminal では Ctrl + / Ctrl -）"),
    ("i", "曲名とシークバー 表示 / 非表示"),
    ("t", "壁紙モードに切り替え"),
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
    HUD). The terminal and the desktop wallpaper both use it; the wallpaper
    may ask for several sizes in one tick (monitors of different sizes):
    time only moves on the first call, and fades are kept per size."""

    def __init__(self, scene=None, any_player=False, no_audio=False, white=None):
        self.forced_scene = scene
        self.show_info = True        # info line at the bottom (terminal only)
        self.keys = True             # key guide on the idle screen (terminal only)
        self.overrides = load_json(OVERRIDES)
        self.settings = load_json(SETTINGS)
        self.hud = bool(self.settings.get("hud", True))
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
        self.notice = ""             # short message shown in the info line
        self.notice_until = 0.0
        self.trans = None            # ({size: old frame}, start time) while fading
        self.prev = {}               # size -> last frame without the HUD
        self.idle_since = None
        self.t0 = time.time()
        self.last = self.t0

    def save(self):
        save_json(SETTINGS, self.settings)

    def set_scene(self, scene):
        if self.scene is not None:
            self.scene.close()
        self.scene = scene

    def _fade(self, now):
        if self.prev:
            self.trans = (dict(self.prev), now)

    @property
    def fading(self):
        return self.trans is not None

    # --- input ---------------------------------------------------------------
    def key(self, k):
        now = time.time()
        tr = self.npl.track
        if self.state != "idle":
            self.update_track(now)
        if k in ("?", "/", "h", "H"):
            self.show_help = not self.show_help
        elif k in ("w", "W", "p"):
            self.paper = not self.paper
            if self.pal is not None:
                self.pal = self.pal.toggled()
            self.settings["white"] = self.paper
            self.save()
        elif k in ("i", "I"):
            self.hud = not self.hud
            self.settings["hud"] = self.hud
            self.save()
        elif k in ("[", "]"):
            au = self.au
            au.latency = max(-0.5, min(1.0, au.latency + (0.02 if k == "]" else -0.02)))
            self.settings["latency_ms"] = int(round(au.latency * 1000))
            self.save()
            self.latency_msg_until = now + 2.5
        elif k in ("n", "N") and self.scene is not None and self.cur_key:
            self.show_cover = not self.show_cover
            self.overrides[tr.key] = "cover" if self.show_cover else "scene"
            save_json(OVERRIDES, self.overrides)
            self.set_scene(make_scene(tr, self.show_cover, self.media, self.forced_scene))
            self._fade(now)

    @property
    def state(self):
        tr = self.npl.track
        if tr.status == "none" or not tr.title:
            return "idle"
        return tr.status

    # --- track changes ---------------------------------------------------------
    def update_track(self, now):
        """Follow the current track: new song -> new scene and colours,
        artwork arriving late -> recolour."""
        tr = self.npl.track
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
            self.au.reset_tempo()
            self.set_scene(make_scene(tr, self.show_cover, self.media, self.forced_scene, avoid))
            self.scene_t = 0.0
            self._fade(now)
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
                    self.set_scene(make_scene(tr, False, None))
                    self._fade(now)

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
            self.prev[(w, h)] = fr
            return fr

        self.update_track(now)
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
        S.ENV["playing"] = tr.status == "playing"
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
            olds, ts = self.trans
            p = (now - ts) / 0.9
            old = olds.get((w, h))
            if p >= 1.0:
                self.trans = None
            elif old is not None and old.chars.shape == fr.chars.shape:
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
            if now < self.notice_until and self.notice:
                info += " · " + self.notice
            info += " · n: %s · t: 壁紙 · ?: 操作 " % other
        if self.hud:
            draw_hud(fr, tr, pal, info)
        elif info:
            fr.text(1, fr.h - 1, clip_text(info, fr.w - 2), pal.tone(0.45))
        if self.show_help:
            draw_help(fr, pal)
        # the fade uses the frame without the HUD, so text never dissolves
        prev = Frame(w, h, fine=False)
        prev.chars[:], prev.fg[:], prev.bg[:] = clean
        self.prev[(w, h)] = prev
        return fr

    def stop(self):
        if self.scene is not None:
            self.scene.close()
        self.au.stop()
        self.npl.stop()


# --- wallpaper mode: the terminal is the remote control -------------------------
PANEL_BG = (12, 12, 16)
PANEL_FG = (200, 200, 212)
PANEL_DIM = (110, 110, 128)
PANEL_KEY = (230, 200, 120)
PANEL_ON = (120, 210, 150)
PANEL_OFF = (200, 120, 110)


def draw_panel(fr, vis, wall, notice=""):
    """What the terminal shows while the picture is on the desktop: the
    keys (always), what each one is set to now, and the monitors."""
    fr.clear(PANEL_BG)
    tr = vis.npl.track
    y = 1
    fr.text(2, y, "spotify-ascii", PANEL_FG)
    fr.text(17, y, "壁紙モード", PANEL_ON)
    y += 2
    if vis.state == "idle":
        msg = "Spotify で曲を再生すると壁紙が動き出します" + "." * (int(time.time() * 2) % 4)
        fr.text(2, y, msg, PANEL_DIM)
        if vis.npl.error:
            fr.text(2, y + 1, clip_text("(%s)" % vis.npl.error, fr.w - 4), PANEL_DIM)
    else:
        icon = "♫" if tr.status == "playing" else "||"
        fr.text(2, y, clip_text("%s  %s — %s" % (icon, tr.title, tr.artist), fr.w - 4), PANEL_FG)
        if tr.duration > 0:
            pos = tr.pos_now()
            left, right = fmt_time(pos), fmt_time(tr.duration)
            x0 = 5 + len(left)
            n = max(0, min(40, fr.w - x0 - len(right) - 4))
            fr.text(5, y + 1, left, PANEL_DIM)
            fr.text(x0 + 1, y + 1, "─" * n, (60, 60, 72))
            fr.text(x0 + 1, y + 1, "━" * int(n * min(1.0, pos / tr.duration)), PANEL_KEY)
            fr.text(x0 + n + 2, y + 1, right, PANEL_DIM)
    y += 3

    nmon = len(wall.mons)
    if vis.scene is None or vis.state == "idle":
        scene_now = "—"
    elif vis.show_cover:
        scene_now = "ジャケット"
    else:
        scene_now = "風景（%s%s）" % (vis.scene.name, " · " + vis.scene.sprite_name if vis.scene.sprite_name else "")
    rows = [
        ("n", "風景 ⇄ ジャケット（曲ごとに記憶）", scene_now),
        ("w", "ダーク ⇄ ホワイト", "ホワイト" if vis.paper else "ダーク"),
        ("+ -", "壁紙の細かさ（+ で細かく）", "%d 行" % wall.rows),
        ("1-%d" % nmon if nmon > 1 else "1", "モニターごとに 表示 / 非表示", ""),
        ("a", "すべてのモニターに表示", "すべて" if wall.selection == "all" else ""),
        ("i", "曲名とシークバー 表示 / 非表示", "表示" if vis.hud else "非表示"),
        ("[ ]", "ビートのタイミング 早く / 遅く", "%+dms" % round(vis.au.latency * 1000)),
        ("t", "ターミナルで表示する（壁紙は元に戻ります）", ""),
        ("q", "終了（壁紙も元に戻ります）", ""),
    ]
    fr.text(2, y, "操作", PANEL_DIM)
    y += 1
    dw = max(dwidth(d) for _, d, _ in rows)
    for k, d, v in rows:
        fr.text(4, y, k, PANEL_KEY)
        fr.text(11, y, d, PANEL_FG)
        if v:
            fr.text(11 + dw + 3, y, clip_text(v, max(4, fr.w - dw - 16)), PANEL_ON)
        y += 1
    y += 1

    fr.text(2, y, "モニター", PANEL_DIM)
    y += 1
    shown = set(wall.shown())
    for i, m in enumerate(wall.mons, 1):
        on = i in shown
        fr.text(4, y, "■" if on else "□", PANEL_ON if on else PANEL_DIM)
        fr.text(6, y, str(i), PANEL_KEY)
        fr.text(9, y, "%d×%d" % (m[2] - m[0], m[3] - m[1]), PANEL_FG)
        if m[4]:
            fr.text(21, y, "メイン", PANEL_DIM)
        if not on:
            st, col = "表示しない", PANEL_DIM
        elif wall.views.get(i, [None, None, False])[2]:
            st, col = "一時停止中（全画面のウィンドウがあります）", PANEL_OFF
        else:
            st, col = "表示中", PANEL_ON
        fr.text(29, y, st, col)
        y += 1

    if notice:
        fr.text(2, min(fr.h - 2, y + 1), clip_text(notice, fr.w - 4), PANEL_KEY)
    fr.text(2, fr.h - 1, clip_text("このウィンドウを閉じると壁紙も止まります（最小化は OK）", fr.w - 4), PANEL_DIM)


class App:
    """One program, two ways to show the picture:

    wallpaper (standard)  the picture is the desktop wallpaper of the chosen
                          monitors; this terminal lists the keys and changes it
    terminal              the picture is drawn in this terminal (t switches)
    """

    def __init__(self, vis, args):
        self.vis = vis
        self.args = args
        self.term = Terminal()
        self.font = ConsoleFont()
        self.font_changed = False
        self.wall = None
        self.mode = None
        self.notice, self.notice_until = "", 0.0
        self.quit = threading.Event()        # set from the console close handler too
        self.done = threading.Event()
        self.work_ema = 0.0
        self.next_wall = 0.0
        self.next_panel = 0.0
        self.was_min = False

    def say(self, msg, secs=3.0):
        self.notice, self.notice_until = msg, time.time() + secs
        self.vis.notice, self.vis.notice_until = msg, self.notice_until
        self.next_panel = 0.0

    # --- modes -------------------------------------------------------------------
    def set_mode(self, mode):
        if mode == self.mode:
            return
        vis = self.vis
        if mode == "wallpaper":
            if self.font_changed:
                self.font.restore()               # the key list reads better at the normal size
                self.font_changed = False
            try:
                import wallpaper as WP
                if self.wall is None:
                    s = vis.settings
                    self.wall = WP.Wallpaper(self.args.rows or s.get("wallpaper_rows", WP.DEFAULT_ROWS),
                                             s.get("wallpaper_monitors"))
            except Exception as e:
                self.wall = None
                self.say("壁紙にできませんでした: %s" % e, 6)
                mode = "terminal"
        if mode == "terminal":
            if self.wall is not None:
                self.wall.close()
                self.wall = None
            # classic console: about 2x finer (half the font size) unless the
            # user picked a size before; + / - change it. Restored on exit.
            if self.font.supported:
                want = int(vis.settings.get("console_font_px", 0)) or max(6, self.font.height // 2)
                self.font_changed = self.font.set_height(want) or self.font_changed
        vis.show_info = vis.keys = mode == "terminal"
        vis.show_help = False
        vis.trans = None
        vis.prev = {}
        self.mode = mode
        self.term.last_size = (0, 0)              # full redraw
        self.next_wall = self.next_panel = 0.0

    def save_wall(self):
        s = self.vis.settings
        s["wallpaper_rows"] = self.wall.rows
        s["wallpaper_monitors"] = self.wall.selection
        self.vis.save()

    # --- keys --------------------------------------------------------------------
    def key(self, k):
        vis = self.vis
        if k in ("q", "Q", "\x1b", "\x03"):
            self.quit.set()
            return
        if k in ("t", "T"):
            self.set_mode("terminal" if self.mode == "wallpaper" else "wallpaper")
            return
        if self.mode == "wallpaper":
            wall = self.wall
            if k in ("+", "=", "-", "_"):
                old = wall.rows
                wall.set_rows(wall.rows + (12 if k in ("+", "=") else -12))
                vis.trans, vis.prev = None, {}
                self.save_wall()
                self.say("壁紙の細かさ: %d 行" % wall.rows if wall.rows != old else "これ以上は変えられません")
            elif k.isdigit() and k != "0":
                i = int(k)
                if i > len(wall.mons):
                    self.say("モニター %s はありません" % k)
                elif wall.toggle(i):
                    self.save_wall()
                    self.say("モニター %d: %s" % (i, "表示" if i in wall.shown() else "非表示"))
                else:
                    self.say("最後の 1 枚は消せません（t でターミナル表示にできます）")
            elif k in ("a", "A"):
                wall.show_all()
                self.save_wall()
                self.say("すべてのモニターに表示")
            elif k in ("?", "/", "h", "H"):
                pass                               # the keys are always listed here
            else:
                vis.key(k)
            self.next_panel = 0.0
            self.next_wall = 0.0                   # show the change right away
            return
        if k in ("+", "=", "-", "_"):
            if self.font.supported:
                h = self.font.height + (-1 if k in ("+", "=") else 1)   # smaller font = finer picture
                if self.font.set_height(h):
                    self.font_changed = True
                    vis.settings["console_font_px"] = self.font.height
                    vis.save()
                self.say("文字の大きさ: %dpx" % self.font.height)
            else:
                self.say("Windows Terminal では Ctrl + / Ctrl - で大きさを変えられます")
            return
        vis.key(k)

    # --- one step of each mode ---------------------------------------------------
    def step_terminal(self, now):
        """Draw the picture in this terminal. Returns seconds to wait."""
        vis, term = self.vis, self.term
        # minimised: draw nothing, just keep up with the music state
        if term.minimized():
            self.was_min = True
            vis.last = time.time()
            return 0.25
        if self.was_min:
            self.was_min = False
            term.last_size = (0, 0)               # full redraw after restoring
        w, h = term.size()
        term.draw(vis.frame(w, h))
        state = vis.state
        if state == "idle":
            return 0.1
        el = time.time() - now
        self.work_ema = el if self.work_ema == 0.0 else self.work_ema * 0.9 + el * 0.1
        frame_dt = 1.0 / max(5, self.args.fps or 30)
        # paused: the picture barely moves, so draw far fewer frames
        target = frame_dt if state == "playing" or vis.fading else max(frame_dt, 1 / 10.0)
        # slow machine / huge window: settle on a steady lower frame rate
        # (with some headroom) instead of stuttering at full speed
        target = max(target, min(1 / 12.0, self.work_ema * 1.25))
        return target - el

    def step_wallpaper(self, now):
        """Draw the wallpaper when due and keep the key list up to date.
        Returns seconds to wait (short, so keys answer quickly)."""
        vis = self.vis
        if now >= self.next_wall:
            if self.wall.draw(vis):
                el = time.time() - now
                self.work_ema = el if self.work_ema == 0.0 else self.work_ema * 0.9 + el * 0.1
                frame_dt = 1.0 / max(2, self.args.fps or 10)
                target = frame_dt if vis.state == "playing" or vis.fading else 1 / 4.0
                target = max(target, min(1 / 5.0, self.work_ema * 1.3))
            else:                                  # every monitor is covered: rest
                vis.last = time.time()
                if vis.state != "idle":
                    vis.update_track(now)
                target = 0.25
            self.next_wall = now + target
        if now >= self.next_panel:
            self.next_panel = now + 0.25
            if self.term.minimized():
                self.was_min = True
            else:
                if self.was_min:
                    self.was_min = False
                    self.term.last_size = (0, 0)
                w, h = self.term.size()
                fr = Frame(w, h, fine=False)
                draw_panel(fr, vis, self.wall, self.notice if now < self.notice_until else "")
                self.term.draw(fr)
        return min(0.03, self.next_wall - time.time())

    # --- main loop ---------------------------------------------------------------
    def run(self, mode):
        self.term.enter()
        try:
            self.set_mode(mode)
            while not self.quit.is_set():
                now = time.time()
                if msvcrt:
                    while msvcrt.kbhit() and not self.quit.is_set():
                        self.key(msvcrt.getwch())
                if self.quit.is_set():
                    break
                vis = self.vis
                if vis.state == "idle" and self.args.quit_idle and vis.idle_since \
                        and now - vis.idle_since > self.args.quit_idle:
                    break
                if vis.state != "idle":
                    vis.idle_since = None
                elif vis.idle_since is None:
                    vis.idle_since = now
                wait = self.step_terminal(now) if self.mode == "terminal" else self.step_wallpaper(now)
                if wait > 0:
                    time.sleep(wait)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                if self.wall is not None:
                    self.wall.close()
                if self.font_changed:
                    self.font.restore()
                self.vis.stop()
                self.term.exit()
            finally:
                self.done.set()


def on_console_close(app):
    """Closing the terminal window ends the program: put the normal wallpaper
    back before Windows ends the process (it allows a few seconds)."""
    if os.name != "nt":
        return
    HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

    def handler(ev):
        if ev in (2, 5, 6):                       # close, log off, shut down
            app.quit.set()
            app.done.wait(4)
            return 1
        return 0                                   # Ctrl+C etc.: the usual handling

    app._ctrl_handler = HANDLER(handler)          # keep a reference
    ctypes.windll.kernel32.SetConsoleCtrlHandler(app._ctrl_handler, True)


def already_running():
    """Only one copy at a time (two would fight over the desktop)."""
    if os.name != "nt":
        return False
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW(None, False, "Local\\spotify-ascii")
    return k32.GetLastError() == 183               # ERROR_ALREADY_EXISTS


def main():
    ap = argparse.ArgumentParser(description="Spotify ASCII visualiser (desktop wallpaper / terminal)")
    ap.add_argument("--terminal", action="store_true", help="start with the picture in the terminal")
    ap.add_argument("--wallpaper", action="store_true", help="start as the desktop wallpaper (default)")
    ap.add_argument("--fps", type=int, default=0, help="frame rate (default: 10 wallpaper / 30 terminal)")
    ap.add_argument("--rows", type=int, default=0, help="wallpaper detail in text rows (more = finer, heavier)")
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

    if already_running():
        print("spotify-ascii は別のウィンドウで動いています。そちらで操作してください。")
        time.sleep(4)
        return
    if os.name == "nt":
        import wallpaper as WP
        WP.set_dpi_aware()                          # real pixels on every monitor

    white = True if args.white else (False if args.dark else None)
    vis = Visualiser(args.scene, args.any_player, args.no_audio, white)
    mode = "terminal" if args.terminal or os.name != "nt" else "wallpaper"
    app = App(vis, args)
    on_console_close(app)
    app.run(mode)


if __name__ == "__main__":
    main()
