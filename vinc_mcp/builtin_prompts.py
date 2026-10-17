# -*- coding: utf-8 -*-
"""그래프와 상관없이 늘 나가는 내장 프롬프트. 역할 프롬프트(`roles.py`)와 따로 산다.

역할 프롬프트는 그래프에서 읽은 데이터라 울타리 안에 넣는다. 여기 본문은 서버가 쓴
지시라 울타리가 없고, 스토어를 읽지 않으므로 `prompts/get` 이 브리지를 부르지 않는다.

이름은 `agent-` 로 시작하면 안 된다. 역할 슬러그는 전부 그 접두를 갖고, `rpc` 는 이
모듈의 이름을 먼저 대조하므로 겹치면 역할 하나가 가려진다.
"""
from __future__ import annotations

import re

BUILD_GRAPH = "build-graph-from-documents"

_DESCRIPTION = ("Build the graph from stored documents in about three tool calls per document: "
                "read it once, write its concepts in one call, then its relations and records "
                "in one call.")

_ARGUMENTS = [{
    "name": "doc_ids",
    "description": "Optional doc ids to work on, separated by commas or spaces. Omit to list "
                   "the stored documents first.",
    "required": False,
}]

#: 문서 id 한 개. 백틱과 공백을 받지 않으므로 인자가 아래 코드 표기 밖으로 못 나온다.
_DOC_ID = re.compile(r"[^\s,`]+")

#: 본문. 도구 이름과 인자 이름은 `tools.py` 스키마와 같아야 한다(tests/mcp/test_builtin_prompts.py).
_BUILD_GRAPH_TEXT = """\
Build this graph from documents already stored in it, in as few tool calls as you can.

The daily limit counts tool calls, not nodes, and the write tools take many items per call. \
So work in batches: about three calls per document, never one call per concept, record or edge.

{documents}

Once, before the first write: call vinc_conventions to learn the node kinds, relation names \
and existing domains to reuse.

For each document:
1. Read it once with vinc_doc_get(doc_id). Skip this when its full text is already in this \
conversation. If verified is false, say so and skip the document instead of building from \
partial text.
2. Write its concepts in one call: vinc_doc_extract(doc_id, apply: true, candidates: \
[{{title, one_liner}}, ...]) with your own list of the document's key concepts. Skip the \
apply: false preview, since you chose the list. Write each title the way the document writes \
it, so the server links the concept to the passages that mention it. The server derives each \
id as concept:<slug>, with the slug returned on each candidate, and reuses an existing concept \
with the same slug instead of overwriting it. If apply is refused because the document has no \
topic, give it one with vinc_doc_put(doc_id, text, topic) and apply again.
3. Write everything else in one call: a single vinc_ingest whose nodes and edges carry the \
relations between the concepts (relates_to, prerequisite_of, derives_from, inherits_from), a \
record for each claim, figure or fact the document states, with a relates_to edge from the \
record to each concept it is about, and a decision with about edges if the document records \
one. Link each record to its source with derives_from to concept:doc.<doc_id>. Refer to the \
concepts from step 2 by id instead of sending them again. Read warnings in the reply: merged \
counts only what landed.

Do not search for each concept. When you need an id this session has not seen, one \
vinc_search for the document is enough. If the work belongs in a team space, pass the same \
space on every call.

At the end, report for each document the calls it took, the concepts created and matched, \
the records and edges merged, and any warnings."""


def is_builtin(name: str) -> bool:
    return str(name or "").strip() == BUILD_GRAPH


def listed() -> list[dict]:
    """`prompts/list` 에 역할보다 앞에 놓는 항목들."""
    return [{"name": BUILD_GRAPH, "description": _DESCRIPTION, "arguments": _ARGUMENTS}]


def _documents_line(raw) -> str:
    ids = _DOC_ID.findall(str(raw or ""))
    if not ids:
        return ("Documents: list them with one vinc_doc_list call (limit up to 200), then work "
                "through them one at a time.")
    return "Documents: " + ", ".join(f"`{i}`" for i in ids) + "."


def get(name: str, arguments: dict | None) -> dict:
    """`prompts/get` 응답. 스토어를 읽지 않는다."""
    if not is_builtin(name):
        raise ValueError(f"unknown prompt: {name}")
    text = _BUILD_GRAPH_TEXT.format(documents=_documents_line((arguments or {}).get("doc_ids")))
    return {"description": _DESCRIPTION,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
