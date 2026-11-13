"""The resting face of ULTRON: a real 3D wireframe head, drawn as a digital-blue hologram.

The head is genuine 3D geometry (a shaped surface of ~2,000 vertices plus eyes, mouth bars, a crest
and a neck) that is rotated and perspective-projected on every frame, with depth-based brightness.
It is an original design: smooth angular skull, flat face plate, slanted eye slits, a grille mouth.
It is not a model or a still from any film.

`build_head()` is pure numpy (testable without Qt). `HologramView` paints it with QPainter, so it
runs everywhere, including headless test runs, with no GPU dependency.

The eyes and mouth react to the voice *state* (standby, listening, thinking, speaking). The mouth
animation is a pattern, not driven by the actual audio level.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

CORE = (53, 182, 255)  # digital blue
BRIGHT = (190, 236, 255)  # hot highlights
DEEP = (8, 40, 96)
BG_CENTER, BG_EDGE = (6, 22, 48), (2, 6, 16)

STATES = ("off", "starting", "standby", "listening", "thinking", "speaking", "muted")


def _gauss(x: np.ndarray | float, width: float) -> np.ndarray | float:
    return np.exp(-((x / width) ** 2))


def _spow(x: np.ndarray, p: float) -> np.ndarray:
    return np.sign(x) * np.abs(x) ** p


def surface(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Head surface. u: azimuth, 0 = straight ahead (+z), pi/2 = ULTRON's left (+x).
    v: 0 at the crown, pi at the chin. Returns (..., 3) as x right, y up, z forward."""
    s = np.sin(v)
    jaw = np.clip((v - 0.58 * np.pi) / (0.30 * np.pi), 0.0, 1.0)
    jaw = jaw * jaw * (3 - 2 * jaw)  # smoothstep: the face narrows toward the chin
    crown = np.clip((0.40 * np.pi - v) / (0.40 * np.pi), 0.0, 1.0)  # narrower, flatter top
    rho = np.abs(s) ** 0.80 * (1.0 - 0.55 * jaw) * (1.0 - 0.16 * crown)
    front = np.maximum(np.cos(u), 0.0)
    uw = (u + np.pi) % (2 * np.pi) - np.pi  # -pi..pi, so features mirror left and right
    x = 0.68 * rho * _spow(np.sin(u), 0.66)  # superellipse cross-section: squarer, machined
    z = 0.92 * rho * _spow(np.cos(u), 0.66)
    y = 1.20 * np.cos(v)
    z = z + 0.12 * _gauss(v - 0.39 * np.pi, 0.07) * front**3  # heavy brow plate
    for side in (-1.0, 1.0):  # eye sockets, set deep under the brow
        z = z - 0.09 * _gauss(v - 0.455 * np.pi, 0.07) * _gauss(uw - side * 0.50, 0.18)
    z = z - 0.04 * _gauss(v - 0.715 * np.pi, 0.08) * front**3  # recessed mouth plate
    x = x + 0.07 * _gauss(v - 0.56 * np.pi, 0.10) * np.sign(x) * _gauss(np.abs(uw) - 0.9, 0.4)
    return np.stack([x, y, z], axis=-1)


@dataclass(frozen=True)
class HeadModel:
    verts: np.ndarray  # (N, 3) surface vertices
    edges: np.ndarray  # (E, 2) index pairs
    eyes: np.ndarray  # (2, 4, 3) polygon corners, left then right
    mouth: np.ndarray  # (bars, 3) bar centres
    crest: np.ndarray  # (3, M, 3) three polylines over the top of the head
    neck: np.ndarray  # (R, K, 3) rings
    features: tuple  # bright polylines on the face: brow, cheeks, jaw, nose bridge
    horns: np.ndarray  # (2, K, 3) curved plates sweeping from the temples down past the cheeks
    radius: float


