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
def draw_idle(fr, t, pal, line2):
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

    overrides = load_json(OVERRIDES)
    settings = load_json(SETTINGS)
    # dark is the standard look; white is the alternative (w toggles, remembered)
    paper = bool(settings.get("white", False))
    if args.white:
        paper = True
    if args.dark:
        paper = False
    au_latency = float(settings.get("latency_ms", 0)) / 1000.0
    latency_msg_until = 0.0
    term = Terminal()
    npl = NowPlaying(any_player=args.any_player)
    npl.start()
    au = Audio(not args.no_audio)
    au.latency = au_latency
    au.start()
    idle_pal = palette_from_seed(1, paper=False)

    cur_key = None
    scene = None
    pal = None
    art_seen = None
    scene_t = 0.0
    show_cover = False
    media = None
    cur_album = ""
    sleep = 0.0
    trans = None
    prev = None
    idle_since = None
    t0 = time.time()
    last = t0
    frame_dt = 1.0 / max(5, args.fps)

    term.enter()
    try:
        while True:
            now = time.time()
            dt = min(0.1, now - last)
            last = now
            w, h = term.size()
            tr = npl.track

            if msvcrt:
                while msvcrt.kbhit():
                    k = msvcrt.getwch()
                    if k in ("q", "Q", "\x1b", "\x03"):
                        return
                    elif k in ("w", "W", "p") and pal is not None:
                        pal = pal.toggled()
                        paper = pal.paper
                        settings["white"] = paper
                        save_json(SETTINGS, settings)
                    elif k in ("[", "]"):
                        au.latency = max(-0.5, min(1.0, au.latency + (0.02 if k == "]" else -0.02)))
                        settings["latency_ms"] = int(round(au.latency * 1000))
                        save_json(SETTINGS, settings)
                        latency_msg_until = now + 2.5
                    elif k == "n" and scene is not None and cur_key:
                        show_cover = not show_cover
                        overrides[tr.key] = "cover" if show_cover else "scene"
                        save_json(OVERRIDES, overrides)
                        scene = make_scene(tr, show_cover, media, args.scene)
                        if prev is not None:
                            trans = (prev, now)

            fr = Frame(w, h)
            if tr.status == "none" or not tr.title:
                if cur_key is not None or idle_since is None:
                    idle_since = now
                cur_key = None
                if args.quit_idle and now - idle_since > args.quit_idle:
                    return
                line2 = ""
                if npl.error:
                    line2 = "(%s)" % npl.error[:60]
                draw_idle(fr, now - t0, idle_pal, line2)
                fr = fr.flatten()          # keep prev at cell resolution for the fade
                term.draw(fr)
                prev = fr
                time.sleep(0.1)
                continue

            if tr.key != cur_key:
                cur_key = tr.key
                media = M.find_media(tr)
                new_cover = overrides.get(tr.key) == "cover"
                akey = album_key(tr)
                # same album: keep the creature, but give the song new scenery
                avoid = scene.name if (akey and akey == cur_album and is_landscape(scene)) else None
                show_cover = new_cover
                pal = palette_from_art(tr.art, tr.seed, paper)
                art_seen = tr.art
                au.reset_tempo()
                scene = make_scene(tr, show_cover, media, args.scene, avoid)
                scene_t = 0.0
                if prev is not None:
                    trans = (prev, now)
                cur_album = akey
            elif tr.art is not None and tr.art is not art_seen:
                # artwork arrived (or changed) after the track started
                art_seen = tr.art
                pal = palette_from_art(tr.art, tr.seed, paper)
                if hasattr(scene, "set_art"):
                    scene.set_art(tr.art)
                elif not show_cover and not media and not args.scene and scene_t < 5:
                    nm = track_scene_name(tr)
                    if nm != scene.name:
                        scene = make_scene(tr, False, None)
                        if prev is not None:
                            trans = (prev, now)

            speed = 1.0 if tr.status == "playing" else 0.08
            scene_t += dt * speed
            target = 1.0 if tr.status == "paused" else 0.0
            sleep += (target - sleep) * min(1.0, dt * (2.5 if target > sleep else 5.0))
            au.tick(now)
            au.advance(dt * speed, now)
            S.ENV["p"] = (tr.pos_now() / tr.duration) if tr.duration > 0 else (scene_t / 240.0) % 1.0
            S.ENV["beats"] = au.beat_count
            S.ENV["front"] = getattr(scene, "front", None)
            S.ENV["pos"] = tr.pos_now()
            S.ENV["dur"] = tr.duration
            S.ENV["sleep"] = sleep
            S.ENV["now"] = now
            S.ENV["cam"] = scene_t * S.PAN_SPEED
            fr = Frame(w, h)
            scene.draw(fr, scene_t, dt * speed, au, pal)
            if is_landscape(scene):
                S.foreground(fr, scene, scene_t, pal)
            fr = fr.flatten()
            if not isinstance(scene, (M.Cover, M.Video)):
                S.grade(fr, 0.5 if scene.name in ("night", "aurora") else 1.0)

            if trans is not None:
                old, ts = trans
                p = (now - ts) / 0.9
                if p >= 1.0 or old.chars.shape != fr.chars.shape:
                    trans = None
                else:
                    X, Y = S.grid(fr)
                    m = hash01(X, Y, int(ts * 1000) & 0xFFFF) >= p
                    fr.chars[m] = old.chars[m]
                    fr.fg[m] = old.fg[m]
                    fr.bg[m] = old.bg[m]
            prev_clean = fr.chars.copy(), fr.fg.copy(), fr.bg.copy()

            other = "scene" if show_cover else "cover"
            info = " %s" % scene.name
            if scene.sprite_name:
                info += " · %s" % scene.sprite_name
            if media:
                info += " · " + os.path.basename(media)
            if now < latency_msg_until:
                info += " · ビート補正 %+dms" % round(au.latency * 1000)
            info += " · n: %s " % other
            draw_hud(fr, tr, pal, info)
            term.draw(fr)
            # the fade uses the frame without the HUD, so text never dissolves
            prev = Frame(w, h, fine=False)
            prev.chars[:], prev.fg[:], prev.bg[:] = prev_clean

            el = time.time() - now
            # paused: the picture barely moves, so draw far fewer frames
            target_dt = frame_dt if tr.status == "playing" or trans is not None else max(frame_dt, 1 / 10.0)
            if el < target_dt:
                time.sleep(target_dt - el)
    except KeyboardInterrupt:
        pass
    finally:
        term.exit()
        au.stop()
        npl.stop()


if __name__ == "__main__":
    main()
