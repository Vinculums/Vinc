# -*- coding: utf-8 -*-
"""MCP graph fragment MERGE — `vinc_ingest` 의 서버측 계약(D11 §5 + D11-R2 포팅) 구현.

상류(사용자의 Claude)가 저작한 fragment(nodes+edges)를 **id 정확 매칭**으로만
MERGE 한다 — 유사도·의미 판단은 하지 않는다(하이브리드 c 백스톱, D11 §3).

지원(스키마 §5-2 확장 + F1 개방층 포팅, 2026-07-19):
- 노드 6종: record | concept | domain | bridge | episode | decision
  (+ 자유 props{} → JSON 문자열 열)
- **의미층 = concept | decision**(v12). 둘은 다른 종류지만(지금도 참인 것 / 그때 고른
  것) 그래프에서 서는 자리가 같아, 아래 엣지 규칙에서 서로 바꿔 쓸 수 있다.
  **예외 하나**: `relates_to` 는 개념↔개념에만 남는다(엔진 제약 + 의미 —
  store.embedded_schema._LOUVAIN_PROJECTED_REL 주석). 결정에는 `about` 이나 계보를 쓴다.
- 엣지: 의미층 4종(relates_to·prerequisite_of·derives_from·inherits_from),
  connects_via(Bridge→의미층 → CONNECTS, 선택 role),
  about(Episode|Decision→의미층 → ABOUT),
  Record→의미층 은 ATTACHES_TO{type}로 원 타입을 보존해 수용.
- **개방층(D11-R2 포팅, F1)**: 위 6종 밖 노드 타입은 *스킵되지 않고* 범용
  `Entity{entity_id,type,title,owner_id,theme_id,tags}` 캐리어로, 양 끝이 개방 타입인
  엣지는 `TYPED{type}`(exact (from,type,to) 매칭) 캐리어로 MERGE 된다 — 정보 손실 0
  (THESIS T1). 어휘 밖 타입은 주제 어휘에 proposed 로 편입되고 응답 `proposed_types`
  로 재가를 유도한다(T3). 휴면 엔진(engines/fragment_ingest.py) 경로가 아니라 이
  라이브 경로에 포팅한 이유: revstamp(동기화 스탬핑)·SEC-3(PK 탈취 차단)·dedupe·
  partial 관례를 단일 쓰기 표면(R4)에서 그대로 승계하기 위함(발주자 결정 2026-07-19,
  구 휴면 결정 2026-07-11 을 '경로 통합'으로 해소).

안정성 보강(실사용 사고 대응, 2026-07-16):
- partial 신호: warnings 가 1개 이상이면 응답에 partial:True 를 얹는다 — executed:True 만
  보고 전량 성공으로 오인하는 것을 막는다(하위호환: warnings 없으면 필드 자체 생략).
- RELATED_TO 무방향 dedupe: relates_to(concept→concept) MERGE 전에 역방향
  (b)-[:RELATED_TO]->(a) 존재를 확인해 있으면 신규 생성을 skip 한다(양방향 재적재로
  degree 가 부풀지 않도록). skip 개수는 merged.edges.skipped_duplicate 로 보고(0이면
  필드 생략). 다른 CC 엣지(방향성 있는 prerequisite_of 등)에는 적용하지 않는다.
  Record→Concept ATTACHES_TO 는 노드 타입이 비대칭이라(from=Record만 허용) 역방향
  MERGE 자체가 구조적으로 불가능 — 별도 dedupe 불필요. TYPED 도 무방향 정준화를
  하지 않는다 — 사용자 저작 타입은 방향성을 추론할 수 없다(exact 방향 dedupe 만).
"""
from __future__ import annotations

import json

from engines import domain_hierarchy

from . import revstamp

_NODE_MAP = {   # fragment type → (label, pk column)
    "record": ("Record", "record_id"),
    "concept": ("Concept", "concept_id"),
    "domain": ("Domain", "name"),
    "bridge": ("Bridge", "bridge_id"),
    "episode": ("Episode", "episode_id"),
    "decision": ("Decision", "decision_id"),   # v12
}

# 끝점 (라벨, PK) 두 쌍 — 엣지 경로가 해석한 결과를 들고 다니는 형태.
_Ends = tuple[tuple[str, str], tuple[str, str]]

# v12 — **의미층**: 지금도 참인 것(concept)과 그때 고른 것(decision). 둘은 다른
# 종류지만 그래프에서 서는 자리가 같다 — 어느 쪽도 개념 엣지의 끝점일 수 있고,
# 기록·사건·다리·청크가 가리키는 대상일 수 있다. 그래서 라우팅에서 이 집합에
# 속하는지만 보고 라벨은 _NODE_MAP 에서 꺼낸다(타입마다 분기를 늘리지 않는다).
_SEMANTIC_TYPES = ("concept", "decision")

