# -*- coding: utf-8 -*-
"""MCP 읽기 결과의 모양: 상한을 넘는 결과를 줄이는 축소기와, 읽기 넷의 기본 다이어트(lean projection).

`rpc` 가 tools/call 결과를 봉투에 싣기 전에 부른다(`rpc._mcp_text` · `rpc._result_text`). 도구 핸들러의
결과는 건드리지 않으므로 REST v1 · CLI 의 꼴은 그대로다. `tools` 가 이 모듈의 이름을 그대로 다시
내보낸다(`rpc` 와 테스트가 `tools.X` 로 부른다). 이 모듈은 `tools` 를 import 하지 않는다.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Callable


# ── 결과 크기 축소기(GM-14) ──────────────────────────────────────────────────
# 봉투(rpc._result_text)가 상한 초과 결과를 **거부하는 대신** 여기 등록된 도구는
# 꼬리 행을 덜어 페이지로 만든다. vinc_mcp 는 api 패키지를 임포트하지 않으므로
# (독립 브리지 계약) 커서 코덱을 여기 미러한다 — 두 사본의 왕복 호환은
# tests/rag/test_search_pagination.py 의 교차 계약 테스트가 고정한다.

def _cursor_encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _shrink_search_result(out: dict, budget: int) -> dict | None:
    """vinc_search 결과를 예산 안으로 줄인다 — 실패(줄일 수 없음)면 None.

    사이드카가 준 `page`(offset·fingerprint) 좌표로 next_cursor 를 다시 써서,
    덜어낸 꼬리를 다음 페이지로 이어 읽을 수 있게 한다. `page` 가 없으면(구
    사이드카) 축소를 포기하고 기존 거부 경로로 돌려보낸다."""
    if not isinstance(out, dict):
        return None
    rows = out.get("rows")
    page = out.get("page")
    if not isinstance(rows, list) or not rows or not isinstance(page, dict):
        return None
    if not page.get("fingerprint"):
        return None
    dropped: list = []
    while rows and len(json.dumps(out, ensure_ascii=False)) > budget:
        dropped.insert(0, rows.pop())
    if not rows:
        return None                     # 행 1개조차 예산을 넘는다 — 거부가 정직하다
    if dropped:
        next_off = int(page.get("offset") or 0) + len(rows)
        row_offsets = page.get("row_offsets")
        if isinstance(row_offsets, dict):
            next_off = int(row_offsets.get(str(dropped[0].get("id") or ""), next_off))
        out["has_more"] = True
        out["next_cursor"] = _cursor_encode(
            {"v": 1, "off": next_off,
             "qh": page.get("fingerprint"),
             "eid": str(dropped[0].get("id") or ""),
             "tc": out.get("total_count"), "arms": out.get("arms")})
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + \
            "page shrunk to fit the result size limit; continue with next_cursor"
    return out


#: 봉투가 참조하는 도구별 축소기 레지스트리. 미등재 도구는 기존 거부 유지
#: (JSON 을 중간에서 자르면 깨진 구문이 된다). 등재된 것도 문자 단위로는 안 자른다 —
#: 덜 중요한 **필드나 항목**을 통째로 덜어낸다.
def _shrink_material_lists(block: dict, over: Callable[[], bool]) -> tuple[int, int]:
    """발췌를 먼저, 관계를 나중에. `over()` 가 참인 동안 항목을 덜어낸다."""
    dropped_ex = dropped_ln = 0
    ex = block.get("excerpts")
    links = block.get("links")
    items = links.get("items") if isinstance(links, dict) else None
    if isinstance(ex, list):
        while ex and over():
            ex.pop()
            dropped_ex += 1
    if isinstance(items, list):
        while items and over():
            items.pop()
            dropped_ln += 1
    return dropped_ex, dropped_ln


def _shrink_brief_result(out: dict, budget: int) -> dict | None:
    """vinc_brief 결과를 예산 안으로 줄인다 — **발췌를 먼저, 관계를 나중에** 덜어낸다.

    관계는 그 노드의 근거 지도라 마지막까지 남긴다. 발췌는 `grounds` 에 doc_id 가
    남으므로 vinc_doc_get 으로 되찾을 수 있다 — 덜어내도 길이 끊기지 않는다는 뜻이다.
    줄일 수 없으면 None 을 돌려 기존 거부 경로로 보낸다(조용한 절단보다 거부가 정직하다).
    근거: [[vinc.plan.mcp-agent-surface §4]]"""
    if not isinstance(out, dict):
        return None

    def _size() -> int:
        return len(json.dumps(out, ensure_ascii=False))

    def _over() -> bool:
        return _size() > budget

    dropped_ex = dropped_ln = 0
    # choose 의 첫 후보 재료(`candidate_material`)의 발췌가 맨 먼저다 — 확정 답이 아니다.
    if isinstance(out.get("candidate_material"), dict):
        dropped_ex += _shrink_material_lists(out["candidate_material"], _over)[0]
    # 확정 답에서는 발췌 → 질문이 가리키는 절(`section`) → 관계 순서다. 절은 발췌보다 답에
    # 가깝고, 관계는 근거 지도라 마지막이다. 절의 미러는 `grounds` 에 남아 되찾을 수 있다.
    ex = out.get("excerpts")
    while isinstance(ex, list) and ex and _over():
        ex.pop()
        dropped_ex += 1
    dropped_sec = 0
    if _over() and isinstance(out.get("section"), dict):
        out.pop("section")
        dropped_sec = 1
    # 인용 단위(`quotes`)는 절 다음이다: 문서 id 가 `grounds` 와 후보에 남아 vinc_doc_get 으로
    # 되찾을 수 있다. 확정 답의 것을 먼저, 후보의 것은 뒤 후보부터 덜어낸다.
    dropped_q = 0
    if _over() and out.get("quotes"):
        out.pop("quotes")
        dropped_q += 1
    cands = out.get("candidates")
    if isinstance(cands, list):
        for c in reversed(cands):
            if _over() and isinstance(c, dict) and c.get("quotes"):
                c.pop("quotes")
                dropped_q += 1
    de, dl = _shrink_material_lists(out, _over)
    dropped_ex += de
    dropped_ln += dl
    if _size() > budget:
        return None
    if dropped_ex or dropped_ln or dropped_sec or dropped_q:
        out["shrunk"] = {"excerpts_dropped": dropped_ex, "links_dropped": dropped_ln}
        if dropped_sec:
            out["shrunk"]["section_dropped"] = True
        if dropped_q:
            out["shrunk"]["quotes_dropped"] = dropped_q
        note = ("shrunk to fit the result size limit: dropped %d excerpt(s)%s then %d "
                "link(s); `grounds` still lists every id"
                % (dropped_ex, ", the section" if dropped_sec else "", dropped_ln))
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + note
    return out


def _shrink_graph_result(out: dict, budget: int) -> dict | None:
    """vinc_graph 결과를 예산 안으로 줄인다 — **시드는 통째로, 이웃은 껍데기만**.

    순서: 이웃 노드의 `props` → 이웃 노드의 `one_liner` → 엣지의 `evidence` → 시드에
    닿지 않는 엣지 → 시드의 직접 이웃이 아닌 노드 → **직접 고리를 id·label·kind 로**
    → **직접 고리의 꼬리**. 시드 노드는 손대지 않는다. 이 도구로 노드 **하나**의
    props 를 읽는 독자가 있기 때문이다: `agents_sync --pull` 과 역할 껍데기의 "두
    호출"이 그것이고, 2026-09-05 에 `concept:agent-dev-account` 의 depth 1 이 75,481자로
    거부되자 `--pull` 이 죽었다(Vinc#204 후속).

    둘째 고리 단은 왜 있나: depth 1 은 개념 이웃만이 아니라 그 이웃들에 붙은 record·
    episode 와 그들 사이의 엣지까지 싣는다(F14 합류). `concept:agent-dev-app` 은 직접
    이웃이 일곱인데 depth 1 이 87,417자였다(2026-09-05).

    고리 단 둘은 왜 있나: 허브는 직접 고리만으로 상한을 넘긴다(`concept:vinc-mcp`
    depth 1 = 342,008자). 여태 여기서 None 을 돌려 **허브는 어느 깊이로도 못 읽었다**.
    이제 고리를 id·제목·종류만 남기고, 그래도 넘치면 고리의 꼬리를 덜고 몇 개를
    덜었는지(`ring_truncated`)와 나머지를 읽을 길(vinc_neighbors)을 말한다. 고리
    순서는 degree 내림차순(동률은 id)이라 남는 것은 가장 많이 이어진 이웃이다.

    `note` 는 **남은 것을 세어서** 쓴다. 고정 문구("직접 고리는 온전하다")가 고리를
    잃은 결과에도 붙어 있었다(감사 §5-5).

    덜어낸 것은 되찾을 수 있다: 이웃의 props 는 그 이웃의 id 로 다시 부르면 나오고,
    엣지의 evidence 는 `vinc_brief` 가, 둘째 고리는 이웃을 시드로 다시 부르면 나온다.
    시드 자신이 예산을 넘길 때만 None 을 돌려 거부 경로로 보낸다.
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4"""
    if not isinstance(out, dict) or not isinstance(out.get("nodes"), list):
        return None

    def _j(v) -> int:
        return len(json.dumps(v, ensure_ascii=False))

    # 매 단계 전체를 다시 직렬화하면 허브(노드 수백·34만 자)에서 제곱 비용이 된다.
    # 값 하나를 바꿀 때 전체 길이의 변화는 그 값의 직렬화 길이 차와 정확히 같으므로
    # 그 차로 따라가고, 끝에서 한 번 실제로 잰다.
    cur = _j(out)
    seed = out.get("seed")
    nodes = out["nodes"]
    edges = out.get("edges") if isinstance(out.get("edges"), list) else None

    # 원본 기준 고리·바깥 — note 가 "무엇이 남았나"를 이 둘에 대어 센다.
    ring0: set = set()
    for e in edges or []:
        if isinstance(e, dict) and seed in (e.get("source"), e.get("target")):
            ring0.add(e.get("target") if e.get("source") == seed else e.get("source"))
    ring0.discard(seed)
    all_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    ring0 &= all_ids
    far0 = all_ids - ring0 - {seed}

    dropped = {"neighbour_props": 0, "neighbour_one_liners": 0, "edge_evidence": 0}
    for key, empty, counter in (("props", {}, "neighbour_props"),
                                ("one_liner", "", "neighbour_one_liners")):
        for n in nodes:
            if cur <= budget:
                break
            if isinstance(n, dict) and n.get("id") != seed and n.get(key):
                cur += _j(empty) - _j(n[key])
                n[key] = empty
                dropped[counter] += 1
    if edges is not None:
        for e in edges:
            if cur <= budget:
                break
            if isinstance(e, dict) and e.get("evidence"):
                cur += _j("") - _j(e["evidence"])
                e["evidence"] = ""
                dropped["edge_evidence"] += 1
    dropped["far_edges"] = dropped["far_nodes"] = 0
    if edges is not None and seed and cur > budget:
        # 시드에 닿지 않는 엣지(이웃끼리, 이웃과 그 record 사이)를 덜어낸다.
        near = [e for e in edges
                if isinstance(e, dict) and seed in (e.get("source"), e.get("target"))]
        dropped["far_edges"] = len(edges) - len(near)
        edges[:] = near
        cur = _j(out)
        if cur > budget:
            # 시드의 직접 이웃이 아닌 노드(둘째 고리)를 덜어낸다.
            keep = {seed} | ring0
            before = len(nodes)
            nodes[:] = [n for n in nodes if isinstance(n, dict) and n.get("id") in keep]
            dropped["far_nodes"] = before - len(nodes)
            cur = _j(out)
    dropped["ring_slimmed"] = dropped["ring_dropped"] = 0
    if seed and cur > budget:
        # 고리 단 ①: 이웃을 id·label·kind 로. 엣지도 끝점과 타입만.
        for i, n in enumerate(nodes):
            if isinstance(n, dict) and n.get("id") != seed:
                nodes[i] = {"id": n.get("id"), "label": n.get("label"),
                            "kind": n.get("kind"), "_deg": n.get("degree") or 0}
                dropped["ring_slimmed"] += 1
        if edges is not None:
            edges[:] = [{"source": e.get("source"), "target": e.get("target"),
                         "type": e.get("type")} for e in edges if isinstance(e, dict)]
        # 고리 순서: 많이 이어진 이웃이 먼저 남는다(결정론 — 동률은 id).
        head = [n for n in nodes if n.get("id") == seed]
        rest = sorted((n for n in nodes if n.get("id") != seed),
                      key=lambda n: (-int(n.get("_deg") or 0), str(n.get("id"))))
        for n in rest:
            n.pop("_deg", None)
        nodes[:] = head + rest
        cur = _j(out)
        if cur > budget:
            # 고리 단 ②: 꼬리부터 던다. 노드와 그 노드에 닿는 엣지를 함께.
            by_node: dict = {}
            for e in edges or []:
                other = e["target"] if e.get("source") == seed else e.get("source")
                by_node.setdefault(other, []).append(e)
            # 뒤에 붙을 표식(ring_truncated·note)의 자리는 봉투의 예비분이 맡는다.
            gone: set = set()
            while cur > budget and len(nodes) > 1 and nodes[-1].get("id") != seed:
                n = nodes.pop()
                gone.add(n.get("id"))
                cur -= _j(n) + 2          # ", " 구분자
                for e in by_node.get(n.get("id"), []):
                    cur -= _j(e) + 2
                dropped["ring_dropped"] += 1
            if edges is not None and gone:
                edges[:] = [e for e in edges
                            if e.get("source") not in gone and e.get("target") not in gone]
            cur = _j(out)
            while cur > budget and len(nodes) > 1:
                # 추정이 모자랐을 때의 안전판(구분자 계산이 어긋나도 예산은 지킨다).
                n = nodes.pop()
                if edges is not None:
                    edges[:] = [e for e in edges if n.get("id") not in
                                (e.get("source"), e.get("target"))]
                dropped["ring_dropped"] += 1
                cur = _j(out)
    if _j(out) > budget:
        return None
    if any(dropped.values()):
        final = {n.get("id") for n in nodes if isinstance(n, dict)}
        ring_kept, far_kept = len(ring0 & final), len(far0 & final)
        dropped.update(ring_total=len(ring0), ring_kept=ring_kept,
                       far_total=len(far0), far_kept=far_kept)
        out["shrunk"] = dropped
        parts = [f"kept the seed whole, {ring_kept} of {len(ring0)} direct neighbour(s) "
                 f"and {far_kept} of {len(far0)} node(s) beyond them",
                 f"emptied props on {dropped['neighbour_props']} node(s), one_liner on "
                 f"{dropped['neighbour_one_liners']}, evidence on "
                 f"{dropped['edge_evidence']} edge(s)",
                 f"dropped {dropped['far_edges']} edge(s) and {dropped['far_nodes']} "
                 f"node(s) not touching the seed"]
        if dropped["ring_slimmed"]:
            parts.append("direct neighbours are reduced to id, label and kind")
        if dropped["ring_dropped"]:
            out["ring_truncated"] = dropped["ring_dropped"]
            parts.append(f"{dropped['ring_dropped']} direct neighbour(s) left out "
                         f"(the least connected); read the whole ring with "
                         f"vinc_neighbors node={seed} limit=500")
        note = ("shrunk to fit the result size limit: " + "; ".join(parts)
                + "; anything dropped comes back by reading that node's own id")
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + note
    return out


SHRINKERS = {"vinc_search": _shrink_search_result,
             # W1 — brief 는 조밀한 이웃에서 예산을 넘길 수 있다. 정책으로
             # 강등한다: 발췌부터 덜고 관계는 마지막까지 남긴다.
             "vinc_brief": _shrink_brief_result,
             # 조밀한 노드의 depth 1 이 상한을 넘겼다(2026-09-05, 75,481자). 시드를
             # 지키고 이웃의 props 부터 던다 — 시드 하나를 읽으러 온 독자가 있다.
             # 허브는 고리까지 줄여 싣는다(거부는 시드 자신이 넘칠 때뿐).
             "vinc_graph": _shrink_graph_result}


# ── MCP 읽기 결과 다이어트 ───────────────────────────────────────────────────
# rpc 의 tools/call 결과에만 건다. 핸들러 출력은 그대로라 REST v1·SDK·`vinc call`·평가 하네스의
# cli·v1 경로는 꼴이 바뀌지 않는다. `raw` 가 참(true 나 문자열 "true")이면 아무것도 빼지 않는다.
# 규칙은 하나다: 차 있지 않은 칸, 건강한 기본값, 같은 객체의 다른 칸에서 되찾을 수 있는 값은 뺀다.
# 값을 바꾸는 것은 하나뿐이다: 120자를 넘는 제목은 "…" 로 자른다(`cut_title`, 노드 제목은 vinc_node 가
# 준다). search·neighbors 는 모든 제목 칸을, vinc_brief 는 문서·관계 제목만 자른다(후보와 노드 제목은 고르는
# 근거이자 답이라 그대로다). 자를 것이 없는 vinc_brief 는 본문이 한 글자도 바뀌지 않는다.
# 저장된 `props` 안은 어느 도구에서도 건드리지 않는다(쓰기가 props 를 통째로 갈아 끼운다).
#: 줄이는 도구. vinc_node·vinc_neighbors 는 칸 규칙만 타고 LEAN_DROP 에 행이 없다.
LEAN_TOOLS = ("vinc_search", "vinc_brief", "vinc_node", "vinc_neighbors")
#: 기본으로 빼는 순위 진단. 키 ""는 결과 최상위, 나머지는 그 목록의 행이다.
#: ⚠ rows 의 `score` 와 node_hits 의 `rrf_score` 는 빼지 않는다: OpenClaw 플러그인이 MCP 로 읽고
#: minScore 로 거른다(integrations/openclaw/src/memory.ts). `page` 는 축소기와 다음 커서가 읽으므로
#: 축소가 끝난 뒤(`lean_finish`)에 뺀다. `node_window`·`node_semantic` 은 노드 팔이 건강할 때만 뺀다.
#: `validity_note` 는 아무 일도 없었다는 고정 문장(`HIDDEN_NOTE`)일 때만 뺀다.
LEAN_DROP: dict[str, dict[str, tuple[str, ...]]] = {
    "vinc_search": {
        "": ("node_rescore", "validity_best_dropped", "validity_best_dropped_distance"),
        "rows": ("rrf_score", "arms"),
        "node_hits": ("matched_in", "hit_tokens", "title_tokens", "token_total", "arms",
                      "distance", "prod_rank", "colbert", "sem_lead", "sem_decisive"),
    },
    "vinc_brief": {
        "": ("resolution", "validity_best_dropped"),
        "candidates": ("matched_in", "hit_tokens", "title_tokens"),
    },
}
#: 줄인 결과에 붙는 한 줄. 도구 목록을 예전에 받아 둔 호스트는 새 설명을 못 읽으므로 빠진 칸의
#: 뜻을 결과가 직접 말한다. 빠지는 칸은 모두 문장이 이름 붙인 종류 중 하나여야 하고(`OMITTED_KINDS`),
#: validity 규칙은 `temporal_expand` 와 같아야 한다(둘 다 테스트가 대조한다). 이 줄은 vinc_brief 의 것이다.
OMITTED_NOTE = ("Omitted: empty, default, derived, duplicate, diagnostic keys, fixed notes, page, "
                "created_at. No validity: unknown if interval_invalid or no valid_from/until, else "
                "current. raw:true has all.")
#: 문서·관계 제목을 하나라도 자른 vinc_brief 의 한 줄: `OMITTED_NOTE` 에 "…=cut title." 만 끼운다.
#: ⚠ 자른 제목이 없는 brief 는 `OMITTED_NOTE` 그대로다(자를 것이 없는 brief 의 본문이 그래야 그대로다).
OMITTED_BRIEF_CUT_NOTE = ("Omitted: empty, default, derived, duplicate, diagnostic keys, fixed notes, page, "
                          "created_at. No validity: unknown if interval_invalid or no valid_from/until, else "
                          "current. …=cut title. raw:true has all.")
#: vinc_search 의 한 줄. 잘린 제목("…")과 node hit 의 빠진 cite_id 를 읽는 법도 말한다.
#: ⚠ 190자 상한 안에 둔다(호출마다 실린다).
OMITTED_SEARCH_NOTE = ("Omitted: empty, default, derived, duplicate, diagnostic, created_at. …=cut title. "
                       "No cite_id: id. No validity: unknown if interval_invalid or no valid_from/until, "
                       "else current. raw:true=all")
#: vinc_node 의 한 줄. 노드는 알려진 validity 를 늘 남기므로 규칙을 싣지 않는다. 빠지는 것은 빈·기본값
#: 칸, 내부 좌표(sync_seq·owner_id·tenant_id), 노드 칸과 겹치는 시간 값이다.
OMITTED_NODE_NOTE = "Unset, internal, repeated keys omitted; raw:true gives all."
#: 검색 팔이 숨긴 노드를 세어 알리는 고정 문장(vinc_rag.retrieval 이 오늘 범위에서 쓴다). 이 문장
#: 그대로일 때만 `validity_note` 를 뺀다. 다른 문장은 무엇이 숨었거나 고르지 못했는지 말하므로 남긴다.
#: ⚠ 사이드카 문장의 미러다. 두 사본의 일치는 tests/mcp/test_mcp_lean_reads.py 가 실제 검색으로 대조한다.
HIDDEN_NOTE = "hid nodes whose claim is no longer current; pass historical=true to include them"
#: 값이 비지 않았는데 빠지는 칸 전부: (도구, 자리, 키, 종류). 자리는 fnmatch 꼴이고 [] 는 목록의 원소다.
#: 빈 값(None·""·[]·{})은 어느 칸이든 "empty" 라 여기 없다. 처음 맞는 줄이 이긴다. 종류는 search·brief 면
#: `OMITTED_NOTE`, vinc_node 면 `OMITTED_NODE_NOTE` 가 부르는 말이다. 손으로 고치고, 테스트가 사본과
#: 대조한다(빼는 칸을 늘리거나 종류를 바꾸면 그 사본도 사람이 고쳐야 한다).
OMITTED_KINDS: tuple[tuple[str, str, str, str], ...] = (
    ("vinc_search", "", "node_rescore", "diagnostic"),
    ("vinc_search", "", "validity_best_dropped", "diagnostic"),
    ("vinc_search", "", "validity_best_dropped_distance", "diagnostic"),
    ("vinc_search", "", "node_window", "diagnostic"),
    ("vinc_search", "", "node_semantic", "diagnostic"),
    ("vinc_search", "", "validity_note", "default"),
    ("vinc_search", "", "page", "diagnostic"),
    ("vinc_search", "", "total_count_capped", "default"),
    ("vinc_search", "/rows[]", "rrf_score", "diagnostic"),
    ("vinc_search", "/rows[]", "arms", "diagnostic"),
    ("vinc_search", "/rows[]", "doc_title", "duplicate"),
    ("vinc_search", "/node_hits[]", "cite_id", "derived"),
    ("vinc_search", "/node_hits[]", "matched_in", "diagnostic"),
    ("vinc_search", "/node_hits[]", "hit_tokens", "diagnostic"),
    ("vinc_search", "/node_hits[]", "title_tokens", "diagnostic"),
    ("vinc_search", "/node_hits[]", "token_total", "diagnostic"),
    ("vinc_search", "/node_hits[]", "arms", "diagnostic"),
    ("vinc_search", "/node_hits[]", "distance", "diagnostic"),
    ("vinc_search", "/node_hits[]", "prod_rank", "diagnostic"),
    ("vinc_search", "/node_hits[]", "colbert", "diagnostic"),
    ("vinc_search", "/node_hits[]", "sem_lead", "diagnostic"),
    ("vinc_search", "/node_hits[]", "sem_decisive", "diagnostic"),
    ("vinc_brief", "", "resolution", "diagnostic"),
    ("vinc_brief", "", "validity_best_dropped", "diagnostic"),
    ("vinc_brief", "", "validity_note", "fixed notes"),
    ("vinc_brief", "", "cite_grounds", "duplicate"),
    ("vinc_brief", "/candidates[]", "matched_in", "diagnostic"),
    ("vinc_brief", "/candidates[]", "hit_tokens", "diagnostic"),
    ("vinc_brief", "/candidates[]", "title_tokens", "diagnostic"),
    ("vinc_brief", "*links", "executed", "default"),
    ("vinc_brief", "*links", "offset", "default"),
    ("vinc_brief", "*links", "truncated", "default"),
    ("vinc_brief", "*links/items[]", "evidence", "default"),
    ("vinc_brief", "*quotes[]", "widened", "default"),
    ("vinc_brief", "/candidate_material", "one_liner", "duplicate"),
    ("vinc_brief", "/candidate_material", "body_cite", "derived"),
    ("vinc_node", "/node", "sync_seq", "internal"),
    ("vinc_node", "/node", "owner_id", "internal"),
    ("vinc_node", "/node", "tenant_id", "internal"),
    ("vinc_node", "/node/temporal", "created_at", "repeated"),
    ("vinc_node", "/node/temporal", "updated_at", "repeated"),
    ("vinc_node", "/node/temporal", "valid_from", "repeated"),
    ("vinc_node", "/node/temporal", "valid_until", "repeated"),
    ("vinc_node", "/node/temporal", "content_time", "repeated"),
    ("vinc_node", "/node/temporal", "superseded_by", "repeated"),
    ("vinc_node", "/node/temporal", "interval_known", "repeated"),
    ("vinc_node", "/node/temporal", "validity", "unset"),
    ("vinc_node", "/node/temporal", "claim_status", "unset"),
    ("vinc_node", "/node/temporal", "claim_assessment_scope", "unset"),
    ("vinc_node", "/node/temporal", "interval_invalid", "unset"),
    ("vinc_neighbors", "/neighbors[]", "rev", "internal"),
    ("vinc_neighbors", "/neighbors[]", "domain", "repeated"),
    ("*", "*/temporal", "created_at", "created_at"),
    ("*", "*/temporal", "claim_assessment_scope", "default"),
    ("*", "*/temporal", "interval_invalid", "default"),
    ("*", "*/temporal", "claim_status", "default"),
    ("*", "*/temporal", "validity", "derived"),
    ("*", "*/temporal", "interval_known", "derived"),
    ("vinc_brief", "*", "created_at", "created_at"),
    ("vinc_brief", "*", "updated_at", "duplicate"),
    ("vinc_brief", "*", "content_time", "duplicate"),
    ("vinc_brief", "*", "valid_from", "duplicate"),
    ("vinc_brief", "*", "valid_until", "duplicate"),
    ("vinc_brief", "*", "superseded_by", "duplicate"),
    ("vinc_brief", "*", "validity", "derived"),
)
#: `stamp` 이 행에 평평하게 한 벌 더 쓰는 칸(`store.search_temporal.FLAT` 과 같다).
_TEMPORAL_FLAT = ("created_at", "updated_at", "content_time", "valid_from", "valid_until",
                  "superseded_by", "validity")
#: 시간 블록에서 값이 없을 때(null) 빠지는 칸.
_TEMPORAL_VALUES = ("content_time", "valid_from", "valid_until", "superseded_by")
#: 비었을 때 빠지는 목록 칸.
_TEMPORAL_LISTS = ("supersession", "supersedes", "conflicts")
#: 평가하지 않은 주장의 claim_status. 검색·brief 는 unscoped, vinc_node 는 unassessed 로 적는다.
_CLAIM_NOT_ASSESSED = ("unscoped", "unassessed")
#: vinc_node 가 노드에서 빼는 저장 열. 소유자·동기화 좌표는 읽는 쪽이 쓰지 않는다. `rev` 는 남긴다.
_NODE_DROP = ("sync_seq", "owner_id", "tenant_id")


def _temporal_validity(t: dict) -> str:
    """validity 가 빠진 블록에서 validity 를 되찾는다. 깨진 구간은 unknown, 구간 끝 하나라도 있으면
    current, 없으면 unknown 이다(`store.search_temporal.metadata` 와 같은 계산)."""
    if t.get("interval_invalid"):
        return "unknown"
    return "current" if (t.get("valid_from") or t.get("valid_until")) else "unknown"


def temporal_expand(t: dict, *, claim_status: str = "unscoped") -> dict:
    """줄인 블록을 계산 칸 셋(validity·interval_known·claim_status)과 기본값으로 되돌린다.
    `OMITTED_NOTE` 와 도구 설명이 말하는 규칙 그 자체다. vinc_node 는 claim_status="unassessed"."""
    out = {k: t.get(k) for k in _TEMPORAL_VALUES}
    out.update({k: list(t.get(k) or []) for k in _TEMPORAL_LISTS})
    out["interval_invalid"] = bool(t.get("interval_invalid"))
    out["holds_in_scope"] = t.get("holds_in_scope")
    out["validity"] = t.get("validity") or _temporal_validity(t)
    out["interval_known"] = bool(t.get("valid_from") or t.get("valid_until")) \
        and not out["interval_invalid"]
    out["claim_status"] = t.get("claim_status") or claim_status
    return out


def _slim_temporal(t: dict) -> dict:
    """시간 블록에서 차 있는 것만 남긴다. 모르는 칸은 그대로 둔다(뜻을 모르는 것은 빼지 않는다).
    validity·interval_known 은 되찾을 수 있을 때만 뺀다: 저장된 날짜에서 계산한 값과 다르면 남는다."""
    out = {}
    for k, v in t.items():
        if k in ("created_at", "claim_assessment_scope"):
            continue
        if k in _TEMPORAL_VALUES and v in (None, ""):
            continue
        if k in _TEMPORAL_LISTS and v == []:
            continue
        if (k == "interval_invalid" and v is False) or (k == "holds_in_scope" and v is None):
            continue
        if k == "validity" and v == _temporal_validity(t):
            continue
        if k == "interval_known" and v == (bool(t.get("valid_from") or t.get("valid_until"))
                                           and not t.get("interval_invalid")):
            continue
        if k == "claim_status" and v in _CLAIM_NOT_ASSESSED:
            continue
        if k == "updated_at" and not v:
            continue                    # 수정 시각은 늘 남긴다(되찾을 길이 없다)
        out[k] = v
    return out


def _lean_temporal(row: dict, *, flat: bool) -> None:
    t = row.get("temporal")
    if not isinstance(t, dict):
        return
    if flat:
        # brief 의 평평한 사본 중 블록과 같은 값만 뺀다(빈 문자열은 null 과 같다). `node.validity` 는
        # 도구 계약이 읽으라고 말하는 칸이라 남긴다. 블록을 줄이기 **전에** 대조한다.
        for k in _TEMPORAL_FLAT:
            if k in row and k in t and (row[k] or None) == (t[k] or None):
                if not (k == "validity" and "claim_validity" in row):
                    row.pop(k)
    slim = _slim_temporal(t)
    if slim:
        row["temporal"] = slim
    else:
        row.pop("temporal")


def _lean_node(node: dict) -> None:
    """vinc_node 의 노드: 시간 블록은 노드의 저장 칸에 이미 있는 값을 빼고, 노드는 빈 칸과 내부 좌표를
    뺀다. props 는 통째로 둔다(비어 있어도). unknown 이 아닌 validity 는 남긴다."""
    t = node.get("temporal")
    if isinstance(t, dict):
        kept = _slim_temporal(t)
        # ⚠ 인용 매니페스트 갱신(api/refs.py)이 MCP 로 validity 를 읽고, 없으면 unknown 으로 적는다.
        # 핸들러의 칸 순서를 지킨다(t 를 따라 걷는다).
        slim = {k: v for k, v in t.items()
                if k in kept or (k == "validity" and v not in (None, "unknown"))}
        for k in [k for k, v in slim.items() if k in _TEMPORAL_VALUES + ("updated_at",)
                  and (node.get(k) or None) == v]:
            slim.pop(k)
        if slim:
            node["temporal"] = slim
        else:
            node.pop("temporal")
    for k in _NODE_DROP:
        node.pop(k, None)
    for k in [k for k, v in node.items() if k != "props" and (v is None or v in ("", [], {}))]:
        node.pop(k)


def _node_arm_healthy(out: dict) -> bool:
    """노드 팔이 다 돌았나: 의미 팔 complete·오류 없음·낡거나 빠진 후보 없음, node_error 없음,
    어휘 후보가 상한에 안 걸림. 하나라도 아니면 `node_window`·`node_semantic` 을 통째로 남긴다."""
    ns, nw = out.get("node_semantic"), out.get("node_window")
    if not isinstance(ns, dict) or ns.get("state") != "complete" or ns.get("error") \
            or ns.get("stale_candidates") or ns.get("missing_candidates") or out.get("node_error"):
        return False
    return nw is None or (isinstance(nw, dict) and not nw.get("lexical_candidates_capped"))


def _lean_links(links: Any) -> None:
    """brief 의 관계: 항목의 null·빈 문자열·false 칸, 그리고 건강한 기본값(executed:true·offset 0·
    truncated:false)을 뺀다. 0.0 은 값이다(False 와 같다고 보지 않는다)."""
    if not isinstance(links, dict):
        return
    for it in links.get("items") or []:
        if isinstance(it, dict):
            for k in [k for k, v in it.items() if v is None or v is False or v == ""]:
                it.pop(k)
    if links.get("executed") is True:
        links.pop("executed")
    if links.get("truncated") is False:
        links.pop("truncated")
    if type(links.get("offset")) is int and links["offset"] == 0:
        links.pop("offset")


#: 기본 본문에서 제목 칸이 갖는 최대 글자 수("…" 포함). vinc_node 의 제목과 vinc_brief 의 후보·노드
#: 제목은 자르지 않는다.
TITLE_MAX = 120
_ELLIPSIS = "\u2026"
#: neighbors 는 `omitted` 줄이 없다. 제목을 하나라도 잘랐을 때만 이 줄을 붙인다(⚠ 60자 미만).
CUT_TITLE_NOTE = "…=cut title; vinc_node or raw:true has it whole"


def _glued(ch: str) -> bool:
    """앞 글자에 붙어 한 글자로 읽히는 코드 포인트: 결합 부호, ZWJ, 이체 선택자, 피부색 수식자,
    태그 문자, 짝 잃은 뒤 서로게이트, 한글 조합형 중성·종성."""
    import unicodedata
    return (unicodedata.combining(ch) != 0 or unicodedata.category(ch) in ("Mn", "Mc", "Me")
            or ch == "\u200d" or "\ufe00" <= ch <= "\ufe0f"
            or "\U0001f3fb" <= ch <= "\U0001f3ff" or "\U000e0020" <= ch <= "\U000e007f"
            or "\udc00" <= ch <= "\udfff" or "\u1160" <= ch <= "\u11ff" or "\ud7b0" <= ch <= "\ud7ff")


def _regional(ch: str) -> bool:
    """국기를 이루는 지역 표시 문자(둘이 한 글자다)."""
    return "\U0001f1e6" <= ch <= "\U0001f1ff"


def _whole_end(text: str, end: int) -> int:
    """`text[:end]` 가 한 글자를 가르면 그 글자가 시작하는 자리까지 `end` 를 당긴다."""
    while 0 < end < len(text):
        if _glued(text[end]) or text[end - 1] == "\u200d" or "\ud800" <= text[end - 1] <= "\udbff":
            end -= 1
            continue
        if _regional(text[end]) and _regional(text[end - 1]):
            run = 0
            while run < end and _regional(text[end - 1 - run]):
                run += 1
            if run % 2:     # 앞에서부터 둘씩 짝이므로 홀수 개 뒤에서 자르면 국기 하나를 가른다
                end -= 1
                continue
        break
    return end


def cut_title(text: Any, limit: int = TITLE_MAX) -> Any:
    """`limit` 자를 넘는 제목을 잘라 "…" 로 끝낸다(결과는 "…" 까지 `limit` 자 이하). 단어 경계가
    남길 몫의 뒤쪽 절반에 있으면 거기서, 없거나 거기서 자르면 글자·숫자가 하나도 안 남으면
    `limit - 1` 자 자리에서 자른다. 글자 수는 코드 포인트로 세고 한 글자(결합 부호·ZWJ 연쇄·피부색
    수식자·국기 짝·서로게이트 쌍)를 가르지 않는다. 문자열이 아니거나 짧으면 그대로 돌려준다."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    room = limit - len(_ELLIPSIS)
    end = room
    if not text[room].isspace():
        space = max(text.rfind(" ", 0, room), text.rfind("\t", 0, room), text.rfind("\n", 0, room))
        if space > room // 2:
            end = space
    head = text[:_whole_end(text, end)].rstrip(" \t\n,;:")
    if not any(ch.isalnum() for ch in head):
        # 경계에서 자르면 "…" 만 남거나 문장부호만 남는다: 글자 자리에서 그대로 자른다.
        head = text[:_whole_end(text, room) or room]
    return head + _ELLIPSIS


def _cut_key(rows: Any, key: str) -> bool:
    """목록의 행마다 `key` 제목을 자른다. 하나라도 잘랐으면 참."""
    cut = False
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get(key), str) and len(row[key]) > TITLE_MAX:
            row[key] = cut_title(row[key])
            cut = True
    return cut