def build_head(nu: int = 44, nv: int = 30) -> HeadModel:
    u = np.linspace(0.0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(0.04 * np.pi, 0.93 * np.pi, nv)
    uu, vv = np.meshgrid(u, v)  # (nv, nu)
    verts = surface(uu, vv).reshape(-1, 3)
    idx = np.arange(nv * nu).reshape(nv, nu)
    around = np.stack([idx, np.roll(idx, -1, axis=1)], axis=-1).reshape(-1, 2)
    down = np.stack([idx[:-1], idx[1:]], axis=-1).reshape(-1, 2)
    edges = np.concatenate([around, down])

    def at(uc: float, vc: float, lift: float = 0.012) -> np.ndarray:
        p = surface(np.array(uc), np.array(vc))
        return p + np.array([0.0, 0.0, lift])

    eyes = np.zeros((2, 4, 3))
    for i, side in enumerate((-1.0, 1.0)):
        c = at(side * 0.50, 0.45 * np.pi, lift=0.035)
        # inner end low, outer end high: a cold, narrowed glare. side mirrors the shape.
        shape = np.array([[-0.14, -0.035], [0.16, 0.05], [0.16, 0.105], [-0.14, 0.015]])
        for k, (dx, dy) in enumerate(shape):
            eyes[i, k] = c + np.array([dx * side, dy, -0.45 * dx * dx])
    bars = 9
    xs = np.linspace(-0.26, 0.26, bars)
    centre = at(0.0, 0.715 * np.pi, lift=0.03)
    mouth = np.stack([centre + np.array([x, 0.0, -0.4 * x * x]) for x in xs])

    t = np.linspace(0.0, 1.0, 40)
    crest = np.zeros((3, 40, 3))
    for j, off in enumerate((-0.015, 0.0, 0.015)):
        # forehead -> crown -> back of the skull, lifted into a thin fin
        pts = []
        for tk in t:
            if tk < 0.5:
                uc, vk = 0.0, (0.26 - 0.20 * (tk / 0.5)) * np.pi + 0.04 * np.pi
            else:
                uc, vk = np.pi, (0.06 + 0.22 * ((tk - 0.5) / 0.5)) * np.pi + 0.04 * np.pi
            q = surface(np.array(uc), np.array(vk))
            n = q / (np.linalg.norm(q) or 1.0)
            pts.append(q + n * (0.07 * math.sin(math.pi * tk) ** 0.6 + 0.015) + [off, 0.0, 0.0])
        crest[j] = np.array(pts)

    def line(us: np.ndarray, vs: np.ndarray, lift: float = 0.02) -> np.ndarray:
        return surface(us, vs) + np.array([0.0, 0.0, lift])

    uu_f = np.linspace(-1.35, 1.35, 40)
    vv_f = np.linspace(0.1 * np.pi, 0.9 * np.pi, 40)
    features = (
        line(uu_f, np.full_like(uu_f, 0.385 * np.pi), 0.03),  # brow
        line(uu_f * 0.85, np.full_like(uu_f, 0.60 * np.pi)),  # cheekbone
        line(uu_f * 0.75, np.full_like(uu_f, 0.80 * np.pi)),  # jaw line
        line(np.zeros_like(vv_f), vv_f, 0.025),  # nose bridge / centre line
        line(np.full_like(vv_f, 0.95), vv_f),  # face plate edge, one side
        line(np.full_like(vv_f, -0.95), vv_f),  # face plate edge, other side
    )
    ring_y = np.linspace(-1.12, -1.62, 5)
    a = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    rad = np.linspace(0.40, 0.52, 5)
    neck = np.stack(
        [np.stack([rad[i] * np.sin(a), np.full_like(a, ring_y[i]), rad[i] * np.cos(a) * 0.9], -1)
         for i in range(5)]
    )  # fmt: skip
    horns = np.zeros((2, 3, 28, 3))  # two sides x three parallel bands
    tt = np.linspace(0.0, 1.0, 28)
    for i, side in enumerate((-1.0, 1.0)):
        for j, off in enumerate((-0.025, 0.0, 0.025)):
            # from above the temple, out and around, then in toward the jaw: a curved horn
            uc = side * (1.55 - 0.95 * tt)
            vc = (0.27 + 0.42 * tt) * np.pi
            q = surface(uc, vc)
            out = np.stack([side * np.ones_like(tt), 0.0 * tt, 0.0 * tt], -1)
            lift = (0.10 + 0.20 * np.sin(np.pi * tt) ** 0.8)[:, None]
            horns[i, j] = (
                q
                + out * lift
                + np.array([0.0, off, 0.0])
                + np.array([0.0, 0.0, 0.06]) * tt[:, None]
            )
    radius = float(np.linalg.norm(verts, axis=1).max())
    return HeadModel(
        verts, edges, eyes, mouth, crest, neck, features, horns.reshape(6, 28, 3), radius
    )


# ---------------------------------------------------------------- state -> look
@dataclass(frozen=True)
class Look:
    eyes: float  # 0..1 brightness
    mouth: float  # 0..1 how animated
    ripple: bool  # expanding rings: "I am listening"
    flicker: float  # eye flicker amount (thinking)
    spin: float  # how far it turns (0 = faces you)
    form: float  # 0 = the blue energy orb (rest), 1 = the head (awake and active)


def look_for(state: str, t: float) -> Look:
    breathe = 0.5 + 0.5 * math.sin(t * 1.6)
    if state == "listening":
        return Look(1.0, 0.0, True, 0.0, 0.25, 1.0)
    if state == "speaking":
        return Look(0.9, 1.0, False, 0.0, 0.18, 1.0)
    if state == "thinking":
        return Look(0.75, 0.0, False, 0.5, 0.45, 1.0)
    if state == "standby":
        return Look(0.42 + 0.16 * breathe, 0.0, False, 0.0, 0.55, 0.0)
    if state == "starting":
        return Look(0.3 + 0.5 * breathe, 0.0, False, 0.0, 0.55, 0.0)
    if state == "muted":
        return Look(0.14, 0.0, False, 0.0, 0.35, 0.0)
    return Look(0.20 + 0.08 * breathe, 0.0, False, 0.0, 0.6, 0.0)  # off: resting


def project(
    pts: np.ndarray, yaw: float, pitch: float, cx: float, cy: float, k: float, d: float = 4.4
) -> tuple[np.ndarray, np.ndarray]:
    """Rotate (yaw about the vertical axis, then pitch) and perspective-project to screen pixels.
    Returns (screen xy (..., 2), depth z' (...,)); larger z' = nearer the viewer."""
    cyw, syw, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, y, z = pts[..., 0], pts[..., 1], pts[..., 2]
    x1 = x * cyw + z * syw
    z1 = -x * syw + z * cyw
    y2 = y * cp - z1 * sp
    z2 = y * sp + z1 * cp
    s = d / (d - z2)
    return np.stack([cx + x1 * s * k, cy - y2 * s * k], axis=-1), z2


def _col(rgb: tuple[int, int, int], a: float) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], int(max(0.0, min(1.0, a)) * 255))