_CC_EDGES = {   # Concept→Concept fragment 엣지 → rel 테이블
    "relates_to": "RELATED_TO",
    "prerequisite_of": "PREREQUISITE_OF",
    "derives_from": "DERIVES_FROM",
    "inherits_from": "INHERITS_FROM",
}
# Episode 참여 엣지 — _CC_EDGES 에 넣지 않는다: sync 의 _REL_TO_FRAGMENT 가
# _CC_EDGES 를 역맵으로 파생하므로(Concept 엣지 export 전용) 섞이면 오염된다.
_EC_EDGES = {"about": "ABOUT"}
_EDGE_TYPES = set(_CC_EDGES) | set(_EC_EDGES) | {"connects_via"}

# 개방층 캐리어(F1 — D11-R2 포팅). 어휘(타입 이름)는 theme_store, 물리는 여기.
_ENTITY_LABEL, _ENTITY_ID, _TYPED_REL = "Entity", "entity_id", "TYPED"

DEFAULT_OWNER = "tenant:local"


def _is_open(ntype: str) -> bool:
    """개방층 타입 판별 — 비어있지 않고 레거시 6종 밖이면 Entity 캐리어 대상."""
    return bool(ntype) and ntype not in _NODE_MAP


class _ThemeFold:
    """어휘 밖 타입의 주제 어휘 편입(D11-R2 §3) — theme_id 는 **lazy 확정**.

    해석 순서: fragment.theme_id(명시 — 미지 테마는 멱등 create) → 활성 테마 →
    개방 타입 최초 조우 시 "default" 테마 lazy 생성(create_theme 은 활성 테마가
    없으면 자신을 활성으로 지정). themes=None(테스트·sync/share 호출부)이면 전부
    무동작 — Entity 는 theme_id="" 로 영속되고 type 문자열은 행에 남는다(손실 0)."""

    def __init__(self, themes, fragment: dict):
        self.themes = themes
        self._explicit = (fragment.get("theme_id") or "").strip() if fragment else ""
        self.theme_id: str | None = None   # None = 미해석, "" = themes 부재
        self.proposed: list[dict] = []

    def resolve(self) -> str:
        if self.theme_id is not None:
            return self.theme_id
        if self.themes is None:
            self.theme_id = ""
            return ""
        if self._explicit:
            self.themes.create_theme(self._explicit)   # 멱등 — 명시는 존재 보장
            self.theme_id = self._explicit
        else:
            self.theme_id = self.themes.active_theme_id() or ""
            if not self.theme_id:
                self.themes.create_theme("default")     # 활성 부재 시 활성으로 지정됨
                self.theme_id = "default"
        return self.theme_id

    def fold(self, kind: str, name: str) -> None:
        if self.themes is None or not name:
            return
        tid = self.resolve()
        if not tid:
            return
        if self.themes.resolve_type(tid, kind, name) is None:
            entry = {"kind": kind, "name": name}
            if self.themes.propose_type(tid, kind, name) and entry not in self.proposed:
                self.proposed.append(entry)


