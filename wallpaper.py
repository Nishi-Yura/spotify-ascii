"""Desktop wallpaper mode: the same picture as the terminal app, drawn behind
the desktop icons.

How it works
- main.Visualiser produces each frame (cells: character + colours).
- Glyphs are drawn from a small pixel atlas at half resolution (braille dots
  and block shapes are generated, text uses system fonts), then Windows
  stretches the image 2x to the monitor: crisp, square "pixels".
- The window is a layered child of the desktop (Progman) placed just below
  the icon layer, which is what Windows 11 24H2+ needs; older Windows uses
  the classic WorkerW window instead.
- Nothing is drawn while another window is maximised / full screen on that
  monitor, or while Spotify is idle for long.

Run: wallpaper.bat (start) / wallpaper-stop.bat (stop)
     .venv\\Scripts\\pythonw.exe wallpaper.py [--monitor N] [--rows 40] [--fps 15]
"""
import os
import sys
import time
import ctypes
import argparse
import ctypes.wintypes as wt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main as app                       # noqa: E402  (Visualiser, settings)
from render import WIDE_PAD, is_wide     # noqa: E402

STOP_EVENT = "Local\\spotify-ascii-wallpaper-stop"
MUTEX = "Local\\spotify-ascii-wallpaper"

u32 = ctypes.windll.user32
g32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32
for _f in ("FindWindowW", "FindWindowExW", "CreateWindowExW", "SetParent", "GetParent",
           "GetDC", "GetAncestor", "MonitorFromWindow", "GetForegroundWindow"):
    getattr(u32, _f).restype = wt.HDC if _f == "GetDC" else (wt.HANDLE if _f == "MonitorFromWindow" else wt.HWND)
u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.DefWindowProcW.restype = ctypes.c_ssize_t
k32.CreateEventW.restype = wt.HANDLE
k32.OpenEventW.restype = wt.HANDLE
k32.CreateMutexW.restype = wt.HANDLE
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
# explicit signatures: handles are 64-bit pointers and must not be passed as C ints
g32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
g32.StretchDIBits.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.DWORD]
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
u32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_ubyte, wt.DWORD]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
u32.SetParent.argtypes = [wt.HWND, wt.HWND]
u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.IsWindow.argtypes = [wt.HWND]
u32.DestroyWindow.argtypes = [wt.HWND]
u32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
u32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                    ctypes.POINTER(ctypes.c_size_t)]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsIconic.argtypes = [wt.HWND]
u32.IsZoomed.argtypes = [wt.HWND]
u32.GetDC.argtypes = [wt.HWND]
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.GetModuleHandleW.restype = wt.HMODULE
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
u32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
u32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]
ctypes.windll.dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
k32.SetEvent.argtypes = [wt.HANDLE]


# =============================================================================
# glyph atlas
# =============================================================================
def _font(paths, size):
    for p in paths:
        fp = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", p)
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


