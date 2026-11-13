# -*- coding: utf-8 -*-
"""브리프(`vinc_brief`)의 해소 규칙: 질문에서 id 를 읽는 직참조, 규칙을 고르는 플래그, 해소 순위와 동점 가드,
학습·v3 규칙, 확정(`_brief_resolve`), 그리고 확정이 부르는 본문·근거·노출 수.

`brief()` 와 그 답 조립(발췌 고르기, 인용, 되묻기 재료, 시간축 가드)은 아직 `api/query.py` 에 있고 여기 이름을
`q` 에서 읽는다. `api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는
그대로다. 두 이름은 같은 객체다. 여기 함수끼리 부르는 이름(`_brief_rank`·`_STRICT_MARGIN`·`_SEM_PROMOTE`·
`_brief_expose_n` 등)은 이 모듈에서 찾으므로, `_brief_resolve` 안의 호출을 바꾸려면 `api.query` 가 아니라
이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_brief_module.py` 가 본다).
"""
from __future__ import annotations

import math
import os
import re

from store import pkns
from vinc_rag import retrieval as _retrieval


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

#: 학습 해소 규칙 — ColBERT 1위를 확정할지 정하는 로지스틱 모형. 상수는 v2 가 개발 풀
#: 3,140행으로 맞춰 고정한 그대로다(`frozen_v2.json`, sha256 bb417ece…). 특징 순서·정의를 바꾸면
#: 상수가 뜻을 잃는다(:func:`_brief_learned_features`). 문턱 아래는 되묻는다.
#: v2 는 `VINC_BRIEF_RULE=learned` 일 때만 돈다(기본은 production 규칙).
#: 근거: [[vinc.canon.rag-production-state]]
_LEARNED_MU = [0.4828262648622321, 0.023761766309001644, 0.26719745222929936, 4.985350318471338, 0.21818054291780406,
               0.7420382165605095, 0.39713375796178346, 0.038502569108280225, 0.49879280364817113, 0.3436305732484076]
_LEARNED_SD = [0.056948800738280285, 0.02434501544247338, 0.4424962990087704, 4.9540347754103236, 0.304360684759962,
               1.303480270826195, 2.7100763980136233, 0.05477666510201067, 0.15564735806801316, 0.47491957564119053]
_LEARNED_W = [0.653670720249066, 1.0327584901774332, -0.18126837306254473, -0.3745033858099314, 0.3405076195044011,
              -0.6251645103744707, 0.10649465847294845, 0.8194124383935194, -0.6231877383599321, 0.10145310307050287,
              -0.42554129869112434]
_LEARNED_T = 0.4855
#: v3 학습 규칙 — v2 의 열 특징에 유효구간 필터가 감춘 행에서 나온 넷을 더한 열넷. brief 경로 전체
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
    """`VINC_BRIEF_RULE` 의 값. `learned`(v2)·`v3` 만 학습 규칙이고, 그 밖의 값과 빈 값은 production 이다."""
    v = (os.environ.get("VINC_BRIEF_RULE") or "production").strip().lower()
    return v if v in ("learned", "v3") else "production"


def _brief_rule_learned() -> bool:
    """`VINC_BRIEF_RULE=learned` — ColBERT 순서 위의 v2 학습 규칙. 기본은 production(기존 규칙)."""
    return _brief_rule() == "learned"


def _brief_rule_v3() -> bool:
    """`VINC_BRIEF_RULE=v3` — brief 경로 전체로 다시 맞춘 v3 학습 규칙(:func:`_brief_resolve_v3`)."""
    return _brief_rule() == "v3"


def _brief_rule_colbert() -> bool:
    """학습 규칙(v2·v3)이면 ColBERT 로 다시 선 후보 위에서 해소한다. 기존 규칙은 그 순서를 안 본다."""
    return _brief_rule() != "production"


def _brief_learned_features(query: str, hits: list[dict]) -> list[float] | None:
    """연구 `stage3.py` `feats` 와 같은 열 개. hits 는 ColBERT 순서이고 행마다 `colbert`·`prod_rank` 가 있다."""
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


def _brief_learned_p(x: list[float]) -> float:
    z = sum(((xi - m) / sd) * w for xi, m, sd, w in zip(x, _LEARNED_MU, _LEARNED_SD, _LEARNED_W)) + _LEARNED_W[-1]
    return 1.0 / (1.0 + math.exp(-z))


def _brief_resolve_learned(query: str, hits: list[dict]) -> tuple[str, list[dict], float | None]:
    """학습 규칙으로 해소한다. 제목이 질문과 글자 그대로 같은 후보가 **하나**면 그것으로 확정하고(이름을
    댄 경우), 아니면 ColBERT 1위의 확률이 문턱 이상일 때만 확정한다. 나머지는 되묻는다.
    연구의 `decide` 와 같다(기존 규칙의 다른 갈래는 여기 없다 — 그 뒤의 가드는 brief 가 그대로 돈다)."""
    norm = lambda t: re.sub(r"\s+", " ", (t or "").strip().lower())  # noqa: E731
    named = [h for h in hits if norm(h.get("title")) == norm(query)]
    if len(named) == 1:
        return "resolved", named, None
    x = _brief_learned_features(query, hits)
    if x is None:
        return "choose", list(hits), None
    p = _brief_learned_p(x)
    return ("resolved", [hits[0]], p) if p >= _LEARNED_T else ("choose", list(hits), p)


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