def _merge_node(store, node: dict, owner: str, warnings: list[str],
                *, owner_explicit: bool = False, theme_id: str = "") -> str | None:
    """단일 노드 MERGE. 반환 = 'created' | 'matched' | None(스킵).

    owner_id 스탬핑은 **생성 시 또는 fragment.owner 명시 시**에만 한다 — owner 없는
    재-MERGE 가 기존 노드의 소유를 기본값으로 조용히 덮어쓰면 스코프 필터(§1 불변)가
    깨진다.

    개방층(F1): 레거시 6종 밖 타입은 스킵이 아니라 Entity 캐리어로 라우팅된다 —
    스킵은 형태 오류(id 부재, Entity 는 title 부재)뿐. ``theme_id`` 는 Entity 행에
    각인되는 주제 귀속(THEME-DEEP §6) — 호출부(_ThemeFold)가 해석해 넘긴다."""
    ntype = (node.get("type") or "").strip()
    nid = (node.get("id") or "").strip()
    if not ntype:
        warnings.append(f"node without type — skipped ({nid or '?'})")
        return None
    if not nid:
        warnings.append(f"node without id — skipped (type={ntype})")
        return None
    is_entity = _is_open(ntype)
    if is_entity and not (node.get("title") or "").strip():
        warnings.append(f"entity node.title required: id={nid} (type={ntype})")
        return None
    label, pk = (_ENTITY_LABEL, _ENTITY_ID) if is_entity else _NODE_MAP[ntype]
    # owner_id 를 가진 라벨 — Bridge(v2)·Entity(F1) 편입으로 SEC-3 가드·스탬프 규칙 승계.
    owner_scoped = label in ("Record", "Concept", "Episode", "Bridge", "Entity",
                             "Decision")

    # 존재확인 + (추적 라벨이면) 현재 추적필드값도 함께 조회 — 변경감지 스탬핑(S0).
    # SEC-3: owner-스코프 라벨이면 기존 owner_id 도 조회해 교차테넌트 충돌을 판별한다.
    tracked = revstamp.tracked_fields(label)
    if tracked:
        cols = ["n.rev AS rev"] + [f"n.{c} AS {c}" for c in tracked]
        if owner_scoped:
            cols.append("n.owner_id AS owner_id")
        cur_rows = store.run(
            f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN {', '.join(cols)}", {"id": nid})
        matched = bool(cur_rows)
        current = cur_rows[0] if cur_rows else {}
    else:
        exists = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN count(n) AS c",
                           {"id": nid})
        matched = bool(exists and exists[0].get("c"))
        current = {}

    # SEC-3: PK 충돌 소유권 탈취 차단. ID(concept:*)는 콘텐츠기반 공유 PK라, 강제 owner
    # 경로(owner_explicit=호스티드/원격/웹)에서 **남이 이미 소유한** 노드에 MERGE 하면
    # owner_id 재스탬핑(탈취) + 내용 덮어쓰기가 된다. 소유자가 다르면 노드를 건너뛴다
    # (SET/MERGE 전에). 로컬(owner_explicit=False)이나 신규(not matched)는 현행 유지.
    if owner_scoped and owner_explicit and matched:
        existing_owner = current.get("owner_id")
        if existing_owner and existing_owner != owner:
            warnings.append(f"id {nid} owned by another tenant — skipped")
            return None

    sets, params = [], {"id": nid}
    def _set(col, val):
        sets.append(f"n.{col} = ${col}")
        params[col] = val

    if is_entity:
        # Entity 캐리어(F1) — 열은 type/title/owner_id/theme_id/tags 뿐. domain/props
        # 는 열이 없어 미보존(휴면 엔진 동형 — warnings 로 통지, 조용한 유실 금지).
        _set("type", ntype)
        _set("title", str(node.get("title")).strip())
        _set("tags", [str(t) for t in (node.get("tags") or [])])
        _set("theme_id", theme_id)
        if node.get("domain") is not None:
            warnings.append(f"node.domain not persisted on Entity (id={nid}); fold into tags/theme")
        if node.get("props"):
            warnings.append(f"node.props not persisted — no schema-free column on Entity (id={nid})")
        if owner_explicit or not matched:
            _set("owner_id", owner)
    elif label != "Domain":
        # ⚠ `title` 은 오래도록 **무조건 SET** 이었다(`node.get("title") or nid`).
        # 그래서 id 만 실은 부분 재전송이 제목을 **id 로 바꿔치기**했다 — 보존이
        # 아니라 파괴다. domain·tags·props 는 `is not None` 가드가 있는데 title 만
        # 없었고, 그 비대칭이 함정의 전부다(CLAUDE.md 가 급소로 적어 둔 자리).
        #
        # 2026-08-10 에 실제로 물렸다: 결정 이관에서 엣지 해석용 스텁(id+type)을
        # 보냈더니 그 페이로드에 담긴 **107개 노드의 제목이 전부 id 로 덮였다**.
        # 스텁은 엣지 계약상 반드시 보내야 하므로(끝점은 같은 payload 안에서만
        # 해석된다) 호출자가 피할 수 있는 함정이 아니었다.
        #
        # 이제 다른 세 필드와 같은 규칙이다: 주면 쓰고, 안 주면 보존한다. 단
        # **신규일 때만** id 로 채운다 — 제목 없는 노드가 생기지 않게.
        if node.get("title") is not None:
            _set("title", str(node["title"]) or nid)
        elif not matched:
            _set("title", nid)
        if node.get("domain") is not None:
            if label == "Bridge":
                # Bridge 는 domain 열이 없다(source_domain/target_domain 대신) — SET
                # 하면 Binder 예외로 죽는다(실측, 2026-07-21). 경고로 강등해 조용한
                # 유실 대신 명시 통지, 나머지 노드/엣지 처리는 계속한다.
                warnings.append(
                    f"node.domain not persisted on Bridge (id={nid}); "
                    "Bridge has source_domain/target_domain instead, not settable via fragment ingest")
            else:
                # 정규화된 값을 **열에도** 넣는다 — 열과 Domain 노드 이름이 정확히
                # 같아야 IN_DOMAIN 이 그 열의 그래프 표현일 수 있다(engines/
                # domain_hierarchy.normalize 주석). 잘 쓴 값에는 항등이다.
                _set("domain", domain_hierarchy.normalize(node["domain"]))
        if node.get("tags") is not None:
            _set("tags", [str(t) for t in (node.get("tags") or [])])
        if node.get("props") is not None:
            _set("props", json.dumps(node["props"], ensure_ascii=False, sort_keys=True))
        if label == "Episode":
            # share pack(D19)의 레코드 노드 — 콘텐츠 필드만(선호 신호는 팩에 없음).
            #
            # F9 — **props.date 를 열로 승격한다.** `vinc_ingest` 의 노드 스키마에는
            # `date`/`summary` 가 선언돼 있지 않았다(서버는 top-level 로 받는데 계약이
            # 그걸 안 알려줬다). 그래서 상류가 날짜를 실을 수 있는 자리는 자유 속성인
            # `props{}` 뿐이었고, 결과로 라이브 에피소드 16/55 가 `date` 열이 빈 채
            # props 안에만 날짜를 갖게 됐다(실측 2026-08-08 — 그 16건은 props 를 가진
            # 쪽, 나머지 39건은 top-level 로 받아 props 가 빈 쪽으로 정확히 갈린다).
            #
            # 그 열은 recall 의 `ORDER BY e.date DESC` · 타임라인의
            # `coalesce(e.date, e.updated_at)` · 팩/공유/동기화 투영이 전부 읽는
            # 자리라, 비면 정렬 자체가 무의미해진다. 승격을 **쓰기 시점**에 두는
            # 이유가 그것이다 — recall 응답에서 coalesce 하면 그 한 응답만 고쳐지고
            # 정렬(ORDER BY)·타임라인·공유는 그대로 빈 채 남는다.
            #
            # top-level 이 오면 그것이 이긴다(기존 계약 무변경 — 빈 문자열로 지우는
            # 것까지 포함). props 폴백은 **키가 아예 없을 때만** 본다. props 원본은
            # 지우지 않는다: 상류가 보낸 것을 그대로 보존하는 것이 props 의 계약이고,
            # 지우면 부분 재전송이 원본을 깎는다.
            _props = node.get("props") if isinstance(node.get("props"), dict) else {}
            for _col in ("date", "summary"):
                _val = node.get(_col)
                if _val is None:
                    # 문자열만 승격한다 — props 는 자유 형태라 dict/list 가 올 수
                    # 있는데 그걸 str() 로 뭉개면 날짜가 아닌 것이 날짜 열에 앉는다.
                    # 승격 못 한 값도 props 에 그대로 남으므로 유실은 아니다.
                    _cand = _props.get(_col)
                    if not isinstance(_cand, str) or not _cand.strip():
                        continue
                    _val = _cand.strip()
                _set(_col, str(_val))
        # 시간 원천 보존 — 팩·MCP 가 실은 `created` 를 그대로 받는다. 없으면 **신규일
        # 때만** 지금으로 찍는다: 재-MERGE 가 원래 시점을 임포트 시각으로 덮으면
        # 타임라인의 시간축이 "마지막으로 만진 때"로 무너진다(updated_at 이 이미 그
        # 역할을 한다). Concept/Record 둘 다 created 열을 가진다.
        if label in ("Concept", "Record", "Decision"):
            if node.get("created"):
                _set("created", str(node["created"]))
            elif not matched:
                _set("created", revstamp.hlc_now(store))
        if label in ("Record", "Concept", "Episode", "Bridge", "Decision") \
                and (owner_explicit or not matched):
            _set("owner_id", owner)
        if label == "Record":
            _set("tenant_id", "tenant:local")
    elif node.get("props") is not None:
        warnings.append(f"domain node '{nid}' — props{{}} 미보존(열 없음)")

    # 변경감지 스탬핑(S0): 신규이거나 추적필드가 실제로 달라졌을 때만 rev/updated_at/
    # sync_seq 를 찍는다 — 멱등 재적재는 무스탬프(가짜 충돌 방지). 원격에서 apply 되는
    # 경로는 스탬프를 원격값 그대로 보존하므로 이 함수를 타지 않는다(api/sync).
    if tracked:
        incoming = {c: params[c] for c in tracked if c in params}
        if not matched or revstamp.differs(current, incoming):
            _set("updated_at", revstamp.hlc_now(store))
            _set("rev", int(current.get("rev") or 0) + 1)
            _set("sync_seq", revstamp.next_seq(store, owner))

    set_clause = (" SET " + ", ".join(sets)) if sets else ""
    try:
        store.run(f"MERGE (n:{label} {{{pk}: $id}}){set_clause}", params)
    except Exception as exc:
        # 방어 심층화: 위에서 못 막은 스키마 불일치(Binder 예외 등)가 여기서 나면
        # 이 노드 하나만 스킵 — merge_fragment 의 문서화 계약("malformed nodes...
        # skipped, reported in warnings with partial:true")대로. 이전엔 여기가
        # 무방비라 노드 1개 오류가 배치 전체(나머지 노드+모든 엣지)를 날렸다(실측,
        # 2026-07-21 — bridge.domain 케이스로 발견, 위에서 그 케이스는 막았지만
        # 미지의 다른 케이스에 대한 안전망으로 존치).
        warnings.append(f"node write failed — skipped (id={nid}, label={label}): {exc}")
        return None

    # 도메인 계층(E-1, v11) — `domain` 열이 설정된 노드에 Domain 노드·PARENT_OF 사슬·
    # IN_DOMAIN 을 파생 유지한다. **열이 갱신될 때만** 돈다: 도메인을 안 실은 부분
    # 재전송에서 엣지를 지우면 `domain` 열은 보존되는데 그래프 표현만 사라져 둘이
    # 갈린다(title 무가드가 냈던 병과 같은 뿌리).
    #
    # 실패해도 노드 쓰기는 되돌리지 않는다 — 계층은 열에서 언제든 다시 파생되는
    # 파생물이고(백필 스크립트가 그 일을 한다), 여기서 죽으면 배치 전체가 날아간다.
    if "domain" in params and label in domain_hierarchy.DOMAIN_LABELS:
        try:
            domain_hierarchy.link_node(store, label, nid, params["domain"])
        except Exception as exc:
            warnings.append(f"domain hierarchy not updated (id={nid}): {exc}")

    return "matched" if matched else "created"


