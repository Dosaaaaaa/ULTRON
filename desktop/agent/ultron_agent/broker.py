"""The agent's core: one code path for every tool call.

    validate -> policy -> (approval) -> re-check policy -> execute with timeout -> audit

No caller (model relay, UI, CLI) can skip a step; the transport layer only moves messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ultron_agent.audit import AuditLog
from ultron_agent.config import AgentConfig
from ultron_agent.levels import Level
from ultron_agent.paths import PathDenied
from ultron_agent.policy import PolicyEngine, Verdict
from ultron_agent.registry import Registry, ToolContext, ToolSpec
from ultron_agent.tools import build_registry

MAX_PENDING = 8
_CODE = re.compile(r"^[a-z][a-z0-9_:]{0,40}$")


class AgentError(Exception):
    """Reported to the caller as {ok:false, error:{code, message}}."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code, self.message = code, message or code


@dataclass
class Approval:
    id: str
    tool: str
    level: Level
    summary: str
    args_preview: dict[str, Any]
    args_hash: str
    confirm_text: str | None
    requested_by: str
    created: float
    expires: float
    future: asyncio.Future = field(repr=False, default=None)  # type: ignore[assignment]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "level": self.level.label,
            "summary": self.summary,
            "args": self.args_preview,
            "confirmation_required": self.confirm_text,
            "requested_by": self.requested_by,
            "expires_in_s": max(0, round(self.expires - time.monotonic(), 1)),
        }


def _with_real_paths(summary: str, scoped: dict[str, Path]) -> str:
    """The policy resolves every path (symlinks, `..`) and the handler acts on the resolved one.
    The operator approves the action, so the dialog must name the file it will really touch."""
    real = [str(p) for raw, p in scoped.items() if str(p) != raw]
    return f"{summary}\nActs on: {'; '.join(real)}" if real else summary


def _preview(value: Any, limit: int = 600) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... ({len(value) - limit} more characters)"
    if isinstance(value, dict):
        return {k: _preview(v, limit) for k, v in value.items()}
    return value


