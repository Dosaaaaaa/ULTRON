"""GPU choice: what gets set, what is never overwritten, and that bad input is not silent."""

from __future__ import annotations

from ultron_desktop import gpu


def test_nvidia_sets_the_offload_variables_and_names_the_card():
    label, env = gpu.plan("nvidia", nvidia_present=True, mesa_present=True)
    assert label == "NVIDIA"
    assert env["__NV_PRIME_RENDER_OFFLOAD"] == "1"
    assert env["__GLX_VENDOR_LIBRARY_NAME"] == "nvidia"
    assert env["__EGL_VENDOR_LIBRARY_FILENAMES"].endswith("10_nvidia.json")
    assert env["__VK_LAYER_NV_optimus"] == "NVIDIA_only"


def test_missing_driver_falls_back_to_automatic_and_says_so():
    label, env = gpu.plan("nvidia", nvidia_present=False, mesa_present=True)
    assert env == {} and "not found" in label and "automatic" in label
    label, env = gpu.plan("intel", nvidia_present=True, mesa_present=False)
    assert env == {} and "not found" in label


def test_intel_forces_mesa_and_auto_changes_nothing():
    label, env = gpu.plan("intel", True, True)
    assert label.startswith("Intel") and env["__EGL_VENDOR_LIBRARY_FILENAMES"].endswith(
        "50_mesa.json"
    )
    assert "__NV_PRIME_RENDER_OFFLOAD" not in env
    assert gpu.plan("auto", True, True)[1] == {}


def test_default_is_nvidia_and_exports_win(monkeypatch):
    monkeypatch.setattr(gpu.NVIDIA_DEVICE.__class__, "exists", lambda self: True)
    monkeypatch.setattr(gpu.NVIDIA_EGL.__class__, "is_file", lambda self: True)
    env: dict[str, str] = {"__GLX_VENDOR_LIBRARY_NAME": "mesa"}  # the user already chose this one
    label = gpu.apply(env)
    assert label == "NVIDIA"
    assert env["__NV_PRIME_RENDER_OFFLOAD"] == "1"
    assert env["__GLX_VENDOR_LIBRARY_NAME"] == "mesa"  # never overwritten


def test_env_selector_is_case_insensitive_and_a_typo_is_reported(capsys, monkeypatch):
    monkeypatch.setattr(gpu.NVIDIA_DEVICE.__class__, "exists", lambda self: True)
    monkeypatch.setattr(gpu.NVIDIA_EGL.__class__, "is_file", lambda self: True)
    assert gpu.apply({"ULTRON_GPU": " AUTO "}).startswith("automatic")
    env: dict[str, str] = {"ULTRON_GPU": "nvdia"}
    assert gpu.apply(env).startswith("automatic")
    assert "not one of" in capsys.readouterr().err
    assert "__NV_PRIME_RENDER_OFFLOAD" not in env  # a typo must not half-apply anything


def test_main_applies_it_before_creating_the_application():
    import inspect

    from ultron_desktop import main as entry

    src = inspect.getsource(entry.main)
    assert src.index("gpu.apply()") < src.index("QApplication(")


def test_apply_without_the_driver_sets_nothing(monkeypatch):
    monkeypatch.setattr(gpu.NVIDIA_DEVICE.__class__, "exists", lambda self: False)
    env: dict[str, str] = {}
    label = gpu.apply(env)
    assert env == {} and "not found" in label