def _node_types(fragment_nodes: list[dict]) -> dict[str, str]:
    return {(n.get("id") or "").strip(): (n.get("type") or "").strip()
            for n in fragment_nodes}


_ALLOWED_PAIRS_HINT = (
    "allowed pairs (concept and decision are interchangeable endpoints, "
    "except relates_to which stays concept→concept): "
    "relates_to = concept→concept; "
    "prerequisite_of|derives_from|inherits_from = concept|decision → concept|decision; "
    "record → concept|decision for any of those four; "
    "connects_via = bridge → concept|decision; "
    "about = episode|decision → concept|decision; "
    "any-type = open-entity→open-entity (TYPED carrier)"
)


def _edge_exists(store, rel: str, ends: _Ends, src: str, dst: str) -> bool:
    (sl, sp), (dl, dp) = ends
    r = store.run(f"MATCH (a:{sl})-[:{rel}]->(b:{dl}) "
                  f"WHERE a.{sp} = $s AND b.{dp} = $d RETURN count(*) AS c",
                  {"s": src, "d": dst})
    return bool(r and r[0].get("c"))


def _stamp_edge_endpoints(store, rel: str, ends: _Ends, src: str, dst: str,
                          owner: str) -> None:
    """엣지 **신규 생성** 시 양 끝 노드에 revstamp — 엣지 자체는 추적열이 없어,
    끝점 스탬프가 없으면 '기존 노드 사이 신규/재생성 엣지'가 증분 동기화에 영영 안
    실린다(delta 번들의 엣지는 양끝 노드가 델타에 있을 때만 동봉). 엣지 활동 =
    끝점 활동으로 간주한다 — 부수 효과로 '편집이 삭제를 이긴다' 가드에서도 엣지
    활동이 노드 편집처럼 취급된다(의도된 의미론). 동시에 같은 (src,rel,dst) 의
    로컬 엣지 툼스톤을 상쇄한다(재생성이 낡은 삭제 표식을 이긴다).

    ``ends`` 는 호출부가 해석한 끝점 (라벨, PK) 쌍이다. v12 전에는 rel 라벨만 받아
    표에서 끌어왔는데, 그 표가 성립하려면 **rel 하나에 끝점 라벨 조합이 하나**여야
    한다. 결정 타입이 들어오면서 RELATED_TO 하나가 Concept↔Decision 네 조합을 갖게
    돼 그 전제가 깨졌다. 호출부는 이미 타입을 알고 있으므로 내려받는 편이 맞다."""
    for (label, pk), nid in zip(ends, (src, dst)):
        rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN n.rev AS rev",
                         {"id": nid})
        if not rows:
            continue
        store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id "
                  "SET n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                  {"id": nid, "ua": revstamp.hlc_now(store),
                   "rev": int(rows[0].get("rev") or 0) + 1,
                   "seq": revstamp.next_seq(store, owner)})
    store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k DELETE t",
              {"k": f"{src}|{rel}|{dst}"})


