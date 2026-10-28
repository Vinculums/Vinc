"""Compile a role's context through a replaceable policy, under fixed invariants.

The compiler reads the role and its required documents, hands a read-only reader to a
policy, checks the plan the policy returns and renders the block with the same renderers
as :meth:`VincClient.prefetch`. A policy chooses what to include, in which form and in
what order; it never writes text into the block. :data:`FULL_TEXT` (P0) reproduces
``prefetch()``'s block byte for byte.

A plan that breaks an invariant is refused with ``context_plan_rejected`` and the
invariant's number in ``details["invariant"]``:

- I1 every graph byte is rendered by the compiler, inside the fence;
- I2 the Sources lines are prefetch's, computed from what was verified;
- I3 the role contract comes first and whole;
- I4 every required reading appears once; a shortened one needs a document-read tool;
- I5 spans are cut from the verified text;
- I6 the block fits ``max_chars`` (``prefetch_budget_exceeded`` otherwise);
- I7 the policy reads only through the reader, and the same reads give the same bytes;
- I8 a non-current item shows its state (a required reading in full form renders exactly as
  prefetch does, which carries no validity fields; spans, pointers and other items do);
- I9 failures raise prefetch's codes.

Interface: graph document vinc.spec.context-compiler-interface, from the plan in
[[vinc.plan.cag-context-compiler-2026-10-05]]. The policy is searched, not designed:
[[vinc.plan.agent-harness-work-order-2026-10-25]].
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import dataclasses
import hashlib
import inspect
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Union, runtime_checkable

from . import __version__
from . import errors as _errors
from .client import _check_pin
from .context import BOUNDARY, evidence_metadata, fence
from .errors import VincError, VincInvalidArgument, VincNotFound
from .prefetch import (DEFAULT_PREFETCH_MAX_CHARS, RolePrefetch, _with_task_evidence,
                       check_budget, citation_policy, cite_id, document_key, reading_list,
                       required_context, required_reading, role_context, verified_document,
                       with_citation)
from .tools import DOC_NAME

#: 렌더러나 검사가 바뀌면 올린다. 같은 판이면 같은 읽기에서 같은 블록이 나와야 한다.
COMPILER_VERSION = "1"
FORMS = ("full", "spans", "pointer")
MAX_SPANS = 64
REJECTED = "context_plan_rejected"
_READS = ("role", "document", "node", "brief", "search")


def _reject(invariant: str, message: str) -> VincInvalidArgument:
    return VincInvalidArgument(REJECTED, f"{invariant}: {message}",
                               details={"invariant": invariant})


@dataclass(frozen=True)
class ContextRequest:
    """What the caller asks for. The reader (key and transport) is the client's own."""

    role: str
    task: str
    space: str | None = None
    pin: str | None = None
    #: 호출자가 명시한 노드 id 나 문서 키. 같은 스페이스에서만 읽고, 못 읽으면 manifest 에 남긴다.
    anchors: tuple[str, ...] = ()
    max_chars: int = DEFAULT_PREFETCH_MAX_CHARS
    #: 과제 근거 읽기(brief·search)에 그대로 넘긴다. 정책은 바꾸지 못한다.
    as_of: str | None = None
    #: 호스트가 모델에게 문서 읽기 도구를 주는가. 아니면 필수 문서는 전문으로만 싣는다(I4).
    read_tool: bool = False


@dataclass(frozen=True)
class ContextItem:
    """One source in the block, by the id it was read under."""

    source: str
    form: str = "full"
    #: ``spans`` 형태의 (시작, 끝) 글자 위치. 검증된 전문에서 자른다.
    spans: tuple[tuple[int, int], ...] = ()
    #: 정책이 남기는 짧은 이유. manifest 에만 실리고 블록에는 렌더하지 않는다.
    reason: str = ""


@dataclass(frozen=True)
class EvidenceRef:
    """A brief or search the policy made, by its index in :attr:`PolicyReader.calls`.

    ``ids`` keeps only those node hits and rows of a search; ``None`` keeps all.
    """

    call: int
    ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ContextPlan:
    items: tuple[ContextItem, ...]
    task_evidence: tuple[EvidenceRef, ...] = ()


