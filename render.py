"""Terminal frame buffer + fast ANSI true-colour renderer.

A Frame is w x h character cells. In *fine* mode the frame also carries a
2x4 sub-cell grid (braille dots): scenes draw intensity fields at that higher
resolution and `flatten()` turns every cell into a braille glyph, while text,
sprites and literal characters still occupy whole cells.
"""
import os
import sys
import ctypes
import shutil
import unicodedata
import numpy as np

WIDE_PAD = "\x01"  # placeholder cell after an east-asian wide character
_BRAILLE = np.array([chr(0x2800 + i) for i in range(256)], dtype="<U1")
_BRAILLE[0] = " "
_WEIGHTS = np.array([[1, 8], [2, 16], [4, 32], [64, 128]], dtype=np.int32)
_QUAD = np.array(list(" ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"), dtype="<U1")
SIL_BODY, SIL_HOLE, SIL_DETAIL, SIL_WIN, SIL_WINDOT = 1, 2, 3, 4, 5


def enable_vt():
    if os.name != "nt":
        return
    try:
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def is_wide(c):
    return unicodedata.east_asian_width(c) in ("W", "F")


class Frame:
    def __init__(self, w, h, fine=True):
        self.w, self.h = w, h
        self.fine = fine
        self.sx, self.sy = (2, 4) if fine else (1, 1)
        self.W, self.H = w * self.sx, h * self.sy
        self.chars = np.full((self.H, self.W), " ", dtype="<U1")
        self.fg = np.zeros((self.H, self.W, 3), np.uint8)
        self.bg = np.zeros((h, w, 3), np.uint8)
        self.dots = np.zeros((self.H, self.W), bool) if fine else None
        # silhouette layer (sub-cell): 0 none, 1 body, 2 hole, 3 detail
        self.sil = np.zeros((self.H, self.W), np.uint8)
        self._overlay = []      # (x, y, char, fg): cell glyphs drawn over silhouettes
        self.silcol = np.zeros((self.H, self.W, 3), np.uint8)
        self.detcol = np.zeros((self.H, self.W, 3), np.uint8)
        self.wincol = np.zeros((self.H, self.W, 3), np.uint8)
        self.texcol = np.zeros((self.H, self.W, 3), np.uint8)

    # --- whole-frame -------------------------------------------------------
    def clear(self, bg, fg=None):
        self.chars[:] = " "
        self.bg[:] = bg
        self.fg[:] = bg if fg is None else fg
        if self.fine:
            self.dots[:] = False
        self.sil[:] = 0
        self._overlay = []

    def overlay(self, x, y, c, fg):
        """A glyph that stays in front of silhouettes (e.g. grass at the feet)."""
        if 0 <= x < self.w and 0 <= y < self.h:
            self._overlay.append((x, y, c, fg))

    def silhouette(self, px, py, code, col, dcol, wincol=None, texcol=None):
        """Blit a silhouette bitmap (0/1/2/3, see sprites.py) at sub-cell
        position (px, py). Body becomes a solid block of `col`; holes show
        the background; details are drawn in `dcol`."""
        H, W = code.shape
        x0, y0 = max(0, px), max(0, py)
        x1, y1 = min(self.W, px + W), min(self.H, py + H)
        if x0 >= x1 or y0 >= y1:
            return
        c = code[y0 - py:y1 - py, x0 - px:x1 - px]
        m = c > 0
        reg = self.sil[y0:y1, x0:x1]
        reg[m] = c[m]
        self.silcol[y0:y1, x0:x1][m] = col
        self.detcol[y0:y1, x0:x1][m] = dcol
        if wincol is not None:
            self.wincol[y0:y1, x0:x1][m] = wincol
            self.texcol[y0:y1, x0:x1][m] = texcol

    def _cell(self, x, y):
        """Slices covering cell (x, y) in the hi-res arrays."""
        return slice(y * self.sy, (y + 1) * self.sy), slice(x * self.sx, (x + 1) * self.sx)

    def _set_cell(self, x, y, c, fg, bg=None, wipe=True):
        ys, xs = self._cell(x, y)
        if wipe:
            self.chars[ys, xs] = " "
            if self.fine:
                self.dots[ys, xs] = False
        self.chars[y * self.sy, x * self.sx] = c
        self.fg[ys, xs] = fg
        if bg is not None:
            self.bg[y, x] = bg

    # --- cell-level drawing (x, y in cells) --------------------------------
    def put(self, x, y, c, fg, bg=None):
        if 0 <= x < self.w and 0 <= y < self.h:
            self._set_cell(x, y, c, fg, bg, wipe=False)

    def put_many(self, xs, ys, chars, fg):
        """Vectorised put for particles. chars: str or array of str."""
        xs = np.asarray(xs, int)
        ys = np.asarray(ys, int)
        ok = (xs >= 0) & (xs < self.w) & (ys >= 0) & (ys < self.h)
        if not ok.any():
            return
        xs, ys = xs[ok] * self.sx, ys[ok] * self.sy
        if isinstance(chars, str):
            self.chars[ys, xs] = chars
        else:
            self.chars[ys, xs] = np.asarray(chars)[ok]
        self.fg[ys, xs] = fg

    def text(self, x, y, s, fg, bg=None):
        """Draw a string; handles double-width (CJK) characters."""
        if y < 0 or y >= self.h:
            return x
        for c in s:
            if c == "\n":
                break
            cw = 2 if is_wide(c) else 1
            if x + cw > self.w:
                break
            if x >= 0:
                self._set_cell(x, y, c, fg, bg)
                if cw == 2:
                    self._set_cell(x + 1, y, WIDE_PAD, fg, bg)
            x += cw
        return x

    def sprite(self, x, y, lines, fg, bg=None, clear=False):
        """Multi-line text sprite; spaces are transparent unless clear=True."""
        for j, line in enumerate(lines):
            yy = y + j
            if yy < 0 or yy >= self.h:
                continue
            for i, c in enumerate(line):
                xx = x + i
                if xx < 0 or xx >= self.w:
                    continue
                if c != " ":
                    self._set_cell(xx, yy, c, fg, bg)
                elif clear:
                    self._set_cell(xx, yy, " ", fg, bg)

    def box(self, x, y, w, h, fg, bg, fill=True):
        """Bordered box; fill=False draws only the border."""
        if w < 2 or h < 2:
            return
        self.text(x, y, "┌" + "─" * (w - 2) + "┐", fg, bg)
        for j in range(1, h - 1):
            if fill:
                self.text(x, y + j, "│" + " " * (w - 2) + "│", fg, bg)
            else:
                self.text(x, y + j, "│", fg, bg)
                self.text(x + w - 1, y + j, "│", fg, bg)
        self.text(x, y + h - 1, "└" + "─" * (w - 2) + "┘", fg, bg)

    # --- fine -> cell -------------------------------------------------------
    def flatten(self):
        """Return a plain cell-resolution Frame (braille for dot areas)."""
        if not self.fine:
            return self
        h, w = self.h, self.w
        out = Frame(w, h, fine=False)
        out.bg[:] = self.bg
        blk = self.chars.reshape(h, 4, w, 2).transpose(0, 2, 1, 3).reshape(h, w, 8)
        lit = blk != " "
        litany = lit.any(axis=2)
        idx = lit.argmax(axis=2)
        litchar = np.take_along_axis(blk, idx[..., None], axis=2)[..., 0]
        fgblk = self.fg.reshape(h, 4, w, 2, 3).transpose(0, 2, 1, 3, 4).reshape(h, w, 8, 3)
        litfg = np.take_along_axis(fgblk, idx[..., None, None], axis=2)[:, :, 0, :]
        d = self.dots.reshape(h, 4, w, 2)
        code = (d * _WEIGHTS[None, :, None, :]).sum(axis=(1, 3))
        cnt = d.sum(axis=(1, 3))
        fgsum = (self.fg.reshape(h, 4, w, 2, 3) * d[..., None]).sum(axis=(1, 3))
        dfg = (fgsum / np.maximum(cnt, 1)[..., None]).astype(np.uint8)
        out.chars[:] = np.where(litany, litchar, _BRAILLE[code])
        out.fg[:] = np.where(litany[..., None], litfg, dfg)
        if self.sil.any():
            self._flatten_sil(out)
        for x, y, c, fg in self._overlay:
            out.chars[y, x] = c
            out.fg[y, x] = fg
        return out

    def _flatten_sil(self, out):
        # work only on the cell rectangle that contains silhouettes
        rows = np.flatnonzero(self.sil.any(axis=1))
        cols = np.flatnonzero(self.sil.any(axis=0))
        cy0, cy1 = rows[0] // 4, rows[-1] // 4 + 1
        cx0, cx1 = cols[0] // 2, cols[-1] // 2 + 1
        sub = Frame.__new__(Frame)
        sub.sil = self.sil[cy0 * 4:cy1 * 4, cx0 * 2:cx1 * 2]
        for name in ("silcol", "detcol", "wincol", "texcol"):
            setattr(sub, name, getattr(self, name)[cy0 * 4:cy1 * 4, cx0 * 2:cx1 * 2])
        sub.h, sub.w = cy1 - cy0, cx1 - cx0
        view = Frame.__new__(Frame)
        view.chars = out.chars[cy0:cy1, cx0:cx1]
        view.fg = out.fg[cy0:cy1, cx0:cx1]
        view.bg = out.bg[cy0:cy1, cx0:cx1]
        Frame._flatten_sil_region(sub, view)

    def _flatten_sil_region(self, out):
        h, w = self.h, self.w
        S = self.sil.reshape(h, 4, w, 2).transpose(0, 2, 1, 3)          # h,w,4,2
        cov = S > 0
        covc = cov.sum(axis=(2, 3))
        anyc = covc > 0
        if not anyc.any():
            return
        full = covc == 8
        part = anyc & ~full
        tl = cov[:, :, 0:2, 0].any(-1)
        tr = cov[:, :, 0:2, 1].any(-1)
        bl = cov[:, :, 2:4, 0].any(-1)
        br = cov[:, :, 2:4, 1].any(-1)
        q = tl * 1 + tr * 2 + bl * 4 + br * 8
        inner = (S == SIL_HOLE) | (S == SIL_DETAIL)
        icode = (inner * _WEIGHTS[None, None]).sum(axis=(2, 3))
        det = S == SIL_DETAIL
        hasdet = det.any(axis=(2, 3))
        sc = self.silcol.reshape(h, 4, w, 2, 3).transpose(0, 2, 1, 3, 4)
        bcol = ((sc * cov[..., None]).sum(axis=(2, 3)) / np.maximum(covc, 1)[..., None]).astype(np.uint8)
        dc = self.detcol.reshape(h, 4, w, 2, 3).transpose(0, 2, 1, 3, 4)
        dcnt = det.sum(axis=(2, 3))
        dcol = ((dc * det[..., None]).sum(axis=(2, 3)) / np.maximum(dcnt, 1)[..., None]).astype(np.uint8)
        # see-through window cells (mostly window pixels): other scenery
        win = S >= SIL_WIN
        wcnt = win.sum(axis=(2, 3))
        wfull = full & (wcnt >= 4)
        if wfull.any():
            wc = self.wincol.reshape(h, 4, w, 2, 3).transpose(0, 2, 1, 3, 4)
            wmean = ((wc * win[..., None]).sum(axis=(2, 3)) / np.maximum(wcnt, 1)[..., None]).astype(np.uint8)
            tc = self.texcol.reshape(h, 4, w, 2, 3).transpose(0, 2, 1, 3, 4)
            tdot = (S == SIL_WINDOT) | (S == SIL_HOLE) | (S == SIL_DETAIL)
            tcode = (tdot * _WEIGHTS[None, None]).sum(axis=(2, 3))
            tcnt = tdot.sum(axis=(2, 3))
            tmean = ((tc * tdot[..., None]).sum(axis=(2, 3)) / np.maximum(tcnt, 1)[..., None]).astype(np.uint8)
            full = full & ~wfull
        under = out.bg.copy()
        # edge cells: quarter blocks in the body colour over the scene background
        out.chars[part] = _QUAD[q[part]]
        out.fg[part] = bcol[part]
        # full cells: solid body colour; holes/details as braille dots
        out.bg[full] = bcol[full]
        out.chars[full] = _BRAILLE[icode[full]]
        out.fg[full] = np.where(hasdet[full][:, None], dcol[full], under[full])
        if wfull.any():
            out.bg[wfull] = wmean[wfull]
            out.chars[wfull] = _BRAILLE[tcode[wfull]]
            out.fg[wfull] = tmean[wfull]


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _FONTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("nFont", ctypes.c_ulong), ("dwFontSize", _COORD),
                ("FontFamily", ctypes.c_uint), ("FontWeight", ctypes.c_uint), ("FaceName", ctypes.c_wchar * 32)]