def _merge_edge(store, edge: dict, types: dict[str, str], warnings: list[str],
                *, owner: str | None = None, owner_explicit: bool = False,
                stamp_edges: bool = True) -> str | None:
    """단일 엣지 MERGE. 반환 = 'created' | 'skipped_duplicate' | None(스킵).

    SEC-3: owner_explicit(강제 owner 경로)면 owner-스코프 끝점(Concept/Record/Bridge/
    Episode/Entity)을 owner_id 로 스코프한다 — 내 fragment 가 남의 노드 ID 를
    참조해도 그 노드로 향한 엣지는 MATCH 되지 않아 no-op(남의 서브그래프에 엣지
    주입 차단). Bridge 는 v2 에서 owner_id 를 얻어 스코프에 편입(구 'Concept 쪽만'
    주석 폐기). 로컬(owner_explicit=False)은 현행.

    개방층(F1): 양 끝이 모두 개방 타입(레거시 6종 밖)이면 엣지 타입 무관하게
    TYPED{type} 캐리어로 exact (from,type,to) MERGE — rel 속성이 매칭 키에 참여해
    같은 쌍에 type 이 다른 엣지가 공존한다. 미지 타입 skip 은 레거시 끝점 조합에만
    남는다(선언된 FROM/TO 쌍 밖 — 스킵이 아니라 토폴로지 한계)."""
    etype = (edge.get("type") or "").strip()
    src, dst = (edge.get("from") or "").strip(), (edge.get("to") or "").strip()
    st, dt = types.get(src, ""), types.get(dst, "")
    osc = bool(owner_explicit and owner)
    def _o(var: str) -> str:
        return f" AND {var}.owner_id = $owner" if osc else ""
    op = {"owner": owner} if osc else {}

    # E-G6 — 선택 증거·신뢰도. 있으면 MERGE 뒤 SET 절로 부여(CC 4종 + about 만 —
    # 그 rel 테이블만 열을 가진다). 미제공이면 열은 스키마 기본값('', 0.0) 유지.
    def _ev_set(rel_var: str) -> tuple[str, dict]:
        ev, conf = edge.get("evidence"), edge.get("confidence")
        sets, p = [], {}
        if ev is not None:
            sets.append(f"{rel_var}.evidence = $evid"); p["evid"] = str(ev)
        if conf is not None:
            try:
                p["conf"] = float(conf); sets.append(f"{rel_var}.confidence = $conf")
            except (TypeError, ValueError):
                warnings.append(f"edge confidence not a number — ignored ({src}→{dst})")
        return ((" SET " + ", ".join(sets)) if sets else ""), p

    # 개방층 우선(F1): 양 끝이 Entity 라우팅이면 알려진 타입명(relates_to 등)이어도
    # TYPED 로 간다 — Entity 테이블 간에는 레거시 rel 테이블이 존재하지 않는다.
    if _is_open(st) and _is_open(dt):
        if not etype:
            warnings.append(f"edge without type — skipped ({src}→{dst})")
            return None
        store.run(
            f"MATCH (a:{_ENTITY_LABEL}), (b:{_ENTITY_LABEL}) "
            f"WHERE a.{_ENTITY_ID} = $s AND b.{_ENTITY_ID} = $d{_o('a')}{_o('b')} "
            f"MERGE (a)-[:{_TYPED_REL} {{type: $t}}]->(b)",
            {"s": src, "d": dst, "t": etype, **op})
        return "created"

    # T4 — 대상 미선언과 타입 불일치를 **구분**한다. 해석기는 이 호출의 `nodes`
    # 배열에서만 타입을 찾는다(스토어 조회 없음 — 조회형 구조 개선은 GM-15 후보).
    # 스토어에 이미 있는 노드로 향하는 엣지도 payload 에 id 를 함께 싣지 않으면
    # 타입이 '' 이 되어 아래 쌍 검사가 전부 빗나가고, 종전에는 그 경우까지
    # "concept→? 미지원" 으로 뭉개져 사용자가 조치를 알 수 없었다(실측: 17개 중
    # 기존 노드로 향하는 6개가 조용히 skip).
    unresolved = [n for n, t in ((src, st), (dst, dt)) if n and not t]
    if unresolved:
        warnings.append(
            f"edge '{etype}' skipped — endpoint not declared in this call's nodes: "
            f"{', '.join(unresolved)} ({st or '?'}→{dt or '?'}). The resolver only "
            "sees node types declared in this payload, so include the target id in "
            "`nodes` in minimal form (id · type · title · domain) even when the "
            "node already exists in the store, then retry")
        return None

    if etype not in _EDGE_TYPES:
        warnings.append(
            f"unsupported edge type '{etype}' — skipped ({src}→{dst}); {_ALLOWED_PAIRS_HINT}")
        return None

    # v12 — 끝점 라벨·PK 를 타입에서 꺼낸다. 아래 분기들이 라벨을 문자열로 박고
    # 있었는데, 결정이 개념과 같은 자리에 서면서 조합이 늘어 그대로는 분기가
    # 배로 불어난다. 자리(어느 층인가)만 보고 라벨은 표에서 꺼내면 분기는 그대로다.
    def _end(ntype: str) -> tuple[str, str]:
        return _NODE_MAP[ntype]

    if etype == "connects_via":
        if st != "bridge" or dt not in _SEMANTIC_TYPES:
            warnings.append(
                f"connects_via expects bridge→concept|decision — skipped "
                f"({st or '?'}→{dt or '?'}); {_ALLOWED_PAIRS_HINT}")
            return None
        (dl, dp) = _end(dt)
        # F9: 선택 role — 있으면 MERGE 패턴에 참여(bridges_ingest 의미론 동형: 같은
        # role 멱등, 다른 role 병렬 엣지). 없으면 bare MERGE(기존 role 엣지에 no-op).
        # role 기본값 조작 금지 — `{dom}_input` 관례는 bridges_ingest 전속.
        role = (edge.get("role") or "").strip()
        if role:
            store.run(
                f"MATCH (a:Bridge), (b:{dl}) "
                f"WHERE a.bridge_id = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
                "MERGE (a)-[:CONNECTS {role: $role}]->(b)",
                {"s": src, "d": dst, "role": role, **op})
        else:
            store.run(
                f"MATCH (a:Bridge), (b:{dl}) "
                f"WHERE a.bridge_id = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
                "MERGE (a)-[:CONNECTS]->(b)", {"s": src, "d": dst, **op})
        return "created"
    if etype in _EC_EDGES:
        # F4 — episodic↔semantic 다리. 비대칭 쌍이라 역방향 dedupe 불요(ATTACHES_TO
        # 동형). 삭제 전파는 엣지 툼스톤(S4 확장 — query.delete_edge)이 담당한다.
        #
        # v12 — 출발점에 decision 이 붙는다. `about` 은 "X 는 Y 에 관한 것"이고,
        # 결정이 어떤 개념을 두고 내려졌다는 말이 정확히 그 형태다.
        if st not in ("episode", "decision") or dt not in _SEMANTIC_TYPES:
            warnings.append(
                f"about expects episode|decision→concept|decision — skipped "
                f"({st or '?'}→{dt or '?'}); {_ALLOWED_PAIRS_HINT}")
            return None
        rel = _EC_EDGES[etype]
        (sl, sp), (dl, dp) = ends = (_end(st), _end(dt))
        was = _edge_exists(store, rel, ends, src, dst)
        ev_set, ev_p = _ev_set("r")
        store.run(
            f"MATCH (a:{sl}), (b:{dl}) "
            f"WHERE a.{sp} = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
            f"MERGE (a)-[r:{rel}]->(b){ev_set}", {"s": src, "d": dst, **op, **ev_p})
        if not was and stamp_edges:
            _stamp_edge_endpoints(store, rel, ends, src, dst, owner or DEFAULT_OWNER)
        return "created"
    if st in _SEMANTIC_TYPES and dt in _SEMANTIC_TYPES:
        if etype == "relates_to" and "decision" in (st, dt):
            # relates_to 만 개념↔개념에 남는다(embedded_schema._LOUVAIN_PROJECTED_REL).
            # 엔진 제약이자 의미상 옳은 자리다 — 결정은 개념에 **관한** 것이고
            # (about), 결정끼리는 **계보**(derives_from · prerequisite_of)다.
            # 조용히 다른 타입으로 바꿔 쓰지 않는다: 사용자가 적은 관계를 서버가
            # 재해석하면 그래프가 사용자가 믿는 것과 달라진다.
            warnings.append(
                f"relates_to is concept→concept only — skipped ({st}→{dt}); "
                "for a decision use `about` (decision→concept) or "
                "`derives_from`/`prerequisite_of` (decision lineage)")
            return None
        (sl, sp), (dl, dp) = ends = (_end(st), _end(dt))
        if etype == "relates_to":
            # RELATED_TO 는 의미상 무방향 — 역방향 (b)-[:RELATED_TO]->(a) 가 이미
            # 있으면 신규 생성을 skip(양방향 재적재로 degree 2배 부풀림 방지).
            rev = store.run(
                f"MATCH (b:{dl})-[:RELATED_TO]->(a:{sl}) "
                f"WHERE b.{dp} = $d AND a.{sp} = $s{_o('b')}{_o('a')} "
                "RETURN count(*) AS c",
                {"s": src, "d": dst, **op})
            if rev and rev[0].get("c"):
                return "skipped_duplicate"
        rel = _CC_EDGES[etype]
        was = _edge_exists(store, rel, ends, src, dst)
        ev_set, ev_p = _ev_set("r")
        store.run(
            f"MATCH (a:{sl}), (b:{dl}) "
            f"WHERE a.{sp} = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
            f"MERGE (a)-[r:{rel}]->(b){ev_set}", {"s": src, "d": dst, **op, **ev_p})
        if not was and stamp_edges:
            _stamp_edge_endpoints(store, rel, ends, src, dst, owner or DEFAULT_OWNER)
        return "created"
    if st == "record" and dt in _SEMANTIC_TYPES:
        # Record 참여 엣지 — ATTACHES_TO{type}로 원 타입 보존(스키마 §5-2 주석).
        # (from=Record만 허용되는 비대칭 rel 테이블이라 역방향 MERGE 자체가 불가능
        # — RELATED_TO 같은 무방향 dedupe 는 이 경로에 해당 없음.)
        (dl, dp) = _end(dt)
        store.run(
            f"MATCH (a:Record), (b:{dl}) "
            f"WHERE a.record_id = $s AND b.{dp} = $d{_o('a')}{_o('b')} "
            "MERGE (a)-[r:ATTACHES_TO]->(b) SET r.type = $t",
            {"s": src, "d": dst, "t": etype, **op})
        return "created"
    # 여기 도달 = 양 끝 타입이 **선언돼 있고** 쌍이 rel 테이블 밖(진짜 토폴로지 한계).
    warnings.append(
        f"edge '{etype}' between {st}→{dt} 미지원 — skipped; "
        f"{_ALLOWED_PAIRS_HINT}")
    return None


