"""Tool registry. Every tool declares: name, description, input schema, permission level,
validation, timeout, how it is described to the human, and how its arguments are audited.

Nothing can run unless it is registered here; there is no generic "run command" tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ultron_agent.levels import Level


@dataclass(frozen=True)
class PathUse:
    """A file path a call intends to touch (for the policy engine's scope check)."""

    raw: str
    write: bool
    must_exist: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    level: Level
    timeout_s: float
    handler: Callable[[BaseModel, ToolContext], Any]
    # Human-readable one-liner shown in the approval dialog. Built from validated args.
    describe: Callable[[BaseModel], str]
    # Paths this call touches; the policy engine verifies each against the approved roots.
    paths: Callable[[BaseModel], list[PathUse]] = lambda a: []
    # Replace payloads with digests/sizes before logging.
    audit_args: Callable[[BaseModel], dict[str, Any]] = lambda a: a.model_dump()
    # For P3+: the exact text the human must type to confirm.
    confirm_text: Callable[[BaseModel], str] | None = None


@dataclass
class ToolContext:
    """What a handler may use. Handlers receive paths already scoped by the policy engine."""

    read_roots: list[Path]
    write_roots: list[Path]
    scoped: dict[str, Path] = field(default_factory=dict)  # raw path string -> resolved path
    roots_of: dict[str, Path] = field(default_factory=dict)  # raw path string -> its root
    apps: dict[str, Any] = field(default_factory=dict)
    trash_dir: Path | None = None
    deadline: float = 0.0  # time.monotonic() deadline for cooperative cancellation


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name}")
        if spec.level >= Level.P3 and spec.confirm_text is None:
            raise ValueError(f"{spec.name}: P3+ tools must define confirm_text")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name) if isinstance(name, str) else None

    def listing(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "level": t.level.label,
                "timeout_s": t.timeout_s,
                "input_schema": t.input_model.model_json_schema(),
            }
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]