def _lean_titles(name: str, out: dict) -> None:
    """neighbors·search 의 긴 제목 자르기와 search 의 문서 제목 한 번만·node hit 의 id 와 같은 cite_id
    빼기. vinc_brief 는 `_lean_brief_quotes_and_titles`, vinc_node 는 제목을 자르지 않는다."""
    if name == "vinc_neighbors":
        if _cut_key(out.get("neighbors"), "title"):
            out["omitted"] = CUT_TITLE_NOTE
        return
    if name == "vinc_search":
        seen: set = set()
        for row in out.get("rows") or []:
            if not isinstance(row, dict) or "doc_title" not in row or not row.get("doc_id"):
                continue
            # 응답 안에서 문서 제목은 그 문서의 첫 행만 싣는다(행은 점수 순이라 첫 행이 앞선다).
            if row["doc_id"] in seen:
                row.pop("doc_title")
            else:
                seen.add(row["doc_id"])
        _cut_key(out.get("rows"), "doc_title")
        _cut_key(out.get("node_hits"), "title")
        # 행의 cite_id 는 남긴다: 행의 id 는 청크 id 라 인용할 수 없다.
        for hit in out.get("node_hits") or []:
            if isinstance(hit, dict) and hit.get("cite_id") and hit["cite_id"] == hit.get("id"):
                hit.pop("cite_id")