class ConsoleFont:
    """Font size of the classic Windows console (conhost). Windows Terminal
    does not let programs change its font - there Ctrl + / Ctrl - zoom."""

    def __init__(self):
        self.supported = os.name == "nt" and not os.environ.get("WT_SESSION")
        self.original = None
        if self.supported:
            try:
                k = ctypes.windll.kernel32
                k.GetStdHandle.restype = ctypes.c_void_p
                self._h = ctypes.c_void_p(k.GetStdHandle(-11))
                info = self._get()
                self.supported = info is not None and info.dwFontSize.Y > 0
                self.original = info
            except Exception:
                self.supported = False

    def _get(self):
        info = _FONTINFO()
        info.cbSize = ctypes.sizeof(_FONTINFO)
        if not ctypes.windll.kernel32.GetCurrentConsoleFontEx(self._h, False, ctypes.byref(info)):
            return None
        return info

    @property
    def height(self):
        info = self._get() if self.supported else None
        return info.dwFontSize.Y if info else 0

    def set_height(self, px):
        if not self.supported:
            return False
        info = self._get()
        if info is None:
            return False
        px = max(4, min(48, int(px)))
        info.dwFontSize.X = 0
        info.dwFontSize.Y = px
        return bool(ctypes.windll.kernel32.SetCurrentConsoleFontEx(self._h, False, ctypes.byref(info)))

    def restore(self):
        if self.supported and self.original is not None:
            try:
                ctypes.windll.kernel32.SetCurrentConsoleFontEx(self._h, False, ctypes.byref(self.original))
            except Exception:
                pass