class Atlas:
    """char -> uint8 alpha mask (ch, cw). Wide (CJK) characters are split into
    a left half (the char) and a right half (key ('R', char))."""

    def __init__(self, cw, ch):
        self.cw, self.ch = cw, ch
        self.fnt = _font(["CascadiaMono.ttf", "consola.ttf"], max(6, int(ch * 0.82)))
        self.cjk = _font(["YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "msyh.ttc", "malgun.ttf"],
                         max(6, int(ch * 0.82)))
        self.sym = _font(["seguisym.ttf"], max(6, int(ch * 0.8)))
        self.index = {" ": 0}
        self.masks = [np.zeros((ch, cw), np.uint8)]

    def _add(self, key, m):
        self.index[key] = len(self.masks)
        self.masks.append(m)
        self._stack = None
        return self.index[key]

    def get(self, key):
        i = self.index.get(key)
        if i is not None:
            return i
        if isinstance(key, tuple):                 # right half of a wide char
            self.get(key[1])
            return self.index[key]
        c = key
        o = ord(c)
        if 0x2800 <= o <= 0x28FF:
            return self._add(c, self._braille(o - 0x2800))
        if c in QUAD:
            return self._add(c, self._quad(QUAD[c]))
        if c in BOX:
            return self._add(c, self._box(c))
        if is_wide(c):
            img = Image.new("L", (self.cw * 2, self.ch), 0)
            self._draw(img, c, self.cjk, self.cw * 2)
            a = np.asarray(img)
            i = self._add(c, a[:, :self.cw].copy())
            self._add(("R", c), a[:, self.cw:].copy())
            return i
        img = Image.new("L", (self.cw, self.ch), 0)
        font = self.fnt if o < 0x2000 or c in "♪♫…—" else self.sym
        self._draw(img, c, font, self.cw)
        return self._add(c, np.asarray(img).copy())

    def _draw(self, img, c, font, width):
        d = ImageDraw.Draw(img)
        try:
            l, t, r, b = d.textbbox((0, 0), c, font=font)
        except Exception:
            l, t, r, b = 0, 0, width, self.ch
        x = (width - (r - l)) / 2 - l
        y = (self.ch - (b - t)) / 2 - t
        if c in ".,_":
            y = self.ch * 0.15 + (self.ch - (b - t)) / 2 - t
        d.text((x, y), c, fill=255, font=font)

    def _braille(self, bits):
        cw, ch = self.cw, self.ch
        m = np.zeros((ch, cw), np.uint8)
        s = max(1, min(cw // 2, ch // 4) - 1)
        order = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 3), (1, 3)]   # dot1..dot8
        for k, (cx, ry) in enumerate(order):
            if bits >> k & 1:
                x0 = int(round(cx * cw / 2 + (cw / 2 - s) / 2))
                y0 = int(round(ry * ch / 4 + (ch / 4 - s) / 2))
                m[y0:y0 + s, x0:x0 + s] = 255
        return m

    def _quad(self, q):
        cw, ch = self.cw, self.ch
        m = np.zeros((ch, cw), np.uint8)
        hx, hy = cw // 2, ch // 2
        if q & 1: m[:hy, :hx] = 255
        if q & 2: m[:hy, hx:] = 255
        if q & 4: m[hy:, :hx] = 255
        if q & 8: m[hy:, hx:] = 255
        return m

    def _box(self, c):
        cw, ch = self.cw, self.ch
        m = np.zeros((ch, cw), np.uint8)
        my, mx = ch // 2, cw // 2
        t = 2 if c == "━" else 1
        left = c in "─━┐┘"
        right = c in "─━┌└"
        up = c in "│┘└"
        down = c in "│┌┐"
        if left: m[my:my + t, :mx + 1] = 255
        if right: m[my:my + t, mx:] = 255
        if up: m[:my + 1, mx:mx + 1] = 255
        if down: m[my:, mx:mx + 1] = 255
        return m

    def stack(self):
        if getattr(self, "_stack", None) is None:
            self._stack = np.stack(self.masks)
        return self._stack


QUAD = {c: i for i, c in enumerate(" ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█") if c != " "}
BOX = set("─━│┌┐└┘")


def compose(fr, atlas):
    """Cell frame -> RGB image (h*ch, w*cw, 3) uint8."""
    h, w = fr.h, fr.w
    chars = fr.chars
    flat = chars.ravel()
    uniq, inv = np.unique(flat, return_inverse=True)
    lut = np.empty(len(uniq), np.int64)
    pad_pos = None
    for i, c in enumerate(uniq):
        if c == WIDE_PAD:
            lut[i] = 0
            pad_pos = i
        else:
            lut[i] = atlas.get(c)
    idx = lut[inv].reshape(h, w)
    if pad_pos is not None:                     # right halves of wide chars
        ys, xs = np.nonzero(chars == WIDE_PAD)
        for y, x in zip(ys.tolist(), xs.tolist()):
            if x > 0:
                idx[y, x] = atlas.get(("R", chars[y, x - 1]))
    masks = atlas.stack()[idx]                                       # h, w, ch, cw
    # most pixels are fully on/off: just pick fg or bg
    out = np.where((masks >= 128)[..., None], fr.fg[:, :, None, None, :], fr.bg[:, :, None, None, :])
    # anti-aliased text edges: blend only those few pixels
    part = (masks > 0) & (masks < 255)
    if part.any():
        ii = np.nonzero(part)
        al = masks[ii].astype(np.uint16)[:, None]
        f = fr.fg[ii[0], ii[1]].astype(np.uint16)
        g = fr.bg[ii[0], ii[1]].astype(np.uint16)
        out[ii] = ((g * (255 - al) + f * al) // 255).astype(np.uint8)
    return out.transpose(0, 2, 1, 3, 4).reshape(h * atlas.ch, w * atlas.cw, 3)


# =============================================================================
# desktop window
# =============================================================================
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def monitors():
    """[(left, top, right, bottom, is_primary)] in virtual-screen pixels."""
    out = []
    MONPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, lprc, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        u32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        out.append((r.left, r.top, r.right, r.bottom, bool(mi.dwFlags & 1)))
        return True

    u32.EnumDisplayMonitors(None, None, MONPROC(cb), 0)
    return out


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, b, 256)
    return b.value


def desktop_parent():
    """-> (parent hwnd, insert-after hwnd or None)."""
    progman = u32.FindWindowW("Progman", None)
    res = ctypes.c_size_t()
    # ask Explorer to create the wallpaper WorkerW layer
    u32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    u32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    defview = u32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if defview:
        # Windows 11 24H2+: icons and wallpaper live inside Progman;
        # our window goes right below the icon layer
        return progman, defview
    # classic: the WorkerW that follows the one hosting the icons
    found = []
    ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, lp):
        if u32.FindWindowExW(h, None, "SHELLDLL_DefView", None):
            w = u32.FindWindowExW(None, h, "WorkerW", None)
            if w:
                found.append(w)
        return True

    u32.EnumWindows(ENUM(cb), 0)
    return (found[0] if found else progman), None