def _lean_brief_quotes_and_titles(out: dict) -> bool:
    """vinc_brief: 인용 단위의 `widened:false` 를 빼고, 120자를 넘는 문서·관계 제목(quotes 의 source,
    결과와 choose 재료의 excerpts doc_title·links 항목 title)을 자른다. 하나라도 잘랐으면 참.
    ⚠ 후보·노드·절의 제목, 모든 cite_id, 인용 문장(text)과 doc_id 는 건드리지 않는다."""
    quotes = [q for q in out.get("quotes") or [] if isinstance(q, dict)] \
        if isinstance(out.get("quotes"), list) else []
    for cand in out.get("candidates") or []:
        if isinstance(cand, dict) and isinstance(cand.get("quotes"), list):
            quotes += [q for q in cand["quotes"] if isinstance(q, dict)]
    for q in quotes:
        if q.get("widened") is False:
            q.pop("widened")
    cut = _cut_key(quotes, "source")
    material = out.get("candidate_material")
    for block in [out] + ([material] if isinstance(material, dict) else []):
        cut = _cut_key(block.get("excerpts"), "doc_title") or cut
        if isinstance(block.get("links"), dict):
            cut = _cut_key(block["links"].get("items"), "title") or cut
    return cut


def _lean_material_body(material: dict) -> None:
    """choose 재료의 본문이 발췌를 옮긴 것(`body_src:"doc"`)이고 one_liner 가 그 발췌의 excerpt 와 바이트까지
    같을 때만 one_liner 를 뺀다. body_cite 는 같은 발췌에 남은 doc_title(없으면 doc_id)·section 으로 다시
    지은 문자열과 같을 때만 뺀다(제목이 잘렸으면 다르므로 남는다). 축소기가 그 발췌를 덜었으면 그대로 둔다."""
    excerpts = material.get("excerpts")
    if material.get("body_src") != "doc" or not isinstance(excerpts, list):
        return
    # 본문은 글자가 있는 첫 발췌다(api.query._brief_body). 축소기는 뒤에서부터 덜므로 첫 발췌가 마지막까지 남는다.
    first = next((e for e in excerpts if isinstance(e, dict) and str(e.get("excerpt") or "").strip()), None)
    one = material.get("one_liner")
    if first is None or not isinstance(one, str) or one != first.get("excerpt"):
        return
    material.pop("one_liner")
    where = str(first.get("doc_title") or first.get("doc_id") or "")
    sec = str(first.get("section") or "")
    if where and material.get("body_cite") == ((where + " · " + sec) if sec else where):
        material.pop("body_cite")


