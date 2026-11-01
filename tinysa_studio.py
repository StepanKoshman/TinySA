#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TinySA Studio — ПК-программа для анализатора спектра TinySA Ultra
(работает и с базовым TinySA).

Возможности: спектр + водопад, маркеры (пик-трекинг, Δ), таблица пиков,
Max/Min hold, усреднение, измерение мощности в полосе и OBW, порог-тревога,
сегментированный скан до 5000 точек (весь диапазон 100 кГц–6 ГГц),
RBW / аттенюатор / LNA / spur, генератор, запись в CSV, экспорт,
скриншот экрана прибора, консоль команд и демо-режим без прибора.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime
import json
import queue
import re
import sys
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal, Slot

from tinysa_core import (F_MAX, F_MIN, USB_PID, USB_VID, DemoDevice,
                         TinySADevice, TinySAError, fmt_freq, parse_freq,
                         plan_segments)

APP = "TinySA Studio"
pg.setConfigOptions(imageAxisOrder="row-major", antialias=True,
                    background="#0b0f14", foreground="#aab4c0")

# --------------------------------------------------------------- пресеты ---
BANDS = {
    "— диапазон —": None,
    "Весь диапазон 0.1 МГц – 6 ГГц": (100e3, 6e9),
    "Low input 0.1 – 800 МГц": (100e3, 800e6),
    "FM-радио 87.5 – 108 МГц": (87.5e6, 108e6),
    "Авиация 118 – 137 МГц": (118e6, 137e6),
    "2 м любительский 144 – 146 МГц": (144e6, 146e6),
    "70 см любительский 430 – 440 МГц": (430e6, 440e6),
    "ISM 433 МГц": (433.05e6, 434.79e6),
    "PMR446": (446e6, 446.2e6),
    "ISM 868 МГц": (863e6, 870e6),
    "GSM-900 (DL)": (925e6, 960e6),
    "ADS-B 1090 МГц": (1080e6, 1100e6),
    "GPS L1": (1565e6, 1586e6),
    "GSM-1800 (DL)": (1805e6, 1880e6),
    "Wi-Fi 2.4 ГГц": (2400e6, 2500e6),
    "Wi-Fi 5 ГГц": (5150e6, 5850e6),
}

COLORMAPS = {
    "Viridis": [(0, (68, 1, 84)), (.25, (59, 82, 139)), (.5, (33, 145, 140)),
                (.75, (94, 201, 98)), (1, (253, 231, 37))],
    "Inferno": [(0, (0, 0, 4)), (.25, (87, 16, 110)), (.5, (188, 55, 84)),
                (.75, (249, 142, 9)), (1, (252, 255, 164))],
    "Спектр": [(0, (48, 18, 59)), (.15, (70, 107, 227)), (.3, (40, 188, 235)),
               (.45, (49, 242, 150)), (.6, (164, 252, 60)), (.75, (251, 185, 56)),
               (.9, (220, 60, 15)), (1, (122, 4, 3))],
    "SDR-классика": [(0, (0, 0, 40)), (.3, (0, 0, 255)), (.55, (0, 255, 255)),
                     (.75, (255, 255, 0)), (1, (255, 0, 0))],
    "Серый": [(0, (0, 0, 0)), (1, (255, 255, 255))],
}


def make_lut(stops):
    pos = [p for p, _ in stops]
    cols = np.array([c for _, c in stops], float)
    x = np.linspace(0, 1, 256)
    return np.stack([np.interp(x, pos, cols[:, k]) for k in range(3)], 1).astype(np.uint8)


STYLE = """
* { font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 12px; color: #d5dde6; }
QMainWindow, QDialog, QWidget#panel { background: #11161d; }
QToolBar { background: #151b23; border: 0; border-bottom: 1px solid #222b36; spacing: 6px; padding: 6px; }
QStatusBar { background: #151b23; border-top: 1px solid #222b36; }
QStatusBar QLabel { padding: 0 10px; color: #8fa1b3; }
QTabWidget::pane { border: 0; background: #11161d; }
QTabBar::tab { background: transparent; padding: 8px 12px; color: #8fa1b3; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #4cc9f0; border-bottom: 2px solid #4cc9f0; }
QGroupBox { border: 1px solid #222b36; border-radius: 8px; margin-top: 14px; padding: 12px 8px 8px 8px; background: #151b23; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #4cc9f0; font-weight: 600; }
QPushButton { background: #1d2631; border: 1px solid #2b3745; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #253142; border-color: #4cc9f0; }
QPushButton:checked { background: #0f4c63; border-color: #4cc9f0; color: white; }
QPushButton:disabled { color: #566472; background: #161c24; }
QPushButton#accent { background: #4cc9f0; color: #06222c; font-weight: 600; border: 0; }
QPushButton#accent:hover { background: #7ddcf7; }
QPushButton#danger:checked { background: #7a1f2e; border-color: #ef476f; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
    background: #0d1218; border: 1px solid #2b3745; border-radius: 5px; padding: 4px 6px;
    selection-background-color: #0f4c63; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #4cc9f0; }
QComboBox QAbstractItemView { background: #0d1218; selection-background-color: #0f4c63; border: 1px solid #2b3745; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #3a4858; border-radius: 4px; background: #0d1218; }
QCheckBox::indicator:checked { background: #4cc9f0; border-color: #4cc9f0; }
QTableWidget { background: #0d1218; gridline-color: #1d2631; border: 1px solid #222b36; border-radius: 6px; }
QHeaderView::section { background: #151b23; border: 0; border-bottom: 1px solid #222b36; padding: 4px; color: #8fa1b3; }
QTableWidget::item:selected { background: #0f4c63; }
QSplitter::handle { background: #1a222c; }
QScrollBar:vertical { background: #11161d; width: 10px; }
QScrollBar::handle:vertical { background: #2b3745; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QToolTip { background: #1d2631; color: #d5dde6; border: 1px solid #4cc9f0; }
QLabel#value { color: #ffd166; font-weight: 600; }
"""


# --------------------------------------------------------------- настройки ---
@dataclass
class Cfg:
    start: float = 88e6
    stop: float = 108e6
    points: int = 450
    rbw: float = 0.0          # кГц, 0 = авто
    atten: int = -1           # -1 = авто
    lna: bool = False
    spur: bool = False
    input_mode: str = "auto"  # auto / low / high
    scale: float = 174.0      # смещение шкалы: дБм = raw/32 - scale