@runtime_checkable
class ContextPolicy(Protocol):
    id: str

    def plan(self, request: ContextRequest, reader: "PolicyReader") -> ContextPlan: ...


@dataclass(frozen=True)
class CompiledContext:
    block: str
    sha256: str
    contract: dict[str, Any]
    manifest: dict[str, Any]
    #: 이 컴파일의 읽기 전부와 그 답. 원본 기록에 그대로 싣고, :func:`replay_context` 가 다시 쓴다.
    trace: tuple[dict[str, Any], ...] = field(repr=False, default=())

    def as_prefetch(self) -> RolePrefetch:
        return RolePrefetch(self.block, self.contract)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _key(method: str, args: dict[str, Any]) -> str:
    return method + " " + json.dumps(args, ensure_ascii=False, sort_keys=True)


def _error_entry(exc: VincError) -> dict[str, Any]:
    return {"type": type(exc).__name__, "code": exc.code, "message": exc.message,
            "status": exc.status, "details": exc.details, "request_id": exc.request_id,
            "retry_after": exc.retry_after}


def _raise_recorded(error: dict[str, Any]) -> None:
    cls = getattr(_errors, error.get("type") or "", None)
    if not (isinstance(cls, type) and issubclass(cls, VincError)):
        cls = VincError
    raise cls(error.get("code") or "", error.get("message") or "", status=error.get("status"),
              details=error.get("details") or {}, request_id=error.get("request_id") or "",
              retry_after=error.get("retry_after"))


