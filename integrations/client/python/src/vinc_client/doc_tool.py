"""The document read tool's work, shared by every framework package: read, check, cut, fence.

A model calls ``vinc_doc_get`` to read a canonical document by id, whole or in part, for
example a required reading that a context policy shortened. The answer is data inside the
same boundary as the other read tools. A citable id is named only when the same-space
mirror read finds it, as prefetch does. Nothing here writes.

Sections are ATX headings (``#`` to ``######``) outside fenced code; setext headings
(underlined with ``=`` or ``-``) are not sections. Duplicate headings: ``section`` takes the
first, and the heading list gives each one's start offset for a range read.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .context import BOUNDARY, fence
from .errors import VincNotFound, VincQuotaExceeded, VincUnavailable
from .prefetch import cite_id, document_key, verified_document
from .tool_policy import RoleToolPolicy
from .tools import DOC_MAX_CHARS, DOC_NAME

DOWN = "Vinc is not available right now ({code}); answer without it."
MISSING = "Vinc found nothing for that ({code}); try a different query."
UNVERIFIED = ("Vinc could not verify the document {doc_id} (incomplete or changed while read); "
              "do not rely on it.")
# 닫는 # 은 앞에 공백이 있을 때만 떼어 낸다(`## C#` 의 #는 제목이다).
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
_STATE = ("validity", "valid_from", "valid_until", "superseded_by")
#: 제목이 없다고 답할 때 싣는 제목 목록의 한도.
HEADINGS_SHOWN = 50
HEADING_CHARS = 200


def doc_tool_allowed(contract: dict[str, Any], capability_map: Any = None) -> bool:
    """Whether the role lets the model use ``vinc_doc_get``: not disallowed, not held for a
    person's approval, and a tool-call budget that is not 0."""
    policy = RoleToolPolicy.from_contract(contract, capability_map)
    return policy.allows(DOC_NAME) and policy.tool_calls != 0


def _heads(text: str) -> list[tuple[int, str, int]]:
    """(level, title, start offset) of each ATX heading outside fenced code."""
    out: list[tuple[int, str, int]] = []
    fence: str | None = None
    offset = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        mark = _FENCE.match(bare)
        if fence is not None:
            if mark and mark.group(1)[0] == fence[0] and len(mark.group(1)) >= len(fence):
                fence = None
        elif mark:
            fence = mark.group(1)
        else:
            head = _HEADING.match(bare)
            if head:
                out.append((len(head.group(1)), " ".join(head.group(2).split()), offset))
        offset += len(line)
    return out


def _section(text: str, name: str) -> tuple[int, int] | None:
    heads = _heads(text)
    want = " ".join(name.split()).lower()
    for i, (level, title, start) in enumerate(heads):
        if title.lower() == want:
            end = next((s for lv, _, s in heads[i + 1:] if lv <= level), len(text))
            return start, end
    return None


def _fenced(data: dict[str, Any]) -> str:
    return f"{BOUNDARY}\n\n{fence(json.dumps(data, ensure_ascii=False, indent=2))}"


def _no_heading(key: str, text: str, section: str, max_chars: int) -> str:
    # 제목 목록도 문서 내용이다. 경계 안에 싣고 하나·전체 길이를 자른다.
    heads = [{"title": title[:HEADING_CHARS], "start": start}
             for _, title, start in _heads(text)[:HEADINGS_SHOWN]]
    data: dict[str, Any] = {"doc_id": key, "error": "no such heading",
                            "section": section[:HEADING_CHARS], "headings": heads}
    while heads and len(json.dumps(data, ensure_ascii=False)) > max_chars:
        heads.pop()
    return _fenced(data)


def format_document(doc_id: str, document: dict[str, Any], mirror: dict[str, Any] | None, *,
                    start: int | None = None, end: int | None = None,
                    section: str | None = None, max_chars: int = DOC_MAX_CHARS) -> str:
    """The tool's answer for one read, or a sentence saying why there is none.

    ``mirror`` is the same-space node read of ``concept:doc.<key>`` (``None`` when absent).
    At most ``max_chars`` characters of text come back; ``next_start`` says where to go on.
    """
    key = document_key(doc_id)
    if not verified_document(document, key):
        return UNVERIFIED.format(doc_id=key)
    text = document["text"]
    if section is not None and (start is not None or end is not None):
        return "Give a character range or a section, not both."
    if (start is not None and (type(start) is not int or start < 0)) or \
            (end is not None and (type(end) is not int or end < 1)):
        return "start must be a whole number of 0 or more, and end of 1 or more."
    if section is not None:
        found = _section(text, section)
        if found is None:
            return _no_heading(key, text, section, max_chars)
        start, end = found
    start = 0 if start is None else start
    end = len(text) if end is None else min(end, len(text))
    if start >= len(text) or end <= start:
        return f"The document {key} has {len(text)} characters; that range is empty."
    stop = min(end, start + max_chars)
    node = (mirror or {}).get("node") if isinstance(mirror, dict) else None
    cited = isinstance(node, dict) and node.get("id") == cite_id(key)
    data: dict[str, Any] = {
        "doc_id": key, "title": document.get("title"), "content_hash": document["content_hash"],
        "cite_as": cite_id(key) if cited else "no citable id; do not cite this document",
        "chars": len(text), "start": start, "end": stop}
    if stop < end:
        # 범위(또는 절) 끝을 함께 준다. 이어 읽기는 start=next_start, end=range_end 다.
        data["next_start"] = stop
        data["range_end"] = end
    data.update({k: document[k] for k in _STATE if document.get(k) not in (None, "")})
    data["text"] = text[start:stop]
    return _fenced(data)


def _mirror_or_none(read) -> dict[str, Any] | None:
    try:
        return read()
    except VincNotFound as exc:
        if exc.code != "not_found":
            raise
    except (VincUnavailable, VincQuotaExceeded):
        pass
    return None


def read_document(client: Any, doc_id: str, *, start: int | None = None, end: int | None = None,
                  section: str | None = None, max_chars: int = DOC_MAX_CHARS) -> str:
    """Run the tool once with a :class:`VincClient` (callers pass a ``without_retries`` view)."""
    key = document_key(doc_id)
    try:
        document = client.document(key)
    except (VincUnavailable, VincQuotaExceeded) as exc:
        return DOWN.format(code=exc.code)
    except VincNotFound as exc:
        if exc.code != "not_found":
            raise
        return MISSING.format(code=exc.code)
    mirror = _mirror_or_none(lambda: client.node(cite_id(key)))
    return format_document(key, document, mirror, start=start, end=end, section=section,
                           max_chars=max_chars)


async def aread_document(client: Any, doc_id: str, *, start: int | None = None,
                         end: int | None = None, section: str | None = None,
                         max_chars: int = DOC_MAX_CHARS) -> str:
    """:func:`read_document` with an :class:`AsyncVincClient`."""
    key = document_key(doc_id)
    try:
        document = await client.document(key)
    except (VincUnavailable, VincQuotaExceeded) as exc:
        return DOWN.format(code=exc.code)
    except VincNotFound as exc:
        if exc.code != "not_found":
            raise
        return MISSING.format(code=exc.code)
    try:
        mirror = await client.node(cite_id(key))
    except VincNotFound as exc:
        if exc.code != "not_found":
            raise
        mirror = None
    except (VincUnavailable, VincQuotaExceeded):
        mirror = None
    return format_document(key, document, mirror, start=start, end=end, section=section,
                           max_chars=max_chars)
