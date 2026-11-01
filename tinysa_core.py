# -*- coding: utf-8 -*-
"""
tinysa_core.py — работа с TinySA / TinySA Ultra по USB-CDC (без зависимостей от Qt).

* TinySADevice — реальный прибор (pyserial, команда scanraw)
* DemoDevice   — имитация прибора для проверки интерфейса без железа
* plan_segments — разбиение широкого скана на куски по ≤450 точек
* parse_freq / fmt_freq — разбор и форматирование частот ("2.4G", "433.92 MHz")
"""
from __future__ import annotations

import math
import re
import threading
import time

import numpy as np

PROMPT = b"ch> "
SEG_MAX = 450                   # максимум точек за один scanraw
USB_VID, USB_PID = 0x0483, 0x5740
F_MIN, F_MAX = 100e3, 6e9       # диапазон TinySA Ultra

_RAW_DT = np.dtype([("m", "u1"), ("v", "<u2")])   # 'x' + uint16 LE


class TinySAError(Exception):
    pass


# ----------------------------------------------------------------- утилиты ---
def parse_freq(text: str) -> float:
    """'100', '100M', '2.4 GHz', '433,92 mhz', '500k', '1e6' -> Гц.
    Число без единицы: < 10000 считается МГц, иначе Гц."""
    s = text.strip().lower().replace(",", ".").replace(" ", "")
    m = re.fullmatch(r"([0-9]*\.?[0-9]+(?:e[+-]?\d+)?)(g|m|k|)(?:hz)?", s)
    if not m:
        raise ValueError(text)
    v = float(m.group(1))
    unit = m.group(2)
    if unit:
        return v * {"g": 1e9, "m": 1e6, "k": 1e3}[unit]
    return v * 1e6 if v < 1e4 else v