class PolicyReader:
    """The only door a policy has: the client's reads, bound to the request's space.

    Reads are recorded with their answers (or errors) and repeated reads inside one
    compile return the recorded answer. Nothing here writes or reaches another space.
    The policy gets copies: changing what it was given changes nothing the compiler
    verified or renders.
    """

    def __init__(self, request: ContextRequest,
                 send: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self._request = request
        self._send = send
        self._seen: dict[str, int] = {}
        self._last = -1
        self._calls: list[dict[str, Any]] = []
        self._contract: dict[str, Any] = {}
        self._required: tuple[tuple[str, dict[str, Any]], ...] = ()
        self._anchors: dict[str, dict[str, Any]] = {}

    @property
    def calls(self) -> tuple[dict[str, Any], ...]:
        """Each read so far as ``{"method", "args"}``, in order; :class:`EvidenceRef` indexes it."""
        return tuple({"method": e["method"], "args": dict(e["args"])} for e in self._calls)

    @property
    def contract(self) -> dict[str, Any]:
        return copy.deepcopy(self._contract)

    @property
    def required(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """(read, verified document with ``cite_id``) in the role's reading order."""
        return copy.deepcopy(self._required)

    @property
    def anchors(self) -> dict[str, dict[str, Any]]:
        """Anchor id to what was read, or ``{"status": ...}`` when it could not be read."""
        return copy.deepcopy(self._anchors)

    @property
    def last_call(self) -> int:
        """Index of the call the last read answered from (a repeated read reuses its entry)."""
        return self._last

    def _call(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        key = _key(method, args)
        index = self._seen.get(key)
        if index is None:
            entry: dict[str, Any] = {"method": method, "args": args}
            try:
                response = self._send(method, args)
            except VincError as exc:
                entry["error"] = _error_entry(exc)
            else:
                entry["response"] = response
                entry["sha256"] = _digest(response)
            index = len(self._calls)
            self._calls.append(entry)
            self._seen[key] = index
        self._last = index
        entry = self._calls[index]
        if "error" in entry:
            _raise_recorded(entry["error"])
        return copy.deepcopy(entry["response"])

    def document(self, doc_id: str) -> dict[str, Any]:
        return self._call("document", {"doc_id": doc_id, "space": self._request.space})

    def node(self, node_id: str) -> dict[str, Any]:
        return self._call("node", {"node_id": node_id, "space": self._request.space})

    def brief(self, query: str, *, max_excerpts: int | None = None) -> dict[str, Any]:
        return self._call("brief", self._temporal({"query": query, "max_excerpts": max_excerpts}))

    def search(self, query: str, *, limit: int | None = None) -> dict[str, Any]:
        return self._call("search", self._temporal({"query": query, "limit": limit}))

    def _temporal(self, args: dict[str, Any]) -> dict[str, Any]:
        # as_of 는 요청에서만 온다. 정책이 과거 읽기를 따로 청하는 길은 없다(I8).
        args["space"] = self._request.space
        if self._request.as_of is not None:
            args["as_of"] = self._request.as_of
        return args

    def _role(self) -> dict[str, Any]:
        return self._call("role", {"role_id": self._request.role, "space": self._request.space,
                                   "pin": self._request.pin})


class FullTextPolicy:
    """P0: every required reading in full, then prefetch's brief and search.

    Its block equals :meth:`VincClient.prefetch`'s for the same reads.
    """

    id = "p0-full"

    def plan(self, request: ContextRequest, reader: PolicyReader) -> ContextPlan:
        # prefetch 처럼 필수 문맥의 예산을 과제 근거를 읽기 전에 본다. 넘치면 brief·search 를 부르지
        # 않고 같은 prefetch_budget_exceeded 로 멈춘다(그 둘의 오류가 예산 오류를 가리지 않는다).
        required_context(reader.contract, list(reader.required), max_chars=request.max_chars)
        domain = reader.contract.get("domain")
        query = f"{request.task}\nRole topic: {domain}" if domain else request.task
        reader.brief(request.task, max_excerpts=3)
        brief = reader.last_call
        reader.search(query, limit=5)
        search = reader.last_call
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in reader.required),
                           task_evidence=(EvidenceRef(brief), EvidenceRef(search)))


#: 문서 읽기 도구가 없을 때의 기본값. prefetch 와 같은 블록을 낸다. 도구가 있으면
#: :data:`vinc_client.task_evidence.TASK_EVIDENCE` 가 기본값이다(VIN-790).
FULL_TEXT = FullTextPolicy()


def default_policy(read_tool: bool) -> ContextPolicy:
    """The policy :func:`compile_context` uses when none is given.

    With a document-read tool the required readings can be pointers, so the task evidence
    policy takes the room; without one only full readings are allowed and P0 stays.
    """
    from .task_evidence import TASK_EVIDENCE   # noqa: PLC0415  (그 모듈이 이 모듈을 읽는다)
    return TASK_EVIDENCE if read_tool else FULL_TEXT


def _read_required(reader: PolicyReader, read: str) -> dict[str, Any]:
    # prefetch 의 _required_document 와 같은 읽기·검사·오류 코드다(I9).
    try:
        document = reader.document(document_key(read))
    except VincNotFound:
        raise VincInvalidArgument("prefetch_spec_unresolved",
                                  "A required role reading is unavailable.") from None
    required_reading(read, document)
    try:
        node = reader.node(cite_id(read))
    except VincNotFound as exc:
        if exc.code != "not_found":
            raise
        node = {"executed": True, "found": False, "node": None}
    return with_citation(read, document, node)


def _read_anchors(reader: PolicyReader, anchors: tuple[str, ...]) -> None:
    for anchor in dict.fromkeys(anchors):
        try:
            if ":" in anchor and not anchor.startswith("concept:doc."):
                got = reader.node(anchor)
                ok = got.get("found") is not False and (got.get("node") or {}).get("id") == anchor
            else:
                got = reader.document(document_key(anchor))
                ok = verified_document(got, document_key(anchor))
        except VincError as exc:
            # 앵커는 호출자가 고른 선택 읽기다. 못 읽으면 manifest 에 남기고 컴파일은 계속한다.
            reader._anchors[anchor] = {"status": exc.code or "error"}
            continue
        reader._anchors[anchor] = got if ok else {"status": "unverified"}


_DOC_KEYS = ("doc_id", "title", "content_hash", "updated_at", "content_time", "verified")
_DOC_STATE = ("validity", "valid_from", "valid_until", "superseded_by")
_NODE_KEYS = ("id", "title", "kind", "domain", "one_liner", "summary")


def _citation(result: dict[str, Any], read: str) -> str:
    return (f"cite as {result['cite_id']}" if result.get("cite_id") == cite_id(read)
            else "no citable id")


def _doc_data(result: dict[str, Any]) -> dict[str, Any]:
    data = {k: result[k] for k in _DOC_KEYS if k in result}
    data.update({k: result[k] for k in _DOC_STATE if result.get(k) not in (None, "")})
    data["chars"] = len(result["text"])
    return data


def _spans_block(read: str, result: dict[str, Any], spans: tuple[tuple[int, int], ...],
                 label: str, read_tool: bool) -> str:
    data = _doc_data(result)
    data["spans"] = [{"start": s, "end": e, "text": result["text"][s:e]} for s, e in spans]
    rest = f"; read the rest with {DOC_NAME}" if read_tool else ""
    return (f"{label} (selected character ranges of the canonical document{rest}): "
            f"{read} ({_citation(result, read)})\n{BOUNDARY}\n\n"
            + fence(json.dumps(data, ensure_ascii=False, indent=2)))


def _pointer_block(read: str, result: dict[str, Any]) -> str:
    return (f"Required reading (not included; read it with {DOC_NAME} before relying "
            f"on it): {read} ({_citation(result, read)})\n{BOUNDARY}\n\n"
            + fence(json.dumps(_doc_data(result), ensure_ascii=False, indent=2)))


def _full_optional(read: str, result: dict[str, Any]) -> str:
    data = {k: result[k] for k in _DOC_KEYS if k in result}
    data.update({k: result[k] for k in _DOC_STATE if result.get(k) not in (None, "")})
    data["text"] = result["text"]
    return (f"Reference document (full canonical document): {read} "
            f"({_citation(result, read)})\n{BOUNDARY}\n\n"
            + fence(json.dumps(data, ensure_ascii=False, indent=2)))


def _node_block(node: dict[str, Any]) -> str:
    data = {k: node[k] for k in _NODE_KEYS if node.get(k) not in (None, "")}
    state = evidence_metadata(node)
    if state:
        data["state"] = state
    return (f"Reference node: {node['id']} (cite as {node['id']})\n{BOUNDARY}\n\n"
            + fence(json.dumps(data, ensure_ascii=False, indent=2)))


def _check_item(item: Any) -> None:
    """The plan's shape, before anything is looked up (I1)."""
    if (not isinstance(item, ContextItem) or not isinstance(item.source, str)
            or not item.source.strip() or item.form not in FORMS
            or not isinstance(item.reason, str) or not isinstance(item.spans, tuple)
            or any(not isinstance(span, tuple) or len(span) != 2 for span in item.spans)):
        raise _reject("I1", f"items are ContextItem values with a source id, a form in {FORMS}, "
                            "a str reason and spans as a tuple of (start, end) pairs")


def _doc_shaped(source: str) -> bool:
    return ":" not in source or source.startswith("concept:doc.")


def _usable(reader: PolicyReader, entry: dict[str, Any]) -> bool:
    """An answered read made in this request's space and time scope, unchanged since it came."""
    args, request = entry.get("args") or {}, reader._request
    if "response" not in entry or _digest(entry["response"]) != entry.get("sha256"):
        return False
    if args.get("space") != request.space:
        return False
    return entry["method"] not in ("brief", "search") or args.get("as_of") == request.as_of


def _check_spans(item: ContextItem, text: str) -> None:
    spans = item.spans
    if (not spans or len(spans) > MAX_SPANS
            or any(not (type(s) is int and type(e) is int and 0 <= s < e <= len(text))
                   for s, e in spans)
            or any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))):
        raise _reject("I5", f"spans of {item.source!r} must be 1 to {MAX_SPANS} ordered, "
                            "non-overlapping ranges inside the verified text")


