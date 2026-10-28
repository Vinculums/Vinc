"""The default context policy when the model has a document-read tool.

:data:`TASK_EVIDENCE` keeps the role contract whole and shows each required reading as a
pointer (the model reads it with the document tool when it needs it). Its room goes to the
task instead: the nodes that the task's briefs and searches agree on, rendered whole, one
document's sections around the matched snippet, a resolved brief and the top search rows.

Every choice is a function of the request and the reader's answers. Nothing is random,
nothing depends on the clock, and nothing is tuned to one language or corpus.

Chosen by a policy search on a pinned store (VIN-790). Graph record:
``record:the-task-evidence-context-policy-beat-full-text-on-held-out-agent-tasks-at-a-third-of-the-tokens-2026-10-28``.
"""
from __future__ import annotations

import re
from typing import Any

from .compiler import ContextItem, ContextPlan, ContextRequest, EvidenceRef, PolicyReader
from .errors import VincError, VincNotFound
from .prefetch import document_key, verified_document

#: 노드 항목 수. 성공한 답이 인용한 노드는 거의 늘 융합 순위 앞쪽 셋 안이었다.
NODE_ITEMS = 6
#: 필수가 아닌 문서는 하나만, 일치한 단락 둘레로 이 글자 수까지.
OPTIONAL_DOCS = 1
DOC_SPAN_CHARS = 2600
SEARCH_LIMIT = 10
#: 순위 융합(reciprocal rank)의 k. 작을수록 각 목록의 첫 몇 자리가 무겁다.
RRF_K = 5
KIND_BOOST = {"decision": 1.15, "record": 1.15, "episode": 1.1}
#: 블록에 남기는 검색 행 수. 노드 항목으로 이미 실은 적중은 다시 싣지 않는다.
EVIDENCE_ROWS = 3
#: 과제의 라틴 문자 비율이 이보다 높으면 두 번째 질의(라틴 낱말만)를 하지 않는다.
LATIN_ENOUGH = 0.7

_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "are", "was", "not", "but",
    "you", "can", "how", "why", "what", "when", "then", "than", "just", "also", "use", "ok",
    "is", "it", "to", "of", "in", "on", "or", "be", "do", "an", "as", "at", "by", "if", "so",
}
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:[./:-][A-Za-z0-9_]+)*")
_HEADING = re.compile(r"(?m)^#{1,6} .*$")


def _safe(fn, *args, **kwargs):
    # 과제 근거 읽기의 실패는 그 근거를 빼고 계속한다. 필수 문서의 실패는 컴파일러가 먼저 올린다.
    try:
        return fn(*args, **kwargs)
    except VincError:
        return None


