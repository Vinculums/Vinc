"""Reads before a run and the explicit writes after one. None of these writes on its own.

Each helper passes the keyword arguments it does not name to the client method of the same
purpose, so the parameters are the client's and the LangGraph adapter's, and the same call
sends the same body.
"""
from __future__ import annotations

from typing import Any, Iterable

from vinc.sdk import AsyncVincClient, VincClient


def prefetch(client: VincClient, role_id: str, task: str, **fields: Any) -> str:
    """The role's required reading and task evidence, read before a model call; never writes.

    ``fields`` are ``VincClient.prefetch``'s keyword arguments (``space``, ``max_chars``,
    ``pin``). With ``pin`` a role that changed raises ``VincRoleChanged`` instead.
    """
    return client.prefetch(role_id, task, **fields)


async def aprefetch(client: AsyncVincClient, role_id: str, task: str, **fields: Any) -> str:
    """Async :func:`prefetch`."""
    return await client.prefetch(role_id, task, **fields)


def get_vinc_package_context(client: VincClient, package_id: str, *, space: str,
                             **fields: Any) -> str:
    """Read one handoff package, checking each source as this key on every call.

    ``fields`` are ``VincClient.get_package_context``'s other keyword arguments (``max_chars``).
    """
    return client.get_package_context(package_id, space=space, **fields)


async def aget_vinc_package_context(client: AsyncVincClient, package_id: str, *, space: str,
                                    **fields: Any) -> str:
    """Async :func:`get_vinc_package_context`; a missing package fails explicitly."""
    return await client.get_package_context(package_id, space=space, **fields)


def record_episode(client: VincClient, title: str, **fields: Any) -> dict[str, Any]:
    """Write one Episode about existing concepts or decisions (needs a ``vinc_sk_`` key).

    ``fields`` are ``VincClient.record_episode``'s keyword arguments (``summary``, ``about``,
    ``date``, ``domain``, ``tags``, ``space``, ``episode_id``, ``create_only``, ``operation_id``).
    Call it after a person approved what the crew did. Save ``episode_id`` and
    ``operation_id`` in host state first and reuse them only for this event's retries.
    """
    return client.record_episode(title, **fields)


async def arecord_episode(client: AsyncVincClient, title: str, **fields: Any) -> dict[str, Any]:
    """Async :func:`record_episode`."""
    return await client.record_episode(title, **fields)


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
                  space: str, **fields: Any) -> dict[str, Any]:
    """Explicit producer write of one handoff package; call it after review.

    ``fields`` are ``VincClient.write_package``'s other keyword arguments (``title``,
    ``package_id``, ``create_only``, ``operation_id``).
    """
    return client.write_package(question, conclusion, claims=claims, produced_by=produced_by,
                                run_id=run_id, space=space, **fields)


async def awrite_package(client: AsyncVincClient, question: str, conclusion: str, *,
                         claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                         space: str, **fields: Any) -> dict[str, Any]:
    """Async :func:`write_package`."""
    return await client.write_package(question, conclusion, claims=claims, produced_by=produced_by,
                                      run_id=run_id, space=space, **fields)