# ------------------------------------------------------------ рабочий поток ---
class Worker(QtCore.QThread):
    sweep = Signal(object, object, bool)       # freqs, dBm, final
    connected = Signal(bool, str, bool)        # ok, текст, is_ultra
    console = Signal(str)
    battery = Signal(int)
    screenshot = Signal(bytes)
    stopped = Signal(str)                      # скан остановлен из-за ошибки
    failed = Signal(str)                       # фатальная ошибка связи

    def __init__(self, dev, parent=None):
        super().__init__(parent)
        self.dev = dev
        self.q: "queue.Queue" = queue.Queue()
        self.cfg = Cfg()
        self.scanning = False
        self.single = False
        self.gen_on = False
        self._quit = False
        self._sent: dict = {}

    def post(self, *item):
        self.q.put(item)

    def stop(self):
        self._quit = True
        self.dev.abort()

    # ---- основной цикл
    def run(self):
        try:
            self.dev.open()
        except Exception as e:
            self.connected.emit(False, str(e), False)
            return
        self.connected.emit(True, self.dev.version, self.dev.is_ultra)
        next_bat = 0.0
        try:
            while not self._quit:
                self._drain()
                if self._quit:
                    break
                now = time.monotonic()
                if now >= next_bat:
                    next_bat = now + 60
                    self._battery()
                if (self.scanning or self.single) and not self.gen_on:
                    self.single = False
                    self._sweep_once()
                else:
                    self.msleep(40)
        except Exception as e:
            if not self._quit:
                self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            try:
                self.dev.close()
            except Exception:
                pass

    def _battery(self):
        try:
            m = re.search(r"(\d+)", self.dev.command("vbat"))
            if m:
                self.battery.emit(int(m.group(1)))
        except TinySAError:
            pass

    def _drain(self):
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            kind = item[0]
            if kind == "cfg":
                self.cfg = item[1]
            elif kind == "run":
                self.scanning = item[1]
            elif kind == "single":
                self.single = True
            elif kind == "cmd":
                try:
                    out = self.dev.command(item[1], timeout=10)
                except TinySAError as e:
                    out = f"ошибка: {e}"
                self.console.emit(f"> {item[1]}\n{out}\n")
                self._sent.clear()
            elif kind == "gen":
                for c in item[1]:
                    try:
                        out = self.dev.command(c)
                    except TinySAError as e:
                        out = f"ошибка: {e}"
                    self.console.emit(f"> {c}\n{out}\n")
                self.gen_on = item[2]
                self._sent.clear()
            elif kind == "capture":
                try:
                    self.screenshot.emit(self.dev.capture())
                except TinySAError as e:
                    self.console.emit(f"> capture\nошибка: {e}\n")

    def _apply(self, cfg: Cfg):
        mode = cfg.input_mode
        if mode == "auto":
            mode = "high" if (cfg.start + cfg.stop) / 2 > 800e6 else "low"
        want = {
            "mode": f"mode {mode} input",
            "rbw": "rbw auto" if cfg.rbw <= 0 else f"rbw {cfg.rbw:g}",
            "att": "attenuate auto" if cfg.atten < 0 else f"attenuate {cfg.atten}",
            "lna": f"lna {'on' if cfg.lna else 'off'}",
            "spur": f"spur {'on' if cfg.spur else 'off'}",
        }
        for k, c in want.items():
            if self._sent.get(k) != c:
                self.dev.command(c)
                self._sent[k] = c

    def _sweep_once(self):
        cfg = self.cfg
        try:
            self._apply(cfg)
            freqs, segs = plan_segments(cfg.start, cfg.stop, cfg.points)
            lvl = np.full(len(freqs), -150.0)
            for k, (i0, i1) in enumerate(segs):
                lvl[i0:i1 + 1] = self.dev.scan(freqs[i0], freqs[i1], i1 - i0 + 1, cfg.scale)
                final = k == len(segs) - 1
                self.sweep.emit(freqs, lvl.copy(), final)
                if not final and (not self.q.empty() or self._quit):
                    return
        except TinySAError as e:
            if not self._quit:
                self.scanning = False
                self.stopped.emit(str(e))


# ------------------------------------------------------------ вспомогательное ---
class FreqEdit(QtWidgets.QLineEdit):
    valueChanged = Signal(float)

    def __init__(self, value=1e6, parent=None):
        super().__init__(parent)
        self._v = float(value)
        self.setText(fmt_freq(self._v))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setToolTip("Например: 100, 433.92M, 2.4G, 500k")
        self.editingFinished.connect(self._done)

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = float(v)
        self.setText(fmt_freq(self._v))

    def _done(self):
        try:
            v = parse_freq(self.text())
        except ValueError:
            self.setText(fmt_freq(self._v))
            return
        v = min(max(v, 0.0), F_MAX)
        changed = abs(v - self._v) > 0.5
        self._v = v
        self.setText(fmt_freq(v))
        if changed:
            self.valueChanged.emit(v)