def _walk_dicts(node: Any, fn: Callable[[dict], None]) -> None:
    if isinstance(node, dict):
        fn(node)
        for k, v in node.items():
            if k != "props":            # 저장된 props 안은 걷지 않는다
                _walk_dicts(v, fn)
    elif isinstance(node, list):
        for v in node:
            _walk_dicts(v, fn)


def _is_raw(args: Any) -> bool:
    # 도구 목록을 예전에 받아 둔 호스트는 선언 안 된 불리언을 문자열로 보낸다.
    raw = args.get("raw") if isinstance(args, dict) else None
    return raw is True or raw == "true"


def lean_project(name: str, args: dict, out: Any) -> Any:
    """줄인 사본에서 `page` 를 빼기 전 단계. 대상이 아니거나 raw 면 None. 축소기는 이 단계의 사본을
    받는다(`page` 로 다음 커서를 쓴다). 반환값은 새 사본이라 축소기가 고쳐도 핸들러 결과는 그대로다."""
    if name not in LEAN_TOOLS or not isinstance(out, dict) or _is_raw(args):
        return None
    import copy
    out = copy.deepcopy(out)        # 핸들러 결과를 캐시가 쥐고 있어도 건드리지 않는다
    if name == "vinc_neighbors":
        listed = out.get("node") if isinstance(out.get("node"), str) else ""
        dom = listed[len("domain:"):] if listed.startswith("domain:") else None
        for row in [out] + [r for r in out.get("neighbors") or [] if isinstance(r, dict)]:
            for k in [k for k, v in row.items() if v is None or v == ""]:
                row.pop(k)
            if row is not out:
                row.pop("rev", None)    # 쓰기 토큰은 vinc_node 가 준다
                if dom is not None and row.get("domain") == dom:
                    row.pop("domain")   # 도메인 목록은 그 도메인을 행마다 되풀이하지 않는다
        _lean_titles(name, out)
        return out
    if name == "vinc_node":
        if isinstance(out.get("node"), dict):
            _lean_node(out["node"])
        return out
    for where, keys in LEAN_DROP[name].items():
        rows = [out] if where == "" else [r for r in out.get(where) or [] if isinstance(r, dict)]
        for row in rows:
            for k in keys:
                row.pop(k, None)
    if out.get("validity_note") == HIDDEN_NOTE:
        out.pop("validity_note")
    if name == "vinc_search":
        if out.get("total_count_capped") is False:
            out.pop("total_count_capped")
        if _node_arm_healthy(out):
            out.pop("node_window", None)
            out.pop("node_semantic", None)
    else:
        _lean_links(out.get("links"))
        if isinstance(out.get("candidate_material"), dict):
            _lean_links(out["candidate_material"].get("links"))
        if "cite_grounds" in out and out.get("cite_grounds") == out.get("grounds"):
            out.pop("cite_grounds")
        if _lean_brief_quotes_and_titles(out):
            out["omitted"] = OMITTED_BRIEF_CUT_NOTE     # lean_finish 가 읽고 맨 끝으로 옮긴다
    if name == "vinc_search":
        _lean_titles(name, out)
    flat = name == "vinc_brief"
    _walk_dicts(out, lambda d: _lean_temporal(d, flat=flat))
    return out


