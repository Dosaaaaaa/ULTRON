"""Which GPU ULTRON's window renders on.

This machine has two: the Intel chip inside the CPU (what the desktop uses by default) and the
NVIDIA card. Without a hint, the embedded browser that draws the 3D orb lands on the Intel one.
`apply()` sets the standard per-process "render offload" environment variables, before Qt starts,
so only ULTRON's own process is steered. Nothing system-wide is changed.

    ULTRON_GPU=nvidia   (default) the NVIDIA card, if the driver is present; otherwise automatic
    ULTRON_GPU=intel    the Mesa/Intel driver
    ULTRON_GPU=auto     leave the choice to the desktop

Variables you already exported win: they are never overwritten.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

EGL_VENDORS = Path("/usr/share/glvnd/egl_vendor.d")
NVIDIA_EGL = EGL_VENDORS / "10_nvidia.json"
MESA_EGL = EGL_VENDORS / "50_mesa.json"
NVIDIA_DEVICE = Path("/dev/nvidia0")
CHOICES = ("nvidia", "intel", "auto")


def plan(pref: str, nvidia_present: bool, mesa_present: bool) -> tuple[str, dict[str, str]]:
    """The label to show and the environment to add. Pure, so it can be tested anywhere."""
    if pref == "nvidia":
        if not nvidia_present:
            return "automatic (NVIDIA driver not found, so ULTRON_GPU=nvidia was not applied)", {}
        return "NVIDIA", {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
            "__EGL_VENDOR_LIBRARY_FILENAMES": str(NVIDIA_EGL),
            "__VK_LAYER_NV_optimus": "NVIDIA_only",
        }
    if pref == "intel":
        if not mesa_present:
            return "automatic (Mesa driver not found, so ULTRON_GPU=intel was not applied)", {}
        return "Intel (Mesa)", {"__EGL_VENDOR_LIBRARY_FILENAMES": str(MESA_EGL)}
    return "automatic (chosen by the desktop)", {}


def apply(env: MutableMapping[str, str] | None = None) -> str:
    """Choose the GPU for this process. Call before QApplication (and so before QtWebEngine)."""
    env = os.environ if env is None else env
    pref = env.get("ULTRON_GPU", "nvidia").strip().lower()
    if pref not in CHOICES:
        print(f"ULTRON_GPU={pref!r} is not one of {CHOICES}; using automatic", file=sys.stderr)
        pref = "auto"
    label, additions = plan(
        pref, NVIDIA_DEVICE.exists() and NVIDIA_EGL.is_file(), MESA_EGL.is_file()
    )
    for key, value in additions.items():
        env.setdefault(key, value)  # an explicit export by the user wins
    return label