class DesktopWindow:
    def __init__(self, rect):
        self.rect = rect                       # monitor rect in virtual-screen pixels
        self._proc = WNDPROC(self._wndproc)    # keep a reference
        wc = WNDCLASS()
        wc.lpfnWndProc = self._proc
        wc.hInstance = k32.GetModuleHandleW(None)
        wc.lpszClassName = "SpotifyAsciiWallpaper"
        u32.RegisterClassW(ctypes.byref(wc))
        WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x80000, 0x08000000, 0x80
        self.hwnd = u32.CreateWindowExW(WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                                        wc.lpszClassName, "spotify-ascii wallpaper",
                                        0x80000000, 0, 0, 10, 10, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError("could not create the wallpaper window")
        u32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2)
        self.attach()
        self.hdc = u32.GetDC(self.hwnd)
        g32.SetStretchBltMode(self.hdc, 3)     # COLORONCOLOR: crisp, nearest pixel

    def attach(self):
        parent, after = desktop_parent()
        self.parent = parent
        u32.SetParent(self.hwnd, parent)
        style = (u32.GetWindowLongW(self.hwnd, -16) & 0xFFFFFFFF)
        style = (style & ~0x80000000) | 0x40000000 | 0x10000000     # -POPUP +CHILD +VISIBLE
        u32.SetWindowLongW(self.hwnd, -16, ctypes.c_int32(style & 0xFFFFFFFF).value)
        # position in the parent's client coordinates (the parent spans the virtual screen)
        pr = wt.RECT()
        u32.GetWindowRect(parent, ctypes.byref(pr))
        l, t, r, b = self.rect
        self.w, self.h = r - l, b - t
        u32.SetWindowPos(self.hwnd, after or None, l - pr.left, t - pr.top, self.w, self.h,
                         0x0010 | 0x0040)      # NOACTIVATE | SHOWWINDOW

    def alive(self):
        return bool(u32.IsWindow(self.hwnd)) and bool(u32.IsWindow(self.parent))

    def _wndproc(self, h, m, w, l):
        if m == 0x0014:                        # WM_ERASEBKGND: we paint everything
            return 1
        return u32.DefWindowProcW(h, m, w, l)

    def pump(self):
        msg = wt.MSG()
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    def blit(self, rgb):
        ih, iw = rgb.shape[:2]
        bgra = np.empty((ih, iw, 4), np.uint8)
        bgra[..., 0] = rgb[..., 2]
        bgra[..., 1] = rgb[..., 1]
        bgra[..., 2] = rgb[..., 0]
        bgra[..., 3] = 255
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = iw, -ih    # top-down
        bmi.biPlanes, bmi.biBitCount = 1, 32
        g32.StretchDIBits(self.hdc, 0, 0, self.w, self.h, 0, 0, iw, ih,
                          bgra.ctypes.data_as(ctypes.c_void_p), ctypes.byref(bmi), 0, 0x00CC0020)

    def close(self):
        try:
            u32.ReleaseDC(self.hwnd, self.hdc)
            u32.DestroyWindow(self.hwnd)
        except Exception:
            pass
        refresh_wallpaper()