def _required_read(source: str, required: dict[str, Any], seen: set[str]) -> str | None:
    """The declared read ``source`` names: itself, or an unused read of the same document.

    A document a role requires never becomes an optional item, so the same text cannot come
    twice under its other spelling (I4).
    """
    if source in required:
        if source in seen:
            raise _reject("I4", f"{source!r} appears twice")
        return source
    same = [read for read in required if document_key(read) == document_key(source)]
    if not same:
        return None
    unused = [read for read in same if read not in seen]
    if not unused:
        raise _reject("I4", f"{source!r} appears twice")
    return unused[0]


def _optional_source(reader: PolicyReader, source: str) -> tuple[str, dict[str, Any]]:
    """A non-required item: a document or node this compile read and verified (I7, I5)."""
    doc = _doc_shaped(source)
    for entry in reader._calls:
        args = entry.get("args") or {}
        if doc and not (entry["method"] == "document" and args.get("doc_id") == document_key(source)):
            continue
        if not doc and not (entry["method"] == "node" and args.get("node_id") == source):
            continue
        if not _usable(reader, entry):
            raise _reject("I7", f"{source!r} was not read through the reader in this space")
        answer = entry["response"]
        if doc:
            if not verified_document(answer, document_key(source)):
                raise _reject("I5", f"{source!r} was not read as a verified document")
            return "document", answer
        else:
            node = answer.get("node")
            if answer.get("found") is False or not isinstance(node, dict) or node.get("id") != source:
                raise _reject("I7", f"{source!r} was read but not found")
            return "node", node
    raise _reject("I7", f"{source!r} was not read through the reader")