def bake_overlay(size: QSize) -> QImage:
    """Dark edges (vignette) and thin horizontal scanlines, transparent elsewhere."""
    img = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    q = QPainter(img)
    w, h = size.width(), size.height()
    vig = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.72)
    vig.setColorAt(0.0, QColor(0, 0, 0, 0))
    vig.setColorAt(0.55, QColor(0, 0, 0, 0))
    vig.setColorAt(1.0, QColor(0, 0, 0, 190))
    q.fillRect(0, 0, w, h, QBrush(vig))
    q.setPen(QPen(QColor(0, 0, 0, 38), 1.0))
    for y in range(0, h, 4):
        q.drawLine(0, y, w, y)
    q.end()
    return img


def _interval(name: str, default_fps: int) -> int:
    """Timer interval in ms from ULTRON_DESKTOP_<name> (frames per second, clamped to 1..60)."""
    try:
        fps = int(os.environ.get(f"ULTRON_DESKTOP_{name}", default_fps))
    except ValueError:
        fps = default_fps
    return round(1000 / max(1, min(60, fps)))


class HologramView(QWidget):
    """Paints the figure. `animate=False` freezes time so a frame is reproducible (tests, stills).

    CPU: QPainter software drawing is the cost. The scene is drawn at SCALE of the window size and
    scaled up (the glow style hides it), and the frame rate is lower at rest than when active.
    """

    SCALE = 0.55
    # Frame rates (override with ULTRON_DESKTOP_REST_FPS / ULTRON_DESKTOP_ACTIVE_FPS, 1..60).
    REST_FPS, ACTIVE_FPS = 14, 22

    def __init__(self, parent: QWidget | None = None, *, animate: bool = True) -> None:
        super().__init__(parent)
        self.model = build_head()
        self.state = "off"
        self.t = 0.0
        self.form = 0.0  # eased 0 (orb) .. 1 (head)
        self._animate = animate
        self._last = time.monotonic()
        self._buf: QImage | None = None
        self._overlay: QImage | None = None
        self._timer = QTimer(self)
        self._rest_ms, self._active_ms = _interval("REST_FPS", 14), _interval("ACTIVE_FPS", 22)
        self._timer.setInterval(self._rest_ms)
        self._timer.timeout.connect(self._tick)
        self.setMinimumSize(360, 360)
        self.setAutoFillBackground(False)

    def set_state(self, state: str) -> None:
        self.state = state if state in STATES else "off"
        target = look_for(self.state, self.t).form
        self._timer.setInterval(self._active_ms if target > 0 else self._rest_ms)
        if not self._animate:
            self.form = target  # no easing when frozen (tests, stills)
        self.update()

    # -- animation clock
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last, 0.1)
        self.t += dt
        self._last = now
        target = look_for(self.state, self.t).form
        self.form += (target - self.form) * min(1.0, dt * 2.4)  # the orb draws in over ~1 s
        self._timer.setInterval(
            self._active_ms if (target > 0 or self.form > 0.05) else self._rest_ms
        )
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        if self._animate:
            self._last = time.monotonic()
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()  # a hidden window draws nothing and costs nothing
        super().hideEvent(event)

    def _overlay_for(self, size: QSize) -> QImage:
        """Vignette plus faint scanlines, baked once per window size (a blit per frame)."""
        if self._overlay is None or self._overlay.size() != size:
            self._overlay = bake_overlay(size)
        return self._overlay

    # -- drawing
    def paintEvent(self, event) -> None:  # noqa: N802
        w, h = max(int(self.width() * self.SCALE), 1), max(int(self.height() * self.SCALE), 1)
        if self._buf is None or self._buf.size() != QSize(w, h):
            self._buf = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        self._buf.fill(0)
        q = QPainter(self._buf)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_scene(q, QRectF(0, 0, w, h), self.model, self.state, self.t, self.form)
        q.end()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.rect(), self._buf)
        p.drawImage(0, 0, self._overlay_for(self.size()))
        p.end()