def _finalize(result: dict, warnings: list[str]) -> dict:
    """warnings 를 응답에 얹고, 1개 이상이면 partial:True 를 추가한다(실사용 사고 §1 —
    executed:True 만 보고 전량 성공으로 오인하는 것을 막음). warnings 없으면 partial
    필드 자체를 생략해 기존 응답과 하위호환을 유지한다."""
    result["warnings"] = warnings
    if warnings:
        result["partial"] = True
    return result


def _domain_gate(fragment_nodes: list[dict], *, store, owner: str,
                 max_domains: int) -> dict | None:
    """주제 개수 상한. 통과면 None, 걸리면 **거부 사유 dict**.

    막는 것은 **새 주제 생성뿐**이다(AP-1 §3.2). 기존 주제에 노드를 더하는 것,
    편집, 주제 없는 노드는 언제나 통과한다 — 그래서 상한을 넘긴 채로 이미 쓰고
    있던 사용자가 자동으로 유예된다(데이터 삭제·강제 이관 없음).

    거부는 **프래그먼트 통째로** 한다. 새 주제 둘 중 하나만 받아 넣으면 사용자는
    자기가 뭘 가졌는지 모르는 상태가 되고, 재시도가 멱등하지 않게 된다.
    """
    incoming = {d for d in ((n.get("domain") or "").strip() for n in fragment_nodes) if d}
    if not incoming:
        return None                       # 주제 없는 적재는 상한과 무관
    from . import query                    # 국소 import — 상한이 꺼져 있으면 비용 0
    existing = query.domain_names(owner=owner, store=store)
    if existing is None:
        return None                       # 스토어 미도달 = 판정 불가 → 막지 않는다
    fresh = incoming - existing
    if not fresh or len(existing) + len(fresh) <= max_domains:
        return None
    return {"error": "domain_limit_reached", "limit": max_domains,
            "current": len(existing), "attempted": sorted(fresh)}