def _evidence(reader: PolicyReader, refs: tuple[EvidenceRef, ...]) -> tuple[dict, dict | None, list]:
    picked: dict[str, dict[str, Any]] = {}
    dropped: list[dict[str, Any]] = []
    if not isinstance(refs, tuple):
        raise _reject("I1", "task_evidence is a tuple of EvidenceRef values")
    for ref in refs:
        if (not isinstance(ref, EvidenceRef) or type(ref.call) is not int
                or not (ref.ids is None or (isinstance(ref.ids, tuple)
                                            and all(isinstance(i, str) for i in ref.ids)))):
            raise _reject("I1", "task evidence is EvidenceRef(call index, ids tuple or None)")
        if not 0 <= ref.call < len(reader._calls):
            raise _reject("I7", "task evidence must name a brief or search this compile made")
        entry = reader._calls[ref.call]
        method = entry["method"]
        if method not in ("brief", "search") or method in picked or not _usable(reader, entry):
            raise _reject("I7", "task evidence takes at most one answered brief and one search, "
                                "read through the reader in this request's space and time scope")
        answer = entry["response"]
        if ref.ids is not None:
            if method != "search":
                raise _reject("I7", "only a search's rows can be filtered")
            keep = set(ref.ids)
            answer = dict(answer)
            for key in ("node_hits", "rows"):
                rows = answer.get(key) or []
                answer[key] = [r for r in rows if r.get("id") in keep]
                dropped.extend({"source": r.get("id"), "reason": "task_evidence:dropped"}
                               for r in rows if r.get("id") not in keep)
        picked[method] = answer
    return picked.get("brief", {}), picked.get("search"), dropped


