"""Reads before a run and the explicit writes after one. None of these writes on its own.

The parameters are the LangGraph adapter's, so the same call sends the same body.
"""
from __future__ import annotations

from typing import Any, Iterable

from vinc.sdk import AsyncVincClient, VincClient
from vinc.sdk.package import DEFAULT_PACKAGE_MAX_CHARS
from vinc.sdk.prefetch import DEFAULT_PREFETCH_MAX_CHARS


def prefetch(client: VincClient, role_id: str, task: str, *, space: str | None = None,
             max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None) -> str:
    """The role's required reading and task evidence, read before a model call; never writes.

    With ``pin`` a role that changed raises ``VincRoleChanged`` instead.
    """
    return client.prefetch(role_id, task, space=space, max_chars=max_chars, pin=pin)


async def aprefetch(client: AsyncVincClient, role_id: str, task: str, *, space: str | None = None,
                    max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None) -> str:
    """Async :func:`prefetch`."""
    return await client.prefetch(role_id, task, space=space, max_chars=max_chars, pin=pin)


def get_vinc_package_context(client: VincClient, package_id: str, *, space: str,
                             max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
    """Read one handoff package, checking each source as this key on every call."""
    return client.get_package_context(package_id, space=space, max_chars=max_chars)


async def aget_vinc_package_context(client: AsyncVincClient, package_id: str, *, space: str,
                                   max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
    """Async :func:`get_vinc_package_context`; a missing package fails explicitly."""
    return await client.get_package_context(package_id, space=space, max_chars=max_chars)


def record_episode(client: VincClient, title: str, *, summary: str = "",
                   about: Iterable[str] = (), date: str | None = None,
                   domain: str | None = None, tags: Iterable[str] | None = None,
                   space: str | None = None, episode_id: str | None = None,
                   create_only: bool | None = None,
                   operation_id: str | None = None) -> dict[str, Any]:
    """Write one Episode about existing concepts or decisions (needs a ``vinc_sk_`` key).

    Call it after a person approved what the crew did. Save ``episode_id`` and
    ``operation_id`` in host state first and reuse them only for this event's retries.
    """
    return client.record_episode(title, summary=summary, about=about, date=date,
                                 domain=domain, tags=tags, space=space, episode_id=episode_id,
                                 create_only=create_only, operation_id=operation_id)


async def arecord_episode(client: AsyncVincClient, title: str, *, summary: str = "",
                          about: Iterable[str] = (), date: str | None = None,
                          domain: str | None = None, tags: Iterable[str] | None = None,
                          space: str | None = None, episode_id: str | None = None,
                          create_only: bool | None = None,
                          operation_id: str | None = None) -> dict[str, Any]:
    """Async :func:`record_episode`."""
    return await client.record_episode(title, summary=summary, about=about, date=date,
                                       domain=domain, tags=tags, space=space, episode_id=episode_id,
                                       create_only=create_only, operation_id=operation_id)


def record_run(client: VincClient, run_id: str, *, role: str, outcome: str,
               **fields: Any) -> dict[str, Any]:
    """Write one crew run as an Episode about its role (needs a ``vinc_sk_`` key).

    ``fields`` are ``VincClient.record_run``'s keyword arguments (``trace`` from
    ``vinc.trace_ref``, counts, ``cited_ids``, ``space`` ...). Call it after
    ``crew.kickoff()`` returns or raises; a retry of the same ``run_id`` merges.
    """
    return client.record_run(run_id, role=role, outcome=outcome, **fields)


async def arecord_run(client: AsyncVincClient, run_id: str, *, role: str, outcome: str,
                      **fields: Any) -> dict[str, Any]:
    """Async :func:`record_run`."""
    return await client.record_run(run_id, role=role, outcome=outcome, **fields)


def write_package(client: VincClient, question: str, conclusion: str, *,
                  claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                  space: str, package_id: str | None = None,
                  create_only: bool | None = None,
                  operation_id: str | None = None) -> dict[str, Any]:
    """Explicit producer write of one handoff package; call it after review."""
    return client.write_package(question, conclusion, claims=claims,
                                produced_by=produced_by, run_id=run_id,
                                space=space, package_id=package_id,
                                create_only=create_only, operation_id=operation_id)


async def awrite_package(client: AsyncVincClient, question: str, conclusion: str, *,
                         claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                         space: str, package_id: str | None = None,
                         create_only: bool | None = None,
                         operation_id: str | None = None) -> dict[str, Any]:
    """Async :func:`write_package`."""
    return await client.write_package(question, conclusion, claims=claims,
                                      produced_by=produced_by, run_id=run_id,
                                      space=space, package_id=package_id,
                                      create_only=create_only, operation_id=operation_id)