def fmt_freq(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        x, u = v / 1e9, "GHz"
    elif a >= 1e6:
        x, u = v / 1e6, "MHz"
    elif a >= 1e3:
        x, u = v / 1e3, "kHz"
    else:
        x, u = v, "Hz"
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return f"{s} {u}"


def plan_segments(start: float, stop: float, points: int, seg_max: int = SEG_MAX):
    """Возвращает (freqs, [(i0, i1), ...]) — индексы включительно."""
    points = max(2, int(points))
    freqs = np.linspace(start, stop, points)
    nseg = max(1, math.ceil(points / seg_max))
    size = math.ceil(points / nseg)
    segs, i = [], 0
    while i < points:
        j = min(points, i + size)
        if j - i < 2 and segs:           # слишком короткий хвост — присоединяем
            segs[-1] = (segs[-1][0], j - 1)
        else:
            segs.append((i, j - 1))
        i = j
    return freqs, segs


# ------------------------------------------------------------ реальный прибор ---
class TinySADevice:
    def __init__(self, port: str, scan_timeout: float = 90.0):
        self.port = port
        self.scan_timeout = scan_timeout
        self.ser = None
        self.version = ""
        self.is_ultra = False
        self._abort = threading.Event()

    # -- низкий уровень
    def abort(self):
        self._abort.set()

    def open(self):
        import serial  # ленивый импорт
        try:
            self.ser = serial.Serial(self.port, 115200, timeout=0.05, write_timeout=2)
        except Exception as e:
            raise TinySAError(f"не удалось открыть {self.port}: {e}")
        self._abort.clear()
        self.ser.reset_input_buffer()
        self.ser.write(b"\r")
        try:
            self._read_until(PROMPT, 2.0)
        except TinySAError:
            raise TinySAError("прибор не отвечает (нет приглашения 'ch>')")
        ver = self.command("version")
        self.version = ver.splitlines()[0].strip() if ver else "tinySA"
        low = ver.lower()
        self.is_ultra = "tinysa4" in low or "ultra" in low
        try:
            self.command("pause")        # собственная развёртка прибора не мешает scanraw
        except TinySAError:
            pass

    def close(self):
        if self.ser:
            try:
                self._abort.clear()
                self.command("resume", timeout=1.0)
            except Exception:
                pass
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _read_until(self, token: bytes, timeout: float) -> bytes:
        buf = bytearray()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._abort.is_set():
                raise TinySAError("прервано")
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if buf.endswith(token):
                    return bytes(buf)
        raise TinySAError("таймаут ответа прибора")

    # -- команды
    def command(self, cmd: str, timeout: float = 4.0) -> str:
        self.ser.reset_input_buffer()
        self.ser.write(cmd.encode() + b"\r")
        raw = self._read_until(PROMPT, timeout)
        text = raw.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")[1:]                      # первая строка — эхо команды
        out = "\n".join(lines)
        if out.endswith("ch> "):
            out = out[:-4]
        return out.strip()

    def scan(self, start: float, stop: float, points: int, scale: float) -> np.ndarray:
        """Один scanraw -> уровни в дБм (np.float64, длина == points)."""
        cmd = f"scanraw {int(round(start))} {int(round(stop))} {int(points)}"
        self.ser.reset_input_buffer()
        self.ser.write(cmd.encode() + b"\r")
        raw = self._read_until(b"}" + PROMPT, self.scan_timeout)
        i = raw.find(b"{")
        if i < 0:
            raise TinySAError("scanraw: нет данных: " + repr(raw[:60]))
        payload = raw[i + 1:-5]
        n = len(payload) // 3
        if n < 2:
            raise TinySAError("scanraw: пустой ответ")
        vals = np.frombuffer(payload[:n * 3], dtype=_RAW_DT)["v"].astype(np.float64)
        if n != points:                                   # на всякий случай
            vals = np.interp(np.linspace(0, n - 1, points), np.arange(n), vals)
        return vals / 32.0 - scale

    def capture(self) -> bytes:
        """Скриншот экрана: RGB565 (480x320 у Ultra, 320x240 у базового)."""
        self.ser.reset_input_buffer()
        self.ser.write(b"capture\r")
        raw = self._read_until(PROMPT, 8.0)
        i = raw.find(b"\n")
        data = raw[i + 1:-4] if i >= 0 else raw[:-4]
        if len(data) not in (480 * 320 * 2, 320 * 240 * 2):
            raise TinySAError(f"capture: неожиданный размер {len(data)}")
        return data


# ----------------------------------------------------------------- демо ---
class DemoDevice:
    """Имитация прибора: FM-станции, ISM, GSM, ADS-B, Wi-Fi..."""
    version = "tinySA4_v1.4-DEMO"
    is_ultra = True

    def __init__(self):
        rng = np.random.default_rng(7)
        self._abort = threading.Event()
        self.sigs = []        # (f_center, width, dBm, kind)
        for k in range(16):
            self.sigs.append((87.7e6 + k * 1.3e6 + rng.uniform(-.2e6, .2e6), 160e3,
                              float(rng.uniform(-88, -38)), "fm"))
        self.sigs += [(433.92e6, 30e3, -62, "burst"), (868.3e6, 60e3, -70, "burst"),
                      (1090e6, 1e6, -75, "pulse"), (145.5e6, 12e3, -55, "carrier"),
                      (446.1e6, 12e3, -66, "burst")]
        for ch in range(0, 30):
            self.sigs.append((935.2e6 + ch * 0.8e6, 200e3, float(rng.uniform(-95, -60)), "gsm"))
        for fc, a in ((2412e6, -58), (2437e6, -66), (2462e6, -74)):
            self.sigs.append((fc, 18e6, a, "wifi"))
        self.sigs.append((5500e6, 80e6, -80, "wifi"))

    def abort(self):
        self._abort.set()

    def open(self):
        pass

    def close(self):
        pass

    def command(self, cmd: str, timeout: float = 4.0) -> str:
        c = cmd.strip().lower()
        if c == "version":
            return self.version
        if c == "vbat":
            return "4012 mV"
        if c == "info":
            return "tinySA ULTRA (DEMO)\n2019-2024 Copyright @Erik Kaashoek\nHW Version:V0.4.5.1"
        return f"(демо) {cmd}"

    def scan(self, start, stop, points, scale):
        time.sleep(0.02 + 0.0003 * points)
        f = np.linspace(start, stop, points)
        t = time.time()
        p = 10 ** (-104 / 10) * np.random.chisquare(8, points) / 8
        for fc, bw, dbm, kind in self.sigs:
            if fc < start - bw * 4 or fc > stop + bw * 4:
                continue
            a = dbm
            if kind == "fm":
                a += 4 * math.sin(t * 2.3 + fc)
                shape = np.exp(-0.5 * ((f - fc) / (bw / 2.355)) ** 2)
            elif kind == "carrier":
                shape = np.exp(-0.5 * ((f - fc) / (bw / 2.355)) ** 2)
            elif kind == "burst":
                a -= 0 if np.random.rand() < 0.35 else 60
                shape = np.exp(-0.5 * ((f - fc) / (bw / 2.355)) ** 2)
            elif kind == "pulse":
                a -= 0 if np.random.rand() < 0.15 else 40
                shape = np.exp(-0.5 * ((f - fc) / (bw / 2.355)) ** 2)
            elif kind == "gsm":
                a -= 0 if np.random.rand() < 0.8 else 25
                shape = np.exp(-0.5 * ((f - fc) / (bw / 2.355)) ** 2)
            else:  # wifi — плоский блок
                a += 5 * np.random.randn() * 0.4
                shape = 1 / (1 + ((f - fc) / (bw / 2)) ** 10)
            p += 10 ** (a / 10) * shape
        return 10 * np.log10(p)

    def capture(self) -> bytes:
        w, h = 480, 320
        x = np.linspace(0, 31, w).astype(np.uint16)[None, :].repeat(h, 0)
        y = np.linspace(0, 63, h).astype(np.uint16)[:, None].repeat(w, 1)
        v = (x << 11) | (y << 5) | (31 - x)
        return v.astype(">u2").tobytes()