def lean_finish(name: str, projected: dict, original: Any) -> dict:
    """`lean_project` (그리고 축소기) 뒤의 마지막 단계: 축소기만 읽는 `page` 를 빼고, choose 재료의
    겹친 본문을 빼고(축소가 끝난 뒤라야 본문을 실은 발췌가 남았는지 안다), 무엇이든 빠졌으면 `omitted`
    한 줄을 맨 끝에 붙인다(search·brief·node). brief 는 제목을 잘랐을 때만 `OMITTED_BRIEF_CUT_NOTE` 다.
    neighbors 는 제목을 잘랐을 때만 `_lean_titles` 가 붙인 `CUT_TITLE_NOTE` 를 싣는다."""
    projected.pop("page", None)
    if name == "vinc_brief" and isinstance(projected.get("candidate_material"), dict):
        _lean_material_body(projected["candidate_material"])
    if name != "vinc_neighbors" and projected != original:
        cut = name == "vinc_brief" and projected.pop("omitted", None) == OMITTED_BRIEF_CUT_NOTE
        projected["omitted"] = OMITTED_BRIEF_CUT_NOTE if cut else {
            "vinc_node": OMITTED_NODE_NOTE, "vinc_search": OMITTED_SEARCH_NOTE}.get(name, OMITTED_NOTE)
    return projected


def lean_result(name: str, args: dict, out: Any) -> Any:
    """MCP 로 내보낼 읽기 결과를 줄인 사본. 대상이 아니거나 `raw` 면 받은 것을 그대로 돌려준다."""
    projected = lean_project(name, args, out)
    if projected is None:
        return out
    return lean_finish(name, projected, out)
