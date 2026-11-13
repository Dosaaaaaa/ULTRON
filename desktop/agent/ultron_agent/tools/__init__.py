"""Built-in tools. `build_registry()` is the single place tools become callable."""

from __future__ import annotations

from ultron_agent.registry import Registry
from ultron_agent.tools import apps, files, system


def build_registry() -> Registry:
    reg = Registry()
    for module in (system, apps, files):
        for spec in module.SPECS:
            reg.register(spec)
    return reg
