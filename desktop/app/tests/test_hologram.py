from __future__ import annotations

import math

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from ultron_desktop.hologram import STATES, build_head, draw_scene, look_for, project


@pytest.fixture(scope="module")
def head():
    return build_head()


def render(
    head, state: str, t: float = 1.0, w: int = 640, h: int = 560, form: float | None = None
) -> np.ndarray:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    draw_scene(p, QRectF(0, 0, w, h), head, state, t, form)
    p.end()
    ptr = img.constBits()
    return np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4)[..., [2, 1, 0]].astype(float)


def test_the_head_is_real_3d_geometry(head):
    assert head.verts.shape[1] == 3 and len(head.verts) > 1000 and len(head.edges) > 2000
    assert np.isfinite(head.verts).all()
    ext = head.verts.max(axis=0) - head.verts.min(axis=0)
    assert (ext > 0.8).all()  # genuine extent in x, y AND z, not a flat sheet
    assert head.edges.min() >= 0 and head.edges.max() < len(head.verts)


def test_the_head_is_left_right_symmetric_and_the_face_looks_forward(head):
    v = head.verts
    assert abs(float(v[:, 0].mean())) < 0.02
    mirrored = np.sort(np.round(v[:, 0], 3))
    assert np.allclose(mirrored, np.sort(np.round(-v[:, 0], 3)), atol=2e-3)
    assert head.eyes[:, :, 2].min() > 0.3  # eyes are on the front (+z)
    assert head.eyes[0, :, 0].max() < 0 < head.eyes[1, :, 0].min()  # one each side of centre
    assert head.mouth[:, 2].min() > 0.3 and abs(float(head.mouth[:, 0].mean())) < 1e-6


def test_the_eyes_are_mirror_images_with_a_cold_slant(head):
    left, right = head.eyes
    assert np.allclose(left[:, 1], right[:, 1], atol=1e-9)  # same heights
    assert np.allclose(left[:, 0], -right[:, 0][::1], atol=2e-3) or np.allclose(
        np.sort(left[:, 0]), np.sort(-right[:, 0]), atol=2e-3
    )
    # outer end is higher than the inner end on each side (a glare, not a smile)
    for eye, side in ((left, -1.0), (right, 1.0)):
        outer = eye[np.argmax(eye[:, 0] * side)]
        inner = eye[np.argmin(eye[:, 0] * side)]
        assert outer[1] > inner[1]


def test_projection_rotates_in_depth_and_has_perspective():
    pts = np.array([[0.5, 0.0, 0.0], [0.0, 0.0, 0.5]])
    s0, z0 = project(pts, 0.0, 0.0, 0.0, 0.0, 100.0)
    s1, z1 = project(pts, math.pi / 2, 0.0, 0.0, 0.0, 100.0)
    assert s0[0, 0] > 40 and abs(s1[0, 0]) < 1  # the side point swings to the centre line
    assert z1[0] < -0.4 and z0[1] > 0.4  # and what faced us now faces away
    near, far = project(np.array([[0.5, 0, 1.0], [0.5, 0, -1.0]]), 0, 0, 0, 0, 100.0)[0]
    assert near[0] > far[0]  # nearer points are drawn larger: perspective


def test_every_state_renders_a_blue_hologram_not_a_blank_or_red_frame(head):
    for state in STATES:
        img = render(head, state)
        lit = img.sum(axis=2) > 120
        assert lit.mean() > 0.01, state  # something is drawn
        r, g, b = img[..., 0], img[..., 1], img[..., 2]
        assert b[lit].mean() > r[lit].mean() * 1.5, state  # blue dominates, never red
        assert b[lit].mean() > g[lit].mean(), state


def test_listening_is_brighter_than_off_and_speaking_moves_the_mouth(head):
    def bright(state: str) -> int:
        return int((render(head, state).max(axis=2) > 200).sum())  # pixels of hot highlight

    assert bright("muted") < bright("standby") * 0.85  # muted looks dimmer, not just labelled so
    assert bright("off") <= bright("standby")  # resting is calmer than waiting for the wake word
    assert bright("listening") > 3000  # the head forms with its eyes and contours lit
    a, b = render(head, "speaking", t=1.0), render(head, "speaking", t=1.13)
    h, w = a.shape[:2]
    mouth = (slice(int(h * 0.62), int(h * 0.82)), slice(int(w * 0.35), int(w * 0.65)))
    assert np.abs(a[mouth] - b[mouth]).mean() > 0.3  # the mouth grille animates while speaking
    still_a, still_b = render(head, "standby", t=1.0), render(head, "standby", t=1.0)
    assert np.array_equal(still_a, still_b)  # a frame is reproducible for a given time


def test_resting_head_slowly_turns_but_keeps_facing_the_viewer(head):
    for st in ("off", "standby"):
        for t in np.linspace(0, 40, 80):
            look = look_for(st, float(t))
            assert abs(look.spin * math.sin(t * 0.38)) < 0.65  # never beyond ~37 degrees


def test_unknown_state_falls_back_to_the_resting_look():
    assert look_for("???", 0.0) == look_for("off", 0.0)