class HistoryLineEdit(QtWidgets.QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hist, self.pos = [], 0

    def remember(self, text):
        if text and (not self.hist or self.hist[-1] != text):
            self.hist.append(text)
        self.pos = len(self.hist)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Up and self.hist:
            self.pos = max(0, self.pos - 1)
            self.setText(self.hist[self.pos])
        elif e.key() == Qt.Key.Key_Down and self.hist:
            self.pos = min(len(self.hist), self.pos + 1)
            self.setText(self.hist[self.pos] if self.pos < len(self.hist) else "")
        else:
            super().keyPressEvent(e)


class ShotDialog(QtWidgets.QDialog):
    def __init__(self, data: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Экран прибора")
        self.data = data
        self.lbl = QtWidgets.QLabel()
        self.chk = QtWidgets.QCheckBox("Поменять порядок байт (если цвета неверны)")
        self.chk.toggled.connect(self.render)
        btn = QtWidgets.QPushButton("Сохранить PNG…")
        btn.clicked.connect(self.save)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.lbl)
        lay.addWidget(self.chk)
        lay.addWidget(btn)
        self.img = None
        self.render()

    def render(self):
        n = len(self.data) // 2
        w, h = (480, 320) if n == 480 * 320 else (320, 240)
        v = np.frombuffer(self.data[:n * 2], dtype="<u2" if self.chk.isChecked() else ">u2").astype(np.uint16)
        r = ((v >> 11) & 31) * 255 // 31
        g = ((v >> 5) & 63) * 255 // 63
        b = (v & 31) * 255 // 31
        rgb = np.ascontiguousarray(np.stack([r, g, b], -1).astype(np.uint8))
        self.img = QtGui.QImage(rgb.data, w, h, w * 3, QtGui.QImage.Format.Format_RGB888).copy()
        self.lbl.setPixmap(QtGui.QPixmap.fromImage(self.img))

    def save(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Сохранить", "tinysa_screen.png", "PNG (*.png)")
        if p:
            self.img.save(p)


def find_peaks(y: np.ndarray, n: int = 8):
    y = y.astype(float).copy()
    sep = max(3, len(y) // 80)
    floor = np.median(y) + 6
    out = []
    for _ in range(n):
        i = int(np.argmax(y))
        if not np.isfinite(y[i]) or y[i] < floor:
            break
        out.append(i)
        y[max(0, i - sep):i + sep + 1] = -np.inf
    return out


class MarkerObj:
    COLORS = ["#00e5ff", "#ff9f1c", "#b388ff", "#69f0ae"]

    def __init__(self, win: "MainWindow", i: int):
        self.i, self.enabled, self.freq = i, False, 0.0
        c = self.COLORS[i]
        self.line = pg.InfiniteLine(angle=90, movable=True,
                                    pen=pg.mkPen(c, width=1.2, style=Qt.PenStyle.DashLine),
                                    hoverPen=pg.mkPen(c, width=3))
        self.line.setZValue(10)
        self.dot = pg.ScatterPlotItem(size=9, brush=pg.mkBrush(c), pen=pg.mkPen("#000"))
        self.text = pg.TextItem(color=c, anchor=(0.5, 1.3), fill=pg.mkBrush(11, 15, 20, 210),
                                border=pg.mkPen(c))
        self.wline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(c, width=1))
        for it in (self.line, self.dot, self.text):
            win.spec.addItem(it)
        win.wfp.addItem(self.wline)
        self.show(False)
        self.line.sigPositionChanged.connect(lambda: win.marker_dragged(self))

    def show(self, on: bool):
        for it in (self.line, self.dot, self.text, self.wline):
            it.setVisible(on)


# ================================================================ главное окно ===
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.resize(1500, 900)
        self.worker: Worker | None = None
        self.freqs = self.live = self.maxh = self.minh = self.avg = self.wf = None
        self.avg_n = 0
        self.unit_off = 0.0
        self.log_f = self.log_w = None
        self.sweep_t = deque(maxlen=15)
        self.sweep_dur = 0.0
        self._last_sweep_start = None
        self._mk_busy = False
        self._block = False
        self._last_key = None
        self._wf_lo, self._wf_hi = -110.0, -40.0
        self._last_beep = 0.0
        self.settings = QtCore.QSettings("TinySAStudio", "TinySAStudio")

        self._build_toolbar()
        self._build_plots()
        self._build_panel()
        self._build_status()
        self._build_menu()
        central = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        central.addWidget(self.plot_split)
        central.addWidget(self.tabs)
        central.setStretchFactor(0, 1)
        central.setSizes([1100, 380])
        self.setCentralWidget(central)

        self.markers = [MarkerObj(self, i) for i in range(4)]
        self._restore()
        self.refresh_ports()
        self.apply_units()
        self.apply_cmap()
        self.cfg_changed()
        self.update_buttons()

    # ------------------------------------------------------------ интерфейс ---
    def _build_toolbar(self):
        tb = self.addToolBar("main")
        tb.setMovable(False)
        self.cb_port = QtWidgets.QComboBox()
        self.cb_port.setMinimumWidth(230)
        b_ref = QtWidgets.QPushButton("⟳")
        b_ref.setToolTip("Обновить список портов")
        b_ref.clicked.connect(self.refresh_ports)
        self.btn_conn = QtWidgets.QPushButton("Подключить")
        self.btn_conn.setObjectName("accent")
        self.btn_conn.clicked.connect(self.toggle_connect)
        self.btn_demo = QtWidgets.QPushButton("Демо")
        self.btn_demo.setToolTip("Работа без прибора (имитация сигналов)")
        self.btn_demo.clicked.connect(self.start_demo)
        self.btn_run = QtWidgets.QPushButton("▶ Старт")
        self.btn_run.setCheckable(True)
        self.btn_run.setShortcut("Space")
        self.btn_run.toggled.connect(self.run_toggled)
        self.btn_single = QtWidgets.QPushButton("Один скан")
        self.btn_single.clicked.connect(lambda: self.worker and self.worker.post("single"))
        self.btn_reset = QtWidgets.QPushButton("Сброс трасс")
        self.btn_reset.setShortcut("Ctrl+R")
        self.btn_reset.clicked.connect(self.reset_data)
        self.btn_rec = QtWidgets.QPushButton("● Запись")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setObjectName("danger")
        self.btn_rec.toggled.connect(self.rec_toggled)
        for w in (QtWidgets.QLabel("Порт"), self.cb_port, b_ref, self.btn_conn, self.btn_demo):
            tb.addWidget(w)
        tb.addSeparator()
        for w in (self.btn_run, self.btn_single, self.btn_reset, self.btn_rec):
            tb.addWidget(w)

    def _build_plots(self):
        # --- спектр
        self.pw1 = pg.PlotWidget()
        self.spec = self.pw1.getPlotItem()
        self.spec.showGrid(x=True, y=True, alpha=0.16)
        self.spec.setLabel("bottom", "Частота", units="Hz")
        self.spec.getAxis("left").setWidth(64)
        self.spec.setMenuEnabled(False)
        self.spec.disableAutoRange()
        self.spec.getAxis("left").enableAutoSIPrefix(False)
        self.spec.addLegend(offset=(-10, 8), labelTextColor="#aab4c0")
        self.c_live = self.spec.plot(pen=pg.mkPen("#ffd166", width=1.5), name="Live",
                                     fillLevel=-400, brush=(255, 209, 102, 26))
        self.c_max = self.spec.plot(pen=pg.mkPen("#ef476f", width=1), name="Max hold")
        self.c_min = self.spec.plot(pen=pg.mkPen("#4cc9f0", width=1), name="Min hold")
        self.c_avg = self.spec.plot(pen=pg.mkPen("#06d6a0", width=1.4), name="Среднее")
        self.band = pg.LinearRegionItem(values=(0, 1), brush=(76, 201, 240, 28),
                                        pen=pg.mkPen("#4cc9f0", width=1))
        self.band.setZValue(5)
        self.band.setVisible(False)
        self.band.sigRegionChanged.connect(self.update_measure)
        self.spec.addItem(self.band)
        self.thr_line = pg.InfiniteLine(pos=-60, angle=0, movable=True,
                                        pen=pg.mkPen("#ef476f", width=1, style=Qt.PenStyle.DashLine))
        self.thr_line.setVisible(False)
        self.thr_line.sigPositionChanged.connect(self.thr_line_moved)
        self.spec.addItem(self.thr_line)

        # --- водопад
        self.pw2 = pg.PlotWidget()
        self.wfp = self.pw2.getPlotItem()
        self.wfp.invertY(True)
        self.wfp.setMouseEnabled(x=True, y=False)
        self.wfp.setMenuEnabled(False)
        self.wfp.disableAutoRange()
        self.wfp.getAxis("left").setWidth(64)
        self.wfp.getAxis("left").setStyle(showValues=False)
        self.wfp.setLabel("left", "время ↓")
        self.wfp.setLabel("bottom", "Частота", units="Hz")
        self.wf_img = pg.ImageItem()
        self.wfp.addItem(self.wf_img)
        self.pw2.setXLink(self.pw1)

        self.plot_split = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        self.plot_split.addWidget(self.pw1)
        self.plot_split.addWidget(self.pw2)
        self.plot_split.setSizes([520, 340])

        self.proxy = pg.SignalProxy(self.pw1.scene().sigMouseMoved, rateLimit=30, slot=self.on_mouse)
        self.proxy2 = pg.SignalProxy(self.pw2.scene().sigMouseMoved, rateLimit=30, slot=self.on_mouse)
        self.pw1.scene().sigMouseClicked.connect(lambda ev: self.on_click(ev, self.pw1))
        self.pw2.scene().sigMouseClicked.connect(lambda ev: self.on_click(ev, self.pw2))

    # --- панель справа
    def _group(self, title, form=True):
        g = QtWidgets.QGroupBox(title)
        lay = QtWidgets.QFormLayout(g) if form else QtWidgets.QVBoxLayout(g)
        return g, lay

    def _spin(self, lo, hi, val, step=1, suffix="", dec=None):
        if dec is None:
            s = QtWidgets.QSpinBox()
            s.setRange(int(lo), int(hi))
            s.setValue(int(val))
            s.setSingleStep(int(step))
        else:
            s = QtWidgets.QDoubleSpinBox()
            s.setDecimals(dec)
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSingleStep(step)
        if suffix:
            s.setSuffix(suffix)
        return s

    def _page(self, *groups):
        w = QtWidgets.QWidget()
        w.setObjectName("panel")
        lay = QtWidgets.QVBoxLayout(w)
        for g in groups:
            lay.addWidget(g)
        lay.addStretch(1)
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sc.setWidget(w)
        return sc

    def _build_panel(self):
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMinimumWidth(340)
        self.tabs.setMaximumWidth(520)

        # ---------- Развёртка
        g1, f1 = self._group("Частота")
        self.e_start, self.e_stop = FreqEdit(88e6), FreqEdit(108e6)
        self.e_center, self.e_span = FreqEdit(98e6), FreqEdit(20e6)
        self.cb_band = QtWidgets.QComboBox()
        self.cb_band.addItems(list(BANDS))
        self.cb_band.activated.connect(self.band_selected)
        self.sp_points = self._spin(51, 5000, 450, 50)
        self.sp_points.setToolTip("Больше 450 точек — скан выполняется сегментами")
        for lbl, w in (("Диапазон", self.cb_band), ("Старт", self.e_start), ("Стоп", self.e_stop),
                       ("Центр", self.e_center), ("Полоса", self.e_span), ("Точек", self.sp_points)):
            f1.addRow(lbl, w)
        b_view = QtWidgets.QPushButton("Диапазон = видимая область графика")
        b_view.clicked.connect(self.range_from_view)
        f1.addRow(b_view)
        self.e_start.valueChanged.connect(self.start_stop_edited)
        self.e_stop.valueChanged.connect(self.start_stop_edited)
        self.e_center.valueChanged.connect(self.center_span_edited)
        self.e_span.valueChanged.connect(self.center_span_edited)
        self.sp_points.valueChanged.connect(self.cfg_changed)

        g2, f2 = self._group("Вход")
        self.cb_rbw = QtWidgets.QComboBox()
        self.cb_rbw.addItems(["Авто", "0.2", "1", "3", "10", "30", "100", "300", "600", "850"])
        self.cb_rbw.setToolTip("RBW, кГц")
        self.sp_att = self._spin(-1, 31, -1, 1, " dB")
        self.sp_att.setSpecialValueText("Авто")
        self.chk_lna = QtWidgets.QCheckBox("LNA (малошумящий усилитель)")
        self.chk_spur = QtWidgets.QCheckBox("Подавление помех (spur removal)")
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems(["Авто (по частоте)", "Low input", "High input"])
        f2.addRow("RBW, кГц", self.cb_rbw)
        f2.addRow("Аттенюатор", self.sp_att)
        f2.addRow("Вход", self.cb_mode)
        f2.addRow(self.chk_lna)
        f2.addRow(self.chk_spur)
        for w in (self.cb_rbw, self.cb_mode):
            w.currentIndexChanged.connect(self.cfg_changed)
        self.sp_att.valueChanged.connect(self.cfg_changed)
        self.chk_lna.toggled.connect(self.cfg_changed)
        self.chk_spur.toggled.connect(self.cfg_changed)

        g3, f3 = self._group("Калибровка и единицы")
        self.sp_scale = self._spin(0, 300, 174, 1, " dB", dec=1)
        self.sp_scale.setToolTip("дБм = raw/32 − значение. Ultra: 174, базовый tinySA: 128")
        self.sp_off = self._spin(-100, 100, 0, 0.5, " dB", dec=1)
        self.sp_off.setToolTip("Внешний аттенюатор/усилитель/кабель: добавляется к показаниям")
        self.cb_unit = QtWidgets.QComboBox()
        self.cb_unit.addItems(["dBm", "dBµV"])
        f3.addRow("Шкала (scale)", self.sp_scale)
        f3.addRow("Смещение уровня", self.sp_off)
        f3.addRow("Единицы", self.cb_unit)
        self.sp_scale.valueChanged.connect(self.cfg_changed)
        self.sp_off.valueChanged.connect(lambda *_: self.reset_data())
        self.cb_unit.currentIndexChanged.connect(self.apply_units)
        self.tabs.addTab(self._page(g1, g2, g3), "Развёртка")

        # ---------- Трассы
        g4, f4 = self._group("Трассы")
        self.cb_live = QtWidgets.QCheckBox("Live")
        self.cb_live.setChecked(True)
        self.cb_max = QtWidgets.QCheckBox("Max hold")
        self.cb_min = QtWidgets.QCheckBox("Min hold")
        self.cb_avg = QtWidgets.QCheckBox("Среднее")
        self.sp_avg = self._spin(2, 500, 10, 1)
        for c in (self.cb_live, self.cb_max, self.cb_min, self.cb_avg):
            c.toggled.connect(self.refresh_curves)
        f4.addRow(self.cb_live)
        f4.addRow(self.cb_max)
        f4.addRow(self.cb_min)
        f4.addRow(self.cb_avg, self.sp_avg)
        b_rst = QtWidgets.QPushButton("Сбросить удержание / среднее")
        b_rst.clicked.connect(self.reset_data)
        f4.addRow(b_rst)

        g5, f5 = self._group("Шкала Y (dBm)")
        self.chk_yauto = QtWidgets.QCheckBox("Авто")
        self.sp_ytop = self._spin(-150, 40, 0, 5, " dBm")
        self.sp_ybot = self._spin(-160, 30, -120, 5, " dBm")
        f5.addRow(self.chk_yauto)
        f5.addRow("Верх", self.sp_ytop)
        f5.addRow("Низ", self.sp_ybot)
        for w in (self.sp_ytop, self.sp_ybot):
            w.valueChanged.connect(self.apply_yrange)
        self.chk_yauto.toggled.connect(self.apply_yrange)

        g6, f6 = self._group("Водопад")
        self.chk_wf = QtWidgets.QCheckBox("Показывать")
        self.chk_wf.setChecked(True)
        self.chk_wf.toggled.connect(lambda on: self.pw2.setVisible(on))
        self.cb_cmap = QtWidgets.QComboBox()
        self.cb_cmap.addItems(list(COLORMAPS))
        self.cb_cmap.currentIndexChanged.connect(self.apply_cmap)
        self.sp_hist = self._spin(50, 2000, 400, 50, " строк")
        self.chk_wfauto = QtWidgets.QCheckBox("Авто-уровни")
        self.chk_wfauto.setChecked(True)
        self.sp_wmin = self._spin(-160, 20, -110, 5, " dBm")
        self.sp_wmax = self._spin(-150, 30, -40, 5, " dBm")
        f6.addRow(self.chk_wf)
        f6.addRow("Палитра", self.cb_cmap)
        f6.addRow("История", self.sp_hist)
        f6.addRow(self.chk_wfauto)
        f6.addRow("Мин", self.sp_wmin)
        f6.addRow("Макс", self.sp_wmax)
        self.sp_hist.valueChanged.connect(lambda *_: setattr(self, "wf", None))
        self.tabs.addTab(self._page(g4, g5, g6), "Трассы")

        # ---------- Маркеры и измерения
        g7, l7 = self._group("Маркеры", form=False)
        self.mtable = QtWidgets.QTableWidget(4, 4)
        self.mtable.setHorizontalHeaderLabels(["", "Частота", "Уровень", "Δ к M1"])
        self.mtable.verticalHeader().setVisible(False)
        self.mtable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.mtable.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.mtable.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mtable.horizontalHeader().setStretchLastSection(True)
        self.mtable.setColumnWidth(0, 52)
        self.mtable.setColumnWidth(1, 105)
        self.mtable.setColumnWidth(2, 80)
        self.mtable.setMaximumHeight(150)
        for r in range(4):
            it = QtWidgets.QTableWidgetItem(f"M{r + 1}")
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setForeground(QtGui.QBrush(QtGui.QColor(MarkerObj.COLORS[r])))
            self.mtable.setItem(r, 0, it)
            for c in (1, 2, 3):
                self.mtable.setItem(r, c, QtWidgets.QTableWidgetItem(""))
        self.mtable.selectRow(0)
        self.mtable.itemChanged.connect(self.marker_toggled)
        l7.addWidget(self.mtable)
        hint = QtWidgets.QLabel("Двойной щелчок по графику — поставить выбранный маркер.\nМаркеры можно перетаскивать.")
        hint.setStyleSheet("color:#8fa1b3")
        l7.addWidget(hint)
        self.cb_track = QtWidgets.QCheckBox("M1 следит за максимумом")
        l7.addWidget(self.cb_track)
        row = QtWidgets.QGridLayout()
        for i, (t, fn) in enumerate((("Пик → M1", self.mk_peak), ("Следующий пик", self.mk_next_peak),
                                     ("Центр = M1", self.mk_center), ("Диапазон M1–M2", self.mk_range),
                                     ("Убрать все", self.mk_clear))):
            b = QtWidgets.QPushButton(t)
            b.clicked.connect(fn)
            row.addWidget(b, i // 2, i % 2)
        l7.addLayout(row)

        g8, l8 = self._group("Пики", form=False)
        self.ptable = QtWidgets.QTableWidget(8, 2)
        self.ptable.setHorizontalHeaderLabels(["Частота", "Уровень"])
        self.ptable.verticalHeader().setVisible(False)
        self.ptable.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ptable.horizontalHeader().setStretchLastSection(True)
        self.ptable.setColumnWidth(0, 150)
        self.ptable.setMaximumHeight(240)
        self.ptable.cellDoubleClicked.connect(self.peak_dclicked)
        for r in range(8):
            for c in range(2):
                self.ptable.setItem(r, c, QtWidgets.QTableWidgetItem(""))
        l8.addWidget(self.ptable)

        g9, f9 = self._group("Измерение в полосе")
        self.chk_band = QtWidgets.QCheckBox("Показать полосу (перетащите границы на графике)")
        self.chk_band.toggled.connect(self.band_toggled)
        f9.addRow(self.chk_band)
        self.m_pwr, self.m_peak, self.m_obw, self.m_mean, self.m_floor = (QtWidgets.QLabel("—") for _ in range(5))
        for w in (self.m_pwr, self.m_peak, self.m_obw, self.m_mean, self.m_floor):
            w.setObjectName("value")
        f9.addRow("Мощность (≈)", self.m_pwr)
        f9.addRow("Пик в полосе", self.m_peak)
        f9.addRow("OBW 99%", self.m_obw)
        f9.addRow("Среднее", self.m_mean)
        f9.addRow("Шумовой пол", self.m_floor)

        g10, f10 = self._group("Порог / тревога")
        self.chk_thr = QtWidgets.QCheckBox("Включить порог")
        self.sp_thr = self._spin(-150, 30, -60, 1, " dBm")
        self.chk_beep = QtWidgets.QCheckBox("Звуковой сигнал")
        self.lbl_thr = QtWidgets.QLabel("")
        f10.addRow(self.chk_thr)
        f10.addRow("Уровень", self.sp_thr)
        f10.addRow(self.chk_beep)
        f10.addRow(self.lbl_thr)
        self.chk_thr.toggled.connect(lambda on: self.thr_line.setVisible(on))
        self.sp_thr.valueChanged.connect(self.thr_spin_changed)
        self.tabs.addTab(self._page(g7, g8, g9, g10), "Маркеры")

        # ---------- Генератор
        gg, fg = self._group("Генератор сигналов")
        self.g_freq = FreqEdit(100e6)
        self.g_level = self._spin(-115, 13, -30, 1, " dBm", dec=1)
        self.g_mod = QtWidgets.QComboBox()
        self.g_mod.addItems(["off", "AM", "NFM", "WFM"])
        self.btn_gen = QtWidgets.QPushButton("Включить выход")
        self.btn_gen.setCheckable(True)
        self.btn_gen.toggled.connect(self.gen_toggled)
        fg.addRow("Частота", self.g_freq)
        fg.addRow("Уровень", self.g_level)
        fg.addRow("Модуляция", self.g_mod)
        fg.addRow(self.btn_gen)
        warn = QtWidgets.QLabel("При включении генератора развёртка останавливается.\n"
                                "Диапазон уровней зависит от выхода (Low/High) прибора.")
        warn.setStyleSheet("color:#8fa1b3")
        fg.addRow(warn)
        self.g_freq.valueChanged.connect(lambda *_: self.gen_apply())
        self.g_level.valueChanged.connect(lambda *_: self.gen_apply())
        self.g_mod.currentIndexChanged.connect(lambda *_: self.gen_apply())
        self.tabs.addTab(self._page(gg), "Генератор")

        # ---------- Консоль
        cw = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(cw)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.cmd_in = HistoryLineEdit()
        self.cmd_in.setPlaceholderText("Команда прибора, например: version, info, help…  (Enter)")
        self.cmd_in.returnPressed.connect(self.send_cmd)
        quick = QtWidgets.QHBoxLayout()
        for c in ("version", "info", "help", "vbat"):
            b = QtWidgets.QPushButton(c)
            b.clicked.connect(lambda _=False, c=c: self.send_cmd(c))
            quick.addWidget(b)
        b_shot = QtWidgets.QPushButton("Скриншот экрана прибора")
        b_shot.clicked.connect(lambda: self.worker and self.worker.post("capture"))
        cl.addWidget(self.console, 1)
        cl.addWidget(self.cmd_in)
        cl.addLayout(quick)
        cl.addWidget(b_shot)
        self.tabs.addTab(cw, "Консоль")

    def _build_status(self):
        sb = self.statusBar()
        self.lbl_conn = QtWidgets.QLabel("● не подключено")
        self.lbl_sweep = QtWidgets.QLabel("")
        self.lbl_cursor = QtWidgets.QLabel("")
        self.lbl_bat = QtWidgets.QLabel("")
        for w in (self.lbl_conn, self.lbl_sweep, self.lbl_cursor):
            sb.addWidget(w)
        sb.addPermanentWidget(self.lbl_bat)

    def _build_menu(self):
        m = self.menuBar().addMenu("Файл")
        for text, fn, sc in (("Сохранить трассы в CSV…", self.save_csv, "Ctrl+S"),
                             ("Сохранить снимок графиков (PNG)…", self.save_png, "Ctrl+P"),
                             ("Сохранить водопад (PNG)…", self.save_wf, None),
                             ("Скриншот экрана прибора…", lambda: self.worker and self.worker.post("capture"), None),
                             ("Сохранить настройки…", self.save_settings_file, None),
                             ("Загрузить настройки…", self.load_settings_file, None),
                             ("Выход", self.close, "Ctrl+Q")):
            a = m.addAction(text)
            a.triggered.connect(lambda _=False, fn=fn: fn())
            if sc:
                a.setShortcut(sc)
        h = self.menuBar().addMenu("Справка")
        h.addAction("О программе").triggered.connect(lambda: QtWidgets.QMessageBox.about(
            self, APP, f"{APP}\nПК-клиент для TinySA Ultra.\nPython + PySide6 + pyqtgraph."))
        QtGui.QShortcut(QtGui.QKeySequence("M"), self, activated=self.mk_peak)

    # ---------------------------------------------------------- подключение ---
    def refresh_ports(self):
        from serial.tools import list_ports
        cur = self.cb_port.currentData()
        self.cb_port.clear()
        found = None
        for p in list_ports.comports():
            tiny = p.vid == USB_VID and p.pid == USB_PID
            self.cb_port.addItem(f"{p.device} — {'TinySA ' if tiny else ''}{p.description}", p.device)
            if tiny and found is None:
                found = self.cb_port.count() - 1
        if found is not None:
            self.cb_port.setCurrentIndex(found)
        else:
            i = self.cb_port.findData(cur)
            if i >= 0:
                self.cb_port.setCurrentIndex(i)
        if not self.cb_port.count():
            self.cb_port.addItem("порты не найдены", None)

    def toggle_connect(self):
        if self.worker:
            self.disconnect_dev()
            return
        port = self.cb_port.currentData()
        if not port:
            QtWidgets.QMessageBox.information(self, APP, "Порт не выбран. Подключите прибор и нажмите ⟳.")
            return
        self.start_worker(TinySADevice(port))

    def start_demo(self):
        if self.worker:
            self.disconnect_dev()
        self.start_worker(DemoDevice())

    def start_worker(self, dev):
        w = Worker(dev, self)
        w.connected.connect(self.on_connected)
        w.sweep.connect(self.on_sweep)
        w.console.connect(self.on_console)
        w.battery.connect(lambda mv: self.lbl_bat.setText(f"🔋 {mv / 1000:.2f} V"))
        w.screenshot.connect(lambda d: ShotDialog(d, self).exec())
        w.stopped.connect(self.on_stopped)
        w.failed.connect(self.on_failed)
        self.worker = w
        self.lbl_conn.setText("● подключение…")
        self.btn_conn.setEnabled(False)
        self.btn_demo.setEnabled(False)
        w.start()

    @Slot(bool, str, bool)
    def on_connected(self, ok, text, ultra):
        self.btn_conn.setEnabled(True)
        if not ok:
            QtWidgets.QMessageBox.warning(self, APP, f"Не удалось подключиться:\n{text}")
            self.worker = None
            self.lbl_conn.setText("● не подключено")
            self.update_buttons()
            return
        self.lbl_conn.setText(f"● {text}")
        self.sp_scale.setValue(174 if ultra else 128)
        self.worker.post("cfg", self.build_cfg())
        self.update_buttons()
        self.btn_run.setChecked(True)

    def disconnect_dev(self):
        w, self.worker = self.worker, None
        if w:
            self.btn_run.blockSignals(True)
            self.btn_run.setChecked(False)
            self.btn_run.blockSignals(False)
            self.btn_gen.blockSignals(True)
            self.btn_gen.setChecked(False)
            self.btn_gen.blockSignals(False)
            w.stop()
            w.wait(4000)
        self.lbl_conn.setText("● не подключено")
        self.lbl_bat.setText("")
        self.update_buttons()

    @Slot(str)
    def on_stopped(self, msg):
        self.btn_run.blockSignals(True)
        self.btn_run.setChecked(False)
        self.btn_run.blockSignals(False)
        self.btn_run.setText("▶ Старт")
        self.statusBar().showMessage(f"Скан остановлен: {msg}", 8000)

    @Slot(str)
    def on_failed(self, msg):
        self.disconnect_dev()
        QtWidgets.QMessageBox.warning(self, APP, f"Связь с прибором потеряна:\n{msg}")

    def update_buttons(self):
        on = self.worker is not None and self.worker.isRunning()
        self.btn_conn.setText("Отключить" if self.worker else "Подключить")
        self.btn_demo.setEnabled(True)
        for b in (self.btn_run, self.btn_single, self.btn_gen):
            b.setEnabled(on)

    @Slot(bool)
    def run_toggled(self, on):
        self.btn_run.setText("■ Стоп" if on else "▶ Старт")
        if self.worker:
            self.worker.post("run", on)

    @Slot(str)
    def on_console(self, text):
        self.console.appendPlainText(text.rstrip())

    def send_cmd(self, cmd=None):
        if not isinstance(cmd, str):
            cmd = self.cmd_in.text().strip()
            self.cmd_in.remember(cmd)
            self.cmd_in.clear()
        if cmd and self.worker:
            self.worker.post("cmd", cmd)
        elif cmd:
            self.console.appendPlainText("(нет подключения)")

    # ----------------------------------------------------------- настройки ---
    def build_cfg(self) -> Cfg:
        rbw_t = self.cb_rbw.currentText()
        return Cfg(start=self.e_start.value(), stop=self.e_stop.value(),
                   points=self.sp_points.value(),
                   rbw=0.0 if rbw_t == "Авто" else float(rbw_t),
                   atten=self.sp_att.value(), lna=self.chk_lna.isChecked(),
                   spur=self.chk_spur.isChecked(),
                   input_mode=("auto", "low", "high")[self.cb_mode.currentIndex()],
                   scale=self.sp_scale.value())

    def cfg_changed(self, *_):
        if self._block:
            return
        cfg = self.build_cfg()
        key = (cfg.start, cfg.stop, cfg.points)
        if key != self._last_key:
            self._last_key = key
            self.reset_data()
            self.spec.setXRange(cfg.start, cfg.stop, padding=0)
        if self.worker:
            self.worker.post("cfg", cfg)

    def _set_range(self, a, b):
        a = min(max(a, 0.0), F_MAX - 1e3)
        b = min(max(b, a + 1e3), F_MAX)
        self._block = True
        self.e_start.setValue(a)
        self.e_stop.setValue(b)
        self.e_center.setValue((a + b) / 2)
        self.e_span.setValue(b - a)
        self._block = False
        self.cfg_changed()

    def start_stop_edited(self, *_):
        self._set_range(self.e_start.value(), self.e_stop.value())

    def center_span_edited(self, *_):
        c, s = self.e_center.value(), self.e_span.value()
        s = max(s, 1e3)
        self._set_range(c - s / 2, c + s / 2)

    def band_selected(self, idx):
        r = BANDS[self.cb_band.itemText(idx)]
        if r:
            self._set_range(*r)

    def range_from_view(self):
        (a, b), _ = self.spec.viewRange()
        self._set_range(a, b)

    # -------------------------------------------------------------- данные ---
    def reset_data(self):
        self.freqs = self.live = self.maxh = self.minh = self.avg = self.wf = None
        self.avg_n = 0
        for c in (self.c_live, self.c_max, self.c_min, self.c_avg):
            c.setData([], [])
        self.wf_img.clear()
        for m in getattr(self, "markers", []):
            m.dot.setData([], [])
            m.text.setText("")

    @Slot(object, object, bool)
    def on_sweep(self, freqs, lvl, final):
        lvl = lvl + self.sp_off.value()
        n = len(lvl)
        if self.freqs is None or len(self.freqs) != n or self.freqs[0] != freqs[0] or self.freqs[-1] != freqs[-1]:
            self.freqs = freqs
            self.maxh = self.minh = self.avg = None
            self.avg_n = 0
            self.wf = None
        self.live = lvl
        if not final:                     # промежуточный результат сегментного скана
            self.c_live.setData(freqs, lvl + self.unit_off)
            return
        if self.maxh is None:
            self.maxh, self.minh, self.avg = lvl.copy(), lvl.copy(), lvl.copy()
            self.avg_n = 0
        self.maxh = np.maximum(self.maxh, lvl)
        self.minh = np.minimum(self.minh, lvl)
        self.avg_n = min(self.avg_n + 1, self.sp_avg.value())
        self.avg = self.avg + (lvl - self.avg) / self.avg_n
        self.refresh_curves()
        self.push_waterfall(lvl)
        self.update_markers()
        self.update_peaks()
        self.update_measure()
        self.check_threshold()
        if self.chk_yauto.isChecked():
            self.apply_yrange()
        self.log_sweep(freqs, lvl)
        now = time.monotonic()
        self.sweep_t.append(now)
        if len(self.sweep_t) > 1:
            rate = (len(self.sweep_t) - 1) / (self.sweep_t[-1] - self.sweep_t[0])
            self.lbl_sweep.setText(f"{rate:.1f} скан/с · {n} точек · шаг {fmt_freq((freqs[-1] - freqs[0]) / (n - 1))}")

    def refresh_curves(self, *_):
        for c, chk, arr in ((self.c_live, self.cb_live, self.live), (self.c_max, self.cb_max, self.maxh),
                            (self.c_min, self.cb_min, self.minh), (self.c_avg, self.cb_avg, self.avg)):
            c.setVisible(chk.isChecked())
            if arr is not None and self.freqs is not None:
                c.setData(self.freqs, arr + self.unit_off)

    def push_waterfall(self, lvl):
        n, H = len(lvl), self.sp_hist.value()
        if self.wf is None or self.wf.shape != (H, n):
            self.wf = np.full((H, n), -150.0)
            self.wfp.setYRange(0, H, padding=0)
        self.wf[1:] = self.wf[:-1]
        self.wf[0] = lvl
        if not self.chk_wf.isChecked():
            return
        if self.chk_wfauto.isChecked():
            lo, hi = float(np.median(lvl)) - 4, float(np.max(lvl)) + 4
            self._wf_lo += (lo - self._wf_lo) * 0.1
            self._wf_hi += (hi - self._wf_hi) * 0.1
        else:
            self._wf_lo, self._wf_hi = self.sp_wmin.value(), self.sp_wmax.value()
        self.wf_img.setImage(self.wf, autoLevels=False, levels=(self._wf_lo, max(self._wf_hi, self._wf_lo + 1)))
        f0, f1 = self.freqs[0], self.freqs[-1]
        self.wf_img.setRect(QtCore.QRectF(f0, 0, f1 - f0, H))

    def apply_cmap(self, *_):
        self.wf_img.setLookupTable(make_lut(COLORMAPS[self.cb_cmap.currentText()]))

    def apply_units(self, *_):
        dbuv = self.cb_unit.currentIndex() == 1
        self.unit_off = 107.0 if dbuv else 0.0
        self.spec.setLabel("left", "Уровень", units="dBµV" if dbuv else "dBm")
        self.thr_line.setValue(self.sp_thr.value() + self.unit_off)
        self.refresh_curves()
        self.apply_yrange()
        self.update_markers()

    def apply_yrange(self, *_):
        if self.chk_yauto.isChecked() and self.live is not None:
            lo_src = self.minh if (self.cb_min.isChecked() and self.minh is not None) else self.live
            hi_src = self.maxh if (self.cb_max.isChecked() and self.maxh is not None) else self.live
            lo = np.floor((np.min(lo_src) - 5) / 10) * 10
            hi = np.ceil((np.max(hi_src) + 8) / 10) * 10
        else:
            hi, lo = self.sp_ytop.value(), self.sp_ybot.value()
        if hi <= lo:
            hi = lo + 10
        self.spec.setYRange(lo + self.unit_off, hi + self.unit_off, padding=0)

    def lvl_txt(self, v):
        return f"{v + self.unit_off:.1f} {'dBµV' if self.unit_off else 'dBm'}"

    # ------------------------------------------------------------- маркеры ---
    def nearest(self, f):
        return int(np.clip(np.searchsorted(self.freqs, f), 1, len(self.freqs) - 1)) if len(self.freqs) > 1 else 0

    def marker_dragged(self, m: MarkerObj):
        if self._mk_busy:
            return
        m.freq = m.line.value()
        self.update_markers()

    def marker_toggled(self, item):
        if item.column() != 0:
            return
        m = self.markers[item.row()]
        m.enabled = item.checkState() == Qt.CheckState.Checked
        if m.enabled and m.freq == 0.0:
            (a, b), _ = self.spec.viewRange()
            m.freq = (a + b) / 2
        m.show(m.enabled)
        self._mk_busy = True
        m.line.setValue(m.freq)
        self._mk_busy = False
        self.update_markers()

    def _enable_marker(self, i, freq):
        m = self.markers[i]
        m.freq = freq
        self.mtable.item(i, 0).setCheckState(Qt.CheckState.Checked)   # вызовет marker_toggled
        self._mk_busy = True
        m.line.setValue(freq)
        self._mk_busy = False
        self.update_markers()

    def update_markers(self):
        if self._mk_busy or self.freqs is None or self.live is None:
            return
        self._mk_busy = True
        try:
            f, y = self.freqs, self.live
            ref = None
            self.mtable.blockSignals(True)
            for m in self.markers:
                if not m.enabled:
                    for c in (1, 2, 3):
                        self.mtable.item(m.i, c).setText("")
                    continue
                if m.i == 0 and self.cb_track.isChecked():
                    m.freq = float(f[int(np.argmax(y))])
                idx = self.nearest(m.freq)
                if abs(f[max(idx - 1, 0)] - m.freq) < abs(f[idx] - m.freq):
                    idx -= 1
                lv = float(y[idx])
                m.freq = float(f[idx]) if m.i == 0 and self.cb_track.isChecked() else m.freq
                m.line.setValue(m.freq)
                m.dot.setData([m.freq], [lv + self.unit_off])
                m.text.setText(f"M{m.i + 1}\n{fmt_freq(m.freq)}\n{self.lvl_txt(lv)}")
                m.text.setPos(m.freq, lv + self.unit_off)
                m.wline.setValue(m.freq)
                self.mtable.item(m.i, 1).setText(fmt_freq(m.freq))
                self.mtable.item(m.i, 2).setText(self.lvl_txt(lv))
                if m.i == 0:
                    ref = (m.freq, lv)
                    self.mtable.item(0, 3).setText("")
                elif ref:
                    self.mtable.item(m.i, 3).setText(
                        f"{'+' if m.freq >= ref[0] else '−'}{fmt_freq(abs(m.freq - ref[0]))} / {lv - ref[1]:+.1f} dB")
                else:
                    self.mtable.item(m.i, 3).setText("")
            self.mtable.blockSignals(False)
        finally:
            self._mk_busy = False

    def mk_peak(self):
        if self.freqs is not None and self.live is not None:
            self._enable_marker(0, float(self.freqs[int(np.argmax(self.live))]))

    def mk_next_peak(self):
        if self.live is None:
            return
        peaks = find_peaks(self.live, 4)
        free = [m.i for m in self.markers if not m.enabled] or [3]
        for k, i in enumerate(free[:1]):
            if peaks:
                taken = [m.freq for m in self.markers if m.enabled]
                for p in peaks:
                    if all(abs(self.freqs[p] - t) > (self.freqs[-1] - self.freqs[0]) / 100 for t in taken):
                        self._enable_marker(i, float(self.freqs[p]))
                        break

    def mk_center(self):
        m = self.markers[0]
        if m.enabled:
            self._set_range(m.freq - self.e_span.value() / 2, m.freq + self.e_span.value() / 2)

    def mk_range(self):
        a, b = self.markers[0], self.markers[1]
        if a.enabled and b.enabled and a.freq != b.freq:
            self._set_range(min(a.freq, b.freq), max(a.freq, b.freq))

    def mk_clear(self):
        for i in range(4):
            self.mtable.item(i, 0).setCheckState(Qt.CheckState.Unchecked)

    def peak_dclicked(self, row, _col):
        t = self.ptable.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if t:
            cur = max(0, self.mtable.currentRow())
            self._enable_marker(cur, float(t))

    def update_peaks(self):
        pk = find_peaks(self.live, 8)
        for r in range(8):
            a, b = self.ptable.item(r, 0), self.ptable.item(r, 1)
            if r < len(pk):
                i = pk[r]
                a.setText(fmt_freq(self.freqs[i]))
                a.setData(Qt.ItemDataRole.UserRole, float(self.freqs[i]))
                b.setText(self.lvl_txt(self.live[i]))
            else:
                a.setText("")
                a.setData(Qt.ItemDataRole.UserRole, None)
                b.setText("")

    # ----------------------------------------------------------- измерения ---
    def band_toggled(self, on):
        self.band.setVisible(on)
        if on and self.freqs is not None:
            a, b = self.freqs[0], self.freqs[-1]
            c, s = (a + b) / 2, (b - a)
            self.band.setRegion((c - s * 0.1, c + s * 0.1))
        self.update_measure()

    def update_measure(self, *_):
        if self.live is None or self.freqs is None:
            return
        self.m_floor.setText(self.lvl_txt(float(np.median(self.live))))
        if not self.chk_band.isChecked():
            for w in (self.m_pwr, self.m_peak, self.m_obw, self.m_mean):
                w.setText("—")
            return
        lo, hi = self.band.getRegion()
        mask = (self.freqs >= lo) & (self.freqs <= hi)
        if mask.sum() < 2:
            return
        f, y = self.freqs[mask], self.live[mask]
        p = 10 ** (y / 10)
        n = len(self.freqs)
        binw = (self.freqs[-1] - self.freqs[0]) / (n - 1)
        rbw = self.build_cfg().rbw * 1e3
        corr = binw / rbw if rbw > 0 else 1.0
        tot = float(p.sum()) * corr
        self.m_pwr.setText(self.lvl_txt(10 * np.log10(tot)))
        i = int(np.argmax(y))
        self.m_peak.setText(f"{self.lvl_txt(y[i])} @ {fmt_freq(f[i])}")
        cum = np.cumsum(p)
        i1 = int(np.searchsorted(cum, 0.005 * cum[-1]))
        i2 = min(int(np.searchsorted(cum, 0.995 * cum[-1])), len(f) - 1)
        self.m_obw.setText(fmt_freq(f[i2] - f[i1]))
        self.m_mean.setText(self.lvl_txt(10 * np.log10(p.mean())))

    def thr_spin_changed(self, v):
        self.thr_line.blockSignals(True)
        self.thr_line.setValue(v + self.unit_off)
        self.thr_line.blockSignals(False)

    def thr_line_moved(self):
        self.sp_thr.blockSignals(True)
        self.sp_thr.setValue(int(round(self.thr_line.value() - self.unit_off)))
        self.sp_thr.blockSignals(False)

    def check_threshold(self):
        if not self.chk_thr.isChecked():
            self.lbl_thr.setText("")
            return
        thr = self.sp_thr.value()
        i = int(np.argmax(self.live))
        if self.live[i] > thr:
            self.lbl_thr.setText(f"⚠ ПРЕВЫШЕНИЕ: {self.lvl_txt(self.live[i])} @ {fmt_freq(self.freqs[i])}")
            self.lbl_thr.setStyleSheet("color:#ef476f;font-weight:700")
            if self.chk_beep.isChecked() and time.monotonic() - self._last_beep > 1.5:
                self._last_beep = time.monotonic()
                QtWidgets.QApplication.beep()
        else:
            self.lbl_thr.setText("в норме")
            self.lbl_thr.setStyleSheet("color:#06d6a0")

    # ---------------------------------------------------------- мышь/курсор ---
    def on_mouse(self, evt):
        pos = evt[0]
        for pw in (self.pw1, self.pw2):
            if pw.sceneBoundingRect().contains(pos):
                p = pw.getPlotItem().vb.mapSceneToView(pos)
                txt = f"f = {fmt_freq(p.x())}"
                if pw is self.pw1:
                    txt += f"    {p.y():.1f} {'dBµV' if self.unit_off else 'dBm'}"
                self.lbl_cursor.setText(txt)
                return

    def on_click(self, ev, pw):
        if not ev.double() or ev.button() != Qt.MouseButton.LeftButton:
            return
        if not pw.sceneBoundingRect().contains(ev.scenePos()):
            return
        x = pw.getPlotItem().vb.mapSceneToView(ev.scenePos()).x()
        self._enable_marker(max(0, self.mtable.currentRow()), float(x))

    # ----------------------------------------------------------- генератор ---
    def _gen_cmds(self, with_mode=True):
        f = self.g_freq.value()
        cmds = []
        if with_mode:
            cmds.append(f"mode {'high' if f > 800e6 else 'low'} output")
        cmds += [f"freq {int(f)}", f"level {self.g_level.value():g}",
                 f"modulation {self.g_mod.currentText()}", "output on"]
        return cmds

    def gen_toggled(self, on):
        self.btn_gen.setText("Выключить выход" if on else "Включить выход")
        if not self.worker:
            return
        if on:
            self.btn_run.setChecked(False)
            self.btn_run.setEnabled(False)
            self.btn_single.setEnabled(False)
            self.worker.post("gen", self._gen_cmds(), True)
        else:
            self.worker.post("gen", ["output off"], False)
            self.btn_run.setEnabled(True)
            self.btn_single.setEnabled(True)

    def gen_apply(self):
        if self.worker and self.btn_gen.isChecked():
            self.worker.post("gen", self._gen_cmds(), True)

    # ------------------------------------------------------ запись / экспорт ---
    def rec_toggled(self, on):
        if on:
            p, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Файл записи", f"tinysa_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv", "CSV (*.csv)")
            if not p:
                self.btn_rec.setChecked(False)
                return
            self.log_f = open(p, "w", newline="", encoding="utf-8")
            self.log_w = csv.writer(self.log_f)
            self._log_header = False
            self.btn_rec.setText("■ Идёт запись")
        else:
            if self.log_f:
                self.log_f.close()
            self.log_f = self.log_w = None
            self.btn_rec.setText("● Запись")

    def log_sweep(self, freqs, lvl):
        if not self.log_w:
            return
        if not self._log_header:
            self.log_w.writerow(["time"] + [int(f) for f in freqs])
            self._log_header = True
        self.log_w.writerow([datetime.datetime.now().isoformat(timespec="milliseconds")]
                            + [f"{v:.2f}" for v in lvl])

    def save_csv(self):
        if self.freqs is None or self.maxh is None:
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Сохранить трассы", "tinysa_trace.csv", "CSV (*.csv)")
        if not p:
            return
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["freq_Hz", "live_dBm", "max_dBm", "min_dBm", "avg_dBm"])
            for row in zip(self.freqs, self.live, self.maxh, self.minh, self.avg):
                w.writerow([f"{row[0]:.0f}"] + [f"{v:.2f}" for v in row[1:]])

    def save_png(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Снимок", "tinysa_spectrum.png", "PNG (*.png)")
        if p:
            self.plot_split.grab().save(p)

    def save_wf(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Водопад", "tinysa_waterfall.png", "PNG (*.png)")
        if p:
            self.pw2.grab().save(p)

    # --------------------------------------------------- сохранение настроек ---
    def _widgets(self):
        return {"start": self.e_start, "stop": self.e_stop, "points": self.sp_points, "rbw": self.cb_rbw,
                "att": self.sp_att, "lna": self.chk_lna, "spur": self.chk_spur, "mode": self.cb_mode,
                "scale": self.sp_scale, "off": self.sp_off, "unit": self.cb_unit, "live": self.cb_live,
                "max": self.cb_max, "min": self.cb_min, "avg": self.cb_avg, "avgn": self.sp_avg,
                "yauto": self.chk_yauto, "ytop": self.sp_ytop, "ybot": self.sp_ybot, "wf": self.chk_wf,
                "cmap": self.cb_cmap, "hist": self.sp_hist, "wfauto": self.chk_wfauto,
                "wmin": self.sp_wmin, "wmax": self.sp_wmax, "thr": self.sp_thr, "thron": self.chk_thr,
                "beep": self.chk_beep, "gfreq": self.g_freq, "glevel": self.g_level}

    def collect_state(self) -> dict:
        out = {}
        for k, w in self._widgets().items():
            if isinstance(w, FreqEdit):
                out[k] = w.value()
            elif isinstance(w, QtWidgets.QCheckBox):
                out[k] = w.isChecked()
            elif isinstance(w, QtWidgets.QComboBox):
                out[k] = w.currentIndex()
            else:
                out[k] = w.value()
        return out

    def apply_state(self, st: dict):
        self._block = True
        for k, w in self._widgets().items():
            if k not in st:
                continue
            v = st[k]
            if isinstance(w, FreqEdit):
                w.setValue(v)
            elif isinstance(w, QtWidgets.QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QtWidgets.QComboBox):
                w.setCurrentIndex(int(v))
            else:
                w.setValue(v)
        self._block = False
        self.e_center.setValue((self.e_start.value() + self.e_stop.value()) / 2)
        self.e_span.setValue(self.e_stop.value() - self.e_start.value())
        self.apply_units()
        self.apply_cmap()
        self.pw2.setVisible(self.chk_wf.isChecked())
        self.thr_line.setVisible(self.chk_thr.isChecked())
        self.cfg_changed()

    def save_settings_file(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Настройки", "tinysa_settings.json", "JSON (*.json)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(self.collect_state(), fh, indent=2)

    def load_settings_file(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Настройки", "", "JSON (*.json)")
        if p:
            with open(p, encoding="utf-8") as fh:
                self.apply_state(json.load(fh))

    def _restore(self):
        try:
            st = json.loads(self.settings.value("state", "{}"))
            geo = self.settings.value("geometry")
            if geo:
                self.restoreGeometry(geo)
            self.apply_state(st)
        except Exception:
            pass

    def closeEvent(self, e):
        try:
            self.settings.setValue("state", json.dumps(self.collect_state()))
            self.settings.setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        if self.btn_rec.isChecked():
            self.btn_rec.setChecked(False)
        self.disconnect_dev()
        e.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