class Terminal:
    def __init__(self):
        enable_vt()
        self.out = sys.stdout.buffer
        # Write straight to the raw console in small pieces that never split a
        # UTF-8 character. Python 3.9's Windows console writer halves large
        # writes at arbitrary byte offsets, which turns multi-byte characters
        # (braille, blocks) cut in half into "?" marks.
        self.raw = getattr(self.out, "raw", None) or self.out
        self._prev = None      # (keys, chars) of the last frame sent, for row diffing
        self.last_size = (0, 0)

    def size(self):
        s = shutil.get_terminal_size((100, 30))
        return max(20, s.columns), max(8, s.lines)

    def minimized(self):
        """True while the terminal window is minimised (Windows Terminal or
        the classic console). Drawing is skipped then to save CPU."""
        if os.name != "nt":
            return False
        try:
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            k.GetConsoleWindow.restype = ctypes.c_void_p
            u.GetWindow.restype = ctypes.c_void_p
            h = k.GetConsoleWindow()
            if not h:
                return False
            owner = u.GetWindow(ctypes.c_void_p(h), 4)      # GW_OWNER: Windows Terminal's window
            return bool(u.IsIconic(ctypes.c_void_p(h))) or bool(owner and u.IsIconic(ctypes.c_void_p(owner)))
        except Exception:
            return False

    def enter(self):
        self.out.write(b"\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
        self.out.flush()

    def exit(self):
        self.out.write(b"\x1b[0m\x1b[?25h\x1b[2J\x1b[H\x1b[?1049l")
        self.out.flush()

    def draw(self, fr):
        fr = fr.flatten()
        h, w = fr.h, fr.w
        if (w, h) != self.last_size:
            self.out.write(b"\x1b[2J")
            self.last_size = (w, h)
            self._prev = None
        fq = (fr.fg >> 3).astype(np.int64)
        bq = (fr.bg >> 3).astype(np.int64)
        key = ((fq[..., 0] << 25) | (fq[..., 1] << 20) | (fq[..., 2] << 15)
               | (bq[..., 0] << 10) | (bq[..., 1] << 5) | bq[..., 2])
        # cells that are blank only depend on their background colour
        blank = fr.chars == " "
        key = np.where(blank, key & 0x7FFF, key)
        # only rows that differ from what is already on screen are sent
        if self._prev is not None:
            pk, pc = self._prev
            changed = (key != pk).any(axis=1) | (fr.chars != pc).any(axis=1)
        else:
            changed = np.ones(h, bool)
        self._prev = (key, fr.chars.copy())
        if not changed.any():
            return
        parts = []
        chars = fr.chars
        fg, bg = fr.fg, fr.bg
        for y in np.flatnonzero(changed).tolist():
            k = key[y]
            breaks = np.flatnonzero(k[1:] != k[:-1]) + 1
            starts = np.concatenate(([0], breaks))
            ends = np.concatenate((breaks, [w]))
            row = chars[y]
            parts.append("\x1b[%d;1H" % (y + 1))
            for a, b in zip(starts.tolist(), ends.tolist()):
                f = fg[y, a]
                g = bg[y, a]
                seg = row[a:b].view("<U%d" % (b - a))[0]
                if "\x01" in seg:
                    seg = seg.replace("\x01", "")
                parts.append("\x1b[38;2;%d;%d;%d;48;2;%d;%d;%dm%s" % (f[0], f[1], f[2], g[0], g[1], g[2], seg))
        parts.append("\x1b[0m")
        self._write_parts(parts)

    CHUNK = 6000  # bytes; well under the console writer's split threshold

    def _write_parts(self, parts):
        self.out.flush()
        buf = []
        size = 0
        for p in parts:
            b = p.encode("utf-8", "replace")
            if size + len(b) > self.CHUNK and buf:
                self._write_all(b"".join(buf))
                buf, size = [], 0
            buf.append(b)
            size += len(b)
        if buf:
            self._write_all(b"".join(buf))

    def _write_all(self, data):
        view = memoryview(data)
        while len(view):
            n = self.raw.write(view)
            if n is None:  # non-blocking raw stream not ready; retry
                continue
            view = view[n:]
        if self.raw is not self.out:
            return
        self.out.flush()