def merge_fragment(fragment: dict, *, store, themes=None,
                   stamp_edges: bool = True,
                   max_domains: int | None = None) -> dict:
    """fragment 전체 MERGE. 반환 = D11 §4 계약(+ dedupe/partial/개방층 확장):
    ``{merged:{nodes:{created,matched}, edges:{created,[skipped_duplicate]}},
    warnings:[…], executed:…, [partial:True], [proposed_types:[…]]}``.

    ``themes``(ThemeStore, 선택): 개방층 타입의 어휘 편입(D11-R2 §3)에 쓴다 —
    서버 잡 큐 경로만 전달(server.write_ingest). sync/share 호출부는 미전달 유지
    (복제에서 어휘 fold-in 은 부적절 — 원 저작 시점에 이미 편입됨).

    ``stamp_edges``: 신규 생성된 동기화 대상 엣지(CC 4종·ABOUT)의 양 끝 노드에
    revstamp(엣지 추가·재생성의 증분 동기화 전파 — _stamp_edge_endpoints 주석).
    **sync apply 경로만 False** — 원격발 엣지를 다시 스탬프하면 에코가 된다.

    ``max_domains``(선택): 무료 등급 주제 개수 상한(AP-1 §3.3). **기본 None = 무제한**
    이라는 점이 설계의 핵심이다 — sync 복제·로컬 경로가 아무 코드 변경 없이 자동
    면제되고, 위험이 "면제를 잊음"(상한 초과 사용자의 복제 파손)에서 "적용을
    잊음"(상한이 안 걸림)으로 뒤집힌다. 뒤쪽이 훨씬 싼 실패다."""
    warnings: list[str] = []
    if store is None:
        return _finalize({"merged": None, "executed": False}, warnings + ["no reachable store"])
    created = matched = e_created = e_skipped_dup = 0
    nodes = list(fragment.get("nodes") or [])
    edges = list(fragment.get("edges") or [])
    owner_explicit = bool((fragment.get("owner") or "").strip())
    owner = (fragment.get("owner") or "").strip() or DEFAULT_OWNER
    if max_domains is not None and owner_explicit:
        denial = _domain_gate(nodes, store=store, owner=owner, max_domains=max_domains)
        if denial:
            return _finalize({"merged": None, "executed": False, **denial}, warnings + [
                f"topic limit reached ({denial['current']}/{max_domains}) — "
                f"new topics rejected: {', '.join(denial['attempted'])}. "
                "Existing topics keep working; Pro removes the limit."])
    fold = _ThemeFold(themes, fragment)
    for n in nodes:
        ntype = (n.get("type") or "").strip()
        if _is_open(ntype):
            fold.fold("node", ntype)
        r = _merge_node(store, n, owner, warnings, owner_explicit=owner_explicit,
                        theme_id=fold.theme_id or "")
        created += (r == "created")
        matched += (r == "matched")
    types = _node_types(nodes)
    for e in edges:
        st, dt = types.get((e.get("from") or "").strip(), ""), types.get((e.get("to") or "").strip(), "")
        etype = (e.get("type") or "").strip()
        if etype and _is_open(st) and _is_open(dt):
            fold.fold("edge", etype)   # TYPED 캐리어행 엣지 타입만 어휘 편입
        r = _merge_edge(store, e, types, warnings, owner=owner,
                        owner_explicit=owner_explicit, stamp_edges=stamp_edges)
        e_created += (r == "created")
        e_skipped_dup += (r == "skipped_duplicate")
    edges_out = {"created": e_created}
    if e_skipped_dup:
        edges_out["skipped_duplicate"] = e_skipped_dup
    result = {"merged": {"nodes": {"created": created, "matched": matched}, "edges": edges_out},
              "executed": True}
    if fold.proposed:
        result["proposed_types"] = fold.proposed
    return _finalize(result, warnings)