def _policy_digest(policy: Any) -> str:
    try:
        source = inspect.getsource(type(policy))
    except (OSError, TypeError):
        return ""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _render(request: ContextRequest, policy: ContextPolicy, reader: PolicyReader,
            plan: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(plan, ContextPlan) or not isinstance(plan.items, tuple):
        raise _reject("I1", "a policy returns a ContextPlan of ContextItem values")
    # 선언한 읽기 문자열 그대로가 단위다. 역할이 같은 문서를 두 표기로 선언했으면 prefetch 처럼 둘 다 싣는다.
    required = dict(reader._required)
    seen: set[str] = set()
    seen_reads: set[str] = set()
    specs: list[tuple[str, dict[str, Any]]] = []
    parts = [role_context(reader._contract)]
    rows: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    extra_ids: list[str] = []
    extra_uncitable: list[str] = []
    for item in plan.items:
        _check_item(item)
        doc = _doc_shaped(item.source)
        read = _required_read(item.source, required, seen_reads) if doc else None
        row: dict[str, Any] = {"source": item.source, "form": item.form, "reason": item.reason}
        if read is not None:
            seen_reads.add(read)
            result = required[read]
            row["source"] = read
            if item.form != "full" and not request.read_tool:
                raise _reject("I4", f"{read!r} is shortened but the host has no "
                                    "document-read tool")
            if item.form == "full":
                text = required_reading(read, result)
            elif item.form == "spans":
                _check_spans(item, result["text"])
                text = _spans_block(read, result, item.spans, "Required reading", True)
            else:
                text = _pointer_block(read, result)
            if item.form != "full":
                omitted.append({"source": read, "reason": f"form:{item.form}"})
            specs.append((read, result))
            row.update(kind="document", required=True, content_hash=result.get("content_hash"),
                       cite_id=result.get("cite_id"))
        else:
            key = ("doc:" + document_key(item.source)) if doc else ("node:" + item.source)
            if key in seen:
                raise _reject("I4", f"{item.source!r} appears twice")
            seen.add(key)
            kind, found = _optional_source(reader, item.source)
            if kind == "node":
                if item.form != "full":
                    raise _reject("I5", f"node {item.source!r} has no text to cut")
                text = _node_block(found)
                extra_ids.append(found["id"])
                row.update(kind="node", required=False, cite_id=found["id"], rev=found.get("rev"),
                           state=evidence_metadata(found))
            else:
                if item.form == "pointer":
                    raise _reject("I4", "only a required reading can be a pointer")
                mirror = _mirror(reader, item.source, found)
                if item.form == "spans":
                    _check_spans(item, mirror["text"])
                    text = _spans_block(item.source, mirror, item.spans, "Reference document",
                                        request.read_tool)
                else:
                    text = _full_optional(item.source, mirror)
                if mirror.get("cite_id"):
                    extra_ids.append(mirror["cite_id"])
                else:
                    extra_uncitable.append(document_key(item.source))
                row.update(kind="document", required=False, cite_id=mirror.get("cite_id"),
                           content_hash=mirror.get("content_hash"))
        if item.form == "spans":
            row["spans"] = [list(s) for s in item.spans]
        row["chars"] = len(text)
        parts.append(text)
        rows.append(row)
    missing = [read for read in required if read not in seen_reads]
    if missing:
        raise _reject("I4", f"required readings left out of the plan: {missing}")
    joined = "\n\n".join(parts)
    footer = citation_policy(reader._contract, specs, extra_ids=extra_ids,
                             extra_uncitable=extra_uncitable)
    required_size = len(joined) + 2 + len(footer)
    check_budget(required_size, request.max_chars)
    brief, search, dropped = _evidence(reader, plan.task_evidence)
    block = _with_task_evidence((joined, footer, required_size), brief, search,
                                max_chars=request.max_chars)
    omitted.extend({"source": a, "reason": f"anchor:{got['status']}"}
                   for a, got in reader._anchors.items() if "status" in got)
    omitted.extend(dropped)
    counts: dict[str, int] = {}
    for entry in reader._calls:
        counts[entry["method"]] = counts.get(entry["method"], 0) + 1
    manifest = {
        "compiler": COMPILER_VERSION, "client": __version__,
        "policy": {"id": str(getattr(policy, "id", "")), "source_sha256": _policy_digest(policy)},
        "role": {k: reader._contract.get(k) for k in ("id", "rev", "hash")},
        "items": rows, "omitted": omitted,
        "budget": {"max_chars": request.max_chars, "used": len(block)},
        "reads": counts, "read_tool": request.read_tool,
    }
    return block, manifest


def _mirror(reader: PolicyReader, read: str, document: dict[str, Any]) -> dict[str, Any]:
    try:
        node = reader.node(cite_id(read))
    except VincNotFound as exc:
        if exc.code != "not_found":
            raise
        node = {"executed": True, "found": False, "node": None}
    return with_citation(read, document, node)


#: ``read_tool`` 자리에 줄 수 있는 것: 값, 또는 이 호출에서 읽은 역할 계약을 받아 값을 내는 함수.
ReadTool = Union[bool, Callable[[dict], bool]]


def _compile(request: ContextRequest, policy: ContextPolicy | None,
             send: Callable[[str, dict[str, Any]], dict[str, Any]],
             read_tool_for: Callable[[dict], bool] | None = None) -> CompiledContext:
    if not isinstance(request, ContextRequest):
        raise VincInvalidArgument("invalid_argument", "request must be a ContextRequest.")
    if isinstance(request.max_chars, bool) or not isinstance(request.max_chars, int) \
            or request.max_chars <= 0:
        raise VincInvalidArgument("invalid_argument", "max_chars must be a positive integer.")
    if policy is not None and not isinstance(policy, ContextPolicy):
        raise VincInvalidArgument("invalid_argument", "policy needs an id and a plan method.")
    _check_pin(request.pin)
    if isinstance(request.anchors, str) or not all(isinstance(a, str) and a.strip()
                                                    for a in request.anchors):
        raise VincInvalidArgument("invalid_argument", "anchors must be a tuple of ids.")
    # 컴파일러가 쓰는 요청과 정책이 받는 요청을 나눈다. 정책이 받은 것을 바꿔도 검사는 그대로다.
    request = dataclasses.replace(request, anchors=tuple(request.anchors))
    reader = PolicyReader(request, send)
    contract = reader._role()
    reads = reading_list(contract)
    if read_tool_for is not None:
        # 문서 읽기 도구가 있어도 역할이 그 도구를 막거나 승인으로 묶으면 모델은 못 쓴다.
        # 그래서 이 판단은 계약을 읽은 뒤에 한다(I4).
        request = dataclasses.replace(request, read_tool=bool(read_tool_for(contract)))
        reader._request = request
    if policy is None:
        # 기본값은 도구 판단이 끝난 뒤에 고른다. 역할이 문서 도구를 막으면 P0 이다.
        policy = default_policy(request.read_tool)
    reader._contract = contract
    reader._required = tuple((read, _read_required(reader, read)) for read in reads)
    _read_anchors(reader, tuple(request.anchors))
    plan = policy.plan(dataclasses.replace(request), reader)
    block, manifest = _render(request, policy, reader, plan)
    return CompiledContext(block=block, sha256=hashlib.sha256(block.encode("utf-8")).hexdigest(),
                           contract=contract, manifest=manifest, trace=tuple(reader._calls))


def _sender(client: Any) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    view = client.without_retries()

    def send(method: str, args: dict[str, Any]) -> dict[str, Any]:
        if method not in _READS:
            raise VincInvalidArgument("invalid_argument", f"{method} is not a read.")
        args = {k: v for k, v in args.items() if v is not None}
        if method == "role":
            return view.role(args.pop("role_id"), **args)
        if method == "document":
            return view.document(args.pop("doc_id"), **args)
        if method == "node":
            return view.node(args.pop("node_id"), **args)
        return getattr(view, method)(args.pop("query"), **args)
    return send


def compile_context(client: Any, request: ContextRequest, policy: ContextPolicy | None = None, *,
                    read_tool_for: Callable[[dict], bool] | None = None) -> CompiledContext:
    """Read and compile ``request`` with ``client`` (a :class:`VincClient`) under ``policy``.

    Reads run one at a time without retries, like prefetch's sequential mode. Nothing is
    cached across calls and nothing is written. Raises prefetch's error codes for the
    role and its readings, and ``context_plan_rejected`` for a plan that breaks an invariant.

    ``read_tool_for`` decides ``request.read_tool`` from the role contract this call read,
    for a host whose document tool the role itself may disallow.

    With no ``policy`` the choice follows the read tool (:func:`default_policy`):
    :data:`~vinc_client.task_evidence.TASK_EVIDENCE` when the model can read documents,
    :data:`FULL_TEXT` (prefetch's block) when it cannot. ``manifest["policy"]["id"]`` says
    which one ran.
    """
    return _compile(request, policy, _sender(client), read_tool_for)


async def acompile_context(client: Any, request: ContextRequest,
                           policy: ContextPolicy | None = None, *,
                           read_tool_for: Callable[[dict], bool] | None = None) -> CompiledContext:
    """:func:`compile_context` for an :class:`AsyncVincClient`.

    The policy stays synchronous: it runs in a worker thread and each read is awaited on
    the caller's event loop.
    """
    loop = asyncio.get_running_loop()
    view = client.without_retries()
    stop = threading.Event()
    running: set[concurrent.futures.Future] = set()

    def send(method: str, args: dict[str, Any]) -> dict[str, Any]:
        if method not in _READS:
            raise VincInvalidArgument("invalid_argument", f"{method} is not a read.")
        if stop.is_set():
            raise _Cancelled()
        args = {k: v for k, v in args.items() if v is not None}
        first = args.pop({"role": "role_id", "document": "doc_id",
                          "node": "node_id"}.get(method, "query"))
        future = asyncio.run_coroutine_threadsafe(getattr(view, method)(first, **args), loop)
        running.add(future)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            raise _Cancelled() from None
        finally:
            running.discard(future)

    try:
        return await asyncio.to_thread(_compile, request, policy, send, read_tool_for)
    except asyncio.CancelledError:
        # 작업 스레드는 취소할 수 없다. 남은 읽기를 막고 진행 중인 읽기를 취소해 곧 끝나게 한다.
        stop.set()
        for future in list(running):
            future.cancel()
        raise


class _Cancelled(Exception):
    """The caller cancelled :func:`acompile_context`; the worker thread stops at its next read."""


def role_prefetch(client: Any, role: str, task: str, *, policy: ContextPolicy | None = None,
                  max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None,
                  read_tool: ReadTool = False) -> RolePrefetch:
    """What the adapters call instead of ``prefetch_with_contract`` when given a policy.

    ``read_tool`` may be a function of the role contract (see :func:`compile_context`).
    """
    fixed = False if callable(read_tool) else read_tool
    return compile_context(client, ContextRequest(role=role, task=task, pin=pin,
                                                  max_chars=max_chars, read_tool=fixed),
                           policy, read_tool_for=read_tool if callable(read_tool) else None
                           ).as_prefetch()


async def arole_prefetch(client: Any, role: str, task: str, *, policy: ContextPolicy | None = None,
                         max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None,
                         read_tool: ReadTool = False) -> RolePrefetch:
    """:func:`role_prefetch` for an :class:`AsyncVincClient`."""
    fixed = False if callable(read_tool) else read_tool
    return (await acompile_context(client, ContextRequest(
        role=role, task=task, pin=pin, max_chars=max_chars, read_tool=fixed), policy,
        read_tool_for=read_tool if callable(read_tool) else None)).as_prefetch()


def replay_context(compiled: CompiledContext, request: ContextRequest,
                   policy: ContextPolicy | None = None) -> CompiledContext:
    """Compile again from ``compiled.trace`` alone, with no network.

    A read the trace does not hold is refused as I7. Comparing the two ``sha256`` values
    checks that the policy depends on nothing but its reads.
    """
    recorded = {_key(e["method"], e["args"]): e for e in compiled.trace}
    if any("response" in e and _digest(e["response"]) != e.get("sha256") for e in compiled.trace):
        raise _reject("I7", "the trace holds an answer that does not match its recorded hash")

    def send(method: str, args: dict[str, Any]) -> dict[str, Any]:
        entry = recorded.get(_key(method, args))
        if entry is None:
            raise _reject("I7", f"replay asked for a read the trace does not hold: {method}")
        if "error" in entry:
            _raise_recorded(entry["error"])
        return entry["response"]

    return _compile(request, policy, send)


def check_replay(compiled: CompiledContext, request: ContextRequest,
                 policy: ContextPolicy | None = None) -> None:
    """Raise I7 when replaying ``compiled`` does not give the same block bytes."""
    again = replay_context(compiled, request, policy)
    if again.sha256 != compiled.sha256:
        raise _reject("I7", "the same reads gave a different block")
