"""System audio analysis via WASAPI loopback (what Spotify is outputting).

Besides levels / spectrum it keeps a *beat grid*: tempo (BPM) from the
autocorrelation of the onset envelope, and the beat phase from where the
onsets cluster on that tempo. Visual "hits" (pulse(), beat_count) come from
the grid instead of raw detections, so they stay regular and on the beat even
when individual drum hits are missed. `latency` shifts the grid to match what
you actually hear (e.g. Bluetooth headphones play sound later).
"""
import math
import time
import threading
from collections import deque
import numpy as np

try:
    import pyaudiowpatch as pa
except Exception:
    pa = None


def _wrap(x):
    """Wrap to [-0.5, 0.5)."""
    return (x + 0.5) % 1.0 - 0.5


class Audio:
    def __init__(self, enabled=True):
        self.enabled = enabled and pa is not None
        self.ok = False
        self.error = ""
        self.level = 0.0
        self.bass = 0.0
        self.mid = 0.0
        self.high = 0.0
        self.energy = 0.0
        self.spectrum = np.zeros(32)
        # visual beat events (driven by the grid when it is locked)
        self.beat_time = -10.0
        self.beat_count = 0
        # tempo / grid
        self.bpm = 100.0
        self.bpm_conf = 0.0
        self.grid_ok = False
        self.grid_anchor = 0.0      # wall-clock time of a beat (as captured)
        self.latency = 0.0          # seconds the audible sound lags the capture
        self.beat_pos = 0.0         # continuous beat counter for animation
        # raw detections (fallback when no grid)
        self._det_time = -10.0
        self._det_count = 0
        self._seen_det = 0
        self._last_ph = 0.0
        self._env = deque(maxlen=400)   # (time, onset strength)
        self._last_grid = 0.0
        self._cand = 0.0
        self._cand_n = 0
        self._fresh = True
        self._run = False
        self._thread = None
        self._hist = deque(maxlen=86)
        self._max = np.full(4, 1e-4)
        self._spec_max = np.full(32, 1e-4)

    # --- public -----------------------------------------------------------
    def start(self):
        if not self.enabled:
            return
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._run = False

    def pulse(self, decay=7.0):
        """1.0 right on a beat, decaying to 0."""
        return math.exp(-max(0.0, time.time() - self.beat_time) * decay)

    def advance(self, dt, now=None):
        """Main thread, every frame: move the beat counter and emit beats.
        dt may be scaled down while paused."""
        now = time.time() if now is None else now
        self.beat_pos += dt * self.bpm / 60.0
        if not self.ok:
            return
        if self.grid_ok and time.time() - self._last_grid < 5.0 and self.level > 0.04:
            P = 60.0 / self.bpm
            ph = ((now - self.latency - self.grid_anchor) / P) % 1.0
            if ph < self._last_ph - 0.5:                     # crossed a beat
                self.beat_count += 1
                self.beat_time = now - ph * P
            self._last_ph = ph
            # keep the animation counter's fraction locked to the grid phase
            self.beat_pos += _wrap(ph - (self.beat_pos % 1.0)) * 0.15
        elif self._det_count != self._seen_det:
            self._seen_det = self._det_count
            self.beat_count += 1
            self.beat_time = self._det_time + self.latency
            self.beat_pos -= _wrap(self.beat_pos % 1.0) * 0.35

    def reset_tempo(self):
        """New track: forget the old tempo and phase."""
        self._env.clear()
        self.grid_ok = False
        self.bpm_conf = 0.0
        self._fresh = True
        self._cand_n = 0

    def tick(self, t):
        """Synthetic gentle motion when no audio capture is available."""
        if self.ok:
            return
        self.bpm = 100.0
        self.level = 0.35 + 0.15 * math.sin(t * 0.9)
        self.bass = 0.3 + 0.2 * math.sin(t * 0.5)
        self.mid = 0.3 + 0.15 * math.sin(t * 1.3 + 1)
        self.high = 0.2 + 0.15 * math.sin(t * 1.7 + 2)
        self.energy = 0.35
        sp = 0.3 + 0.25 * np.sin(np.linspace(0, 6, 32) + t * 1.5) * np.sin(t * 0.4 + np.arange(32) * 0.3)
        self.spectrum = np.clip(sp, 0, 1)

    # --- analysis ---------------------------------------------------------
    def _update_grid(self, fr):
        if len(self._env) < fr * 4:
            return
        ts = np.array([e[0] for e in self._env])
        x = np.array([e[1] for e in self._env])
        if x.std() < 1e-6:
            self.grid_ok = False
            return
        # tempo: autocorrelation of the normalised onset envelope
        z = (x - x.mean()) / x.std()
        n = len(z)
        f = np.fft.rfft(z, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        ac /= ac[0]
        bpms = np.arange(70.0, 181.0, 0.25)
        lags = 60.0 * fr / bpms
        vals = np.interp(lags, np.arange(n), ac)
        vals2 = np.interp(np.minimum(lags * 2, n - 1), np.arange(n), ac)
        score = (vals + 0.5 * vals2) * np.exp(-0.5 * (np.log2(bpms / 115.0) / 0.5) ** 2)
        if not self._fresh:
            # continuity: prefer tempi near (or an octave from) the current one
            r = np.log2(bpms / self.bpm)
            near = np.exp(-0.5 * (np.minimum(abs(r), np.minimum(abs(r - 1), abs(r + 1))) / 0.06) ** 2)
            score = score * (0.7 + 0.3 * near)
        i = int(np.argmax(score))
        best, conf = float(bpms[i]), float(vals[i])
        if conf < 0.08:
            self.grid_ok = False
            return

        def same(a, b):
            for k in (1.0, 2.0, 0.5):
                if abs(a / (b * k) - 1) < 0.04:
                    return k
            return 0

        if self._fresh:
            self.bpm, self._fresh = best, False
        else:
            k = same(best, self.bpm)
            if k == 1.0:
                self.bpm = self.bpm * 0.7 + best * 0.3
                self._cand_n = 0
            elif k:                       # octave of the current tempo: keep it
                self._cand_n = 0
            elif same(best, self._cand) == 1.0:
                self._cand_n += 1
                if self._cand_n >= 2:     # consistently different: switch
                    self.bpm, self._cand_n = best, 0
            else:
                self._cand, self._cand_n = best, 1
        self.bpm_conf = self.bpm_conf * 0.5 + conf * 0.5
        # phase: where do onsets cluster on this tempo? (circular mean)
        P = 60.0 / self.bpm
        recent = ts > ts[-1] - 6.0
        ts, x = ts[recent], x[recent]
        loc = np.convolve(x, np.ones(9) / 9.0, mode="same")
        w = np.maximum(x - loc, 0)
        if w.sum() < 1e-9:
            return
        t_ref = ts[-1]
        ang = 2 * np.pi * ((ts - t_ref) / P)
        zc = (w * np.exp(1j * ang)).sum()
        strength = abs(zc) / w.sum()
        phi = (np.angle(zc) / (2 * np.pi)) % 1.0            # beats at t_ref + (k + phi) * P
        new_anchor = t_ref + phi * P
        if self.grid_ok:
            # blend with the previous grid instead of jumping; trust strong phases more
            d = _wrap((new_anchor - self.grid_anchor) / P)
            self.grid_anchor = self.grid_anchor + d * P * min(0.6, 0.15 + strength)
        else:
            self.grid_anchor = new_anchor
        self.grid_ok = True
        self._last_grid = time.time()

    # --- capture thread ---------------------------------------------------
    def _loop(self):
        while self._run:
            try:
                self._capture()
            except Exception as e:  # device changed, etc. -> retry
                self.ok = False
                self.error = str(e)
                time.sleep(2.0)

    def _capture(self):
        p = pa.PyAudio()
        try:
            dev = p.get_default_wasapi_loopback()
            rate = int(dev["defaultSampleRate"])
            ch = max(1, int(dev["maxInputChannels"]))
            N, HOP = 2048, 1024
            stream = p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=HOP)
            self.ok = True
            win = np.hanning(N)
            freqs = np.fft.rfftfreq(N, 1.0 / rate)
            edges = np.geomspace(40, 12000, 33)
            idx = np.searchsorted(freqs, edges)
            bands = [(int(a), int(max(a + 1, b))) for a, b in zip(idx[:-1], idx[1:])]
            b_lo = (freqs >= 30) & (freqs < 150)
            b_mid = (freqs >= 150) & (freqs < 2000)
            b_hi = (freqs >= 2000) & (freqs < 10000)
            b_on = (freqs >= 30) & (freqs < 4000)
            fr = rate / float(HOP)                      # envelope frame rate (~47 Hz)
            self._env = deque(maxlen=int(fr * 12))
            buf = np.zeros(N, np.float32)
            prev_log = None
            last_grid = 0.0
            last_det = 0.0
            while self._run:
                data = stream.read(HOP, exception_on_overflow=False)
                now = time.time()
                try:
                    backlog = stream.get_read_available()
                except Exception:
                    backlog = 0
                # time at the centre of the analysis window (as captured)
                t_mid = now - (backlog + N / 2.0) / rate
                x = np.frombuffer(data, np.float32).reshape(-1, ch).mean(axis=1)
                buf = np.concatenate([buf[HOP:], x])
                rms = float(np.sqrt(np.mean(buf * buf)) + 1e-9)
                mag = np.abs(np.fft.rfft(buf * win))
                raw = np.array([rms, mag[b_lo].mean(), mag[b_mid].mean(), mag[b_hi].mean()])
                self._max = np.maximum(self._max * 0.998, raw)
                self._max = np.maximum(self._max, [0.01, 0.5, 0.2, 0.05])
                v = np.clip(raw / self._max, 0, 1)
                cur = np.array([self.level, self.bass, self.mid, self.high])
                cur = np.where(v > cur, cur + (v - cur) * 0.5, cur * 0.9 + v * 0.1)
                self.level, self.bass, self.mid, self.high = [float(c) for c in cur]
                self.energy = self.energy * 0.985 + (0.5 * self.level + 0.3 * self.bass + 0.2 * self.mid) * 0.015
                sp = np.array([mag[a:b].mean() for a, b in bands])
                self._spec_max = np.maximum(self._spec_max * 0.9975, sp)
                self._spec_max = np.maximum(self._spec_max, 0.05)
                sp = np.clip(sp / self._spec_max, 0, 1)
                self.spectrum = np.maximum(sp, self.spectrum * 0.88)
                # onset envelope (spectral flux)
                lg = np.log1p(mag[b_on] * 10.0)
                if prev_log is not None:
                    self._env.append((t_mid, float(np.maximum(lg - prev_log, 0).sum())))
                prev_log = lg
                if now - last_grid > 1.0:
                    last_grid = now
                    self._update_grid(fr)
                # raw beat detection (fallback): bass jumps above its recent average
                e = float(raw[1])
                self._hist.append(e)
                if len(self._hist) > 20:
                    avg = sum(self._hist) / len(self._hist)
                    if e > avg * 1.45 and e > 0.4 and t_mid - last_det > 0.22:
                        last_det = t_mid
                        self._det_time = t_mid
                        self._det_count += 1
            stream.stop_stream()
            stream.close()
        finally:
            self.ok = False
            p.terminate()
