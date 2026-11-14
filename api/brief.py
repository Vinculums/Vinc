# -*- coding: utf-8 -*-
"""브리프(`vinc_brief`): 질문 하나로 답변 재료를 모으는 결정론 조인 읽기.

해소 규칙(질문에서 id 를 읽는 직참조, 규칙을 고르는 플래그, 해소 순위와 동점 가드, v3 학습 규칙, 확정
`_brief_resolve`)과 답 조립(발췌·절·인용 고르기, 되묻기 재료, 고아·의미 없음·시간축 가드), 그리고 그 둘을
잇는 `brief()` 가 여기 있다. `brief()` 는 `node_read` 의 `search`·`node_context` 로 읽는다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.brief`·`q.X` 와 `from api.query import X` 는
그대로다. 두 이름은 같은 객체다. 여기 함수가 부르는 이름(`search`·`node_context`·`_brief_resolve` 등)은 이
모듈에서 찾으므로, `brief()` 안의 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_brief_module.py` 가 본다).
"""
from __future__ import annotations

import math
import os
import re

from store import pkns
from store.backend import open_store
from store.config import load_config
from store.envflag import truthy
from store.querybase import holds_during, normalize_query_tokens, time_scope, validity_state
from vinc_rag import excerpt as _excerpt
from vinc_rag import retrieval as _retrieval
from vinc_rag.retrieval import UNSTAMPED_RUNTIME_NOTE

from .node_read import node_context, search
from .query_base import _DOC_CONCEPT_PREFIX, _seed_table


# ── vinc_brief — 질문 하나로 답변 재료를 모으는 결정론 조인 읽기 ──────────────
#
# 생성은 없다. 재료가 곧 답이고, 해소 규칙은 데스크탑 Ask 흐름
# (`app/src/ask.ts` 의 resolve·answerFor·pickBody)에서 그대로 옮겨 왔다. 임의
# 임계값을 두지 않는 것이 요점이다 — 1위가 2위를 **엄격히** 이길 때만 확정하고,
# 비기면 후보를 돌려주며 되묻는다. 근거: [[vinc.plan.mcp-agent-surface §4]]

#: brief 가 내부 search 에 넘기는 top_k. 청크 페이지만이 아니라 **node_hits 도 이 수로
#: 잘린다**(노드 팔의 top_k 가 같은 값이다). 이 풀은 되묻기 목록에만 쓴다. 확정은 그 앞
#: `_BRIEF_RESOLVE_N` 개(융합 순서)로 해서, 풀을 넓혀도 어느 노드로 확정하는지는 5 일 때의
#: 규칙 그대로다. 풀을 넓히고 확정까지 넓은 풀로 하면 맞는 확정과 보이는 정답이 함께 줄었다.
#: 근거: [[vinc.spec.retrieval-knobs]] §2
_BRIEF_TOP_K = 10
_BRIEF_RESOLVE_N = 5

#: v3 학습 규칙 — ColBERT 1위를 확정할지 정하는 로지스틱 모형. 특징은
#: :func:`_brief_learned_features` 의 열에 유효구간 필터가 감춘 행에서 나온 넷을 더한 열넷. brief 경로 전체
#: (사전 순서 기권 → ColBERT 순서 위의 규칙, 토큰 동점 가드 없음)에서 맞춰 고정한 그대로다
#: (`frozen_v3.json`, sha256 c9a3fb2b…). 특징 순서·정의를 바꾸면 상수가 뜻을 잃는다
#: (:func:`_brief_v3_features`). 문턱 아래는 되묻는다.
#: 근거: [[decision:ship-brief-rule-v3-on-the-owners-reading-of-the-wrong-picks-2026-11-03]]
_V3_MU = [0.4890195805011388, 0.02600471254428717, 0.2799710249909453, 4.690329590727997, 0.22764569427916043,
          0.7576964867801521, 0.4469395146685983, 0.04225958312205718, 0.4878007591914889, 0.32560666425208257,
          1.932633103947845, 1.4154291923216227, 0.3487029348902844, 0.9974646867077146]
_V3_SD = [0.05659572299459152, 0.02600220280012217, 0.44898468910915157, 4.776808934344869, 0.30744768971516356,
          1.2894831272731022, 2.7083567306240948, 0.05636093631911328, 0.15418905886504955, 0.468601072751569,
          2.5222996592202773, 1.6853656409255495, 0.4304271895055913, 0.050288026202780646]
_V3_W = [0.4862374491844391, 1.062161570947516, -0.12316389848275623, -0.37911250114801065, 0.25814693177986686,
         -0.38898580913481623, 0.027189152410250123, 0.8091480763571104, -0.5459152296028742, 0.2354164923841601,
         -0.1228823968080043, 0.35268237679848913, 0.09248826361012848, 0.1539361828003368, -0.02308750100000047]
_V3_T = 0.5397
#: 되묻을 때 실을 후보 수. 3 에서 5 로 늘렸다 — 같은 풀의 앞자리를 더 보여 줄 뿐이라
#: 보이던 정답이 가려지지 않고, 풀 안의 정답이 전부 보인다(G0 43 → 47/60). 앱의 ask.ts 는
#: brief 를 부르지 않고 따로 3 개를 보여 준다. `VINC_BRIEF_EXPOSE` 로 프로세스마다 바꾼다.
#: 근거: [[decision:brief-shows-five-candidates-when-it-asks-back-and-keeps-the-list-beside-resolved-off-2026-09-29]]
_BRIEF_CANDIDATES = 5
_BRIEF_BODY_KEYS = ("note", "summary", "one_liner", "description", "desc")
_BRIEF_MIN_PROP_BODY = 12     # props 값을 문장으로 볼 최소 길이 — ask.ts 와 같다
_BRIEF_FALLTHROUGH = 3        # 1위가 안 열릴 때 시도할 후보 수 상한(왕복 비용 유계)


#: 질문 속 노드 id — brief 직참조의 탐지 대상. bridge 는 node_context 가 읽지
#: 못해 넣으면 항상 실패해 fell_through 소음만 늘므로 뺀다(랭킹 경로가 받는다).
#: 끝 문자를 글자·숫자로 조여 문장부호(`concept:foo.` 의 마침표)가 id 에 안 붙는다.
#: 슬러그는 **유니코드**다. 한국어 슬러그 개념이 있고, ASCII 로 조이면 첫 한글에서
#: 잘린 접두를 열어 버린다 — 그 접두가 실재하면 조용히 **다른 노드**다.
#: 근거: [[record:brief-direct-id-pattern-was-ascii-only-2026-09-12]]
_BRIEF_ID_RE = re.compile(
    r"\b(?:concept|record|decision|episode):[^\W_](?:[\w.-]*[^\W_])?")