# ---------------------------------------------------------------- the energy orb (rest form)
def test_the_orb_is_a_3d_sphere_of_rings_marks_and_debris():
    from ultron_desktop.hologram import ORB  # noqa: PLC0415

    # three great circles of unit radius in three different planes
    assert ORB.rings.shape[0] == 3
    for ring in ORB.rings:
        assert np.allclose(np.linalg.norm(ring, axis=1), 1.0, atol=1e-9)
    normals = [np.cross(r[10] - r[0], r[30] - r[0]) for r in ORB.rings]
    normals = [n / np.linalg.norm(n) for n in normals]
    for i in range(3):
        for j in range(i + 1, 3):
            assert abs(float(normals[i] @ normals[j])) < 1e-6  # mutually perpendicular
    # a band of fine concentric rings in one plane, growing in radius
    radii = [float(np.linalg.norm(r, axis=1).mean()) for r in ORB.band]
    assert len(ORB.band) >= 20 and radii == sorted(radii) and np.allclose(ORB.band[..., 1], 0)
    # code-like marks lie on a spherical shell and fill all three axes (not a flat sheet)
    r = np.linalg.norm(ORB.marks, axis=1)
    assert len(ORB.marks) > 400 and 0.75 < r.min() and r.max() < 1.1
    assert (ORB.marks.max(axis=0) - ORB.marks.min(axis=0) > 1.5).all()
    assert len(ORB.debris) > 20 and np.linalg.norm(ORB.debris[:, :3], axis=1).min() > 1.2
    assert np.isfinite(ORB.dust).all() and len(ORB.mark_len) == len(ORB.marks)


def test_horn_plates_are_symmetric_and_stand_clear_of_the_skull(head):
    left, right = head.horns[:3], head.horns[3:]
    assert np.allclose(left[..., 0], -right[..., 0], atol=2e-3)
    assert np.allclose(left[..., 1], right[..., 1]) and np.allclose(left[..., 2], right[..., 2])
    assert np.abs(head.horns[..., 0]).max() > np.abs(head.verts[:, 0]).max()  # flare outside


def test_rest_is_the_orb_and_awake_is_the_head(head):
    assert look_for("off", 0).form == look_for("standby", 0).form == look_for("muted", 0).form == 0
    assert look_for("listening", 0).form == look_for("speaking", 0).form == 1
    orb, awake = render(head, "standby"), render(head, "listening")
    assert np.abs(orb - awake).mean() > 5  # visibly a different figure
    # the orb is a centred, roughly round mass of light
    lit = orb.max(axis=2) > 150
    ys, xs = np.nonzero(lit)
    h, w = lit.shape
    assert abs(xs.mean() - w / 2) < 0.06 * w and 0.25 * h < ys.mean() < 0.55 * h
    assert 0.8 < (xs.max() - xs.min()) / (ys.max() - ys.min() + 1) < 1.6


def test_form_blends_smoothly_between_orb_and_head(head):
    frames = [render(head, "listening", form=f) for f in (0.0, 0.5, 1.0)]
    d01 = np.abs(frames[0] - frames[1]).mean()
    d12 = np.abs(frames[1] - frames[2]).mean()
    d02 = np.abs(frames[0] - frames[2]).mean()
    assert d01 > 1 and d12 > 1 and d02 > max(d01, d12) * 0.9  # monotone, no snap


def test_view_eases_between_forms_and_snaps_when_frozen(qapp):
    from ultron_desktop.hologram import HologramView  # noqa: PLC0415

    frozen = HologramView(animate=False)
    frozen.set_state("speaking")
    assert frozen.form == 1.0
    live = HologramView(animate=True)
    live.set_state("speaking")
    assert live.form == 0.0  # not instant: it draws in over about a second
    for _ in range(40):
        live._last -= 0.033  # pretend 33 ms passed
        live._tick()
    assert 0.9 < live.form <= 1.0
    live.set_state("standby")
    for _ in range(40):
        live._last -= 0.033
        live._tick()
    assert live.form < 0.1


def test_the_orb_core_fades_out_as_the_head_forms(head):
    def core(form: float) -> float:
        a = render(head, "listening", form=form)
        h, w = a.shape[:2]
        return float(a[h * 45 // 100 - 12 : h * 45 // 100 + 12, w // 2 - 12 : w // 2 + 12].mean())

    c = [core(f) for f in (0.0, 0.5, 0.7, 0.9, 1.0)]
    assert c[0] > 200 and c[-1] < 80  # a blazing core at rest, gone once the head has formed
    assert c[3] < 130  # 90 % of the way, the orb is a faint ghost (it fades, not just shrinks)
    assert all(a > b for a, b in zip(c[1:], c[2:], strict=False)) and c[1] < c[0] + 1


def test_the_first_frames_of_a_transition_are_partial_not_a_jump(qapp):
    from ultron_desktop.hologram import HologramView  # noqa: PLC0415

    live = HologramView(animate=True)
    live.set_state("speaking")
    live._last -= 0.033
    live._tick()
    assert 0.0 < live.form < 0.3  # one 33 ms frame moves it a little; it does not jump to 1


def test_frame_rate_settings_are_read_and_clamped(qapp, monkeypatch):
    from ultron_desktop.hologram import HologramView  # noqa: PLC0415

    monkeypatch.setenv("ULTRON_DESKTOP_REST_FPS", "10")
    monkeypatch.setenv("ULTRON_DESKTOP_ACTIVE_FPS", "9999")
    v = HologramView(animate=False)
    assert (v._rest_ms, v._active_ms) == (100, 17)  # 10 fps; 60 fps is the ceiling
    monkeypatch.setenv("ULTRON_DESKTOP_REST_FPS", "banana")
    assert HologramView(animate=False)._rest_ms == 71  # unparseable -> the default 14 fps
