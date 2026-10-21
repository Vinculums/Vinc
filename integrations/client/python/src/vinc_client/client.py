"""Synchronous and asynchronous clients for the Vinc REST v1 API.

Both read a member key (``vinc_sk_`` full, ``vinc_ro_`` read only) and act on
one space: the personal graph, or a team by its id. Nothing here writes on its
own; ``ingest``, ``record_episode``, ``write_package`` and the delete and merge
wrappers are explicit calls.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import httpx

from . import __version__
from .context import DEFAULT_MAX_CHARS, format_context
from .episode import build_episode_fragment, check_about
from .errors import (VincAuthError, VincError, VincInvalidArgument, VincPartialWrite,
                     VincNotFound, VincQuotaExceeded, VincRoleChanged, VincUnavailable,
                     error_class)
from .package import (DEFAULT_PACKAGE_MAX_CHARS, build_package_fragment, format_package,
                      package_space, validate_package)
from .prefetch import (DEFAULT_PREFETCH_MAX_CHARS, PREFETCH_READ_CONCURRENCY, RolePrefetch,
                       cite_id, document_key, reading_list, required_context, required_reading,
                       with_citation, _with_task_evidence)

log = logging.getLogger("vinc_client")

DEFAULT_BASE_URL = "https://mcp.vincs.io"
KEY_PREFIXES = ("vinc_sk_", "vinc_ro_")
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
#: 브리프가 한 노드로 못 좁힌 두 경우. 자연어 질문은 동점(choose)이 흔하다(라이브 실측).
_WIDEN = ("none", "choose")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: 역할 고정은 role()['hash'] 그대로다: 소문자 16진수 64자.
_PIN = re.compile(r"^[0-9a-f]{64}$")
#: 일시 오류를 다시 보내는 기본 상한(초). 허브 배포 중 요청을 붙잡는 상한과 같다.
DEFAULT_RETRY_FOR_S = 90.0
_TRANSIENT_STATUS = frozenset({502, 503, 504})
#: 본문이 질의일 뿐인 POST. 다시 보내도 그래프가 바뀌지 않는다.
_READ_POSTS = frozenset({"/v1/brief", "/v1/search", "/v1/edges/query"})
_jitter = random.random
_clock = time.monotonic
_sleep = time.sleep
_asleep = asyncio.sleep


def resolve_key(api_key: str | None) -> str:
    key = (api_key or os.environ.get("VINC_API_KEY") or "").strip()
    if not key:
        raise VincAuthError("not_authenticated", "Pass api_key or set VINC_API_KEY.")
    if not key.startswith(KEY_PREFIXES):
        raise VincAuthError("not_authenticated",
                            "A Vinc member key starts with vinc_sk_ or vinc_ro_.")
    return key


def resolve_base_url(base_url: str | None) -> str:
    base = (base_url or os.environ.get("VINC_API_URL") or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(base)
    # http 는 루프백만 — 키가 평문으로 네트워크를 지나지 않게. CLI 와 같은 규칙.
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in _LOOPBACK):
        return base
    raise VincInvalidArgument("invalid_argument",
                              "The API URL must use https (http is allowed only for localhost).")


def normalize_space(space: str | None) -> str:
    value = (space or "personal").strip().lower()
    if value != "personal" and not _UUID.fullmatch(value):
        raise ValueError("space must be 'personal' or a team id (a UUID)")
    return value


def _segment(value: str) -> str:
    # `.`·`..` 는 경로 정규화가 되감는 조각이라 그대로 보내면 다른 라우트로 간다.
    text = quote(str(value), safe="")
    return text.replace(".", "%2E") if text in (".", "..") else text


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


@dataclass
class _Call:
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] | None = None
    space: str | None = None


def _replayable(call: _Call) -> bool:
    """다시 보내도 결과가 한 번 보낸 것과 같은가.

    읽기(GET·질의 POST·역할 출력 검사)와 미리보기(dry_run)는 늘 그렇다. 쓰기는
    ``operation_id`` 가 있을 때만이다: 서버가 같은 작업을 한 번만 적용한다."""
    if call.method == "GET" or call.path in _READ_POSTS:
        return True
    if call.method == "POST" and call.path.startswith("/v1/roles/") and call.path.endswith("/check"):
        return True
    body = call.body or {}
    if body.get("dry_run") or call.params.get("dry_run"):
        return True
    return bool(body.get("operation_id") or call.params.get("operation_id"))


def _retry_after(response: httpx.Response, details: dict[str, Any]) -> float | None:
    raw = response.headers.get("Retry-After") or details.get("retry_after_s")
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse(response: httpx.Response) -> dict[str, Any]:
    status = response.status_code
    if 300 <= status < 400:
        # 리다이렉트를 따라가면 Authorization 이 그 주소로 가고, POST 쓰기가 본문 없는
        # GET 으로 바뀌어 성공처럼 보인다. 따라가지 않고 실패로 말한다.
        raise VincUnavailable("upstream_unavailable",
                              f"The API answered a redirect (HTTP {status}); it was not followed.",
                              status=status)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if 200 <= status < 300:
        if not isinstance(payload, dict):
            raise VincUnavailable("upstream_unavailable", "The API returned invalid JSON.",
                                  status=status)
        return payload
    raw = payload.get("error") if isinstance(payload, dict) else None
    error: dict[str, Any] = raw if isinstance(raw, dict) else {
        "code": "upstream_unavailable" if status >= 500 else "http_error",
        "message": f"HTTP {status}"}
    raw_details = error.get("details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    code = str(error.get("code") or "http_error")
    raise error_class(status, code)(
        code, str(error.get("message") or ""),
        status=status, request_id=str(error.get("request_id") or
                                      response.headers.get("Vinc-Request-Id") or ""),
        details=details, retry_after=_retry_after(response, details))


def _check_pin(pin: str | None) -> None:
    if pin is not None and (not isinstance(pin, str) or not _PIN.fullmatch(pin)):
        raise VincInvalidArgument("invalid_argument",
                                  "pin must be the 64 character hash from role()['hash'].")


def _pinned(contract: dict[str, Any], pin: str | None) -> dict[str, Any]:
    """서버가 고정을 확인한 계약만 넘긴다. 해시가 없거나 다르면(고정을 모르는 허브 포함) 멈춘다."""
    if pin is not None and contract.get("hash") != pin:
        raise VincRoleChanged("role_changed", "The role does not match the pinned hash.",
                              details={k: contract.get(k) for k in ("id", "rev", "hash")})
    return contract


def _raise_if_partial(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("partial"):
        raise VincPartialWrite("The write was accepted only in part; read result['warnings'].",
                               result)
    return result


class _Base:
    def __init__(self, api_key: str | None = None, *, base_url: str | None = None,
                 space: str | None = None, timeout: float = 30.0,
                 prefetch_concurrency: int = PREFETCH_READ_CONCURRENCY,
                 retry_for: float = DEFAULT_RETRY_FOR_S) -> None:
        if (isinstance(prefetch_concurrency, bool) or not isinstance(prefetch_concurrency, int)
                or not 1 <= prefetch_concurrency <= PREFETCH_READ_CONCURRENCY):
            raise VincInvalidArgument("invalid_argument", "prefetch_concurrency must be an integer from 1 to 8.")
        self._prefetch_concurrency = prefetch_concurrency
        self._key = resolve_key(api_key)
        self.base_url = resolve_base_url(base_url)
        self.space = normalize_space(space)
        self.timeout = timeout
        if isinstance(retry_for, bool) or not isinstance(retry_for, (int, float)) or retry_for < 0:
            raise VincInvalidArgument("invalid_argument", "retry_for must be a number of seconds, 0 or more.")
        self.retry_for = float(retry_for)

    @property
    def prefetch_concurrency(self) -> int:
        """Maximum concurrent reads in one prefetch call; 1 preserves sequential reads."""
        return self._prefetch_concurrency

    @property
    def read_only(self) -> bool:
        """True for a ``vinc_ro_`` key: every write is refused before it is sent."""
        return self._key.startswith("vinc_ro_")

    @property
    def context_cache_identity(self) -> tuple[str, str, bytes]:
        """Non-secret fingerprint of the effective transport reader and default space."""
        auth = self._http.headers.get("Authorization", "")
        return (str(self._http.base_url), normalize_space(self.space),
                hashlib.sha256(auth.encode("utf-8")).digest())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Accept": "application/json",
                "User-Agent": f"vinc-client/{__version__}"}

    def _request_args(self, call: _Call) -> dict[str, Any]:
        space = normalize_space(call.space) if call.space is not None else self.space
        params = {k: (",".join(v) if isinstance(v, (list, tuple)) else
                      ("true" if v else "false") if isinstance(v, bool) else v)
                  for k, v in call.params.items() if v is not None}
        if space != "personal":
            params["space"] = space
        args: dict[str, Any] = {"method": call.method, "url": call.path, "params": params}
        if call.body is not None:
            args["json"] = call.body
        return args

    def _retry_delay(self, call: _Call, exc: VincError, attempt: int, elapsed: float) -> float | None:
        """다시 보내기 전에 기다릴 초, 또는 None(오류를 그대로 올린다).

        일시 오류만 다시 보낸다: 502·503·504, 연결 실패, 시간 초과. 서버가
        ``retryable: false`` 라고 한 것, 할당량, 인증, 그 밖의 응답은 다시 보내도 같다.
        ``Retry-After`` 가 있으면 그것을 따르고, 없으면 지수 백오프(최대 8초)에 흔들림을
        섞는다. 첫 시도부터 ``retry_for`` 초를 넘기게 되면 멈춘다."""
        if self.retry_for <= 0 or not isinstance(exc, VincUnavailable) or not _replayable(call):
            return None
        if exc.status is not None and exc.status not in _TRANSIENT_STATUS:
            return None
        if exc.details.get("retryable") is False:
            return None
        backoff = min(8.0, 0.5 * 2 ** (attempt - 1)) * (0.5 + _jitter() / 2)
        delay = exc.retry_after if exc.retry_after is not None else backoff
        return delay if elapsed + delay <= self.retry_for else None

    def without_retries(self):
        """The same client (same connection, key and space) with transient retries turned off.

        For code that runs inside a model turn, where waiting for the hub would hold up the
        conversation: ``get_context``, ``prefetch`` and the framework adapters' tools use it.
        Close the original, not this view; they share one connection.
        """
        view = copy.copy(self)
        view.retry_for = 0.0
        return view

    def _require_write_key(self) -> None:
        if self.read_only:
            raise VincAuthError("read_only_key",
                                "This is a read-only key (vinc_ro_); writing needs a vinc_sk_ key.")

    # ── 라우트 한 줄씩. 경로·필드 이름은 vinc_mcp/openapi_v1.json 과 대조된다 ──
    @staticmethod
    def _whoami(space) -> _Call:
        return _Call("GET", "/v1/me", space=space)

    @staticmethod
    def _brief(query, max_excerpts, historical, space, mode=None, as_of=None, known_at=None) -> _Call:
        return _Call("POST", "/v1/brief", body=_drop_none(
            {"query": query, "max_excerpts": max_excerpts, "historical": historical,
             "mode": mode, "as_of": as_of, "known_at": known_at}), space=space)

    @staticmethod
    def _node(node_id, space) -> _Call:
        return _Call("GET", f"/v1/nodes/{_segment(node_id)}", space=space)

    @staticmethod
    def _document(doc_id, space) -> _Call:
        return _Call("GET", f"/v1/documents/{_segment(doc_id)}", space=space)

    @staticmethod
    def _role(role_id, space, pin=None) -> _Call:
        _check_pin(pin)
        return _Call("GET", f"/v1/roles/{_segment(role_id)}", params={"expected_hash": pin},
                     space=space)

    @staticmethod
    def _search(query, limit, cursor, detail, historical, space, mode=None, as_of=None, known_at=None) -> _Call:
        return _Call("POST", "/v1/search", body=_drop_none(
            {"query": query, "limit": limit, "cursor": cursor, "detail": detail,
             "historical": historical, "mode": mode, "as_of": as_of, "known_at": known_at}), space=space)

    @staticmethod
    def _recall(domain, tags, limit, cursor, space) -> _Call:
        return _Call("GET", "/v1/episodes", params={
            "domain": domain, "tags": list(tags) if tags else None,
            "limit": limit, "cursor": cursor}, space=space)

    @staticmethod
    def _ingest(nodes, edges, space, create_only=None, operation_id=None, review_claims=None) -> _Call:
        return _Call("POST", "/v1/fragments",
                     body=_drop_none({"nodes": list(nodes), "edges": list(edges or []),
                         "create_only": create_only, "operation_id": operation_id,
                         "review_claims": review_claims}), space=space)

    @staticmethod
    def _operation_status(operation_id, space) -> _Call:
        return _Call("GET", "/v1/operations/" + quote(operation_id, safe=""), space=space)

    @staticmethod
    def _edges(node, nodes, type, dir, limit, space) -> _Call:
        # node 하나는 GET, 여러 개(nodes)는 POST. 둘 다 주거나 둘 다 빼면 보내기 전에 거절한다.
        if (node is None) == (nodes is None):
            raise VincInvalidArgument("invalid_argument", "Pass exactly one of node or nodes.")
        if node is not None:
            return _Call("GET", f"/v1/nodes/{_segment(node)}/edges",
                         params={"type": type, "dir": dir, "limit": limit}, space=space)
        return _Call("POST", "/v1/edges/query", body=_drop_none(
            {"nodes": list(nodes), "type": type, "dir": dir, "limit": limit}), space=space)

    @staticmethod
    def _role_check(role_id, output, person_only_requests, self_authored, space) -> _Call:
        return _Call("POST", f"/v1/roles/{_segment(role_id)}/check", body=_drop_none(
            {"output": output, "self_authored": self_authored,
             "person_only_requests": (list(person_only_requests)
                                      if person_only_requests is not None else None)}),
            space=space)

    # 삭제·병합 4종: dry_run=True 는 미리보기(preview_digest 포함)를 돌려주고 아무것도 바꾸지 않는다.
    # 적용 호출에 그 preview_digest 를 실으면 미리본 뒤 상태가 바뀐 경우 서버가 거절한다.
    @staticmethod
    def _preview_options(dry_run, preview_digest, operation_id) -> dict[str, Any]:
        return {"dry_run": True if dry_run else None, "preview_digest": preview_digest,
                "operation_id": operation_id}

    @staticmethod
    def _delete(node_id, dry_run, preview_digest, operation_id, space) -> _Call:
        return _Call("DELETE", f"/v1/nodes/{_segment(node_id)}",
                     params=_Base._preview_options(dry_run, preview_digest, operation_id), space=space)

    @staticmethod
    def _delete_edge(from_id, to_id, type, dry_run, preview_digest, operation_id, space) -> _Call:
        return _Call("DELETE", "/v1/edges", params={
            "from_id": from_id, "to_id": to_id, "type": type,
            **_Base._preview_options(dry_run, preview_digest, operation_id)}, space=space)

    @staticmethod
    def _merge_nodes(keep_id, drop_id, dry_run, preview_digest, operation_id, space) -> _Call:
        return _Call("POST", "/v1/nodes/merge", body=_drop_none({
            "keep_id": keep_id, "drop_id": drop_id,
            **_Base._preview_options(dry_run, preview_digest, operation_id)}), space=space)

    @staticmethod
    def _doc_delete(doc_id, dry_run, preview_digest, operation_id, space) -> _Call:
        return _Call("DELETE", f"/v1/documents/{_segment(doc_id)}",
                     params=_Base._preview_options(dry_run, preview_digest, operation_id), space=space)


class VincClient(_Base):
    """Blocking client. Use as a context manager or call ``close()``."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None,
                 space: str | None = None, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None,
                 prefetch_concurrency: int = PREFETCH_READ_CONCURRENCY,
                 retry_for: float = DEFAULT_RETRY_FOR_S) -> None:
        super().__init__(api_key, base_url=base_url, space=space, timeout=timeout,
                         prefetch_concurrency=prefetch_concurrency, retry_for=retry_for)
        self._http = httpx.Client(base_url=self.base_url, headers=self._headers(),
                                  timeout=timeout, follow_redirects=False, transport=transport)

    def __enter__(self) -> "VincClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _send(self, call: _Call) -> dict[str, Any]:
        started, attempt = _clock(), 0
        while True:
            attempt += 1
            try:
                return self._send_once(call)
            except VincError as exc:
                delay = self._retry_delay(call, exc, attempt, _clock() - started)
                if delay is None:
                    raise
                log.info("vinc %s %s was transient (%s); retry %d in %.1fs",
                         call.method, call.path, exc.code, attempt, delay)
                _sleep(delay)

    def _send_once(self, call: _Call) -> dict[str, Any]:
        try:
            response = self._http.request(**self._request_args(call))
        except httpx.TimeoutException as exc:
            raise VincUnavailable("upstream_unavailable", f"Timed out: {exc}") from None
        except httpx.TransportError as exc:
            raise VincUnavailable("upstream_unavailable",
                                  f"The API is unreachable: {type(exc).__name__}") from None
        return _parse(response)

    def whoami(self, *, space: str | None = None) -> dict[str, Any]:
        """``GET /v1/me``: the key's owner, plan and the teams it can reach."""
        return self._send(self._whoami(space))

    def brief(self, query: str, *, max_excerpts: int | None = None,
              historical: bool | None = None, space: str | None = None,
              mode: str | None = None, as_of: str | None = None, known_at: str | None = None) -> dict[str, Any]:
        """``POST /v1/brief``: resolve a question (or a node id) to one node with its material."""
        return self._send(self._brief(query, max_excerpts, historical, space, mode, as_of, known_at))

    def node(self, node_id: str, *, space: str | None = None) -> dict[str, Any]:
        """``GET /v1/nodes/{id}``: read one node by id. Raises ``VincNotFound`` if absent."""
        return self._send(self._node(node_id, space))

    def role(self, role_id: str, *, space: str | None = None,
             pin: str | None = None) -> dict[str, Any]:
        """Read a genuine owner-visible role's complete contract and collaboration links.

        The contract carries ``rev`` and ``hash``. Pass an earlier ``hash`` as ``pin`` to get
        the contract only while the role is unchanged; otherwise ``VincRoleChanged`` is raised
        and no contract is returned. Moving the pin to a new hash is the caller's decision.
        """
        return _pinned(self._send(self._role(role_id, space, pin)), pin)

    def document(self, doc_id: str, *, space: str | None = None) -> dict[str, Any]:
        """Read exact canonical text and its integrity metadata via GET /v1/documents/{id}."""
        return self._send(self._document(doc_id, space))

    def _required_document(self, read: str, *, space: str | None) -> dict[str, Any]:
        try:
            document = self.document(document_key(read), space=space)
        except VincNotFound:
            raise VincInvalidArgument("prefetch_spec_unresolved",
                                      "A required role reading is unavailable.") from None
        required_reading(read, document)
        try:
            node = self.node(cite_id(read), space=space)
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            node = {"executed": True, "found": False, "node": None}
        return with_citation(read, document, node)

    def prefetch(self, role_id: str, task: str, *, space: str | None = None,
                 max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None) -> str:
        """Before a model call, read the chosen role, every required spec and task evidence.

        No content or permission cache, write, delegation or automatic role adoption.
        Domain supplies task search context; it is not claimed to be a search filter.
        Reads are exact canonical document keys (or concept:doc.<key>), not queries.
        Full verified text is mandatory. No excerpt or section selection satisfies reads.
        A required miss or integrity failure stops validation and later batches.
        Optional task lookups may start before validation: brief is submitted first and
        search right after the role read. If validation fails they are cancelled, and any
        result that already arrived is discarded; the required error is raised unchanged.
        Concurrency is bounded by the constructor setting (default 8, 1 is sequential):
        task lookups and document/mirror reads share one pool of that size; the role read runs on the
        calling thread. Task lookups take slots in submission order and documents queue
        behind them. With 1 the order stays role, documents, budget, brief, search.
        Budget overflow reports the full required_size after all required reads, before
        any task lookup result is used. Retry with that size only after accepting the input cost.
        Large roles require an explicit max_chars above the 24000 character default.
        Each verified document gets one same-space mirror lookup in its existing worker.
        Missing mirrors preserve the full document with no citable id; other errors raise.
        Transient errors are not retried here (unlike the plain calls): this runs before
        every model call, so a hub that is briefly unavailable raises at once.
        With ``pin`` (a hash from :meth:`role`) a role that changed raises
        ``VincRoleChanged`` before any document is read. The pin covers the role contract,
        not the text of the documents it lists.
        """
        return self.prefetch_with_contract(role_id, task, space=space, max_chars=max_chars,
                                           pin=pin).block

    def prefetch_with_contract(self, role_id: str, task: str, *, space: str | None = None,
                               max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                               pin: str | None = None) -> RolePrefetch:
        """:meth:`prefetch`, returning the block together with the role contract it read.

        Same reads, checks and errors; the contract is the one the block was built from, so a
        caller can apply its ``disallowed_tools`` without reading the role again.
        """
        _check_pin(pin)
        return self.without_retries()._prefetch(role_id, task, space=space, max_chars=max_chars,
                                                pin=pin)

    def _prefetch(self, role_id: str, task: str, *, space: str | None = None,
                  max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                  pin: str | None = None) -> RolePrefetch:
        limit = self.prefetch_concurrency
        early = limit > 1
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="vinc-prefetch") as pool:
            # 선택 읽기(brief·search)를 검증 전에 먼저 띄운다. 필수 실패 시 취소·폐기.
            # 근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]
            optional = [pool.submit(self.brief, task, max_excerpts=3, space=space)] if early else []
            try:
                contract = self.role(role_id, space=space, pin=pin)
                specs = []
                reads = reading_list(contract)
                query = f"{task}\nRole topic: {contract['domain']}" if contract.get("domain") else task
                if early:
                    optional.append(pool.submit(self.search, query, limit=5, space=space))
                for offset in range(0, len(reads), limit):
                    batch = reads[offset:offset + limit]
                    futures = [pool.submit(self._required_document, doc, space=space)
                               for doc in batch] if early else []
                    try:
                        for index, doc in enumerate(batch):
                            result = (futures[index].result() if futures
                                      else self._required_document(doc, space=space))
                            required_reading(doc, result)
                            specs.append((doc, result))
                    finally:
                        # 아직 시작하지 않은 요청은 취소한다. 실행 중인 스레드는 풀 종료 때 회수한다.
                        for future in futures:
                            future.cancel()
                required = required_context(contract, specs, max_chars=max_chars)
                # 선택 읽기 결과(와 그 예외)는 필수 검증과 예산을 통과한 뒤에만 꺼낸다.
                if early:
                    brief, search = [future.result() for future in optional]
                else:
                    brief = self.brief(task, max_excerpts=3, space=space)
                    search = self.search(query, limit=5, space=space)
            finally:
                for future in optional:
                    future.cancel()
        return RolePrefetch(_with_task_evidence(required, brief, search, max_chars=max_chars),
                            contract)

    def search(self, query: str, *, limit: int | None = None, cursor: str | None = None,
               detail: str | None = None, historical: bool | None = None,
               mode: str | None = None, as_of: str | None = None, known_at: str | None = None,
               space: str | None = None) -> dict[str, Any]:
        """``POST /v1/search``: hybrid text and meaning search, paged by ``cursor``."""
        return self._send(self._search(query, limit, cursor, detail, historical, space, mode, as_of, known_at))

    def recall(self, *, domain: str | None = None, tags: Iterable[str] | None = None,
               limit: int | None = None, cursor: str | None = None,
               space: str | None = None) -> dict[str, Any]:
        """``GET /v1/episodes``: the episode timeline, newest first."""
        return self._send(self._recall(domain, tags, limit, cursor, space))

    def ingest(self, nodes: Iterable[dict[str, Any]], edges: Iterable[dict[str, Any]] | None = None,
               *, space: str | None = None, create_only: bool | None = None,
               operation_id: str | None = None, review_claims: bool | None = None) -> dict[str, Any]:
        """``POST /v1/fragments``: merge nodes and edges by id.

        ``props`` sent for a node replace its stored props whole; read them first
        and send the merged dict, or use expected_rev with props_patch for a guarded patch.
        operation_id binds identical retries; check operation_status after an uncertain response.
        Raises ``VincPartialWrite`` when part was skipped.
        """
        self._require_write_key()
        return _raise_if_partial(self._send(self._ingest(nodes, edges, space, create_only, operation_id, review_claims)))

    def operation_status(self, operation_id: str, *, space: str | None = None) -> dict[str, Any]:
        """Read a historical owner-scoped receipt. Unknown is not proof that no write happened."""
        return self._send(self._operation_status(operation_id, space))

    def edges(self, node: str | None = None, *, nodes: Iterable[str] | None = None,
              type: str | None = None, dir: str | None = None, limit: int | None = None,
              space: str | None = None) -> dict[str, Any]:
        """Stored edges with direction and evidence: ``GET /v1/nodes/{id}/edges`` for one
        ``node``, ``POST /v1/edges/query`` for ``nodes``. Pass exactly one of the two."""
        return self._send(self._edges(node, nodes, type, dir, limit, space))

    def role_check(self, role_id: str, output: str, *,
                   person_only_requests: Iterable[dict[str, Any]] | None = None,
                   self_authored: bool | None = None, space: str | None = None) -> dict[str, Any]:
        """``POST /v1/roles/{id}/check``: the hub applies the role's checks to ``output``.

        Read-only; returns verdict pass, block or interrupt. pass grants no permission.
        """
        return self._send(self._role_check(role_id, output, person_only_requests,
                                           self_authored, space))

    def delete(self, node_id: str, *, dry_run: bool = False, preview_digest: str | None = None,
               operation_id: str | None = None, space: str | None = None) -> dict[str, Any]:
        """``DELETE /v1/nodes/{id}``: irreversibly delete one node and its edges.

        ``dry_run=True`` changes nothing and returns the preview with ``preview_digest``;
        pass that digest on the apply call so a changed graph refuses instead of deleting.
        """
        self._require_write_key()
        return self._send(self._delete(node_id, dry_run, preview_digest, operation_id, space))

    def delete_edge(self, from_id: str, to_id: str, type: str, *, dry_run: bool = False,
                    preview_digest: str | None = None, operation_id: str | None = None,
                    space: str | None = None) -> dict[str, Any]:
        """``DELETE /v1/edges``: delete one relationship; ``dry_run`` and ``preview_digest``
        work as in :meth:`delete`."""
        self._require_write_key()
        return self._send(self._delete_edge(from_id, to_id, type, dry_run, preview_digest,
                                            operation_id, space))

    def merge_nodes(self, keep_id: str, drop_id: str, *, dry_run: bool = False,
                    preview_digest: str | None = None, operation_id: str | None = None,
                    space: str | None = None) -> dict[str, Any]:
        """``POST /v1/nodes/merge``: irreversibly fold ``drop_id`` into ``keep_id``;
        ``dry_run`` and ``preview_digest`` work as in :meth:`delete`."""
        self._require_write_key()
        return self._send(self._merge_nodes(keep_id, drop_id, dry_run, preview_digest,
                                            operation_id, space))

    def doc_delete(self, doc_id: str, *, dry_run: bool = False, preview_digest: str | None = None,
                   operation_id: str | None = None, space: str | None = None) -> dict[str, Any]:
        """``DELETE /v1/documents/{id}``: delete one document; ``dry_run`` and
        ``preview_digest`` work as in :meth:`delete`."""
        self._require_write_key()
        return self._send(self._doc_delete(doc_id, dry_run, preview_digest, operation_id, space))

    def get_context(self, query: str, *, search_fallback: bool = False,
                    max_chars: int = DEFAULT_MAX_CHARS, max_excerpts: int = 3,
                    min_query_chars: int = 3, space: str | None = None) -> str | None:
        """A fenced context block for ``query``, or ``None``.

        One call per turn, two with ``search_fallback`` when the brief found
        nothing or several nodes tied (a natural question often ties). When Vinc is unavailable or the quota is spent this logs a
        warning and returns ``None``: the caller's model call goes on without it. It does not
        retry transient errors, so a turn is never held up waiting for the hub.
        A key or space problem still raises, because it will not fix itself.
        """
        text = (query or "").strip()
        if len(text) < min_query_chars:
            return None
        try:
            fast = self.without_retries()
            brief = fast.brief(text, max_excerpts=max_excerpts, space=space)
            search = (fast.search(text, limit=5, space=space)
                      if search_fallback and brief.get("status") in _WIDEN else None)
            return format_context(brief, search, max_chars=max_chars)
        except (VincUnavailable, VincQuotaExceeded) as exc:
            log.warning("vinc context skipped for this turn: %s (retry_after=%s)",
                        exc, exc.retry_after)
            return None

    def record_episode(self, title: str, *, summary: str = "", about: Iterable[str] = (),
                       date: str | None = None, domain: str | None = None,
                       tags: Iterable[str] | None = None, episode_id: str | None = None,
                       space: str | None = None, create_only: bool | None = None,
                       operation_id: str | None = None) -> dict[str, Any]:
        """Write one Episode, ``about`` existing concepts or decisions.

        Call it on purpose, after a person approved what happened; nothing in
        this package calls it for you. Every ``about`` id is read first, so a
        typo raises ``VincNotFound`` instead of creating an empty node.
        ``create_only`` and ``operation_id`` reach :meth:`ingest` unchanged.
        """
        self._require_write_key()
        targets = check_about(about)
        for target in targets:
            self.node(target, space=space)
        fragment = build_episode_fragment(title, summary=summary, about=targets, date=date,
                                          domain=domain, tags=tags, episode_id=episode_id)
        return self.ingest(fragment["nodes"], fragment["edges"], space=space,
                           create_only=create_only, operation_id=operation_id)

    def write_package(self, question: str, conclusion: str, *,
                      claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                      space: str, title: str | None = None,
                      package_id: str | None = None, create_only: bool | None = None,
                      operation_id: str | None = None) -> dict[str, Any]:
        """Explicitly write one handoff Record into the selected space, requiring a write key.

        ``create_only`` and ``operation_id`` reach :meth:`ingest` unchanged.
        """
        self._require_write_key()
        selected = package_space(space)
        fragment = build_package_fragment(question, conclusion, claims=claims,
                                          produced_by=produced_by, run_id=run_id,
                                          title=title, package_id=package_id)
        result = self.ingest(fragment["nodes"], fragment["edges"], space=selected,
                             create_only=create_only, operation_id=operation_id)
        return {"package_id": fragment["nodes"][0]["id"], "space": selected, "write": result}

    def get_package_context(self, package_id: str, *, space: str,
                            max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
        """Read a package and check each distinct source as this key, every call; never write."""
        selected = package_space(space)
        node = self.node(package_id, space=selected).get("node")
        if not isinstance(node, dict) or node.get("id") != package_id:
            raise VincInvalidArgument("invalid_package", "The package carrier is unavailable.")
        readable = {}
        for nid in validate_package(node):
            try:
                result = self.node(nid, space=selected)
            except VincNotFound:
                continue
            except VincAuthError as exc:
                if exc.status != 403:
                    raise
                continue
            source = result.get("node")
            if result.get("found") and isinstance(source, dict) and source.get("id") == nid:
                readable[nid] = source
        return format_package(node, space=selected, readable_ids=readable,
                              evidence_nodes=readable, max_chars=max_chars)


class AsyncVincClient(_Base):
    """Async twin of :class:`VincClient`; same methods, awaited."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None,
                 space: str | None = None, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 prefetch_concurrency: int = PREFETCH_READ_CONCURRENCY,
                 retry_for: float = DEFAULT_RETRY_FOR_S) -> None:
        super().__init__(api_key, base_url=base_url, space=space, timeout=timeout,
                         prefetch_concurrency=prefetch_concurrency, retry_for=retry_for)
        self._http = httpx.AsyncClient(base_url=self.base_url, headers=self._headers(),
                                       timeout=timeout, follow_redirects=False,
                                       transport=transport)

    async def __aenter__(self) -> "AsyncVincClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, call: _Call) -> dict[str, Any]:
        started, attempt = _clock(), 0
        while True:
            attempt += 1
            try:
                return await self._send_once(call)
            except VincError as exc:
                delay = self._retry_delay(call, exc, attempt, _clock() - started)
                if delay is None:
                    raise
                log.info("vinc %s %s was transient (%s); retry %d in %.1fs",
                         call.method, call.path, exc.code, attempt, delay)
                await _asleep(delay)

    async def _send_once(self, call: _Call) -> dict[str, Any]:
        try:
            response = await self._http.request(**self._request_args(call))
        except httpx.TimeoutException as exc:
            raise VincUnavailable("upstream_unavailable", f"Timed out: {exc}") from None
        except httpx.TransportError as exc:
            raise VincUnavailable("upstream_unavailable",
                                  f"The API is unreachable: {type(exc).__name__}") from None
        return _parse(response)

    async def whoami(self, *, space: str | None = None) -> dict[str, Any]:
        return await self._send(self._whoami(space))

    async def brief(self, query: str, *, max_excerpts: int | None = None,
                    historical: bool | None = None, space: str | None = None,
                    mode: str | None = None, as_of: str | None = None, known_at: str | None = None) -> dict[str, Any]:
        return await self._send(self._brief(query, max_excerpts, historical, space, mode, as_of, known_at))

    async def node(self, node_id: str, *, space: str | None = None) -> dict[str, Any]:
        return await self._send(self._node(node_id, space))

    async def role(self, role_id: str, *, space: str | None = None,
                   pin: str | None = None) -> dict[str, Any]:
        """Async complete role contract read; ``pin`` works as in :meth:`VincClient.role`."""
        return _pinned(await self._send(self._role(role_id, space, pin)), pin)

    async def document(self, doc_id: str, *, space: str | None = None) -> dict[str, Any]:
        """Async exact canonical document read; does not replace integrity validation."""
        return await self._send(self._document(doc_id, space))

    async def _required_document(self, read: str, *, space: str | None) -> dict[str, Any]:
        try:
            document = await self.document(document_key(read), space=space)
        except VincNotFound:
            raise VincInvalidArgument("prefetch_spec_unresolved",
                                      "A required role reading is unavailable.") from None
        required_reading(read, document)
        try:
            node = await self.node(cite_id(read), space=space)
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            node = {"executed": True, "found": False, "node": None}
        return with_citation(read, document, node)

    async def prefetch(self, role_id: str, task: str, *, space: str | None = None,
                       max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None) -> str:
        """Async fresh role/spec/task evidence retrieval before the model runs."""
        return (await self.prefetch_with_contract(role_id, task, space=space,
                                                  max_chars=max_chars, pin=pin)).block

    async def prefetch_with_contract(self, role_id: str, task: str, *, space: str | None = None,
                                     max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                                     pin: str | None = None) -> RolePrefetch:
        """Async :meth:`VincClient.prefetch_with_contract`."""
        _check_pin(pin)
        return await self.without_retries()._prefetch(role_id, task, space=space,
                                                      max_chars=max_chars, pin=pin)

    async def _prefetch(self, role_id: str, task: str, *, space: str | None = None,
                        max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                        pin: str | None = None) -> RolePrefetch:
        limit = self.prefetch_concurrency
        early = limit > 1
        gate = asyncio.Semaphore(limit)

        async def bounded(read, *args, **kwargs):
            async with gate:
                return await read(*args, **kwargs)

        # 선택 읽기를 검증 전에 먼저 띄운다. 선택 읽기와 문서는 gate 하나를 나눠 쓰고,
        # 역할 읽기는 gate 밖에서 이 코루틴이 직접 한다(동기판의 호출 스레드와 같다).
        # 근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]
        optional = ([asyncio.create_task(bounded(self.brief, task, max_excerpts=3, space=space))]
                    if early else [])
        try:
            contract = await self.role(role_id, space=space, pin=pin)
            specs = []
            reads = reading_list(contract)
            query = f"{task}\nRole topic: {contract['domain']}" if contract.get("domain") else task
            if early:
                optional.append(asyncio.create_task(
                    bounded(self.search, query, limit=5, space=space)))
            for offset in range(0, len(reads), limit):
                batch = reads[offset:offset + limit]
                tasks = [asyncio.create_task(bounded(self._required_document, doc, space=space))
                         for doc in batch] if early else []
                try:
                    for index, doc in enumerate(batch):
                        result = (await tasks[index] if tasks
                                  else await self._required_document(doc, space=space))
                        required_reading(doc, result)
                        specs.append((doc, result))
                finally:
                    # 호출 종료 후 요청이나 관측되지 않은 예외를 남기지 않는다.
                    for pending in tasks:
                        pending.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            required = required_context(contract, specs, max_chars=max_chars)
            # 선택 읽기 결과(와 그 예외)는 필수 검증과 예산을 통과한 뒤에만 꺼낸다.
            if early:
                brief, search = [await pending for pending in optional]
            else:
                brief = await self.brief(task, max_excerpts=3, space=space)
                search = await self.search(query, limit=5, space=space)
        finally:
            for pending in optional:
                pending.cancel()
            await asyncio.gather(*optional, return_exceptions=True)
        return RolePrefetch(_with_task_evidence(required, brief, search, max_chars=max_chars),
                            contract)

    async def search(self, query: str, *, limit: int | None = None, cursor: str | None = None,
                     detail: str | None = None, historical: bool | None = None,
                     mode: str | None = None, as_of: str | None = None, known_at: str | None = None,
                     space: str | None = None) -> dict[str, Any]:
        return await self._send(self._search(query, limit, cursor, detail, historical, space, mode, as_of, known_at))

    async def recall(self, *, domain: str | None = None, tags: Iterable[str] | None = None,
                     limit: int | None = None, cursor: str | None = None,
                     space: str | None = None) -> dict[str, Any]:
        return await self._send(self._recall(domain, tags, limit, cursor, space))

    async def ingest(self, nodes: Iterable[dict[str, Any]],
                     edges: Iterable[dict[str, Any]] | None = None, *,
                     space: str | None = None, create_only: bool | None = None,
                     operation_id: str | None = None, review_claims: bool | None = None) -> dict[str, Any]:
        self._require_write_key()
        return _raise_if_partial(await self._send(self._ingest(nodes, edges, space, create_only, operation_id, review_claims)))

    async def operation_status(self, operation_id: str, *, space: str | None = None) -> dict[str, Any]:
        return await self._send(self._operation_status(operation_id, space))

    async def edges(self, node: str | None = None, *, nodes: Iterable[str] | None = None,
                    type: str | None = None, dir: str | None = None, limit: int | None = None,
                    space: str | None = None) -> dict[str, Any]:
        return await self._send(self._edges(node, nodes, type, dir, limit, space))

    async def role_check(self, role_id: str, output: str, *,
                         person_only_requests: Iterable[dict[str, Any]] | None = None,
                         self_authored: bool | None = None,
                         space: str | None = None) -> dict[str, Any]:
        return await self._send(self._role_check(role_id, output, person_only_requests,
                                                 self_authored, space))

    async def delete(self, node_id: str, *, dry_run: bool = False,
                     preview_digest: str | None = None, operation_id: str | None = None,
                     space: str | None = None) -> dict[str, Any]:
        self._require_write_key()
        return await self._send(self._delete(node_id, dry_run, preview_digest, operation_id, space))

    async def delete_edge(self, from_id: str, to_id: str, type: str, *, dry_run: bool = False,
                          preview_digest: str | None = None, operation_id: str | None = None,
                          space: str | None = None) -> dict[str, Any]:
        self._require_write_key()
        return await self._send(self._delete_edge(from_id, to_id, type, dry_run, preview_digest,
                                                  operation_id, space))

    async def merge_nodes(self, keep_id: str, drop_id: str, *, dry_run: bool = False,
                          preview_digest: str | None = None, operation_id: str | None = None,
                          space: str | None = None) -> dict[str, Any]:
        self._require_write_key()
        return await self._send(self._merge_nodes(keep_id, drop_id, dry_run, preview_digest,
                                                  operation_id, space))

    async def doc_delete(self, doc_id: str, *, dry_run: bool = False,
                         preview_digest: str | None = None, operation_id: str | None = None,
                         space: str | None = None) -> dict[str, Any]:
        self._require_write_key()
        return await self._send(self._doc_delete(doc_id, dry_run, preview_digest, operation_id, space))

    async def get_context(self, query: str, *, search_fallback: bool = False,
                          max_chars: int = DEFAULT_MAX_CHARS, max_excerpts: int = 3,
                          min_query_chars: int = 3, space: str | None = None) -> str | None:
        text = (query or "").strip()
        if len(text) < min_query_chars:
            return None
        try:
            fast = self.without_retries()
            brief = await fast.brief(text, max_excerpts=max_excerpts, space=space)
            search = (await fast.search(text, limit=5, space=space)
                      if search_fallback and brief.get("status") in _WIDEN else None)
            return format_context(brief, search, max_chars=max_chars)
        except (VincUnavailable, VincQuotaExceeded) as exc:
            log.warning("vinc context skipped for this turn: %s (retry_after=%s)",
                        exc, exc.retry_after)
            return None

    async def record_episode(self, title: str, *, summary: str = "", about: Iterable[str] = (),
                             date: str | None = None, domain: str | None = None,
                             tags: Iterable[str] | None = None, episode_id: str | None = None,
                             space: str | None = None, create_only: bool | None = None,
                             operation_id: str | None = None) -> dict[str, Any]:
        self._require_write_key()
        targets = check_about(about)
        for target in targets:
            await self.node(target, space=space)
        fragment = build_episode_fragment(title, summary=summary, about=targets, date=date,
                                          domain=domain, tags=tags, episode_id=episode_id)
        return await self.ingest(fragment["nodes"], fragment["edges"], space=space,
                                 create_only=create_only, operation_id=operation_id)

    async def write_package(self, question: str, conclusion: str, *,
                            claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                            space: str, title: str | None = None,
                            package_id: str | None = None, create_only: bool | None = None,
                            operation_id: str | None = None) -> dict[str, Any]:
        """Async explicit package write; no framework callback invokes this automatically."""
        self._require_write_key()
        selected = package_space(space)
        fragment = build_package_fragment(question, conclusion, claims=claims,
                                          produced_by=produced_by, run_id=run_id,
                                          title=title, package_id=package_id)
        result = await self.ingest(fragment["nodes"], fragment["edges"], space=selected,
                                   create_only=create_only, operation_id=operation_id)
        return {"package_id": fragment["nodes"][0]["id"], "space": selected, "write": result}

    async def get_package_context(self, package_id: str, *, space: str,
                                  max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
        """Async fresh package and evidence reads under the recipient's current credentials."""
        selected = package_space(space)
        node = (await self.node(package_id, space=selected)).get("node")
        if not isinstance(node, dict) or node.get("id") != package_id:
            raise VincInvalidArgument("invalid_package", "The package carrier is unavailable.")
        readable = {}
        for nid in validate_package(node):
            try:
                result = await self.node(nid, space=selected)
            except VincNotFound:
                continue
            except VincAuthError as exc:
                if exc.status != 403:
                    raise
                continue
            source = result.get("node")
            if result.get("found") and isinstance(source, dict) and source.get("id") == nid:
                readable[nid] = source
        return format_package(node, space=selected, readable_ids=readable,
                              evidence_nodes=readable, max_chars=max_chars)


__all__ = ["VincClient", "AsyncVincClient", "VincError", "resolve_key", "resolve_base_url",
           "normalize_space", "DEFAULT_BASE_URL"]