#: 위 매치의 ASCII 접두 — 되물림 후보. 한국어 문장은 조사를 id 에 붙여 쓰는데
#: (`concept:foo는`) 문법으로는 한국어 슬러그와 못 가르므로 스토어가 가른다: 긴 쪽이
#: 안 열리면 이 접두를 열고, 그 대체는 `fell_through` 로 밝힌다.
_BRIEF_ID_ASCII_RE = re.compile(
    r"(?:concept|record|decision|episode):[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
#: 개방 타입(Entity) id 후보 — 접두가 사용자 저작 타입이라 열거할 수 없다(`theory:x`).
#: 문법만으로는 `note:foo` 같은 문장 조각과 못 가르므로 **스토어에 실재할 때만** 연다
#: (:func:`_brief_entity_ids`). 없으면 무시하고 랭킹으로 간다 — 위 네 접두와 달리
#: "없다"고 답하지 않는다. 근거: 감사 F12.
_BRIEF_OPEN_ID_RE = re.compile(r"(?<![\w/.:-])([A-Za-z][\w-]*):[^\W_](?:[\w.-]*[^\W_])?")
_BRIEF_NOT_OPEN = frozenset(("concept", "record", "decision", "episode", "domain",
                             "bridge", "http", "https", "ftp", "mailto", "file", "urn"))


def _brief_query_ids(query: str, cap: int = _BRIEF_FALLTHROUGH) -> list[str]:
    """질문에 그대로 들어 있는 노드 id — 등장 순서, 중복 제거. 매치마다 바로 뒤에
    그 ASCII 접두를 되물림 후보로 둔다(매치와 다를 때만).

    `cap` 은 **언급** 수 상한이다(변형 수가 아니다). 변형으로 자르면 `A는 … B는` 이
    `[A는, A, B는]` 에서 잘려 실재하는 B 가 시도조차 안 된다. 새 후보를 하나도 못 보태는
    언급(앞 언급의 접두와 같은 id)은 상한을 쓰지 않는다. 왕복은 언급당 둘까지다."""
    seen: set[str] = set()
    out: list[str] = []
    mentions = 0
    for m in _BRIEF_ID_RE.findall(query or ""):
        a = _BRIEF_ID_ASCII_RE.match(m)
        new = [c for c in dict.fromkeys((m, a.group(0) if a else ""))
               if c and c not in seen]
        if not new:
            continue
        if mentions >= cap:
            break
        mentions += 1
        seen.update(new)
        out.extend(new)
    return out


def _brief_entity_ids(store, query: str, *, owner: str,
                      cap: int = _BRIEF_FALLTHROUGH) -> list[str]:
    """질문 속 개방 타입 id 중 이 소유자의 Entity 로 실재하는 것만, 등장 순서로."""
    cands = []
    for m in _BRIEF_OPEN_ID_RE.finditer(query or ""):
        if m.group(1).lower() not in _BRIEF_NOT_OPEN and m.group(0) not in cands:
            cands.append(m.group(0))
        if len(cands) >= cap:
            break
    if not cands:
        return []
    try:
        rows = store.run("MATCH (n:Entity) WHERE n.entity_id IN $ids AND n.owner_id = $owner "
                         "RETURN n.entity_id AS id",
                         {"ids": pkns.keys(owner, cands), "owner": owner}) or []
    except Exception:
        return []      # Entity 표가 없는 스토어 — 직참조 후보가 없을 뿐 랭킹은 그대로 돈다
    found = {pkns.strip(str(r.get("id") or "")) for r in rows}
    return [c for c in cands if c in found]


def brief_reads_by_id(query: str) -> bool:
    """brief 가 이 질문을 id 직참조로만 답하는가 — 그렇다면 검색을 돌지 않는다.
    `/brief` 는 이것을 보고 질의 임베딩을 건너뛴다. 직참조 갈래가 검색으로 넘어가게
    바뀌면 여기도 함께 바꿀 것 — 안 그러면 그 검색이 의미 팔 없이 돈다.

    개방 타입(Entity) id 는 여기서 참이 되지 않는다. 실재를 스토어로 가려야 하는데 이
    검사는 스토어 없이 돈다. 그 질문은 임베딩을 한 번 더 구할 뿐 답은 같다."""
    return bool(_brief_query_ids(query))


def _brief_material(base: dict, cand: dict, ctx: dict) -> dict:
    """확정된 노드의 답변 재료를 base 에 채운다 — 랭킹 경로와 id 직참조 경로가
    같은 꼴을 내도록 한 자리로 모은 꼬리. cand 는 검색 후보(랭킹 경로) 또는
    {"id": ...} 하나(직참조 경로)다."""
    nid = str(cand.get("id") or "")
    base["node"] = {"id": nid, "cite_id": nid,
                    "title": ctx.get("title") or cand.get("title") or nid,
                    "domain": cand.get("domain") or ctx.get("domain") or "",
                    "kind": ctx.get("node_kind") or cand.get("kind") or "",
                    # 노드 자신의 유효구간 — 직참조는 필터를 거치지 않으므로 여기가
                    # historical 을 말할 유일한 자리다.
                    "valid_from": ctx.get("valid_from") or "",
                    "valid_until": ctx.get("valid_until") or "",
                    "validity": ctx.get("validity") or "current",
                    "superseded_by": ctx.get("superseded_by") or ""}
    from store.search_temporal import stamp
    stamp(base["node"], ctx)
    if "temporal" in ctx:
        base["node"]["temporal"] = cand.get("temporal") or ctx["temporal"]
    base["node"]["validity_basis"] = "legacy_visibility"
    base["node"]["claim_validity"] = base["node"]["temporal"]["validity"]
    for key in ("created_at", "updated_at", "content_time", "temporal_error"):
        if key in ctx:
            base["node"][key] = ctx[key]
    # 알림은 증거 유효성(claim_validity)에서 낸다(감사 F13). 기존 validity 필드는 구간이
    # 없는 노드를 current 로 읽어서, 은퇴한 문서의 미러가 알림 없이 나갔다. unknown 은
    # "날짜를 모른다"이지 "현재가 아니다"가 아니므로 알리지 않는다.
    claim = base["node"]["claim_validity"]
    if claim in ("historical", "future"):
        _sb = base["node"]["superseded_by"] or str(
            (base["node"].get("temporal") or {}).get("superseded_by") or "")
        base.setdefault("validity_note",
                        f"this claim is {claim}, not current"
                        + (f"; superseded by {_sb}" if _sb else ""))
    body = _brief_body(ctx)
    base["one_liner"] = body["text"]
    base["body_src"] = body["src"]
    base["body_cite"] = body["cite"]
    base["excerpts"] = list(ctx.get("excerpts") or [])
    links = ctx.get("links") if isinstance(ctx.get("links"), dict) else {}
    items = list(links.get("items") or [])
    items = [{**item, "cite_id": item.get("id") or None} for item in items]
    # 근거로서 강한 것부터: 신뢰도 → 근거 문장 보유 → node_context 의 결정론 순서
    # (안정 정렬이라 동점이면 그 순서가 그대로 남는다). ask.ts answerFor 와 같다.
    # 저장되지 않은 신뢰도(None)는 0.0 보다도 뒤다 — 0.0 은 저작된 값이다(감사 F8).
    items.sort(key=lambda it: (it.get("confidence") is None,
                               -float(it.get("confidence") or 0.0),
                               0 if it.get("evidence") else 1))
    base["links"] = {**links, "items": items}
    base["grounds"] = _brief_grounds(nid, base["excerpts"], items)
    base["cite_grounds"] = sorted({nid} | {
        row["cite_id"] for row in base["excerpts"] + items if row.get("cite_id")})
    if ctx.get("citation_error"):
        base["citation_error"] = ctx["citation_error"]
    # 노드는 읽었는데 그 뒤(발췌·문서 제목·관계)가 실패했으면 **반쪽**이라고 말한다.
    # node_context 는 그때도 executed 를 세우므로, 표식이 없으면 빈 관계·빈 발췌가 "연결
    # 없는 노드"로 읽힌다. 최상위 `error` 는 쓰지 않는다 — REST v1 이 해소된 답을 500 으로 바꾼다.
    why = []
    if ctx.get("error"):
        why.append(f"reading around the node failed: {ctx['error']}; "
                   "excerpts and links may be missing")
    if ctx.get("excerpt_error"):
        why.append(f"excerpts could not be searched: {ctx['excerpt_error']}")
    if links.get("executed") is False:
        why.append("links could not be read, so an empty list does not mean "
                   "the node has no relations")
    if why:
        base["partial"] = True
        base["partial_note"] = "; ".join(why)
    return base


def _brief_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _brief_norm(s) -> str:
    return str(s or "").strip().lower()


def _brief_strict_on() -> bool:
    """엄격 판정을 켤 것인가. **기본은 켜짐**이고 `VINC_BRIEF_STRICT=off` 가 옛 규칙으로
    되돌린다(비상구 — 켠 채로 두는 것이 맞다는 근거는 아래에 있다).

    옛 규칙은 토큰 하나 차이면 확신했다. 실측(2026-09-09, 정답 66문항 · 1365노드):
    답 가능 60건 중 **46건을 확신하고 그중 36건이 틀렸다**. 확신 46번에 정답 10번은
    답이 아니라 소음이고, 지식 도구에서 그것은 되묻는 것보다 나쁘다.

    엄격 판정은 확신 15건 · 그중 정답 8건 · **오답 7건**이다. 맞는 확신 둘과 노출 R@1
    한 건을 내주고 확신 오답 29건을 지운다. 의미 팔까지 켜면 확신 20 · 정답 13 · 오답 7
    이고 노출 R@1 이 0.1667 에서 0.2833 으로 오른다 — 두 변경은 **따로 재면 둘 다 안
    오르고 함께여야 오른다**(팔은 후보 1위를 9건에서 17건으로 올려 놓는데, 옛 판정이
    그 위에 확신 오답을 얹고 목록을 감췄다).
    근거: [[record:the-confidence-bar-was-one-token-and-that-was-the-defect-2026-09-10]]"""
    return str(os.environ.get("VINC_BRIEF_STRICT", "")).strip().lower() not in (
        "0", "off", "false", "no")


#: 확신에 필요한 토큰 차. 옛 값 1 은 **토큰 하나 차이면 확신한다**는 뜻이라 사실상
#: 문턱이 없었다. 이 값을 내리기 전에 :func:`_brief_strict_on` 의 수치를 볼 것.
_STRICT_MARGIN = 2


def _brief_sem_promote_on() -> bool:
    """되묻기 1위를 의미 우위로 확정할 것인가. **기본은 켜짐**이고
    `VINC_BRIEF_SEM_PROMOTE=off` 가 토큰 규칙만 쓰던 동작으로 되돌린다(측정용 비상구).
    끄면 brief 의 맞는 확정이 절반 아래로 줄고, 틀린 노드 확정은 줄지 않는다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    return str(os.environ.get("VINC_BRIEF_SEM_PROMOTE", "")).strip().lower() not in (
        "0", "off", "false", "no")


#: 승격에 필요한 의미 우위: 의미 팔 원시 목록에서 1위가 나머지보다 가까운 거리(`sem_lead`,
#: search 가 단다). 유효구간 동점 가드가 지운 행과 견줄 때도 같은 값을 쓴다. `_SEM_GAP`
#: (0.010, 순서만 바꾼다)과 다른 값이다. 내리면 틀린 노드 확정이 늘어난다. 옮기려면 다시 잴 것.
#: 0.12 에서 0.10 으로 옮긴 것은 봉인 셋 7을 운영 search 출력 재현으로 채점한 결과다(2026-10-24).
#: 근거: [[vinc.spec.retrieval-knobs]] §2
_SEM_PROMOTE = 0.10

#: 한글 없는 질문(어느 노드든), 또는 한글 있는 질문 + 한글 없는 노드의 승격 문턱. 언어를 건너면 거리가
#: 넓어져(정답 거리 중앙값 en2ko 0.419, ko 0.328) 같은 1위인데도 우위가 `_SEM_PROMOTE` 밑에 머문다.
#: 영어 질문은 영어 노드에도 이 문턱을 쓴다: 1위가 정답인데 우위가 0.06~0.10 에 머문 행이 많았다.
#: 한글 질문 + 한국어 노드는 `_SEM_PROMOTE` 그대로다. 0.06 으로 내리면 맞는 확정은 늘지만 그래프에 없는
#: 사실을 묻는 질문의 확정이 등록 한도보다 늘어 채택하지 않았다.
#: 근거: [[vinc.plan.brief-korean-on-korean-bar-sealed13-2026-10-26]]
#: `_brief_xlang_lead` 가 쓰는 조건에서만 `_SEM_PROMOTE` 자리에 들어가고, 유효구간 동점 가드의 거리는
#: 그대로 `_SEM_PROMOTE` 다. 내리면 틀린 노드 확정이 는다. 옮기려면 다시 잴 것.
#: 근거: [[vinc.spec.retrieval-knobs-brief]]
_SEM_PROMOTE_XLANG = 0.06

_HANGUL = re.compile("[가-힣]")


def retrieval_overrides() -> list[str]:
    """측정상 크게 나빠지는 비상구 가운데 지금 열린 것(환경변수 이름). 없으면 빈 목록.

    `VINC_BRIEF_STRICT`·`VINC_NODE_SEM_RANK`·`VINC_NODE_SEMANTIC_ARM` 은 둘 다 켜야 오르는
    한 쌍이 셋으로 묶인 것이라, 하나라도 끄면 1위 정답과 오답 확정이 함께 무너진다.
    `VINC_BRIEF_SEM_PROMOTE` 를 끄면 brief 의 맞는 확정이 절반 아래로 준다.
    `/health` 가 이 목록을 실어 꺼진 채 배포된 것이 밖에서 보이게 한다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    out = []
    if not _brief_strict_on():
        out.append("VINC_BRIEF_STRICT")
    if not _brief_sem_promote_on():
        out.append("VINC_BRIEF_SEM_PROMOTE")
    if not _retrieval._node_sem_rank_on():
        out.append("VINC_NODE_SEM_RANK")
    if not _retrieval._node_semantic_arm_on():
        out.append("VINC_NODE_SEMANTIC_ARM")
    return out


def retrieval_switches() -> dict:
    """노드 재채점과 brief 규칙의 **실제** 값. `/health` 가 늘 싣는다 — 운영 값은 저장소 변수에만
    있어서 밖에서 확인할 길이 없었다. ColBERT 는 설정이 on 이어도 numpy 가 없으면 꺼지므로
    환경변수가 아니라 :func:`vinc_rag.colbert.rescore_on` 을 읽는다.
    근거: [[vinc.canon.rag-production-state]]"""
    from vinc_rag import colbert as _colbert
    return {"VINC_NODE_COLBERT": "on" if _colbert.rescore_on() else "off",
            "VINC_BRIEF_RULE": _brief_rule()}


def _brief_token_decisive(top: dict, second: dict) -> bool:
    """1위가 2위를 토큰으로 갈랐는가 — 엄격 규칙이면 `_STRICT_MARGIN`, 아니면 1."""
    margin = _STRICT_MARGIN if _brief_strict_on() else 1   # 1 이면 현행과 글자 그대로 같다
    return _brief_int(top.get("hit_tokens")) > 0 and (
        _brief_int(top.get("hit_tokens")) - _brief_int(second.get("hit_tokens")) >= margin
        or _brief_int(top.get("title_tokens"))
        - _brief_int(second.get("title_tokens")) >= margin)


def _brief_hidden_ties(query: str, top: dict, hidden) -> bool:
    """유효구간 필터가 감춘 행이 남았다면 1위의 토큰 확정이 서지 않았는가.

    비교는 **확정에 쓴 규칙 그대로**다. `hidden` 은 감춘 행들의 축별 최댓값
    `(hit_tokens, title_tokens)` 이라, 거기에 이기면 감춘 모든 행에 이긴다.
    - 이름을 댄 확정(제목이 질문과 같음)은 같은 제목이 둘이면 되묻는다. 감춘 행이 두 축
      모두 1위 이상이면 같은 제목일 수 있으므로 비긴 것으로 본다.
    - 그 밖의 확정은 `_brief_token_decisive` 다. 튜플 하나로 사전순 비교하면 hit 가 하나 높은
      행이 제목으로 크게 앞선 확정을 되돌리고, 제목이 높은 행은 놓친다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    h_hit = _brief_int(hidden[0]) if len(hidden) > 0 else 0
    h_title = _brief_int(hidden[1]) if len(hidden) > 1 else 0
    if _brief_norm(top.get("title")) == _brief_norm(query):
        return (h_hit >= _brief_int(top.get("hit_tokens"))
                and h_title >= _brief_int(top.get("title_tokens")))
    return not _brief_token_decisive(top, {"hit_tokens": h_hit, "title_tokens": h_title})


def _brief_semantic_tie_clear(picked: list[dict], res: dict) -> bool:
    """의미 우위 승격으로 확정된 1위가 유효구간 동점 가드를 통과하는가.

    가드는 지운 행과 1위를 **확정에 쓴 잣대로** 견준다. 토큰으로 가른 확정은 토큰 점수로
    견주고(이 함수는 False), 승격(토큰으로는 못 가르고 의미 우위로 확정)은 의미 거리로
    견준다: 의미 팔이 지운 행 중 가장 가까운 것이 1위보다 `_SEM_PROMOTE` 안쪽이면, 그 행이
    남았을 때 우위가 없었으므로 확정하지 않는다. 의미 팔이 아무것도 지우지 않았으면 우위는
    필터가 만든 것이 아니다. 엄격 규칙이 꺼져 있으면 순서가 토큰으로 정해지므로 토큰 비교를 쓴다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    if len(picked) < 2 or not _brief_strict_on() or _brief_token_decisive(picked[0], picked[1]):
        return False
    top = picked[0]
    try:
        top_d = float(top.get("distance"))
    except (TypeError, ValueError):
        return False
    dropped = res.get("validity_best_dropped_distance")
    if dropped is None:
        return True
    try:
        return float(dropped) - top_d >= _SEM_PROMOTE
    except (TypeError, ValueError):
        return False


def _brief_rank(rows: list[dict]) -> list[dict]:
    """해소 순위. 확정 판정과 되묻기 목록이 같은 순서를 쓰도록 한 자리에 둔다.

    안정 정렬이라 동점이면 사이드카가 준 순서(RRF)를 그대로 존중한다.
    `arms` 가 없는 호출부(직접 만든 행·구 테스트)는 전부 0 이라 순서가 안 바뀐다."""
    strict = _brief_strict_on()
    return sorted(rows, key=lambda h: (
        # 의미 팔이 자기 기준으로 단호했던 노드를 먼저 놓는다(`_SEM_GAP` 주석).
        # **순서만** 바꾼다 — `_brief_resolve` 의 `decisive` 는 여전히 어휘 여유를 요구한다.
        -1 if (strict and h.get("sem_decisive")) else 0,
        -len(h.get("arms") or []) if strict else 0,
        -_brief_int(h.get("hit_tokens")),
        -_brief_int(h.get("title_tokens"))))


def _brief_rule() -> str:
    """`VINC_BRIEF_RULE` 의 값. `v3` 만 학습 규칙이고, 그 밖의 값과 빈 값은 production 이다."""
    v = (os.environ.get("VINC_BRIEF_RULE") or "production").strip().lower()
    return v if v == "v3" else "production"


def _brief_rule_v3() -> bool:
    """`VINC_BRIEF_RULE=v3` — brief 경로 전체로 다시 맞춘 v3 학습 규칙(:func:`_brief_resolve_v3`)."""
    return _brief_rule() == "v3"


def _brief_rule_colbert() -> bool:
    """학습 규칙(v3)이면 ColBERT 로 다시 선 후보 위에서 해소한다. 기존 규칙은 그 순서를 안 본다."""
    return _brief_rule() != "production"


def _brief_learned_features(query: str, hits: list[dict]) -> list[float] | None:
    """연구 `stage3.py` `feats` 와 같은 열 개 — v3 특징(:func:`_brief_v3_features`)의 앞 열이다.
    hits 는 ColBERT 순서이고 행마다 `colbert`·`prod_rank` 가 있다."""
    if not hits or "colbert" not in hits[0]:
        return None
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0  # noqa: E731
    h1, h2 = hits[0], (hits[1] if len(hits) > 1 else {})
    s1, s2 = num(h1.get("colbert")), (num(h2.get("colbert")) if h2 else 0.0)
    rank = int(h1.get("prod_rank") or 21)
    return [s1, s1 - s2, float(rank == 1), float(rank),
            num(h1.get("hit_tokens")) / max(num(h1.get("token_total")), 1.0),
            num(h1.get("title_tokens")), num(h1.get("hit_tokens")) - num(h2.get("hit_tokens")),
            num(h1.get("sem_lead")), num(h1.get("distance")) if h1.get("distance") is not None else 1.0,
            float(bool(_HANGUL.search(query or "")))]


def _brief_v3_prod_head(hits: list[dict]) -> list[dict]:
    """재채점 **전** 순서(`prod_rank`)의 앞 `_BRIEF_RESOLVE_N` 개. v3 의 의미만 기권은 이 목록으로 본다.
    `prod_rank` 가 없는 행은 뒤로 간다(안정 정렬)."""
    return sorted(hits, key=lambda h: h.get("prod_rank") or 99)[:_BRIEF_RESOLVE_N]


def _brief_v3_features(query: str, hits: list[dict], res: dict) -> list[float] | None:
    """v3 특징 열넷. 앞 열은 :func:`_brief_learned_features` 그대로(ColBERT 순서의 앞 다섯), 뒤 넷은
    유효구간 필터가 감춘 행과 ColBERT 1위의 차이다:
    감춘 행 최대 hit 토큰 − 1위 hit 토큰 · 감춘 행 최대 제목 토큰 − 1위 제목 토큰 ·
    감춘 행 최소 의미 거리 − 1위 거리(어느 쪽이든 없으면 1.0) · 감춘 행이 있었는가."""
    a = _brief_learned_features(query, hits)
    if a is None:
        return None
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    pick = hits[0]
    hb = res.get("validity_best_dropped") or [0, 0]
    d, hd = num(pick.get("distance")), num(res.get("validity_best_dropped_distance"))
    return a + [_brief_int(hb[0] if hb else 0) - _brief_int(pick.get("hit_tokens")),
                _brief_int(hb[1] if len(hb) > 1 else 0) - _brief_int(pick.get("title_tokens")),
                (hd - d) if (d is not None and hd is not None) else 1.0,
                float(bool(res.get("validity_filtered")))]


def _brief_v3_p(x: list[float]) -> float:
    z = sum(((xi - m) / sd) * w for xi, m, sd, w in zip(x, _V3_MU, _V3_SD, _V3_W)) + _V3_W[-1]
    return 1.0 / (1.0 + math.exp(-z))


def _brief_resolve_v3(query: str, hits: list[dict], res: dict) -> tuple[str, list[dict], float | None]:
    """v3 규칙으로 해소한다. `hits` 는 ColBERT 순서의 앞 `_BRIEF_RESOLVE_N` 개, `res` 는 search 응답이다.
    의미만 기권(:func:`_brief_v3_prod_head`)은 brief 가 이 앞에서 끝냈다. 특징을 못 만들면 되묻는다.
    제목이 질문과 같은 후보가 **하나**면 그것으로, 아니면 ColBERT 1위의 확률이 문턱 이상일 때만 확정한다.
    토큰 동점 가드는 이 확정에 돌지 않는다 — 그 정보는 뒤 네 특징에 들어 있다."""
    x = _brief_v3_features(query, hits, res)
    if x is None:
        return "choose", list(hits), None
    norm = lambda t: " ".join((t or "").strip().lower().split())  # noqa: E731
    named = [h for h in hits if norm(h.get("title")) == norm(query)]
    p = _brief_v3_p(x)
    if len(named) == 1:
        return "resolved", named, p
    return ("resolved", [hits[0]], p) if p >= _V3_T else ("choose", list(hits), p)


def _brief_list_rank(rows: list[dict]) -> list[dict]:
    """되묻기 목록의 순서. 의미 팔이 단호했던 노드가 먼저고, 그다음은 의미 거리가 가까운 순이며,
    거리가 없는 후보(제목·그래프 팔만)는 뒤에 융합 순서대로 둔다. 확정에는 쓰지 않는다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    strict = _brief_strict_on()

    def dist(h):
        d = h.get("distance")
        return d if isinstance(d, (int, float)) and not isinstance(d, bool) else float("inf")

    return sorted(rows, key=lambda h: (-1 if (strict and h.get("sem_decisive")) else 0, dist(h)))


def _brief_mirror_text(store, owner, hit: dict) -> str | None:
    """후보 한 노드의 검색 미러 텍스트(`NodeVec.text`). 행이 없으면 None."""
    from . import node_mirror
    rows = store.run("MATCH (n:NodeVec) WHERE n.vec_id = $v RETURN n.text AS t LIMIT 1",
                     {"v": node_mirror.vec_id(owner or "", str(hit.get("id") or ""))}) or []
    return (rows[0].get("t") or None) if rows else None


def _brief_xlang_lead(query: str, top: dict, mirror_text=None) -> bool:
    """`_SEM_PROMOTE_XLANG` 을 쓸 자리인가: 한글 없는 질문이거나(노드 언어와 상관없이),
    한글 있는 질문인데 승격될 1위의 미러 텍스트에 한글이 없다.

    `mirror_text(h)` 는 노드의 미러 텍스트(제목 + 부가 본문 앞 300자, 에피소드는 600자)를 주거나 None 이다. 없거나
    읽기가 실패하면 제목으로 갈음한다(미러 행이 없는 후보도 같은 길이다). 한글 없는 질문은 미러를 읽지 않는다."""
    asked_korean = bool(_HANGUL.search(query or ""))
    if not asked_korean:
        return True
    text = None
    if mirror_text is not None:
        try:
            text = mirror_text(top)
        except Exception:
            text = None
    return asked_korean != bool(_HANGUL.search(text or str(top.get("title") or "")))


def _brief_resolve(query: str, hits: list[dict],
                   mirror_text=None) -> tuple[str, list[dict]]:
    """node_hits → (상태, 고른 것). resolved 면 1건, choose 면 후보 여럿.

    ⚠ 그래프·의미 팔만 올라온 경우(hit_tokens 가 전부 0)는 확정하지 않는다 — 후보가
    **하나뿐이어도** 그렇다. 청크 인접이나 벡터 거리는 "관련 있다"는 뜻이지 "이것을
    물었다"는 뜻이 아니다. 이 갈래가 없던 시절 이 표면은 후보를 id 알파벳순으로 집어
    질문과 무관한 답을 냈다(ask.ts 2026-08-03 재설계).

    제목이 질문과 글자 그대로 같으면(이름을 댄 경우) 랭킹 앞에서 그 노드로 확정한다.
    같은 제목이 둘 이상이면 어느 쪽을 물었는지 모르므로 되묻는다(doc 미러 제목 충돌).
    근거: [[vinc.plan.rag-code-review-fixes-2026-09-29 §7]]

    **의미 우위 승격**(`VINC_BRIEF_SEM_PROMOTE`, 기본 켜짐)은 위 ⚠ 의 유일한 예외다.
    랭킹 갈래에서 choose 가 될 1위가 의미 팔 원시 목록의 나머지보다 `_SEM_PROMOTE` 이상
    가까우면 토큰이 0 이어도 확정한다. 이름 댄 갈래(같은 제목 둘 포함)와 단독 후보 갈래는
    그 앞에서 돌아오므로 승격되지 않는다. 단독 후보에는 앞설 나머지가 없어 `sem_lead` 도
    안 붙는다. 유효구간 동점 가드와 답변 가능성 게이트는 brief 에서 이 뒤에 그대로 돈다.
    한글 없는 질문이거나 질문과 1위 노드의 언어가 다르면(한글 있는 질문 + 미러 텍스트에 한글 없음) 문턱은
    `_SEM_PROMOTE_XLANG` 이다.

    **엄격 판정**(`VINC_BRIEF_STRICT`, 기본 켜짐)은 순서에 `arms` 를 먼저 넣고 문턱을
    `_STRICT_MARGIN` 으로 올린다. 여러 팔이 같은 노드를 올렸다는 것은 어휘와 의미가
    함께 가리켰다는 뜻이라, 토큰 하나를 더 맞힌 것보다 강한 증거다.
    ⚠ **융합 점수(RRF)로 정렬하는 안은 기각됐다.** 팔을 켜면 좋아 보이지만 끄면 나빠진다
    (오답확정 37 → 44) — 팔이 둘뿐일 때 RRF 순위는 토큰 순서보다 못하다. 판정은 팔의
    기본값과 무관하게 모두에게 적용되므로 팔에 의존하는 규칙은 쓸 수 없다.
    근거: [[record:the-confidence-bar-was-one-token-and-that-was-the-defect-2026-09-10]]"""
    rows = [h for h in hits if isinstance(h, dict) and h.get("id")]
    if not rows:
        return "none", []
    q = _brief_norm(query)
    # 이름을 댄 경우는 한 행만 돌려준다 — 폴스루가 다른 이름의 노드로 넘어가면 안 된다.
    named = [h for h in rows if _brief_norm(h.get("title")) == q]
    if len(named) == 1:
        return "resolved", named
    if named:
        return "choose", _brief_rank(named)[:_brief_expose_n()]
    if len(rows) == 1:
        return ("resolved" if _brief_int(rows[0].get("hit_tokens")) > 0
                else "choose"), rows
    ranked = _brief_rank(rows)
    top, second = ranked[0], ranked[1]
    decisive = _brief_token_decisive(top, second)
    # 의미 우위 승격(플래그 뒤): 되묻기가 될 자리에서만 돈다. 1위가 의미 팔의 1위가
    # 아니면 `sem_lead` 가 없다(그 노드보다 가까운 것이 있으니 우위가 음수다).
    lead = top.get("sem_lead")
    promoted = (not decisive and _brief_sem_promote_on()
                and isinstance(lead, (int, float)) and lead >= _SEM_PROMOTE)
    # 질문과 1위 노드의 언어가 다르면 더 낮은 문턱을 쓴다. 미러 텍스트는 이 문턱에 걸릴
    # 때만 읽는다(`mirror_text` 는 호출부가 스토어를 쥐고 있을 때만 주는 지연 조회다).
    if (not decisive and not promoted and _brief_sem_promote_on()
            and isinstance(lead, (int, float)) and lead >= _SEM_PROMOTE_XLANG
            and _brief_xlang_lead(query, top, mirror_text)):
        promoted = True
    # 확정이어도 **순위 전체**를 돌려준다. 1위가 상세에서 안 열릴 때 호출부가 다음
    # 후보로 넘어갈 수 있어야 하기 때문이고, 그 폴스루는 밝혀서 한다(brief 참조).
    return (("resolved", ranked) if decisive or promoted
            else ("choose", ranked[:_brief_expose_n()]))


def _brief_body(ctx: dict) -> dict:
    """본문 고르기 — one_liner → props → 정본 발췌. ask.ts pickBody 의 이식.

    one_liner 만 보면 답이 통째로 빌 수 있다(그 열이 생기기 전에 쓰인 개념은 여전히
    빈 값이다). 마지막 폴백인 발췌는 생성이 아니라 **인용**이라 출처를 함께 낸다.
    ⚠ ask.ts 와 한 곳 다르다: 같은 길이의 props 값이 여럿일 때 ask.ts 는 객체 순서에
    기대지만, 여기서는 키 이름으로 갈라 같은 질문이 늘 같은 답을 내게 했다."""
    ol = str(ctx.get("one_liner") or "").strip()
    if ol:
        return {"text": ol, "src": "one_liner", "cite": ""}
    props = ctx.get("props")
    props = props if isinstance(props, dict) else {}
    for k in _BRIEF_BODY_KEYS:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return {"text": v.strip(), "src": "props", "cite": ""}
    rest = sorted(((k, v.strip()) for k, v in props.items()
                   if isinstance(v, str) and len(v.strip()) > _BRIEF_MIN_PROP_BODY),
                  key=lambda kv: (-len(kv[1]), kv[0]))
    if rest:
        return {"text": rest[0][1], "src": "props", "cite": ""}
    for e in ctx.get("excerpts") or []:
        text = str(e.get("excerpt") or "").strip()
        if text:
            where = str(e.get("doc_title") or e.get("doc_id") or "")
            sec = str(e.get("section") or "")
            return {"text": text, "src": "doc",
                    "cite": (where + " · " + sec) if sec else where}
    return {"text": "", "src": "none", "cite": ""}


def _brief_grounds(node_id: str, excerpts: list[dict], items: list[dict]) -> list[str]:
    """모든 주장이 딛고 선 id — 노드·정본 문서·관계 상대. 정렬해 결정론으로 둔다.

    축소기가 발췌를 덜어내도 이 목록은 남으므로, 잘린 재료를 doc_id 로 다시 가져올
    수 있다."""
    out = {node_id} if node_id else set()
    out |= {str(e.get("doc_id") or "") for e in excerpts}
    out |= {str(i.get("id") or "") for i in items}
    return sorted(x for x in out if x)


def _brief_answerability_on() -> bool:
    """답변 가능성 게이트를 켤 것인가. **기본은 꺼짐**(`VINC_BRIEF_ANSWERABILITY`).

    끄고 켤 수 있어야 하는 이유: 이 문턱은 정답 81문항 중 **확정 18건** 위에서 골랐고,
    답 불가 21문항 중 15문항을 이 세션에 내가 썼다. 즉 시험지 절반이 이 신호를 편들
    수 있다. 기본값을 바꾸기 전에 남이 쓴 문항이 늘어나야 한다."""
    return truthy(os.environ.get("VINC_BRIEF_ANSWERABILITY"))


def _brief_section_on() -> bool:
    """질문으로 문서 미러에 닿은 brief 에 `section` 을 실을 것인가(`VINC_BRIEF_SECTION`).
    기본은 켜짐이고 ``off``·``0``·``false``·``no`` 가 끈다(끄면 이 키가 없던 때와 같다).
    근거: [[vinc.research.answer-material-section-ai-corpus-2026-10-17]]"""
    v = str(os.environ.get("VINC_BRIEF_SECTION", "")).strip().lower()
    return v not in ("off", "0", "false", "no")


def _brief_pick_chunk(store, chunks: list[dict], *, query: str, query_embedding,
                      top_id: str = "") -> tuple[int, str]:
    """한 문서(또는 한 노드)의 청크 가운데 질문이 가리키는 것의 자리와 근거.

    질문 벡터에 가장 가까운 조각을 가진 청크(`vector`), 벡터가 없으면 검색 1위 청크(`rank`),
    그것도 아니면 질문 토큰이 가장 여러 가지 든 청크(`lexical`)다. `chunks` 는 {cid, text} 를
    순서대로 준 것이고 비어 있으면 안 된다."""
    ids = [str(c["cid"]) for c in chunks]
    if query_embedding is not None:
        dist = store.chunk_vector_distances(ids, query_embedding)
        if dist:
            return (min((i for i, cid in enumerate(ids) if cid in dist),
                        key=lambda i: (dist[ids[i]], i)), "vector")
    if top_id and top_id in ids:
        return ids.index(top_id), "rank"
    from store.lexical import find_tokens, lex_tokens
    toks = lex_tokens(query)
    return (max(range(len(chunks)), key=lambda i: (
        len({t for _, t in find_tokens(str(chunks[i].get("text") or ""), toks)}), -i)), "lexical")


def _brief_section(store, *, owner: str, query: str, rows: list[dict],
                   query_embedding, nodes: list[str]) -> dict | None:
    """질문 검색 1위 문서에서 **질문이 가리키는 절**. 그 문서의 미러가 ``nodes``(확정 노드나
    노출 후보)에 있을 때만 낸다 — 아니면 답과 무관한 문서의 본문이 실린다.

    노드 발췌는 노드를 뒷받침하는 창이라 문서 미러에서는 문서 제목이 나오는 자리를 보여 준다.
    이 절은 질문에 답하는 자리다. 가리키는 청크는 그 문서 청크 가운데 질문 벡터와 가장 가까운
    조각을 가진 것이고(`basis: vector`), 벡터가 없으면 검색 1위 청크(`rank`), 그것도 그 문서가
    아니면 질문 토큰이 가장 여러 가지 든 청크다(`lexical`). 절의 범위는 `section_span`.
    읽기 실패는 None 이다 — 이 키는 증강이라 답을 막지 않는다."""
    top = next((r for r in rows if isinstance(r, dict) and r.get("doc_id")), None)
    if top is None:
        return None
    doc_id = str(top["doc_id"])
    mirror = _DOC_CONCEPT_PREFIX + doc_id
    if mirror not in nodes:
        return None
    try:
        chunks = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d AND (c.owner_id = $o OR c.owner_id = '') "
            "RETURN c.chunk_id AS cid, c.section_title AS sec, c.text AS text "
            "ORDER BY c.chunk_index", {"d": doc_id, "o": owner}) or []
        if not chunks:
            return None
        ids = [str(c["cid"]) for c in chunks]
        at, basis = _brief_pick_chunk(store, chunks, query=query, query_embedding=query_embedding,
                                      top_id=str(top.get("id") or ""))
        lo, hi, text, cut = _excerpt.section_span(chunks, at)
    except Exception:
        return None
    return {"node_id": mirror, "cite_id": mirror, "doc_id": doc_id,
            "section": str(chunks[at].get("sec") or ""), "chunk_ids": ids[lo:hi + 1],
            "text": text, "truncated": cut, "basis": basis}


def _brief_attach_section(base: dict, store, *, owner: str, query: str, res: dict,
                          query_embedding, nodes: list[str]) -> None:
    """`section` 을 싣고 그 미러를 `grounds` 에 더한다(플래그가 꺼져 있으면 아무것도 안 한다)."""
    if not _brief_section_on():
        return
    sec = _brief_section(store, owner=owner, query=query, rows=res.get("rows") or [],
                         query_embedding=query_embedding, nodes=nodes)
    if sec is None:
        return
    base["section"] = sec
    for key in ("grounds", "cite_grounds"):
        if key in base and sec["node_id"] not in base[key]:
            base[key] = sorted(set(base[key]) | {sec["node_id"]})


def _brief_quotes_on() -> bool:
    """후보와 확정 노드에 `quotes`(글자 그대로의 인용 단위)를 실을 것인가(`VINC_BRIEF_QUOTES`).
    기본은 켜짐이고 ``off``·``0``·``false``·``no`` 가 끈다(끄면 이 키가 없던 때와 같다).
    근거: [[vinc.manual.rag]]"""
    v = str(os.environ.get("VINC_BRIEF_QUOTES", "")).strip().lower()
    return v not in ("off", "0", "false", "no")


#: 노드마다 싣는 인용 단위 수와, 개념의 MENTIONS 청크에서 고를 후보 수.
_BRIEF_QUOTES_N = 2
_BRIEF_QUOTE_POOL = 6


def _brief_node_quotes(store, *, owner: str, node_id: str, query: str,
                       query_embedding) -> list[dict]:
    """노드를 받치는 정본 문서에서 질문이 가리키는 인용 단위. 문서 미러는 그 문서의 청크에서,
    개념은 MENTIONS 로 닿는 청크(링크 방법이 강한 순)에서 질문 벡터에 가장 가까운 청크 하나를
    골라 그 안에서 단위를 낸다(`vinc_rag.excerpt.quote_units`).

    단위마다 `doc_id`·`source`(문서 제목)·`section`(절 제목)을 함께 싣는다 — 인용문이 어느
    문서의 어느 절에서 왔는지를 주장이 인용문 밖에서 끌어오지 않게 하려는 것이다. 다른 종류의
    노드나 읽기 실패는 빈 목록이다. 이 키는 증강이라 답을 막지 않는다."""
    try:
        if node_id.startswith(_DOC_CONCEPT_PREFIX):
            chunks = store.run(
                "MATCH (c:Chunk) WHERE c.doc_id = $d AND (c.owner_id = $o OR c.owner_id = '') "
                "RETURN c.chunk_id AS cid, c.doc_id AS doc_id, c.section_title AS sec, "
                "c.text AS text ORDER BY c.chunk_index",
                {"d": node_id[len(_DOC_CONCEPT_PREFIX):], "o": owner}) or []
        elif node_id.startswith("concept:"):
            label, pk = _seed_table(node_id)
            if label != "Concept":
                return []
            chunks = store.run(
                f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{label}) "
                f"WHERE c.{pk} = $id AND c.owner_id = $o "
                "AND (ch.owner_id = $o OR ch.owner_id = '') "
                "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, ch.section_title AS sec, "
                "ch.text AS text "
                "ORDER BY CASE m.method WHEN 'title' THEN 0 WHEN 'stem' THEN 1 ELSE 2 END, "
                f"ch.doc_id, ch.chunk_index LIMIT {_BRIEF_QUOTE_POOL}",
                {"id": pkns.key(owner, node_id), "o": owner}) or []
        else:
            return []
        if not chunks:
            return []
        at, _ = _brief_pick_chunk(store, chunks, query=query, query_embedding=query_embedding)
        pick = chunks[at]
        units = _excerpt.quote_units(str(pick.get("text") or ""), query, n=_BRIEF_QUOTES_N)
        if not units:
            return []
        doc_id = str(pick.get("doc_id") or "")
        title = ""
        for r in store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d "
                           "AND (d.owner_id = $o OR d.owner_id = '') RETURN d.title AS t",
                           {"d": doc_id, "o": owner}) or []:
            title = str(r.get("t") or "")
            break
        return [{"doc_id": doc_id, "source": title or doc_id,
                 "section": str(pick.get("sec") or ""), **u} for u in units]
    except Exception:
        return []


def _brief_expose_n() -> int:
    """되묻기(choose)에 싣는 후보 수. 기본은 `_BRIEF_CANDIDATES`.

    `VINC_BRIEF_EXPOSE` 는 노출을 바꿔 재기 위한 손잡이다(X1). 기본값을 바꾸는 것은
    결정 노드로만 한다. 잘못된 값은 기본으로 읽는다.
    근거: [[vinc.plan.rag-eval-v2-0-9-6]]"""
    raw = str(os.environ.get("VINC_BRIEF_EXPOSE", "")).strip()
    try:
        return max(1, int(raw)) if raw else _BRIEF_CANDIDATES
    except ValueError:
        return _BRIEF_CANDIDATES


def _brief_list_resolved_on() -> bool:
    """확정(resolved)에도 순위 목록을 `candidates` 로 곁에 실을 것인가. **기본은 꺼짐**
    (`VINC_BRIEF_LIST_RESOLVED`). 확정이 틀렸을 때 그 뒤에 가려진 정답을 부르는 쪽이
    볼 수 있게 한다(X1). status 와 node 는 그대로다 — 목록이 답을 바꾸지 않는다.
    근거: [[vinc.plan.rag-eval-v2-0-9-6]]"""
    return truthy(os.environ.get("VINC_BRIEF_LIST_RESOLVED"))


def _brief_candidate_rows(picked: list[dict]) -> list[dict]:
    """노드 후보를 brief 가 내보내는 후보 꼴로 줄인다."""
    return [{
        "id": h.get("id") or "",
        "cite_id": h.get("id") or None,
        "title": h.get("title") or (h.get("id") or ""),
        "domain": h.get("domain") or "", "kind": h.get("kind") or "",
        "matched_in": h.get("matched_in") or "",
        "hit_tokens": _brief_int(h.get("hit_tokens")),
        "title_tokens": _brief_int(h.get("title_tokens")),
        **{k: h[k] for k in ("temporal", "created_at", "updated_at", "content_time",
                             "valid_from", "valid_until", "validity", "superseded_by") if k in h},
    } for h in picked]


#: choose 의 첫 후보 재료에 싣는 발췌 수 상한(`max_excerpts` 가 더 작으면 그쪽).
_BRIEF_CHOOSE_EXCERPTS = 2


def _brief_candidate_material(store, cand: dict, *, max_excerpts: int, owner: str, mode,
                              historical: bool, as_of_scope, known_scope) -> dict | None:
    """choose 의 첫 후보 재료 — 확정 답과 같은 이름(one_liner·body_src·body_cite·excerpts)으로,
    발췌는 같은 시간 규칙으로 거른다. 확정이 아니므로 `node`·`grounds`·관계는 싣지 않는다.
    후보가 안 열리면 None. 감춘 수는 choose 응답의 수에 더하지 않고 재료 안의 알림으로만 낸다.
    근거: [[vinc.plan.choose-cost-measurement-2026-10-19]]"""
    cid = str(cand.get("id") or "")
    if not cid:
        return None
    got = node_context(owner=owner, node=cid, limit=max(1, int(max_excerpts)), store=store)
    if not got.get("executed"):
        return None
    ctx, note = _brief_scope_material({**got, "links": {}}, store, {}, owner=owner, mode=mode,
                                      historical=historical, as_of_scope=as_of_scope,
                                      known_scope=known_scope)
    ctx["excerpts"] = list(ctx.get("excerpts") or [])[:min(_BRIEF_CHOOSE_EXCERPTS,
                                                          max(0, int(max_excerpts)))]
    body = _brief_body(ctx)
    out = {"candidate": cid, "cite_id": cid, "one_liner": body["text"],
           "body_src": body["src"], "body_cite": body["cite"], "excerpts": ctx["excerpts"]}
    if note:
        out["validity_note"] = note
    why = [f"reading around the node failed: {got['error']}" if got.get("error") else "",
           f"excerpts could not be searched: {got['excerpt_error']}"
           if got.get("excerpt_error") else ""]
    if any(why):
        out["partial"] = True
        out["partial_note"] = "; ".join(w for w in why if w)
    return out


#: 제목이 안 먹은 부분 중 **검색이 가져온 어디에도 없는** 토큰의 비율이 이 값을 넘으면
#: 확신하지 않는다. 실측으로 고른 자리다(2026-09-10, 정답 81문항 · 확신 18건):
#:
#:     문턱   확신  정답  오답  답불가확정
#:     꺼짐    18     8     7      3
#:     0.34     9     5     4      0
#:     0.67    14     7     7      0
#:
#: 0.34 는 오답 확정도 셋 줄이지만 그것은 **이 게이트의 일이 아니라 랭킹의 일**이고
#: (`_brief_strict_on` 이 한 것), 그 대가로 맞는 확신을 둘 더 잃는다. 0.67 은 목적
#: (답 불가에 확신을 주지 않는다)을 최소 비용으로 달성한다 — 맞는 확신 하나.
#:
#: ⚠ 잃는 그 하나는 **형태 변화** 때문이다: 질문의 `sent` 가 코퍼스의 `send` 와 표면이
#: 달라 고아로 보인다. 이것이 이 게이트의 알려진 천장이다.
#:
#: ⚠ **"어간 분석을 붙이면 된다"는 처방이 아니다 — 기각됐다(2026-09-10).** 한때 이 자리에
#: 그렇게 적혀 있었으나 재 본 적이 없는 추측이었다. 붙이면 표면이 뭉쳐 고아율이 함께
#: 낮아지므로 게이트가 무뎌질 수 있고, 그 방향은 측정되지 않았다. 그리고 이 리포에는
#: 반대 방향의 실측이 이미 있다 — 한국어 전문 검색이 지는 원인은 어간 처리가 아니었다
#: (`scripts/r1c_ko_column.py`). 천장을 넘으려면 새 측정이 필요하고, 그 측정 전에는
#: 이 문장을 처방으로 읽지 말 것.
_ORPHAN_BLOCK = 0.67


def _brief_orphan_residual(query: str, picked_title, res: dict) -> float:
    """질문에서 확정 노드의 제목이 안 먹은 부분 중, **아무 데서도 못 본** 토큰의 비율.

    어떤 토큰이 검색이 가져온 후보 제목과 청크 발췌 어디에도 없다면, 그 노드가 아니라
    **어느 노드도** 질문의 그 부분을 다루지 못한다. 주제는 맞는데 물은 것이 없는 자리다.

    코퍼스 어휘 전량과 대조하는 것이 정확하지만 질의당 402ms 다(실측). 이미 가져온
    것만 보는 이 근사는 공짜이고, 운용 문턱(0.34~0.50)에서 정확판과 **결과가 같았다**.
    근거: [[record:answerability-is-a-residual-coverage-question-2026-09-10]]"""
    qt = normalize_query_tokens(query)
    if not qt:
        return 0.0
    tt = set(normalize_query_tokens(str(picked_title or "")))
    resid = [t for t in qt if t not in tt]
    if not resid:
        return 0.0
    seen: set = set()
    for h in res.get("node_hits") or []:
        seen |= set(normalize_query_tokens(str(h.get("title") or ""), cap=64))
    for r in res.get("rows") or []:
        seen |= set(normalize_query_tokens(str(r.get("snippet") or ""), cap=256))
    return sum(1 for t in resid if t not in seen) / len(resid)


_BRIEF_SEMANTIC_NONE_DISTANCE = 0.42970955319294174


def _brief_semantic_none(res: dict) -> bool:
    """Reject distant, exclusively zero-token semantic candidates on a healthy arm.

    VIN-327: the owner approved the measured cutoff with zero answerable losses
    on the pinned evaluation set. Lexical/graph matches and unavailable measurements
    keep their existing resolution behavior. See [[vinc.manual.rag]].
    """
    hits = res.get("node_hits") or []
    if not hits or any(res.get(k) for k in (
            "error", "node_error", "vector_rebuilding")):
        return False
    # Legacy vectors lack runtime stamps but the semantic arm still ran. The
    # pinned evaluation counts this specific notice separately from failures.
    note = str(res.get("vector_note") or "")
    if note.replace(UNSTAMPED_RUNTIME_NOTE, "").strip("; "):
        return False
    if res.get("encoder_runtime_warning") and note != UNSTAMPED_RUNTIME_NOTE:
        return False
    if any(type(h.get("hit_tokens")) is not int or h["hit_tokens"] != 0
           for h in hits):
        return False
    # Lexical/graph evidence is outside semantic-only abstention.
    if any(set(h.get("arms") or []) != {"semantic"} for h in hits):
        return False
    distances = [h.get("distance") for h in hits]
    if any(type(d) not in (int, float) or not math.isfinite(d)
           or not 0.0 <= d <= 2.0 for d in distances):
        return False
    return min(distances) > _BRIEF_SEMANTIC_NONE_DISTANCE


def _brief_current_uncertain(store, *, owner: str, answer: dict, hits: list[dict]) -> bool:
    """current 모드에서 기권할 것인가 — 답(1위)과, 답과 **주장 키가 같은** 후보만 본다(B5).

    주장 키(entity·predicate·scope)가 다른 후보는 다른 주장이라 그 날짜가 이 답의 현재
    상태를 흔들지 않는다. 키를 못 읽으면 기권한다(모르면 고르지 않는다)."""
    from store import search_temporal as temporal

    def shaky(h: dict) -> bool:
        t = h.get("temporal") or {}
        return (t.get("validity") == "unknown"
                or t.get("claim_status") in ("conflict", "cycle", "unknown"))

    if shaky(answer):
        return True
    rest = [h for h in hits if isinstance(h, dict) and h.get("id")
            and h.get("id") != answer.get("id")]
    if not rest:
        return False
    try:
        sources = temporal.node_sources(store, [answer.get("id")] + [h["id"] for h in rest],
                                        owner=owner)
    except Exception:
        return True
    key = temporal.claim_key(sources.get(answer.get("id"), {}))
    if key is None:
        return False
    return any(shaky(h) for h in rest
               if temporal.claim_key(sources.get(h["id"], {})) == key)


def _brief_holds(ctx: dict, scope) -> bool:
    """직참조 노드가 as_of 기간에 성립했나. 사건은 날짜가 시작이고 끝나지 않는다 —
    `api.temporal_read` 와 검색이 같은 규칙을 쓴다(감사 F14)."""
    from store.search_temporal import overlaps_scope
    t = ctx.get("temporal") or ctx
    if (ctx.get("node_kind") or ctx.get("kind")) == "episode":
        return holds_during(t.get("content_time") or "", "", scope)
    return overlaps_scope(t.get("valid_from"), t.get("valid_until"), scope)


def _brief_known(row: dict, known_scope) -> bool | None:
    """known_at 기간이 끝나기 전에 저장됐나. 저장 시각을 모르면 None(감추지 않는다)."""
    from store.search_temporal import instant
    created = instant((row.get("temporal") or row).get("created_at"))
    if created is None:
        return None
    return created < instant(known_scope[1])


def _brief_excerpt_kept(row: dict, *, mode, historical: bool, as_of_scope) -> bool:
    """발췌 한 줄을 그 문서의 유효구간으로 가린다. 검색이 청크를 거르는 규칙과 같다
    (`vinc_rag.retrieval._drop_retired_doc_rows`, current 는 계보까지)."""
    from store.search_temporal import overlaps_scope
    t = row.get("temporal") or {}
    vf, vu = t.get("valid_from") or "", t.get("valid_until") or ""
    if mode == "current":
        return (t.get("validity") not in ("historical", "future")
                and t.get("claim_status") != "superseded")
    if as_of_scope is not None:
        return overlaps_scope(vf, vu, as_of_scope)
    if historical:
        return True
    return validity_state(vf, vu) != "historical"


def _brief_scope_material(ctx: dict, store, base: dict, *, owner: str, mode,
                          historical: bool, as_of_scope, known_scope) -> tuple[dict, str]:
    """확정 재료(발췌·관계)를 요청의 시간 규칙으로 거른다. 걸러진 ctx 사본과, 감춘 것이
    있으면 알림 문장을 돌려준다(알림은 노드 자신의 알림 뒤에 단다).

    - 발췌(B2): 문서 유효구간 — 기본은 은퇴한 문서, as_of 는 그 기간, current 는 현재·계보.
      known_at 은 문서가 처음 저장된 시각. 검색이 감추는 청크를 brief 가 인용하면 안 된다.
    - 관계(F14·B1): as_of 와 known_at 을 `api.temporal_read.Gate` 로 거른다 —
      vinc_neighbors 가 같은 요청에서 감추는 노드를 여기서도 감춘다. 수(out·in·attached·
      total)도 감춘 만큼 줄인다. 잘린 창 뒤(next_offset)의 관계는 여기서 거르지 않는다.
    감춘 수는 validity_filtered(구간)와 known_at_filtered(저장 시각)에 더한다."""
    ctx = dict(ctx)
    hidden_validity = hidden_known = 0
    unknown: set[str] = set()
    kept = []
    for row in ctx.get("excerpts") or []:
        if not _brief_excerpt_kept(row, mode=mode, historical=historical,
                                   as_of_scope=as_of_scope):
            hidden_validity += 1
            continue
        if known_scope is not None:
            known = _brief_known(row, known_scope)
            if known is None:
                unknown.add("doc:" + str(row.get("doc_id") or ""))
            elif not known:
                hidden_known += 1
                continue
        kept.append(row)
    ctx["excerpts"] = kept
    links = ctx.get("links")
    if (isinstance(links, dict) and links.get("items")
            and (as_of_scope is not None or known_scope is not None)):
        from . import temporal_read
        gate = temporal_read.Gate(store, [i.get("id") for i in links["items"]], owner=owner,
                                  as_of_scope=as_of_scope, known_scope=known_scope)
        links = dict(links)
        items = []
        for item in links["items"]:
            if gate.keep(item.get("id")):
                items.append(item)
                continue
            d = item.get("dir")
            if d == "att":
                links["attached"] = int(links.get("attached") or 0) - 1
            elif d in ("out", "in"):
                links[d] = int(links.get(d) or 0) - 1
                links["degree"] = int(links.get("degree") or 0) - 1
            links["total"] = int(links.get("total") or 0) - 1
        links["items"] = items
        ctx["links"] = links
        hidden_validity += gate.hidden_validity
        hidden_known += gate.hidden_known
        unknown |= gate.unknown_known
    note = ""
    if hidden_validity:
        base["validity_filtered"] = int(base.get("validity_filtered") or 0) + hidden_validity
        note = ("hid excerpts or relations outside the requested time scope"
                if as_of_scope is not None or mode == "current" else
                "hid excerpts of documents whose validity ended; "
                "pass historical=true to include them")
    if known_scope is not None:
        base["known_at_filtered"] = int(base.get("known_at_filtered") or 0) + hidden_known
        base["known_at_unknown"] = int(base.get("known_at_unknown") or 0) + len(unknown)
    return ctx, note


def _brief_answer(base: dict, cand: dict, ctx: dict, store, *, owner: str, mode,
                  historical: bool, as_of_scope, known_scope) -> dict:
    """시간 규칙으로 거른 재료로 답을 채운다. 직참조와 랭킹 경로가 같은 꼬리를 쓴다."""
    ctx, note = _brief_scope_material(ctx, store, base, owner=owner, mode=mode,
                                      historical=historical, as_of_scope=as_of_scope,
                                      known_scope=known_scope)
    out = _brief_material(base, cand, ctx)
    if note:
        out.setdefault("validity_note", note)
    return out


def brief(query: str, *, owner: str, query_embedding: list[float] | None = None,
          max_excerpts: int = 3, historical: bool = False, as_of: str | None = None,
          mode: str | None = None, known_at: str | None = None,
          cfg=None, store=None,
          query_runtime: str | None = None, query_model: str | None = None,
          query_colbert: str | None = None, kinds=None) -> dict:
    """질문 하나 → 답변 재료 한 벌(조인 읽기, 조회 전용).

    질문에 노드 id 가 그대로 들어 있으면 그 노드를 **직참조**한다(검색·랭킹 생략,
    resolved_via="id"). 아니면 search 로 후보를 찾아 해소하고, 확정되면
    node_context 로 본문·발췌·관계를 한 번에 붙인다. 확정이 안 되면
    status="choose" 로 후보를 돌려주지 **추측하지 않는다**.

    반환: {kind, executed, query, status: resolved|choose|none, node:{id,title,
           domain,kind}, one_liner, body_src, body_cite, excerpts:[...],
           links:{degree(=out+in),out,in,attached,total(=degree+attached, 연결수),
           items,truncated} (items 는 evidence 불리언과
           evidence_text 문장을 함께 싣는다), candidates:[...], grounds:[id...],
           resolved_via("id" — 직참조로 확정된 경우에만),
           fell_through/unreadable(안 열린 후보·질문 속 id 의 공개),
           search_error(청크 팔이 실패한 사유 — 해소는 노드 팔로 계속한다),
           partial/partial_note(노드는 읽었는데 발췌·관계를 못 읽었을 때),
           candidate_material(choose 에서만 — 첫 후보의 {candidate, cite_id, one_liner,
           body_src, body_cite, excerpts(최대 2)})}.
    확정 노드와 되묻기 후보는 `quotes`(글자 그대로의 인용 단위)를 달고 올 수 있다.

    시간 인자(`as_of`·`mode`·`historical`·`known_at`)는 여기서 전부 적용한다 —
    해소(검색), 직참조 노드, 발췌, 관계 모두에. 사이드카가 뒤에서 다시 거르지 않는다
    (감사 B1·B2·F14). `known_at` 은 저장 시각 컷오프이고 과거 본문을
    복원하지 않는다(검색과 같다).
    `kinds` 가 있으면 후보와 확정 노드를 그 종류로 한정한다(검색의 노드 팔이 자르기 전에 거른다).
    질문 속 id 가 다른 종류면 열지 않고 none 으로 답한다(as_of 밖의 id 와 같다).
    근거: [[vinc.plan.mcp-agent-surface §4]]"""
    cfg = cfg or load_config()
    base: dict = {"kind": "brief", "executed": False, "query": query,
                  "status": "none", "candidates": [], "grounds": []}
    try:
        kinds = _retrieval.parse_kinds(kinds)
    except ValueError as exc:
        base["note"] = base["error"] = f"invalid_kinds: {exc}"
        return base
    # Direct-ID reads bypass search by design, but an explicit time request must
    # still be validated and applied before the early return.
    try:
        from store.search_temporal import mode_scope
        historical, direct_scope, direct_scope_info = mode_scope(mode, historical, as_of, query)
    except ValueError as exc:
        base["note"] = base["error"] = f"invalid_temporal_mode: {exc}" if mode else f"invalid_as_of: {exc}"
        return base
    known_scope = time_scope(known_at) if (known_at or "").strip() else None
    if (known_at or "").strip() and known_scope is None:
        base["note"] = base["error"] = f"invalid_time_scope: invalid known_at {known_at!r}"
        return base
    if direct_scope_info is not None:
        base["time_scope"] = direct_scope_info
    if known_scope is not None:
        base["time_scope"] = {**(base.get("time_scope") or {}), "known_at": {
            "value": known_at.strip(), "from": known_scope[0], "until": known_scope[1]}}
    # 재료(발췌·관계)를 거를 as_of 기간. current 는 기간이 아니라 상태로 가린다.
    as_of_scope = direct_scope if mode != "current" else None
    rule = {"owner": owner, "mode": mode, "historical": historical,
            "as_of_scope": as_of_scope, "known_scope": known_scope}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        # 질문이 노드 id 를 그대로 담고 있으면 검색·랭킹을 거치지 않고 그 노드를
        # 연다 — 재측정 체인 arm 의 v1.1 수정("묻는 쪽이 이미 준 id 를 검색하는
        # 것은 낭비")을 도구 안으로 옮긴 것. 태스크 C 가 잰 실패가 정확히 이
        # 모양이었다: id 가 질문에 있는데 긴 제목의 기록이 랭킹을 이겨 **조용한
        # 오답**이 났다. 랭킹 자체는 여기서 건드리지 않는다 — id 없는 질의의
        # 동점 처리(제목 언어 갭)는 열린 결정으로 남는다:
        # [[record:title-language-gap-survives-the-vector-arm-2026-08-30]]
        unread_ids: list[dict] = []
        out_of_scope_ids: list[str] = []
        known_out: list[str] = []
        # 개방 타입 id 는 네 접두 id 가 없을 때만, 실재하는 것만 연다(F12).
        qids = _brief_query_ids(query) or _brief_entity_ids(store, query, owner=owner)
        if kinds and qids:
            kept = [i for i in qids if i.split(":", 1)[0] in kinds]
            if not kept:
                base["executed"] = True
                base["kinds_filtered"] = len(qids)
                base["note"] = "the referenced node is not of the requested kinds"
                return base
            qids = kept
        for qid in qids:
            got = node_context(owner=owner, node=qid, limit=max_excerpts,
                               with_evidence=True, store=store)
            if got.get("executed"):
                if mode == "current" and got.get("temporal", {}).get("validity") in ("future", "historical"):
                    out_of_scope_ids.append(qid)
                    continue
                if mode != "current" and direct_scope is not None and not _brief_holds(
                        got, direct_scope):
                    out_of_scope_ids.append(qid)
                    continue
                if known_scope is not None:
                    # B1 — 컷오프 뒤에 저장된 노드는 그 시점의 그래프에 없었다. 저장 시각을
                    # 모르면 감추지 않고 센다(검색과 같다).
                    known = _brief_known(got, known_scope)
                    if known is False:
                        known_out.append(qid)
                        continue
                    if known is None:
                        base["known_at_unknown"] = int(base.get("known_at_unknown") or 0) + 1
                base["executed"] = True
                base["status"] = "resolved"
                if mode == "current" and (got.get("temporal", {}).get("validity") == "unknown" or got.get("temporal", {}).get("claim_status") in ("conflict", "cycle", "superseded", "unknown")):
                    base["status"] = "choose"
                    base["candidates"] = _brief_candidate_rows([{"id": qid, "kind": got.get("node_kind"), "title": got.get("title"), "temporal": got.get("temporal")}])
                    base["validity_note"] = "the current claim is uncertain; no current state was chosen"
                    return base
                base["resolved_via"] = "id"
                # 앞선 후보가 안 열려 ASCII 접두나 다음 id 로 열었으면 그 대체를
                # 밝힌다 — 잘린 접두는 우연히 실재하는 다른 노드일 수 있다.
                if unread_ids:
                    base["fell_through"] = list(unread_ids)
                if known_scope is not None:
                    base.setdefault("known_at_filtered", 0)
                return _brief_answer(base, {"id": qid}, got, store, **rule)
            unread_ids.append({"id": qid,
                               "note": got.get("note") or ("node not found: " + qid)})
        # 질문이 준 id 가 하나도 안 열리면 **없다고 답한다**(status="none" + unreadable).
        # 랭킹으로 넘어가면 다른 노드가 resolved 로 나오고, 호출부는 그것을 자기가
        # 물은 노드로 읽는다 — fell_through 한 줄로는 그 대체를 막지 못했다.
        # 접두 되물림(위 루프)은 여전히 id 경로 안에서의 대체라 그대로 둔다.
        # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
        if unread_ids:
            base["executed"] = True
            base["unreadable"] = list(unread_ids)
            base["note"] = unread_ids[0]["note"]
            return base
        if out_of_scope_ids or known_out:
            base["executed"] = True
            if out_of_scope_ids:
                base["validity_filtered"] = len(out_of_scope_ids)
                base["validity_note"] = (
                    "the referenced node is not current" if mode == "current" else
                    f"the referenced node did not hold during {direct_scope_info['as_of']}")
            if known_out:
                base["known_at_filtered"] = len(known_out)
                base["known_at_node_note"] = (
                    f"the referenced node was recorded after {known_at.strip()}; "
                    "the graph did not hold it then")
            return base
        res = search(query, query_embedding=query_embedding, top_k=_BRIEF_TOP_K,
                     owner=owner, store=store, historical=historical,
                     as_of=as_of, mode=mode, known_at=known_at,
                     query_runtime=query_runtime, query_model=query_model,
                     # 기존 규칙은 ColBERT 순서 위에서 잰 적이 없다 — 학습 규칙일 때만 재채점된 후보로 해소한다.
                     query_colbert=query_colbert if _brief_rule_colbert() else None,
                     **({"kinds": kinds} if kinds else {}))
        # executed 는 노드 팔 기준이다 — 청크 팔만 죽어도 노드 팔은 owner 스코프 결과를 낸다.
        base["executed"] = bool(res.get("executed") or res.get("node_hits"))
        # 벡터 팔의 상태도 함께 나른다. 두 표면이 같은 임베딩 창구를 쓰는데 `/search`
        # 만 강등을 말하면 같은 고장이 도구에 따라 보이거나 안 보인다. 규칙은 `/search`
        # 와 같다 — **검색이 실제로 돈 경우에만** 싣는다(id 직참조는 이 줄에 오기 전에
        # 반환하므로 안 돈 팔에 이름이 붙지 않는다. 게이트① 동등성은 q.brief 한 곳에서
        # 세우는 것으로 지킨다 — HTTP 계층에서 붙이면 CLI 와 갈린다).
        for k in ("note", "node_error",
                  "arms", "vector_note", "vector_rebuilding", "time_scope",
                  "encoder_runtime_warning",
                  "validity_filtered", "validity_note", "validity_best_dropped",
                  "temporal_error", "partial", "mode", "claim_assessment_scope"):
            if res.get(k):
                base[k] = res[k]
        # 노드 의미 팔이 모델 불일치로 꺼졌으면 해소가 그 팔 없이 돌았다. 그 사실도 나른다.
        if (res.get("node_semantic") or {}).get("state") == "model_mismatch":
            base["node_semantic"] = res["node_semantic"]
        if known_scope is not None:
            # B1 — 검색이 known_at 으로 감춘 수를 그대로 잇는다(0 도 값이다: 거르기가 돌았다).
            base["known_at_filtered"] = int(res.get("known_at_filtered") or 0)
            base["known_at_unknown"] = int(res.get("known_at_unknown") or 0)
        if mode == "current" and base.get("validity_note"):
            # D15 — current 는 as_of 를 받지 않는다. 검색의 알림이 기간 문장("drop as_of")을
            # 달고 와도 여기서는 상태로 말한다.
            base["validity_note"] = ("hid claims that are not current; "
                                     "use mode=historical to include them")
        # 검색의 `error` 는 청크 팔 실패다(FTS 잔해·확장 미가용·색인 부재 …). 같은 검색의
        # 노드 팔 결과로 해소를 계속하고 사유는 `search_error` 로 싣는다 — 최상위 `error` 는
        # REST v1 이 HTTP 500 으로 바꾸므로 해소된 답에 쓸 수 없다. 노드 팔도 빈손일 때만
        # 여기서 돌아온다. 근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]
        if res.get("error"):
            base["search_error"] = res["error"]
            if not res.get("node_hits"):
                return base
        # 넓은 풀은 되묻기 목록용으로 따로 두고, 확정·기권·가드는 앞 `_BRIEF_RESOLVE_N` 개로 본다.
        pool = [h for h in res.get("node_hits") or [] if isinstance(h, dict) and h.get("id")]
        full = res.get("node_hits") or []
        res = {**res, "node_hits": full[:_BRIEF_RESOLVE_N]}
        rescored = (res.get("node_rescore") or {}).get("state") == "applied"
        # v3 는 의미만 기권을 재채점 **전** 순서의 앞 다섯으로 본다(재채점이 기권 대상을 바꾸지 않게).
        # 재채점이 안 된 질의는 기존 규칙 그대로 간다.
        v3 = _brief_rule_v3() and rescored and bool(full)
        configured = _brief_rule()
        if configured != "production" and not v3:
            # 학습 규칙이 설정돼 있어도 재채점이 안 된 질의는 기존 규칙이 답한다. 그 사실을 답에 싣는다:
            # 없으면 평가도 사람도 어느 규칙의 답인지 가를 수 없다(토큰이 빠진 노드 하나로도 재채점이 빠진다).
            # 근거: [[vinc.canon.rag-production-state]]
            _rs = res.get("node_rescore") or {}
            base["resolution"] = {"rule": "production", "configured": configured,
                                  "rescore": _rs.get("state") or "missing"}
            if _rs.get("reason"):
                base["resolution"]["reason"] = _rs["reason"]
        if _brief_semantic_none({**res, "node_hits": _brief_v3_prod_head(full)} if v3 else res):
            base["note"] = "no sufficiently close semantic candidate"
            return base
        if v3:
            status, picked, _p = _brief_resolve_v3(query, res.get("node_hits") or [], res)
            base["resolution"] = {"rule": "v3", "p": None if _p is None else round(_p, 4)}
        else:
            status, picked = _brief_resolve(
                query, res.get("node_hits") or [],
                mirror_text=lambda h: _brief_mirror_text(store, owner, h))
        # B5 — current 의 기권은 답(1위)과 그 주장 키를 공유하는 후보만 본다. 후보 아무나
        # 보면 구간이 없어 늘 unknown 인 개념 후보 하나가 다른 주장의 답을 막는다.
        if mode == "current" and picked and _brief_current_uncertain(
                store, owner=owner, answer=picked[0], hits=res.get("node_hits") or []):
            status = "choose"
            base["validity_note"] = "current claim evidence is conflicting or unknown; no current state was chosen"
        # D11 — current 에서 동점이던 노드가 은퇴했으면 남은 노드로 확정한다. 의도된 뜻이다:
        # current 는 "지금 성립하는 것"을 묻고, 은퇴한 노드는 그 질문의 경쟁자가 아니다.
        # 아래 동점 가드는 일반 모드에서 감춘 행의 점수(validity_best_dropped)를 보는데,
        # current 에서는 finish_nodes 가 은퇴한 노드를 감추며 그 점수를 남기지 않으므로
        # 가드가 서지 않는다. 그 차이가 이 뜻을 구현한다(감사 D11).
        # 🔴 **유효구간 필터가 동점을 깨서 확신을 만들면 안 된다.** 지운 행이 남은 1위와
        # 같은 점수였다면, 원래 이 질문은 "모르겠다"(choose)였다. 그 자리에서 확정을
        # 주면 필터가 **보이는 것**이 아니라 **답**을 바꾼 것이고, 그건 §4.2 가 의도한
        # 바가 아니다. 실측(2026-09-09): 이 갈래가 없는 동안 답 불가 질문 6건 중 1건이
        # 확정됐다(정직성 0.0 → 0.1667). 동점이던 Record 둘 중 하나가 걸러지면서 남은
        # 하나가 단독 승자가 된 모양이었다.
        # 근거: [[record:the-validity-filter-broke-a-tie-and-manufactured-confidence-2026-09-09]]
        # v3 확정에는 돌지 않는다: 감춘 행의 점수는 v3 특징(:func:`_brief_v3_features`)에 이미 있다.
        if status == "resolved" and picked and base.get("validity_filtered") and not v3:
            _bd = tuple(base.get("validity_best_dropped") or (0, 0))
            if (_brief_hidden_ties(query, picked[0], _bd)
                    and not _brief_semantic_tie_clear(picked, res)):
                status = "choose"
                # 시점 읽기(as_of)에서도 같은 가드다 — 그 기간 밖이라 감춘 것이 동점이었으면
                # 확정하지 않는다(계획의 "tie guard applies unchanged"). 알림은 경우마다
                # 다르다(D15): current 는 as_of 를 받지 않으므로 "drop as_of" 라고 말하지 않는다.
                _ts = base.get("time_scope") or {}
                if mode == "current":
                    base["validity_note"] = (
                        "a claim that is not current tied with the leading candidate, "
                        "so this was not resolved; use mode=historical to see it")
                elif _ts.get("from"):
                    base["validity_note"] = (
                        "a claim that did not hold during {} tied with the leading candidate, "
                        "so this was not resolved; drop as_of to see it".format(_ts["as_of"]))
                else:
                    base["validity_note"] = (
                        "a claim that is no longer current tied with the leading candidate, "
                        "so this was not resolved; pass historical=true to see it")
        # 🔴 **주제를 찾은 것과 질문에 답하는 것은 다르다.** 질문이 노드 제목을 그대로
        # 인용하고 그 **속성**을 물으면(“…는 문장은 누가 썼나”) 제목이 통째로 맞아
        # 토큰 여유가 넘치고, 확신 문턱은 그것을 못 거른다 — 랭킹 문제가 아니기 때문이다.
        # 실측(2026-09-10, 답 불가 21문항): 이 갈래가 없으면 '주제는 있는데 속성이 없는'
        # 7건 중 3건을 확신했다. 이 게이트는 그 셋을 전부 막고(답불가 확정 0.1429 → 0.0000)
        # 맞는 확신 하나를 잃는다. **노출 R@1 은 안 변한다** — 강등된 질문도 후보 목록에
        # 정답을 1위로 싣기 때문이다.
        # ⚠ 기본은 꺼져 있다 — 이유는 :func:`_brief_answerability_on` 에 있다.
        if status == "resolved" and picked and _brief_answerability_on():
            _orphan = _brief_orphan_residual(query, picked[0].get("title"), res)
            if _orphan >= _ORPHAN_BLOCK:
                status = "choose"
                base["answerability_note"] = (
                    "the topic matched but the question asks for something this graph "
                    "does not hold, so this was not resolved")
        base["status"] = status
        if status == "choose":
            # 되묻기 목록은 넓은 풀 전체를 의미 순서(`_brief_list_rank`)로 세워 노출 수에서
            # **한 번** 자른다. 확정용 순위(팔 개수 → 토큰)는 의미 팔에만 걸린 후보를 맨 뒤로
            # 미는데, 넓은 풀에 새로 들어오는 정답은 대부분 그런 후보다.
            # v3 는 ColBERT 순서를 그대로 보인다 — 의미 순서로 다시 세우면 재채점을 되돌린다.
            picked = list(pool) if v3 else _brief_list_rank(pool)
            base["candidates"] = _brief_candidate_rows(picked[:_brief_expose_n()])
            if _brief_quotes_on():
                for _c in base["candidates"]:
                    _q = _brief_node_quotes(store, owner=owner, node_id=str(_c.get("id") or ""),
                                            query=query, query_embedding=query_embedding)
                    if _q:
                        _c["quotes"] = _q
            _brief_attach_section(base, store, owner=owner, query=query, res=res,
                                  query_embedding=query_embedding,
                                  nodes=[str(c.get("id") or "") for c in base["candidates"]])
            # 첫 후보의 재료를 따로 한 키에 싣는다. status·candidates 는 그대로다 — 이 재료가
            # 확정 답으로 읽히면 안 된다(재료의 꼴과 이유는 `_brief_candidate_material`).
            if base["candidates"]:
                _mat = _brief_candidate_material(store, base["candidates"][0],
                                                 max_excerpts=max_excerpts, **rule)
                if _mat is not None:
                    base["candidate_material"] = _mat
            return base
        if status == "none":
            return base
        # 1위가 상세에서 안 열리면 다음 후보로 넘어간다. 그 전에는 곧장
        # status="none" 을 냈는데, 계약상 none 은 "아무것도 안 맞았다"라서 **맞았는데
        # 못 읽었다**를 안 맞은 것처럼 보고했다. 관계마다 근거 문장을 함께 받는다 —
        # 에이전트는 그것을 인용해야 한다.
        #
        # ⚠ 폴스루는 밝혀서 한다. 조용히 다음 후보를 답하면 이 표면은 "질문에 맞는
        # 노드"가 아니라 "열리기만 하는 노드"를 답하게 되고, 그것은 이 도구가 임의
        # 임계값을 거부하며 지키려던 성질을 정반대로 뒤집는다.
        hit = ctx = None
        unread: list[dict] = []
        for cand in picked[:_BRIEF_FALLTHROUGH]:
            cid = str(cand.get("id") or "")
            if not cid:
                continue
            got = node_context(owner=owner, node=cid, limit=max_excerpts,
                               with_evidence=True, store=store)
            if got.get("executed"):
                hit, ctx = cand, got
                break
            unread.append({"id": cid,
                           "note": got.get("note") or ("node not found: " + cid)})
        if ctx is None:
            base["status"] = "none"
            base["note"] = (unread[0]["note"] if unread
                            else "node not found: " + str(picked[0].get("id") or ""))
            if unread:
                base["unreadable"] = unread
            return base
        if unread:
            base["fell_through"] = unread
        out = _brief_answer(base, hit, ctx, store, **rule)
        if _brief_quotes_on():
            _q = _brief_node_quotes(store, owner=owner, node_id=str(hit.get("id") or ""),
                                    query=query, query_embedding=query_embedding)
            if _q:
                out["quotes"] = _q
        _brief_attach_section(out, store, owner=owner, query=query, res=res,
                              query_embedding=query_embedding,
                              nodes=[str(hit.get("id") or "")])
        if _brief_list_resolved_on():
            # 확정은 그대로 두고 순위 목록을 곁에 싣는다. 첫 자리는 순위 1위다.
            out["candidates"] = _brief_candidate_rows(picked[:_brief_expose_n()])
        return out
    finally:
        if owns:
            store.close()
