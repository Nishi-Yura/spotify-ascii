"""Icon in the notification area (next to the clock): the same controls as
the keys, plus "show the window", autostart and quit, so the program can be
handled without finding its terminal window.

The menu runs on its own thread. Every item only puts a key into
App.pending; the main loop handles it like a key press, so all the real work
stays on the main thread.
"""
import ctypes
import math
import threading

import autostart
import version

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:                      # not installed yet / not Windows
    pystray = None

WM_RBUTTONUP = 0x0205


def icon_image(size=64):
    """A small dot-art picture: hills under a sun, in braille-like dots."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=(16, 18, 26, 255))
    step = size / 8
    r = max(1, size // 22)
    for gy in range(8):
        for gx in range(8):
            x, y = (gx + 0.5) * step, (gy + 0.5) * step
            hill = size * (0.62 - 0.12 * math.sin(gx * 0.8 + 0.6))
            sun = math.hypot(x - size * 0.68, y - size * 0.3) < size * 0.16
            if y > hill:
                col = (120, 210, 150, 255)
            elif sun:
                col = (240, 200, 110, 255)
            else:
                continue
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    return img


def show_console():
    """Bring the program's terminal window to the front."""
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GetConsoleWindow.restype = ctypes.c_void_p
    h = k32.GetConsoleWindow()
    if h:
        u32.ShowWindow(ctypes.c_void_p(h), 9)          # SW_RESTORE
        u32.SetForegroundWindow(ctypes.c_void_p(h))


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = None
        if pystray is None:
            return

        class _Icon(pystray.Icon):
            # pystray builds the menu once; rebuild it right before it opens,
            # so ticks and labels show the current settings
            def _on_notify(icon, wparam, lparam):
                if lparam == WM_RBUTTONUP:
                    try:
                        icon._update_menu()
                    except Exception:
                        pass
                super(_Icon, icon)._on_notify(wparam, lparam)

        self.icon = _Icon("spotify-ascii", icon_image(), "spotify-ascii " + version.label(),
                          menu=pystray.Menu(self._items))
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.icon.run()
        except Exception:
            self.icon = None

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    # --- menu --------------------------------------------------------------------
    def _send(self, k):
        return lambda icon, item: self.app.pending.put(k)

    def _items(self):
        Item, Menu = pystray.MenuItem, pystray.Menu
        app, vis = self.app, self.app.vis
        tr = vis.npl.track
        wall = app.wall if app.mode == "wallpaper" else None
        now = "♫ %s — %s" % (tr.title, tr.artist) if vis.state != "idle" else "Spotify を待っています"
        items = [
            Item("spotify-ascii " + version.label(), None, enabled=False),
            Item(now[:60], None, enabled=False),
            Menu.SEPARATOR,
            Item("ウィンドウを表示", lambda icon, item: show_console(), default=True),
            Item("ジャケットを表示", self._send("n"), checked=lambda item: vis.show_cover,
                 enabled=vis.state != "idle"),
            Item("ホワイト", self._send("w"), checked=lambda item: vis.paper),
            Item("曲名とシークバー", self._send("i"), checked=lambda item: vis.hud),
        ]
        if wall is not None:
            mons = [Item("%d: %d×%d%s" % (i, m[2] - m[0], m[3] - m[1], "（メイン）" if m[4] else ""),
                         self._send(str(i)), checked=lambda item, i=i: i in wall.shown())
                    for i, m in enumerate(wall.mons, 1) if i <= 9]
            mons += [Menu.SEPARATOR, Item("すべて", self._send("a"), checked=lambda item: wall.selection == "all")]
            items += [
                Item("細かくする（%d 行）" % wall.rows, self._send("+")),
                Item("粗くする", self._send("-")),
                Item("モニター", Menu(*mons)),
            ]
        items += [
            Item("ターミナルで表示", self._send("t"), checked=lambda item: app.mode == "terminal"),
            Menu.SEPARATOR,
            Item("Windows の起動時に開始", lambda icon, item: autostart.set_enabled(not autostart.enabled()),
                 checked=lambda item: autostart.enabled()),
            Item("終了", self._send("q")),
        ]
        return items