def _hash_call(tool: str, args: BaseModel) -> str:
    body = json.dumps([tool, args.model_dump()], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _error_code(e: Exception) -> tuple[str, str]:
    """Only deliberate, coded messages are passed on; library messages may contain paths."""
    msg = str(e)
    coded = msg if _CODE.match(msg) else ""
    if isinstance(e, PathDenied):
        return "denied", f"path refused: {e}"
    if isinstance(e, FileExistsError):
        return "exists", coded or "destination already exists"
    if isinstance(e, FileNotFoundError):
        return coded or "not_found", "file or program not found"
    if isinstance(e, PermissionError):
        return "permission_error", "the operating system refused access"
    if isinstance(e, (LookupError, ValueError, RuntimeError)) and coded:
        return coded.split(":")[0], coded
    return "failed", type(e).__name__


class Agent:
    def __init__(
        self, cfg: AgentConfig, registry: Registry | None = None, audit: AuditLog | None = None
    ) -> None:
        self.cfg = cfg
        self.registry = registry or build_registry()
        self.audit = audit or AuditLog(cfg.audit_file)
        self.pending: dict[str, Approval] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._exec_lock = asyncio.Lock()  # one mutating operation at a time
        self.started = time.time()

    # ------------------------------------------------------------ events
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _emit(self, event: str, **data: Any) -> None:
        for q in list(self._subscribers):
            if q.full():
                q.get_nowait()
            q.put_nowait({"event": event, **data})

    # ------------------------------------------------------------ policy
    def _policy(self) -> PolicyEngine:
        return PolicyEngine(
            self.cfg.read_roots(), self.cfg.write_roots(), allow_p4=self.cfg.stored.allow_p4
        )

    def status(self) -> dict[str, Any]:
        return {
            "version": "0.1.0",
            "uptime_s": round(time.time() - self.started),
            "tools": len(self.registry.listing()),
            "pending_approvals": len(self.pending),
            **self.cfg.summary(),
        }

    # ------------------------------------------------------------ the one entry point
    async def call(self, tool: str, args: Any, *, source: str) -> dict[str, Any]:
        t0 = time.monotonic()
        rec: dict[str, Any] = {"source": source, "tool": tool if isinstance(tool, str) else "?"}
        try:
            result, rec = await self._call(tool, args, source, rec)
            rec["outcome"] = "ok"
            return {"ok": True, "tool": tool, "result": result, "decision": rec.get("decision")}
        except AgentError as e:
            rec.setdefault("decision", "rejected")
            rec["outcome"] = "error"
            rec["error_code"] = e.code
            raise
        except asyncio.CancelledError:  # the requester disconnected
            rec.setdefault("decision", "cancelled")
            rec["outcome"] = "cancelled"
            raise
        finally:
            rec["duration_ms"] = round((time.monotonic() - t0) * 1000)
            try:
                row = self.audit.write(**rec)
            except OSError:
                print("ultron-agent: could not write the audit record", file=sys.stderr)
            else:
                self._emit("audit", record=row)

    async def _call(
        self, tool: str, args: Any, source: str, rec: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        spec = self.registry.get(tool)
        if spec is None:
            rec["decision"] = "unknown_tool"
            raise AgentError("unknown_tool", "no such tool")
        rec["level"] = spec.level.label
        try:
            parsed = spec.input_model.model_validate(args if args is not None else {})
        except ValidationError as e:
            rec["decision"] = "invalid_arguments"
            fields = sorted({".".join(map(str, err["loc"])) or "(root)" for err in e.errors()})
            raise AgentError("invalid_arguments", "invalid: " + ", ".join(fields)) from None
        rec["args"] = spec.audit_args(parsed)

        decision = self._policy().evaluate(spec, parsed)
        rec["policy"] = decision.reason
        if decision.verdict is Verdict.DENY:
            rec["decision"] = "denied_by_policy"
            raise AgentError("denied", decision.reason)

        if decision.verdict is Verdict.ALLOW:
            rec["decision"] = "auto"
        else:
            try:
                approver, waited = await self._await_approval(spec, parsed, source, decision.scoped)
            except AgentError as e:
                rec["decision"] = "refused_by_operator" if e.code == "denied" else e.code
                raise
            rec["decision"] = "approved"
            rec["approver"], rec["approval_wait_ms"] = approver, waited
            decision = self._policy().evaluate(spec, parsed)  # state may have changed while waiting
            if decision.verdict is Verdict.DENY:
                rec["decision"] = "denied_by_policy_after_approval"
                raise AgentError("denied", decision.reason)

        if spec.level >= Level.P2:
            # Fail closed: a state-changing action is never taken if it cannot be recorded first.
            try:
                self.audit.write(
                    source=source,
                    tool=spec.name,
                    level=spec.level.label,
                    args=rec["args"],
                    decision=rec["decision"],
                    approver=rec.get("approver"),
                    outcome="intent",
                )
            except OSError:
                rec["decision"] = "audit_unavailable"
                raise AgentError(
                    "audit_unavailable", "audit log not writable; action refused"
                ) from None
        result = await self._execute(spec, parsed, decision.scoped, decision.roots)
        rec["result"] = self._result_summary(result)
        return result, rec

    # ------------------------------------------------------------ approval
    async def _await_approval(
        self, spec: ToolSpec, parsed: BaseModel, source: str, scoped: dict[str, Path]
    ):
        if len(self.pending) >= MAX_PENDING:
            raise AgentError("too_many_pending", "too many approvals are waiting")
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        ap = Approval(
            id=secrets.token_urlsafe(9),
            tool=spec.name,
            level=spec.level,
            summary=_with_real_paths(spec.describe(parsed), scoped),
            args_preview=_preview(parsed.model_dump()),
            args_hash=_hash_call(spec.name, parsed),
            confirm_text=spec.confirm_text(parsed) if spec.confirm_text else None,
            requested_by=source,
            created=now,
            expires=now + self.cfg.stored.approval_timeout_s,
            future=loop.create_future(),
        )
        self.pending[ap.id] = ap
        self._emit("approval_requested", approval=ap.public())
        try:
            approver = await asyncio.wait_for(ap.future, self.cfg.stored.approval_timeout_s)
        except TimeoutError:
            self._emit("approval_resolved", id=ap.id, outcome="timeout")
            raise AgentError("approval_timeout", "no decision was made in time") from None
        except asyncio.CancelledError:  # requester went away
            self._emit("approval_resolved", id=ap.id, outcome="cancelled")
            raise
        finally:
            self.pending.pop(ap.id, None)
        if approver is None:
            self._emit("approval_resolved", id=ap.id, outcome="denied")
            raise AgentError("denied", "the operator refused")
        self._emit("approval_resolved", id=ap.id, outcome="approved")
        return approver, round((time.monotonic() - now) * 1000)

    def decide(self, approval_id: str, approve: bool, confirmation: str | None, who: str) -> None:
        """Called only from the console socket."""
        ap = self.pending.get(approval_id)
        if ap is None or ap.future.done():
            raise AgentError("unknown_approval", "no such pending approval")
        if (
            approve
            and ap.confirm_text is not None
            and (confirmation or "").strip() != ap.confirm_text
        ):
            raise AgentError("confirmation_mismatch", "the confirmation text does not match")
        ap.future.set_result(who if approve else None)

    # ------------------------------------------------------------ execution
    async def _execute(
        self, spec: ToolSpec, parsed: BaseModel, scoped: dict[str, Path], roots: dict[str, Path]
    ) -> Any:
        # One mutating operation at a time. Waiting for the lock is not part of this call's own time
        # budget, and the lock is released only when the worker thread has really finished: a thread
        # cannot be killed, so after a timeout it may still be changing files.
        held = spec.level >= Level.P2
        if held:
            await self._exec_lock.acquire()
        try:
            ctx = ToolContext(
                read_roots=self.cfg.read_roots(),
                write_roots=self.cfg.write_roots(),
                scoped=scoped,
                roots_of=roots,
                apps=dict(self.cfg.stored.apps),
                trash_dir=self.cfg.trash_dir,
                deadline=time.monotonic() + spec.timeout_s,
            )
            work = asyncio.ensure_future(asyncio.to_thread(spec.handler, parsed, ctx))
        except BaseException:
            if held:
                self._exec_lock.release()
            raise

        def finished(f: asyncio.Future) -> None:
            if not f.cancelled():
                f.exception()  # mark retrieved: an abandoned (timed out) call must not warn
            if held:
                self._exec_lock.release()

        work.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(work), spec.timeout_s + 1)
        except TimeoutError:
            raise AgentError(
                "timeout", f"{spec.name} exceeded {spec.timeout_s:.0f}s (it may still finish)"
            ) from None
        except AgentError:
            raise
        except Exception as e:  # noqa: BLE001 - mapped to a coded error, never leaked raw
            code, msg = _error_code(e)
            raise AgentError(code, msg) from None

    @staticmethod
    def _result_summary(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return {
                k: v
                for k, v in result.items()
                if isinstance(v, (int, float, bool))
                or (isinstance(v, str) and len(v) <= 200 and k != "text")
            }
        return {}
