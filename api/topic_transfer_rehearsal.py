from __future__ import annotations

from typing import Callable, Protocol

from store.topic_transfer import Plan, TransferRefused, _apply_rehearsal


class PreparedAccount(Protocol):
    """Required reservation contract, not a production MySQL implementation."""

    def reserve(self, run: str, actor: str, target: str) -> None: ...
    def advance(self, run: str, actor: str, status: str) -> None: ...


def resume_rehearsal(store, account: PreparedAccount, plan: Plan, *, actor: str,
                     run_id: str, stamp: str, verify: Callable[[], bool]) -> str:
    """Executable synthetic saga; never exposed by HTTP or MCP.

    reserve must bind this run to a new, inaccessible team, suppress default packs,
    and retain source/target fences. advance must be durable and idempotent. A
    failed verifier leaves graph_committed for retry or isolated compensation.
    Real adapters, billing/readiness gates and durable fence release are pending.
    """
    if actor != plan.source:
        raise TransferRefused("actor_not_source_owner")
    if not plan.ready:
        raise TransferRefused("preflight_required")
    account.reserve(run_id, actor, plan.target)
    _apply_rehearsal(store, plan, run_id=run_id, stamp=stamp)
    account.advance(run_id, actor, "graph_committed")
    if not verify():
        raise TransferRefused("activation_verification_failed")
    account.advance(run_id, actor, "verified")
    account.advance(run_id, actor, "active")
    return "active"