def _latin_terms(task: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in _LATIN.finditer(task):
        tok = m.group(0)
        if len(tok) >= 2 and tok.lower() not in _STOP:
            seen.setdefault(tok, None)
    return list(seen)


def _second_query(task: str) -> str:
    """과제가 주로 라틴 문자가 아니면, 그 안의 식별자·명령·영어 낱말만 모은 질의.

    다른 말로 쓴 과제가 영어 문서의 키워드 갈래에 닿게 한다. 영어 과제는 제 글로 이미 닿는다.
    """
    terms = " ".join(_latin_terms(task))
    letters = [ch for ch in task if ch.isalpha()]
    latin_share = sum(1 for ch in letters if ch.isascii()) / max(1, len(letters))
    return "" if len(terms) < 2 or latin_share > LATIN_ENOUGH else terms


def _prefix(node_id: str) -> str:
    return node_id.split(":", 1)[0] if ":" in node_id else ""


def _doc_shaped(node_id: str) -> bool:
    return ":" not in node_id or node_id.startswith("concept:doc.")


def _brief_list(brief: dict | None) -> list[dict]:
    if not brief:
        return []
    if brief.get("status") == "resolved":
        out = [brief.get("node") or {}]
        out.extend((brief.get("links") or {}).get("items") or [])
        return [n for n in out if n.get("id")]
    return [c for c in brief.get("candidates") or [] if c.get("id")]


def _fuse_nodes(lists: list, role_id: str, own_domain: str) -> list[tuple[str, dict]]:
    score: dict[str, float] = {}
    info: dict[str, dict] = {}
    for weight, lst in lists:
        for rank, row in enumerate(lst):
            nid = row.get("id")
            if not isinstance(nid, str) or not nid or nid == role_id:
                continue
            score[nid] = score.get(nid, 0.0) + weight / (RRF_K + rank)
            info.setdefault(nid, row)
    out = []
    for nid, s in score.items():
        row = info[nid]
        if own_domain and row.get("domain") == own_domain:
            continue   # 역할 자신의 토픽에 있는 역할 카드는 과제 근거가 아니다
        out.append((s * KIND_BOOST.get(row.get("kind") or _prefix(nid), 1.0), nid))
    out.sort(key=lambda p: (-p[0], p[1]))
    return [(nid, info[nid]) for _, nid in out]


def _node_ok(reader: PolicyReader, node_id: str) -> bool:
    got = _safe(reader.node, node_id)
    if not isinstance(got, dict) or got.get("found") is False:
        return False
    node = got.get("node")
    return isinstance(node, dict) and node.get("id") == node_id


def _mirror_ok(reader: PolicyReader, doc_id: str) -> bool:
    # 컴파일러가 렌더링하며 문서의 미러 노드를 읽는다. 그 읽기가 실패할 문서는 고르지 않는다.
    try:
        got = reader.node("concept:doc." + doc_id)
    except VincNotFound as exc:
        return exc.code == "not_found"
    except VincError:
        return False
    if got.get("executed") is not True or got.get("error"):
        return False
    node = got.get("node")
    if got.get("found") is False and node is None:
        return True
    return got.get("found") is True and isinstance(node, dict) \
        and node.get("id") == "concept:doc." + doc_id


# ── 문서 안의 단락 ────────────────────────────────────────────────────────────

def _norm(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    idx: list[int] = []
    prev = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev:
                continue
            out.append(" ")
            idx.append(i)
            prev = True
        else:
            out.append(ch)
            idx.append(i)
            prev = False
    return "".join(out), idx


def _locate(norm: tuple[str, list[int]], snippet: str) -> int | None:
    """검색 행이나 발췌(``snippet``)가 원문에서 시작하는 자리."""
    s = " ".join((snippet or "").replace("…", " ").split())
    if len(s) < 24:
        return None
    text, idx = norm
    for probe in (s[:60], s[len(s) // 2 - 25: len(s) // 2 + 25], s[-60:]):
        probe = probe.strip()
        if len(probe) < 20:
            continue
        p = text.find(probe)
        if p >= 0:
            return idx[p]
    return None


def _sections(text: str) -> list[tuple[int, int, int]]:
    """제목으로 나눈 단락마다 (시작, 제목 끝, 끝). 제목이 없으면 단락 하나."""
    heads = [(m.start(), m.end()) for m in _HEADING.finditer(text)]
    if not heads:
        return [(0, 0, len(text))]
    out = []
    if heads[0][0] > 0:
        out.append((0, 0, heads[0][0]))
    for i, (s, e) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append((s, e, end))
    return out


def _section_of(sections, pos: int):
    for sec in sections:
        if sec[0] <= pos < sec[2]:
            return sec
    return sections[-1]


def _window(text: str, sec, pos: int | None, cap: int) -> list[tuple[int, int]]:
    """단락의 제목 줄과, ``pos`` 둘레로 ``cap`` 안에 드는 본문 창."""
    start, head_end, end = sec
    if end - start <= cap:
        return [(start, end)]
    anchor = start if pos is None else pos
    lo = max(head_end, anchor - cap // 4)
    hi = min(end, lo + cap - (head_end - start))
    if hi - lo < cap // 2:
        lo = max(head_end, hi - (cap - (head_end - start)))
    nl = text.find("\n", lo, hi)
    if lo > head_end and nl != -1 and nl + 1 < hi:
        lo = nl + 1
    nl = text.rfind("\n", lo, hi)
    if hi < end and nl > lo:
        hi = nl
    spans = []
    if head_end > start:
        spans.append((start, head_end))
    if hi > lo:
        spans.append((lo, hi))
    return spans


def _merge(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    out: list[list[int]] = []
    for s, e in sorted(spans):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return tuple((s, e) for s, e in out)


def _snippet_spans(text: str, snippets: list[str], cap: int) -> tuple[tuple[int, int], ...]:
    norm = _norm(text)
    sections = _sections(text)
    spans: list[tuple[int, int]] = []
    used = 0
    seen_secs = []
    for snip in snippets:
        pos = _locate(norm, snip)
        if pos is None:
            continue
        sec = _section_of(sections, pos)
        if sec in seen_secs:
            continue
        room = cap - used
        if room < 400:
            break
        win = _window(text, sec, pos, room)
        spans.extend(win)
        used += sum(e - s for s, e in win)
        seen_secs.append(sec)
    return _merge(spans)


# ── 정책 ──────────────────────────────────────────────────────────────────────

class TaskEvidencePolicy:
    """Required readings as pointers; the room goes to what the task's reads agree on.

    Needs ``read_tool=True`` (I4): with no document-read tool the compiler refuses a
    pointer, and :func:`compile_context` falls back to :data:`FULL_TEXT` by default.
    """

    id = "task-evidence-1"

    def plan(self, request: ContextRequest, reader: PolicyReader) -> ContextPlan:
        contract = reader.contract
        role_id = contract.get("id") or ""
        own_domain = contract.get("domain") or ""
        required = list(reader.required)
        required_keys = {document_key(read) for read, _ in required}
        task = request.task
        q2 = _second_query(task)

        # 1. 과제 읽기. P0 와 달리 질의에 역할 토픽을 붙이지 않는다(붙이면 역할 자신의 토픽이 결과를 채운다).
        b1 = _safe(reader.brief, task, max_excerpts=3)
        b1_call = reader.last_call if b1 is not None else None
        s1 = _safe(reader.search, task, limit=SEARCH_LIMIT)
        s1_call = reader.last_call if s1 is not None else None
        b2 = s2 = None
        if q2:
            b2 = _safe(reader.brief, q2, max_excerpts=3)
            s2 = _safe(reader.search, q2, limit=SEARCH_LIMIT)

        # 2. 노드 후보를 순위 융합해 앞쪽을 노드 항목으로 싣는다(인용할 수 있고 근거 한도 밖이다)
        lists = [(1.0, _brief_list(b1)), (1.0, (s1 or {}).get("node_hits") or []),
                 (1.0, _brief_list(b2)), (1.0, (s2 or {}).get("node_hits") or [])]
        fused = _fuse_nodes(lists, role_id, own_domain)
        node_ids: list[str] = []
        for nid, _row in fused:
            if len(node_ids) >= NODE_ITEMS:
                break
            if not _doc_shaped(nid) and _node_ok(reader, nid):
                node_ids.append(nid)

        # 3. 싣는 brief: 동점 후보 목록 대신 융합 1위가 풀린 brief 를 고른다(본문·발췌·관계가 실린다)
        brief_call = b1_call
        briefs = [b for b in (b1, b2) if b]
        if node_ids:
            target = node_ids[0]
            if not (b1 is not None and b1.get("status") == "resolved"
                    and (b1.get("node") or {}).get("id") == target):
                title = (dict(fused).get(target) or {}).get("title") or ""
                b3 = _safe(reader.brief, title, max_excerpts=3) if title.strip() else None
                if b3 is not None and b3.get("status") == "resolved" \
                        and (b3.get("node") or {}).get("id") == target:
                    brief_call = reader.last_call
                    briefs.append(b3)
                elif b1 is not None and b1.get("status") != "resolved" and b2 is not None \
                        and b2.get("status") == "resolved" and (b2.get("node") or {}).get("id") in node_ids:
                    reader.brief(q2, max_excerpts=3)
                    brief_call = reader.last_call

        # 4. 필수가 아닌 문서: 검색 행과 발췌로 순위를 매기고, 일치한 단락 둘레만 싣는다
        doc_score: dict[str, float] = {}
        doc_snips: dict[str, list[str]] = {}
        for result in (s1, s2):
            for rank, row in enumerate((result or {}).get("rows") or []):
                did = row.get("doc_id")
                if not isinstance(did, str) or not did:
                    continue
                doc_score[did] = doc_score.get(did, 0.0) + 1.0 / (RRF_K + rank)
                doc_snips.setdefault(did, []).append(row.get("snippet") or "")
        for b in briefs:
            for rank, ex in enumerate(b.get("excerpts") or []):
                did = ex.get("doc_id")
                if not isinstance(did, str) or not did:
                    continue
                doc_score[did] = doc_score.get(did, 0.0) + 0.5 / (RRF_K + rank)
                doc_snips.setdefault(did, []).append(ex.get("excerpt") or "")
        optional: list[ContextItem] = []
        for did in sorted(doc_score, key=lambda d: (-doc_score[d], d)):
            if len(optional) >= OPTIONAL_DOCS:
                break
            if did in required_keys or ":" in did:
                continue
            doc = _safe(reader.document, did)
            if not isinstance(doc, dict) or not verified_document(doc, did) \
                    or not _mirror_ok(reader, did):
                continue
            spans = _snippet_spans(doc["text"], doc_snips.get(did, []), DOC_SPAN_CHARS)
            if spans:
                optional.append(ContextItem(did, form="spans", spans=spans,
                                            reason="the task's search hit this document"))

        # 5. 필수 문서는 포인터로. 모델이 문서 읽기 도구로 읽는다(I4)
        req_items = [ContextItem(read, form="pointer", reason="pointer; the task evidence carries the specifics")
                     for read, _ in required]

        # 6. 근거: 고른 brief 와, 이미 실은 문서의 행을 뺀 과제 검색의 앞 행들
        evidence = []
        if brief_call is not None:
            evidence.append(EvidenceRef(brief_call))
        if s1_call is not None:
            shown_docs = {it.source for it in optional}
            rows = [r.get("id") for r in (s1 or {}).get("rows") or []
                    if r.get("id") and r.get("doc_id") not in shown_docs]
            evidence.append(EvidenceRef(s1_call, ids=tuple(rows[:EVIDENCE_ROWS])))

        nodes = [ContextItem(nid, reason="the task's reads agree on this node") for nid in node_ids]
        return ContextPlan(items=tuple(nodes + optional + req_items), task_evidence=tuple(evidence))


#: 문서 읽기 도구가 있을 때의 기본값(VIN-790). 없으면 :data:`FULL_TEXT` 가 기본값이다.
TASK_EVIDENCE = TaskEvidencePolicy()