# ---------------------------------------------------------------- the energy orb (rest form)
HOT = (205, 246, 255)  # near-white cyan: the hottest light
MID = (30, 120, 220)  # deeper blue for the fine rings


def _rotate(pts: np.ndarray, yaw: float, pitch: float) -> np.ndarray:
    cy_, sy_, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    x, y, z = pts[..., 0], pts[..., 1], pts[..., 2]
    x1, z1 = x * cy_ + z * sy_, -x * sy_ + z * cy_
    return np.stack([x1, y * cp - z1 * sp, y * sp + z1 * cp], axis=-1)


def _persp(pts: np.ndarray, cx: float, cy: float, k: float, d: float = 4.4) -> np.ndarray:
    s = d / (d - pts[..., 2])
    return np.stack([cx + pts[..., 0] * s * k, cy - pts[..., 1] * s * k], axis=-1)


def _poly(sc: np.ndarray) -> list[QPointF]:
    return [QPointF(x, y) for x, y in sc.tolist()]


@dataclass(frozen=True)
class OrbModel:
    rings: np.ndarray  # (3, P, 3) great circles of the shell, unit radius, each in its own plane
    band: np.ndarray  # (B, P, 3) concentric fine rings of the equatorial band
    marks: np.ndarray  # (M, 3) tiny code-like marks scattered over a spherical shell
    mark_len: np.ndarray  # (M,) their lengths, 0..1
    debris: np.ndarray  # (D, 4) drifting glowing debris: x, y, z, size
    dust: np.ndarray  # (U, 3) fine dust


def _circle(n: int, radius: float = 1.0) -> np.ndarray:
    a = np.linspace(0.0, 2 * np.pi, n)
    return np.stack([np.cos(a) * radius, np.sin(a) * radius, np.zeros_like(a)], -1)