def refresh_wallpaper():
    """Ask Explorer to repaint the desktop so the normal wallpaper shows again.
    (Only a redraw: the wallpaper setting itself is never touched, so
    slideshows and Windows Spotlight keep working.)"""
    u32.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    progman = u32.FindWindowW("Progman", None)
    flags = 0x0001 | 0x0004 | 0x0080 | 0x0100                  # INVALIDATE | ERASE | ALLCHILDREN | UPDATENOW
    u32.RedrawWindow(progman, None, None, flags)
    ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, lp):
        if _cls(h) == "WorkerW":
            u32.RedrawWindow(h, None, None, flags)
        return True

    u32.EnumWindows(ENUM(cb), 0)


def covered(rect, own):
    """True if a maximised / full-screen window hides this monitor."""
    l, t, r, b = rect
    hit = [False]
    ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    skip = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

    def cb(h, lp):
        if h == own or not u32.IsWindowVisible(h) or u32.IsIconic(h):
            return True
        cloaked = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked), 4)
        if cloaked.value or _cls(h) in skip:
            return True
        wr = wt.RECT()
        u32.GetWindowRect(h, ctypes.byref(wr))
        ix = max(0, min(r, wr.right) - max(l, wr.left))
        iy = max(0, min(b, wr.bottom) - max(t, wr.top))
        if ix * iy >= 0.92 * (r - l) * (b - t) or (u32.IsZoomed(h) and ix * iy > 0.5 * (r - l) * (b - t)):
            hit[0] = True
            return False
        return True

    u32.EnumWindows(ENUM(cb), 0)
    return hit[0]


# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="spotify-ascii desktop wallpaper")
    ap.add_argument("--monitor", type=int, default=0, help="1, 2, ... (default: primary monitor)")
    ap.add_argument("--rows", type=int, default=36, help="text rows on screen (more = finer, heavier)")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--no-hud", action="store_true", help="hide the song title and seek bar")
    ap.add_argument("--any-player", action="store_true")
    ap.add_argument("--stop", action="store_true", help="stop a running wallpaper")
    args = ap.parse_args()

    if args.stop:
        ev = k32.OpenEventW(0x0002, False, STOP_EVENT)      # EVENT_MODIFY_STATE
        if ev:
            k32.SetEvent(ev)
            print("stopping the wallpaper ...")
        else:
            print("the wallpaper is not running")
        return

    k32.CreateMutexW(None, False, MUTEX)
    if k32.GetLastError() == 183:                            # already running
        return
    stop_ev = k32.CreateEventW(None, True, False, STOP_EVENT)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        u32.SetProcessDPIAware()

    mons = monitors()
    if args.monitor and 1 <= args.monitor <= len(mons):
        rect = mons[args.monitor - 1][:4]
    else:
        rect = next((m[:4] for m in mons if m[4]), mons[0][:4])
    mw, mh = rect[2] - rect[0], rect[3] - rect[1]

    # cell grid: `rows` rows; glyphs rendered at half size and stretched 2x
    cell_h = max(8, mh // max(10, args.rows))
    cell_w = max(4, cell_h // 2)
    gh, gw = max(4, cell_h // 2), max(2, cell_w // 2)
    cols, rows = mw // cell_w, mh // cell_h
    atlas = Atlas(gw, gh)

    win = DesktopWindow(rect)
    vis = app.Visualiser(any_player=args.any_player, show_info=False, keys=False)
    if args.no_hud:
        app.draw_hud = lambda *a, **k: None

    frame_dt = 1.0 / max(2, args.fps)
    last_check = 0.0
    hidden = False
    work_ema = 0.0
    try:
        while k32.WaitForSingleObject(stop_ev, 0) != 0:      # WAIT_OBJECT_0 -> stop
            now = time.time()
            win.pump()
            if now - last_check > 0.5:
                last_check = now
                if not win.alive():                          # Explorer restarted
                    win.attach()
                hidden = covered(rect, win.hwnd)
                vis.sync_settings()
            if hidden:
                vis.last = time.time()
                time.sleep(0.25)
                continue
            fr = vis.frame(cols, rows)
            win.blit(compose(fr, atlas))
            state = vis.state
            el = time.time() - now
            work_ema = el if work_ema == 0.0 else work_ema * 0.9 + el * 0.1
            target = frame_dt if state == "playing" else 1 / 4.0
            target = max(target, min(1 / 6.0, work_ema * 1.3))
            if el < target:
                time.sleep(target - el)
    finally:
        vis.stop()
        win.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # pythonw has no console: keep the error for troubleshooting
        import traceback
        with open(os.path.join(HERE, "wallpaper.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n")
        raise
