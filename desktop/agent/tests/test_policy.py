from __future__ import annotations

from pydantic import BaseModel

from ultron_agent.levels import Level
from ultron_agent.policy import PolicyEngine, Verdict
from ultron_agent.registry import PathUse, ToolSpec


class A(BaseModel):
    path: str = ""


def spec(level, paths=False, write=False):
    return ToolSpec(
        name="t",
        description="",
        input_model=A,
        level=level,
        timeout_s=1,
        handler=lambda a, c: None,
        describe=lambda a: "t",
        paths=(lambda a: [PathUse(a.path, write=write, must_exist=False)])
        if paths
        else (lambda a: []),
        confirm_text=(lambda a: "yes") if level >= Level.P3 else None,
    )


def test_levels_map_to_verdicts(ws):
    e = PolicyEngine([ws], [ws])
    assert e.evaluate(spec(Level.P0), A()).verdict is Verdict.ALLOW
    assert e.evaluate(spec(Level.P1), A()).verdict is Verdict.ALLOW
    assert e.evaluate(spec(Level.P2), A()).verdict is Verdict.APPROVE
    assert e.evaluate(spec(Level.P3), A()).verdict is Verdict.CONFIRM


def test_p4_denied_unless_enabled(ws):
    assert PolicyEngine([ws], [ws]).evaluate(spec(Level.P4), A()).verdict is Verdict.DENY
    assert (
        PolicyEngine([ws], [ws], allow_p4=True).evaluate(spec(Level.P4), A()).verdict
        is Verdict.CONFIRM
    )


def test_out_of_scope_path_is_denied_not_escalated_to_approval(ws, short_tmp):
    e = PolicyEngine([ws.resolve()], [ws.resolve()])
    d = e.evaluate(spec(Level.P2, paths=True, write=True), A(path=str(short_tmp / "x.txt")))
    assert d.verdict is Verdict.DENY  # no human is asked to approve an impossible action


def test_in_scope_path_is_returned_resolved(ws):
    e = PolicyEngine([ws.resolve()], [ws.resolve()])
    d = e.evaluate(spec(Level.P2, paths=True, write=True), A(path=str(ws / "n.txt")))
    assert d.verdict is Verdict.APPROVE and d.scoped[str(ws / "n.txt")] == ws.resolve() / "n.txt"
