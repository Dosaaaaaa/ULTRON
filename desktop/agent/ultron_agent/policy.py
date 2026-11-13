"""Independent policy engine. It never looks at model output or at who is asking nicely.

Input : a validated tool call.  Output: allow / needs_approval / needs_confirmation / deny.
Hard denials (out-of-scope or sensitive path, P4 while disabled) cannot be overridden by approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from ultron_agent.levels import MAX_AUTOMATIC, Level
from ultron_agent.paths import PathDenied, resolve_scoped
from ultron_agent.registry import ToolSpec


class Verdict(StrEnum):
    ALLOW = "allow"
    APPROVE = "needs_approval"  # P2: a human must approve the exact action
    CONFIRM = "needs_confirmation"  # P3/P4: a human must type the confirmation text
    DENY = "deny"


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    scoped: dict[str, Path] = field(default_factory=dict)
    roots: dict[str, Path] = field(default_factory=dict)


class PolicyEngine:
    def __init__(self, read_roots: list[Path], write_roots: list[Path], allow_p4: bool = False):
        self.read_roots = read_roots
        self.write_roots = write_roots
        self.allow_p4 = allow_p4

    def evaluate(self, spec: ToolSpec, args: BaseModel) -> Decision:
        scoped: dict[str, Path] = {}
        roots: dict[str, Path] = {}
        for use in spec.paths(args):
            try:
                s = resolve_scoped(
                    use.raw,
                    self.read_roots,
                    self.write_roots,
                    write=use.write,
                    must_exist=use.must_exist,
                )
            except PathDenied as e:
                return Decision(Verdict.DENY, f"path refused: {e}")
            scoped[use.raw] = s.path
            roots[use.raw] = s.root

        if spec.level >= Level.P4 and not self.allow_p4:
            return Decision(Verdict.DENY, "P4 actions are disabled")
        if spec.level <= MAX_AUTOMATIC:
            return Decision(Verdict.ALLOW, f"{spec.level.label}: automatic", scoped, roots)
        if spec.level == Level.P2:
            return Decision(Verdict.APPROVE, "P2: requires approval", scoped, roots)
        return Decision(
            Verdict.CONFIRM, f"{spec.level.label}: requires confirmation", scoped, roots
        )