def build_orb(seed: int = 5) -> OrbModel:
    rng = np.random.default_rng(seed)
    base = _circle(96)
    # three great circles: one equatorial-ish, two meridians turned relative to each other
    # three mutually perpendicular great circles: together they read as a sphere of rings
    rings = np.stack([
        base,  # x-y plane (faces the viewer at the start)
        _rotate(base, math.pi / 2, 0.0),  # y-z plane
        _rotate(base, 0.0, math.pi / 2),  # x-z plane
    ])  # fmt: skip
    band = np.stack(
        [_rotate(_circle(72, r), 0.0, math.pi / 2) for r in np.linspace(0.40, 1.20, 30)]
    )
    v = rng.normal(size=(520, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    marks = v * rng.uniform(0.80, 1.04, size=(520, 1))
    deb = rng.normal(size=(30, 3))
    deb = deb / np.linalg.norm(deb, axis=1, keepdims=True) * rng.uniform(1.35, 2.7, size=(30, 1))
    debris = np.concatenate([deb, rng.uniform(1.5, 4.5, size=(30, 1))], axis=1)
    dust = rng.normal(size=(110, 3))
    dust = dust / np.linalg.norm(dust, axis=1, keepdims=True) * rng.uniform(1.2, 3.4, size=(110, 1))
    return OrbModel(rings, band, marks, rng.random(520), debris, dust)


ORB = build_orb()


def draw_orb(
    p: QPainter, m: OrbModel, state: str, cx: float, cy: float, k: float, t: float
) -> None:
    """A blue holographic orb: a hot solid core, three thick glowing great-circle rings, a band of
    fine rings around the equator, a shell of tiny code-like marks and drifting debris."""
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)  # light adds up
    energy = {"listening": 1.0, "thinking": 1.0, "speaking": 1.0, "standby": 0.75,
              "starting": 0.8, "muted": 0.35}.get(state, 0.55)  # fmt: skip
    speed = {"thinking": 2.2, "listening": 1.4, "speaking": 1.2}.get(state, 1.0)
    pulse = 0.5 + 0.5 * math.sin(t * 1.5)
    beat = 0.5 + 0.5 * math.sin(t * 11.0) * math.sin(t * 2.3) if state == "speaking" else 0.0
    yaw, pitch = t * 0.30 * speed, 0.38 + 0.07 * math.sin(t * 0.21)

    # bloom behind everything
    bloom = QRadialGradient(QPointF(cx, cy), k * 2.2)
    bloom.setColorAt(0.0, _col(CORE, 0.30 * energy))
    bloom.setColorAt(0.5, _col(MID, 0.10 * energy))
    bloom.setColorAt(1.0, _col(MID, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bloom))
    p.drawEllipse(QPointF(cx, cy), k * 2.2, k * 2.2)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # equatorial band: many fine concentric rings (the plane slowly turns)
    band = _persp(_rotate(m.band, yaw * 0.5, pitch), cx, cy, k)
    for i, ring in enumerate(band):
        edge = i / (len(band) - 1)
        p.setPen(QPen(_col(MID if i % 3 else CORE, (0.20 + 0.25 * (1 - edge)) * energy), 1.0))
        p.drawPolyline(_poly(ring))

    # the three great circles, thick and glowing, each precessing at its own rate
    for i, ring in enumerate(m.rings):
        sc = _persp(_rotate(ring, yaw * (1.0 + 0.4 * i), pitch), cx, cy, k)
        poly = _poly(sc)
        p.setPen(QPen(_col(CORE, 0.14 * energy), 14.0))
        p.drawPolyline(poly)
        p.setPen(QPen(_col(CORE, 0.50 * energy), 6.0))
        p.drawPolyline(poly)
        p.setPen(QPen(_col(HOT, 0.95 * energy), 2.2))
        p.drawPolyline(poly)

    # shell of tiny code-like marks: short dashes, brighter on the near side
    rot = _rotate(m.marks, -yaw * 0.7, pitch)
    sc = _persp(rot, cx, cy, k)
    near = rot[:, 2]
    for lo, hi, al in ((-9.0, -0.15, 0.25), (-0.15, 0.4, 0.55), (0.4, 9.0, 0.95)):
        sel = np.where((near >= lo) & (near < hi))[0]
        if not len(sel):
            continue
        seg = [
            QLineF(x, y, x + 2.0 + 6.0 * n, y)
            for (x, y), n in zip(sc[sel].tolist(), m.mark_len[sel].tolist(), strict=True)
        ]
        p.setPen(QPen(_col(HOT if lo > 0.3 else CORE, al * energy), 1.3))
        p.drawLines(seg)

    # hot solid core
    core_r = 0.26 * (1.0 + 0.10 * pulse + 0.35 * beat) * k
    for rad, a0 in ((2.6, 0.30), (1.5, 0.65)):
        g = QRadialGradient(QPointF(cx, cy), core_r * rad)
        g.setColorAt(0.0, _col(HOT, a0 * (0.5 + 0.5 * energy)))
        g.setColorAt(0.6, _col(CORE, a0 * 0.45 * energy))
        g.setColorAt(1.0, _col(CORE, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), core_r * rad, core_r * rad)
    ball = QRadialGradient(QPointF(cx - core_r * 0.12, cy - core_r * 0.12), core_r * 0.75)
    ball.setColorAt(0.0, _col((235, 252, 255), 1.0))
    ball.setColorAt(0.65, _col((120, 214, 255), 0.95))
    ball.setColorAt(1.0, _col((40, 150, 240), 0.9))
    p.setBrush(QBrush(ball))
    p.drawEllipse(QPointF(cx, cy), core_r * 0.72, core_r * 0.72)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # drifting debris (glowing blobs) and dust
    deb = _rotate(m.debris[:, :3], yaw * 0.35, pitch * 0.6)
    dsc = _persp(deb, cx, cy, k)
    for (x, y), sz, z in zip(
        dsc.tolist(), m.debris[:, 3].tolist(), deb[:, 2].tolist(), strict=True
    ):
        a = (0.35 + 0.65 * (z + 2.7) / 5.4) * energy
        r = sz * (0.8 + 0.4 * pulse) * k / 300.0 * 3.0
        g = QRadialGradient(QPointF(x, y), r * 2.4)
        g.setColorAt(0.0, _col(HOT, 0.9 * a))
        g.setColorAt(0.35, _col(CORE, 0.45 * a))
        g.setColorAt(1.0, _col(CORE, 0.0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), r * 2.4, r * 2.4)
    p.setBrush(Qt.BrushStyle.NoBrush)
    dust = _persp(_rotate(m.dust, -yaw * 0.25 + t * 0.05, pitch * 0.5), cx, cy, k)
    p.setPen(QPen(_col(CORE, 0.55 * energy), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPoints(_poly(dust))

    # a scan ring sweeping out from the orb
    f = (t * 0.28) % 1.0
    p.setPen(QPen(_col(CORE, 0.40 * (1 - f) * energy), 1.4))
    p.drawEllipse(QPointF(cx, cy), k * (1.0 + 1.4 * f), k * (1.0 + 1.4 * f) * 0.30)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def draw_scene(
    p: QPainter, rect: QRectF, m: HeadModel, state: str, t: float, form: float | None = None
) -> None:
    """One frame. `form` 0..1 blends the energy orb (0) into the head (1); None = the state's."""
    w, h = rect.width(), rect.height()
    look = look_for(state, t)
    form = look.form if form is None else max(0.0, min(1.0, form))
    yaw = look.spin * math.sin(t * 0.38)
    pitch = 0.05 * math.sin(t * 0.27) - 0.04
    k = 0.27 * min(h * 1.05, w * 1.25)
    cx, cy = rect.center().x(), rect.top() + h * 0.45

    # background: deep navy with a soft blue bloom behind the figure
    bg = QRadialGradient(QPointF(cx, cy), max(w, h) * 0.75)
    bg.setColorAt(0.0, _col(BG_CENTER, 1))
    bg.setColorAt(1.0, _col(BG_EDGE, 1))
    p.fillRect(rect, QBrush(bg))
    _draw_floor(p, rect, cx, cy + k * 1.62, k, t)
    if look.ripple:
        _draw_ripples(p, cx, cy, k, t)

    ease = form * form * (3 - 2 * form)
    if ease < 0.995:  # the orb draws in and fades as the head forms
        p.setOpacity(1.0 - 0.9 * ease)
        draw_orb(p, ORB, state, cx, cy, k * (1.0 - 0.45 * ease), t)
    if ease > 0.005:
        p.setOpacity(ease)
        _draw_head(p, m, look, yaw, pitch, cx, cy, k * (0.7 + 0.3 * ease), t)
    p.setOpacity(1.0)
    _draw_scanline(p, rect, t)
    _draw_motes(p, rect, t)


def _draw_head(p, m, look, yaw, pitch, cx, cy, k, t) -> None:
    # --- project everything
    sv, zv = project(m.verts, yaw, pitch, cx, cy, k)
    scan_y = math.sin(t * 0.8) * 1.2
    bright = np.clip(0.5 + 0.5 * zv / 0.95, 0.0, 1.0)
    bright = np.clip(bright + 0.85 * _gauss(m.verts[:, 1] - scan_y, 0.07), 0.0, 1.0)

    # mesh: wide soft pass then a thin sharp pass; edges bucketed by depth so each pass is a batch
    a_i, b_i = m.edges[:, 0], m.edges[:, 1]
    eb = (bright[a_i] + bright[b_i]) * 0.5
    seg = np.concatenate([sv[a_i], sv[b_i]], axis=1)
    for lo, hi, alpha in (
        (0.0, 0.30, 0.06),
        (0.30, 0.55, 0.14),
        (0.55, 0.80, 0.30),
        (0.80, 1.01, 0.60),
    ):
        sel = seg[(eb >= lo) & (eb < hi)]
        if not len(sel):
            continue
        lines = _qlines(sel)
        col = BRIGHT if lo >= 0.8 else CORE
        p.setPen(QPen(_col(col, alpha * 0.22), 3.0))
        p.drawLines(lines)
        p.setPen(QPen(_col(col, alpha), 1.0))
        p.drawLines(lines)

    # vertices as points on the nearer side
    near = bright > 0.45
    p.setPen(QPen(_col(BRIGHT, 0.85), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPoints([QPointF(x, y) for x, y in sv[near & (bright > 0.7)]])
    p.setPen(QPen(_col(CORE, 0.55), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPoints([QPointF(x, y) for x, y in sv[near & (bright <= 0.7)]])

    # neck and crest
    for ring in m.neck:
        s2, z2 = project(ring, yaw, pitch, cx, cy, k)
        pts = [QPointF(x, y) for x, y in s2] + [QPointF(*s2[0])]
        p.setPen(QPen(_col(CORE, 0.30), 1.0))
        p.drawPolyline(pts)
    s2, _ = project(m.neck[0], yaw, pitch, cx, cy, k)
    s3, _ = project(m.neck[-1], yaw, pitch, cx, cy, k)
    p.setPen(QPen(_col(CORE, 0.22), 1.0))
    p.drawLines([_qline(np.concatenate([s2[i], s3[i]])) for i in range(0, len(s2), 3)])
    for line in m.crest:
        s2, _ = project(line, yaw, pitch, cx, cy, k)
        poly = [QPointF(x, y) for x, y in s2]
        p.setPen(QPen(_col(CORE, 0.25), 4.0))
        p.drawPolyline(poly)
        p.setPen(QPen(_col(BRIGHT, 0.9), 1.2))
        p.drawPolyline(poly)

    for band in m.horns:  # the curved horn plates
        s2, z2 = project(band, yaw, pitch, cx, cy, k)
        poly = [QPointF(float(x), float(y)) for x, y in s2]
        p.setPen(QPen(_col(CORE, 0.30), 4.5))
        p.drawPolyline(poly)
        p.setPen(QPen(_col(BRIGHT, 0.9), 1.3))
        p.drawPolyline(poly)
    for feat in m.features:  # bright contour lines that give the face its structure
        s2, z2 = project(feat, yaw, pitch, cx, cy, k)
        runs: list[list[QPointF]] = [[]]
        for (x, y), zz in zip(s2, z2, strict=True):
            if zz > -0.02:
                runs[-1].append(QPointF(float(x), float(y)))
            elif runs[-1]:
                runs.append([])
        for poly in runs:
            if len(poly) > 1:
                p.setPen(QPen(_col(CORE, 0.22), 4.0))
                p.drawPolyline(poly)
                p.setPen(QPen(_col(BRIGHT, 0.80), 1.3))
                p.drawPolyline(poly)
    _draw_mouth(p, m, look, yaw, pitch, cx, cy, k, t)
    _draw_eyes(p, m, look, yaw, pitch, cx, cy, k, t)


def _qline(r: np.ndarray):
    return QLineF(float(r[0]), float(r[1]), float(r[2]), float(r[3]))


def _qlines(seg: np.ndarray) -> list[QLineF]:
    """(N, 4) x1 y1 x2 y2 -> QLineF list. tolist() first: it is several times faster per item."""
    return [QLineF(a, b, c, d) for a, b, c, d in seg.tolist()]


def _draw_eyes(p, m, look, yaw, pitch, cx, cy, k, t) -> None:
    level = look.eyes
    if look.flicker:
        level *= 0.65 + 0.35 * (0.5 + 0.5 * math.sin(t * 23.0 + math.sin(t * 7.0) * 3.0))
    for eye in m.eyes:
        s, z = project(eye, yaw, pitch, cx, cy, k)
        if float(z.mean()) < -0.05:  # turned away: the face plate hides it
            continue
        poly = QPolygonF([QPointF(x, y) for x, y in s])
        centre = QPointF(float(s[:, 0].mean()), float(s[:, 1].mean()))
        glow = QRadialGradient(centre, k * 0.34)
        glow.setColorAt(0.0, _col(BRIGHT, 0.75 * level))
        glow.setColorAt(0.35, _col(CORE, 0.35 * level))
        glow.setColorAt(1.0, _col(CORE, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(centre, k * 0.34, k * 0.2)
        p.setBrush(_col(BRIGHT, 0.25 + 0.75 * level))
        p.setPen(QPen(_col(CORE, 0.9 * level + 0.1), 1.2))
        p.drawPolygon(poly)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _draw_mouth(p, m, look, yaw, pitch, cx, cy, k, t) -> None:
    n = len(m.mouth)
    for i, c in enumerate(m.mouth):
        wave = 0.5 + 0.5 * math.sin(t * 9.0 + i * 1.7 + math.sin(t * 3.1 + i) * 2.0)
        half = 0.03 + 0.085 * look.mouth * wave * (1.0 - 0.45 * abs(i - (n - 1) / 2) / (n / 2))
        top, bottom = c + np.array([0, half, 0]), c - np.array([0, half, 0])
        s, z = project(np.stack([top, bottom]), yaw, pitch, cx, cy, k)
        if float(z.mean()) < -0.05:
            continue
        lit = 0.6 + 0.4 * look.mouth
        p.setPen(QPen(_col(CORE, 0.25 * lit), 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(*s[0]), QPointF(*s[1]))
        p.setPen(QPen(_col(BRIGHT, 0.9 * lit), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(*s[0]), QPointF(*s[1]))


def _draw_floor(p, rect, cx, y0, k, t) -> None:
    """Hologram projector: concentric ellipses on the ground and a faint cone of light."""
    cone = QLinearGradient(QPointF(cx, y0), QPointF(cx, y0 - k * 2.8))
    cone.setColorAt(0.0, _col(CORE, 0.10))
    cone.setColorAt(1.0, _col(CORE, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(cone))
    base = [(-0.55, 0.0), (0.55, 0.0), (1.0, -2.8), (-1.0, -2.8)]
    p.drawPolygon(QPolygonF([QPointF(cx + a * k, y0 + b * k) for a, b in base]))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i, r in enumerate((0.55, 0.85, 1.2, 1.7)):
        pulse = 0.5 + 0.5 * math.sin(t * 1.2 - i * 0.9)
        p.setPen(QPen(_col(CORE, 0.12 + 0.28 * pulse * (1 - i * 0.18)), 1.2))
        p.drawEllipse(QPointF(cx, y0), k * r, k * r * 0.22)
    p.setPen(QPen(_col(CORE, 0.10), 1.0))
    for a in np.linspace(0, 2 * np.pi, 16, endpoint=False):
        p.drawLine(QPointF(cx + math.cos(a) * k * 0.55, y0 + math.sin(a) * k * 0.12),
                   QPointF(cx + math.cos(a) * k * 1.7, y0 + math.sin(a) * k * 0.37))  # fmt: skip


def _draw_ripples(p, cx, cy, k, t) -> None:
    for i in range(3):
        f = ((t * 0.55) + i / 3.0) % 1.0
        r = k * (1.35 + 1.1 * f)
        p.setPen(QPen(_col(CORE, 0.45 * (1.0 - f)), 1.6))
        p.drawEllipse(QPointF(cx, cy), r, r)


def _draw_scanline(p, rect, t) -> None:
    y = rect.top() + (t * 0.12 % 1.0) * rect.height()
    band = QLinearGradient(QPointF(0, y - 18), QPointF(0, y + 18))
    band.setColorAt(0.0, _col(CORE, 0.0))
    band.setColorAt(0.5, _col(CORE, 0.07))
    band.setColorAt(1.0, _col(CORE, 0.0))
    p.fillRect(QRectF(rect.left(), y - 18, rect.width(), 36), QBrush(band))


def _draw_motes(p, rect, t) -> None:
    rng = np.random.default_rng(11)
    xs, ys, sp, ph = rng.random(48), rng.random(48), 0.02 + 0.05 * rng.random(48), rng.random(48)
    p.setPen(QPen(_col(CORE, 0.45), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    pts = []
    for x, y, s, f in zip(xs, ys, sp, ph, strict=True):
        yy = (y - t * s) % 1.0
        pts.append(QPointF(rect.left() + (x + 0.02 * math.sin(t + f * 6.28)) * rect.width(),
                           rect.top() + yy * rect.height()))  # fmt: skip
    p.drawPoints(pts)
