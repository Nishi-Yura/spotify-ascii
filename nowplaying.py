"""Now-playing info for the Spotify desktop app via Windows media sessions.

No login / API key: uses GlobalSystemMediaTransportControls (same source as the
Windows volume flyout). Falls back to reading the Spotify window title.
"""
import time
import zlib
import threading
import datetime
from dataclasses import dataclass
from typing import Optional

try:
    import asyncio
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
    from winsdk.windows.storage.streams import Buffer, InputStreamOptions
    HAVE_WINSDK = True
except Exception:
    HAVE_WINSDK = False


@dataclass
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "none"       # none | playing | paused
    position: float = 0.0
    duration: float = 0.0
    art: Optional[bytes] = None
    captured: float = 0.0

    @property
    def key(self):
        return "%s - %s" % (self.artist, self.title) if self.title else ""

    @property
    def seed(self):
        return zlib.crc32(self.key.encode("utf-8"))

    def pos_now(self):
        if self.status == "playing" and self.duration > 0:
            return min(self.duration, self.position + (time.time() - self.captured))
        return self.position


class NowPlaying:
    def __init__(self, any_player=False):
        self.any_player = any_player
        self.track = Track()
        self.error = ""
        self.backend = "winsdk" if HAVE_WINSDK else "window-title"
        self._run = False
        self._art_cache = {}
        self._art_tries = {}

    def start(self):
        self._run = True
        threading.Thread(target=self._thread, daemon=True).start()

    def stop(self):
        self._run = False

    def _thread(self):
        if HAVE_WINSDK:
            try:
                asyncio.run(self._loop_winsdk())
                return
            except Exception as e:
                self.error = "winsdk: %s" % e
                self.backend = "window-title"
        while self._run:
            self.track = self._poll_window_title()
            time.sleep(1.0)

    # --- media session backend --------------------------------------------
    async def _loop_winsdk(self):
        mgr = await _Manager.request_async()
        while self._run:
            try:
                sess = None
                for s in mgr.get_sessions():
                    if "spotify" in (s.source_app_user_model_id or "").lower():
                        sess = s
                        break
                if sess is None and self.any_player:
                    sess = mgr.get_current_session()
                if sess is None:
                    self.track = Track()
                    await asyncio.sleep(1.0)
                    continue
                info = await sess.try_get_media_properties_async()
                pi = sess.get_playback_info()
                tl = sess.get_timeline_properties()
                title = info.title or ""
                artist = info.artist or ""
                album = info.album_title or ""
                playing = pi.playback_status == _Status.PLAYING
                status = "playing" if playing else ("paused" if title else "none")
                pos = tl.position.total_seconds() if tl.position is not None else 0.0
                dur = tl.end_time.total_seconds() if tl.end_time is not None else 0.0
                if dur < 0:
                    dur = 0.0
                captured = time.time()
                try:
                    lu = tl.last_updated_time
                    if lu is not None and playing:
                        age = (datetime.datetime.now(datetime.timezone.utc) - lu).total_seconds()
                        if 0 < age < 3600:
                            pos += age
                except Exception:
                    pass
                key = "%s - %s" % (artist, title)
                art = self._art_cache.get(key)
                if art is None and self._art_tries.get(key, 0) < 6 and info.thumbnail is not None:
                    self._art_tries[key] = self._art_tries.get(key, 0) + 1
                    try:
                        art = await self._read_thumb(info.thumbnail)
                        if art:
                            self._art_cache[key] = art
                            if len(self._art_cache) > 60:
                                self._art_cache.pop(next(iter(self._art_cache)))
                    except Exception:
                        art = None
                self.track = Track(title, artist, album, status, pos, dur, art, captured)
                self.error = ""
            except Exception as e:
                self.error = str(e)
            await asyncio.sleep(0.4)

    async def _read_thumb(self, ref):
        st = await ref.open_read_async()
        size = st.size
        if not size:
            return None
        buf = Buffer(size)
        await st.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        return bytes(buf)

    # --- window-title fallback --------------------------------------------
    def _poll_window_title(self):
        try:
            import ctypes
            import ctypes.wintypes as wt
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
            def cb(hwnd, lp):
                if not user32.IsWindowVisible(hwnd):
                    return True
                pid = wt.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                h = kernel32.OpenProcess(0x1000, False, pid)
                if not h:
                    return True
                buf = ctypes.create_unicode_buffer(1024)
                size = wt.DWORD(1024)
                ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
                kernel32.CloseHandle(h)
                if ok and buf.value.lower().endswith("spotify.exe"):
                    n = user32.GetWindowTextLengthW(hwnd)
                    tb = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, tb, n + 1)
                    if tb.value:
                        found.append(tb.value)
                return True

            user32.EnumWindows(cb, 0)
        except Exception as e:
            self.error = str(e)
            return Track()
        if not found:
            return Track()
        titled = [t for t in found if " - " in t]
        if titled:
            artist, title = max(titled, key=len).split(" - ", 1)
            return Track(title.strip(), artist.strip(), "", "playing", 0, 0, None, time.time())
        return Track("", "", "", "none")
