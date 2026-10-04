# -*- coding: utf-8 -*-
"""vinc_sync — 로컬↔원격 양방향 동기화의 데이터플레인(S1: export/apply).

왜 share pack 을 그대로 못 쓰나: `build_share_pack` 은 **전량 스냅샷**이고 delta
(since) 개념이 없다. 동기화는 "지난 번 이후 바뀐 것"만 날라야 한다 — S0 가 넣은
`sync_seq`(owner별 단조 카운터)가 그 커서다.

**오프라인 우선 약속의 각인**: export 는 `_SyncMeta.selected_domains` 에 든 도메인만
훑는다. 안 고른 도메인은 쿼리 자체가 닿지 않으므로 **기기를 떠날 수 없다**. 선택이
비어 있으면 아무것도 내보내지 않는다(명시적 옵트인).

**최초 동기화**: S0 마이그레이션은 기존 노드를 `sync_seq=0` 으로 백필한다. 커서도 0
이라 `sync_seq > 0` 은 그것들을 놓친다 — 그래서 `since=0`(=한 번도 push 안 함)이면
**선택 도메인 전량**을 내보낸다(delta 아님). 그 다음부터 delta.

적용(apply)은 `fragment.merge_fragment` 를 재사용하되 **rev 가드**를 건다: 인바운드
rev 가 저장본보다 낮으면 무시(늦게 도착한 옛 변경이 최신을 덮지 않게). 원격 스탬프
(updated_at/rev)는 **그대로 보존**한다 — 재스탬프하면 pull 이 곧 새 로컬 변경으로
보여 무한 왕복이 된다.
"""
from __future__ import annotations

import hashlib
import json

from . import fragment as frag
from . import revstamp
from . import syncmerge
from . import query as q
from store import pkns

# 청크 상한 — 원격 `MAX_BODY`(4MB, vinc_mcp/http_server.py)보다 넉넉히 아래.
# 초과 시 서버가 빈 바디로 400 을 내므로 조용히 실패한다 → 여유를 크게 둔다.
CHUNK_BYTES = 3 * 1024 * 1024

_SEL_KEY = "selected_domains"

# ── 동기화 필드(=merge_fragment 가 실제로 왕복시키는 것만, S3) ─────────────────
# 급소: **fragment 가 왕복시키는 필드만** 나른다 — 그 밖의 필드(Concept 의 topic·
# one_liner·status·stem, Episode 의 pinned)는 동기화하면 유실된다
# ([[vinc.spec.dataplane §6.1]]).
# ⚠ 열이 있는 라벨에만 넣을 것 — 없는 라벨에 넣으면 `_local_snapshot` 의 SELECT 가
# Binder 로 죽는다(`content_time` 은 Concept 에만 있다).
# ⚠ `created`(출생)는 여기 넣지 않는다 — 3-way 병합 대상이 아니고 규칙이 다르다
# (`_reconcile_birth` — 가장 이른 값이 이긴다 · [[vinc.plan.sync-node-time-fields]]).
SYNC_FIELDS: dict[str, tuple[str, ...]] = {
    "Concept": ("title", "domain", "tags", "props", "content_time"),
    "Episode": ("title", "date", "domain", "summary", "tags"),
    # Record — 라이브 실측 277건으로 **노드 종류 중 최대**인데 여기 없어서 기기와
    # 계정 사이를 통째로 못 오갔다. Bridge·Entity 제외는 아래 스코프 결정이지만
    # Record 는 그 문장에도 없었다: 빠진 줄 아무도 몰랐다는 뜻이다.
    # 열은 Concept 과 같다(content_time 은 v7 이 Concept·Record 둘 다에 붙였다).
    # v15(W13) — 유효구간 둘. 여기 안 넣으면 한 기기의 무효화("X 까지였다")가
    # 다른 기기로 안 넘어가 그쪽에서 낡은 주장이 유효한 채 남는다.
    "Record": ("title", "domain", "tags", "props", "content_time",
               "valid_from", "valid_until"),
    # v12 — 결정. **빼면 안 된다**: 결정은 여태 Concept 이었고 그래서 동기화되고
    # 있었다. 라벨만 옮기고 여기 안 넣으면 기기 간에 결정이 조용히 안 넘어간다
    # (오류도 경고도 없이 — 다른 기기에서 결정이 통째로 사라진 것처럼 보인다).
    "Decision": ("title", "domain", "tags", "props"),
}
# fragment 노드 타입 → 라벨(export/apply 대상). Bridge/Entity 제외는 이제 왕복 결함이
# 아니라 **스코프 결정**(F1 포팅으로 Entity 필드는 왕복 가능; Bridge 구조 필드
# source/target_domain 은 여전히 fragment 미기록) — 편입은 후속 과제.
_SYNC_TYPES = {"concept": "Concept", "episode": "Episode", "decision": "Decision",
               "record": "Record"}
#: 노드 툼스톤 전파 대상 라벨 — 동기화 라벨과 같아야 한다(따로 적으면 어긋난다).
_SYNC_LABELS = tuple(_SYNC_TYPES.values())
#: 라벨 → PK 열. fragment 의 타입 표에서 파생한다(중복 정의 회피).
_SYNC_PK = {lbl: frag._NODE_MAP[t][1] for t, lbl in _SYNC_TYPES.items()}


def _snapshot(label: str, node: dict) -> dict:
    """노드(번들 노드 또는 로컬 조회 dict)에서 sync 필드만 **raw** 로 뽑되 표현을
    통일한다: props 는 항상 dict(로컬 DB 는 JSON 문자열이라 파싱), tags 는 항상 list.
    로컬·원격·base 가 같은 표현이라야 3-way 비교·쓰기·저장이 일관된다. 값 비교는
    syncmerge._norm(dict→정렬 JSON, list→tuple)이 담당한다."""
    snap: dict = {}
    for f in SYNC_FIELDS.get(label, ()):
        v = node.get(f)
        if f == "props":
            if isinstance(v, str):
                try:
                    v = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    v = {}
            elif v is None:
                v = {}
        elif f == "tags" and v is None:
            v = []
        snap[f] = v
    return snap


# ── 3-way base(_SyncBase, 로컬 전용) ──────────────────────────────────────────
def get_base(store, node_id: str, owner: str = "") -> dict | None:
    # PK 네임스페이싱 — `_SyncBase.node_id` 는 노드 id 그 자체라, 두 소유자가 같은
    # 슬러그를 가지면 **3-way 병합의 공통 조상 한 행을 나눠 쓴다**(감사 M3). 그러면
    # 남의 조상으로 내 병합이 계산된다. 근거: [[vinc.plan.node-pk-namespacing]] §7
    rows = store.run("MATCH (b:_SyncBase) WHERE b.node_id = $id "
                     "RETURN b.fields AS f, b.base_rev AS rev, b.base_updated_at AS ua",
                     {"id": pkns.key(owner, node_id)})
    if not rows:
        return None
    return {"fields": syncmerge.parse_base(rows[0].get("f")),
            "rev": int(rows[0].get("rev") or 0), "ua": rows[0].get("ua") or ""}


def set_base(store, node_id: str, label: str, fields: dict, rev: int, ua: str,
             owner: str = "") -> None:
    store.run("MERGE (b:_SyncBase {node_id: $id}) "
              "SET b.label = $l, b.fields = $f, b.base_rev = $rev, b.base_updated_at = $ua",
              {"id": pkns.key(owner, node_id), "l": label,
               "f": syncmerge.dump_base(fields),
               "rev": int(rev or 0), "ua": ua or ""})


def del_base(store, node_id: str, owner: str = "") -> None:
    store.run("MATCH (b:_SyncBase) WHERE b.node_id = $id DELETE b",
              {"id": pkns.key(owner, node_id)})


# ── tombstone(삭제 전파, S4) ──────────────────────────────────────────────────
#
# ⚠ 이 표들의 PK 는 `node_id`·`ekey` **하나**다(`_META_TABLES`). 노드 id 가 전역
# 네임스페이스라(감사 H1) 슬러그가 겹치는 두 소유자는 **한 행을 나눠 쓰게 된다.**
# 그러면 마지막 쓰기가 이기고, 진 쪽의 삭제는 전파가 멈춰 다른 기기에서 노드가
# 되살아난다. 소유자마다 자기 행을 갖는 것이 진짜 처방인데 그건 PK 를 바꾸는 일이라
# H1 의 재구축에 태운다([[vinc.plan.node-pk-namespacing]] §6).
#
# 그때까지 **열이 이미 있는 쪽**은 지금 닫는다: 읽기·삭제는 소유자로 걸고, 쓰기는
# 남의 행을 덮지 않는다. 이걸로 "남의 대기 중 삭제를 취소한다"와 "남의 툼스톤을
# 조용히 덮는다"가 사라진다. 남는 것은 겹친 슬러그에서 **둘째 소유자가 툼스톤을
# 못 갖는다**는 것뿐인데, 그 소유자는 애초에 허브에서 그 노드를 갖지 못한다
# (SEC-3 가 id 를 스킵한다) — 즉 지울 것이 없다.
def get_tombstone(store, node_id: str, owner: str = "") -> dict | None:
    where = "t.node_id = $id" + (" AND t.owner_id = $owner" if owner else "")
    params = {"id": pkns.key(owner, node_id)}
    if owner:
        params["owner"] = owner
    rows = store.run(f"MATCH (t:_Tombstone) WHERE {where} "
                     "RETURN t.deleted_at AS da, t.rev AS rev", params)
    if not rows:
        return None
    return {"deleted_at": rows[0].get("da") or "", "rev": int(rows[0].get("rev") or 0)}


def _tombstone_owner(store, node_id: str, owner: str = "") -> str:
    rows = store.run("MATCH (t:_Tombstone) WHERE t.node_id = $id RETURN t.owner_id AS o",
                     {"id": pkns.key(owner, node_id)})
    return (rows[0].get("o") or "") if rows else ""


def put_tombstone(store, node_id: str, label: str, owner: str, deleted_at: str,
                  rev: int) -> bool:
    """수신한 삭제를 재전파용으로 남긴다. deleted_at 은 **원래 삭제 시각 보존**(의미),
    sync_seq 는 수신측 자기 번호(다른 기기의 pull 커서용) — 노드 스탬프와 같은 규칙.

    반환 False = 그 자리를 **다른 소유자가 이미 쓰고 있어** 남기지 않았다(위 주석)."""
    held = _tombstone_owner(store, node_id, owner)
    if held and owner and held != owner:
        return False
    store.run("MERGE (t:_Tombstone {node_id: $id}) SET t.label = $l, t.owner_id = $o, "
              "t.deleted_at = $da, t.rev = $rev, t.sync_seq = $seq, t.origin = 'remote'",
              {"id": pkns.key(owner, node_id), "l": label, "o": owner,
               "da": deleted_at,
               "rev": int(rev), "seq": revstamp.next_seq(store, owner)})
    return True


def del_tombstone(store, node_id: str, owner: str = "") -> None:
    where = "t.node_id = $id" + (" AND t.owner_id = $owner" if owner else "")
    params = {"id": pkns.key(owner, node_id)}
    if owner:
        params["owner"] = owner
    store.run(f"MATCH (t:_Tombstone) WHERE {where} DELETE t", params)


def put_edge_tombstone(store, src: str, rel: str, dst: str, owner: str,
                       deleted_at: str) -> bool:
    """수신한 엣지 삭제를 재전파용으로 남긴다(노드 put_tombstone 동형 — deleted_at
    원본 보존, sync_seq 는 수신측 자기 번호)."""
    # `ekey` 만 합성한다 — `src`/`rel`/`dst` **열**은 동기화 와이어로 그대로 나가므로
    # 논리여야 한다(계약 테스트가 번들에 U+001F 가 없음을 고정한다).
    k = pkns.ekey(owner, src, rel, dst)
    held = store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN t.owner_id AS o",
                     {"k": k})
    if held and (held[0].get("o") or "") and owner and held[0].get("o") != owner:
        return False                       # 남의 행 — 덮지 않는다(위 주석)
    store.run("MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = $r, "
              "t.dst = $d, t.owner_id = $o, t.deleted_at = $da, t.sync_seq = $seq, "
              "t.origin = 'remote'",
              {"k": k, "s": src, "r": rel, "d": dst, "o": owner,
               "da": deleted_at, "seq": revstamp.next_seq(store, owner)})
    return True


def _edge_tombstones_delta_plan(*, owner: str, since: int) -> q.QueryPlan:
    """owner 의 엣지 툼스톤 중 sync_seq > since — 동기화 rel(CC 4종+ABOUT)만."""
    rels = sorted(q.SYNC_EDGE_RELS)
    where = ["t.owner_id = $owner", f"(t.rel IN {json.dumps(rels)} OR starts_with(t.rel, 'ATTACHES_TO:'))"]
    params: dict = {"owner": owner}
    if since > 0:
        where.append("t.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (t:_EdgeTombstone) WHERE {' AND '.join(where)} "
        "RETURN t.src AS src, t.rel AS rel, t.dst AS dst, "
        "t.deleted_at AS deleted_at, t.sync_seq AS sync_seq ORDER BY t.sync_seq"
    )
    return q.QueryPlan("sync_edge_tombstones", cypher, params,
                       note="edge deletion markers for delta (S4 확장)")


def _tombstones_delta_plan(*, owner: str, since: int) -> q.QueryPlan:
    """owner 의 tombstone 중 sync_seq > since(최초 sync 면 since=0 전량). 동기화 라벨만
    (_SYNC_LABELS) — 그 외 라벨 삭제는 전파할 곳이 없다."""
    where = ["t.owner_id = $owner", "t.label IN $labels"]
    params: dict = {"owner": owner, "labels": list(_SYNC_LABELS)}
    if since > 0:
        where.append("t.sync_seq > $since")
        params["since"] = since
    cypher = (f"MATCH (t:_Tombstone) WHERE {' AND '.join(where)} "
              "RETURN t.node_id AS node_id, t.label AS label, t.deleted_at AS deleted_at, "
              "t.rev AS rev, t.sync_seq AS sync_seq ORDER BY t.sync_seq")
    return q.QueryPlan("sync_tombstones", cypher, params, note="owner-scoped delete markers")


def gc_tombstones(store, *, keep_days: int = 90, now_ms: int | None = None) -> int:
    """안전 지평 지난 tombstone 청소. 보수적 90일 — 그 안에 모든 기기가 pull 했다고
    가정. 문자열 비교(HLC ISO 고정폭)라 임계 ISO 를 만들어 그 이하를 지운다.
    문서 표식(_DocTombstone)도 같은 지평이다 — docs-pull 델타(docsync)가 노드·엣지와
    같은 sync_seq 커서 계약이고 deleted_at 도 같은 HLC 스탬프라 위험 모양이 동일하다."""
    import datetime
    ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000) \
        if now_ms is None else now_ms
    cutoff = revstamp._fmt(ms - keep_days * 86400 * 1000)
    n = 0
    for label in ("_Tombstone", "_EdgeTombstone", "_DocTombstone"):
        cur = store.run(f"MATCH (t:{label}) WHERE t.deleted_at < $c AND t.deleted_at <> '' "
                        "RETURN count(t) AS c", {"c": cutoff})
        k = int(cur[0].get("c") or 0) if cur else 0
        if k:
            store.run(f"MATCH (t:{label}) WHERE t.deleted_at < $c AND t.deleted_at <> '' "
                      "DELETE t", {"c": cutoff})
        n += k
    return n


# ── 선택 도메인 (오프라인 우선 옵트인) ────────────────────────────────────────
def get_selected_domains(store) -> list[str]:
    raw = revstamp._meta_get(store, _SEL_KEY, "")
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def set_selected_domains(store, domains: list[str]) -> list[str]:
    clean = sorted({str(d) for d in (domains or []) if str(d).strip()})
    revstamp._meta_set(store, _SEL_KEY, json.dumps(clean, ensure_ascii=False))
    return clean


# ── 커서 ──────────────────────────────────────────────────────────────────────
def get_cursor(store, key: str) -> int:
    try:
        return int(revstamp._meta_get(store, f"cursor:{key}", "0"))
    except (TypeError, ValueError):
        return 0


def set_cursor(store, key: str, value: int) -> None:
    revstamp._meta_set(store, f"cursor:{key}", int(value))


#: 이 스토어의 커서가 **어느 계정 것인가**(키 지문 — 원문 아님).
_ACCOUNT_KEY = "sync_account"
#: 계정이 바뀌면 되돌릴 커서 전량. 새 국면을 추가하면 여기 넣을 것.
_CURSOR_KEYS = ("push", "pull", "docs-push", "docs-pull")


def _account_fingerprint(remote) -> str:
    """원격이 어느 계정인지 가리키는 안정적인 지문. 키 원문은 저장하지 않는다."""
    token = (getattr(remote, "token", "") or "").strip()
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32] if token else ""


def reset_cursors_on_account_change(store, remote) -> bool:
    """계정이 바뀌었으면 동기화 커서를 전부 0 으로 되돌린다(되돌렸으면 True).

    커서는 **원격 owner 의 시퀀스 번호**라 계정마다 뜻이 다르다. A 로 쓰던 커서를
    B 에 그대로 들이대면 B 의 그 번호 아래 있는 것이 통째로 delta 에서 빠진다 —
    오류도 경고도 없이 안 내려온다. 화면에 [연결 해제]가 있는 이상 계정 전환은
    일어나고, 자동 동기화는 그 일을 **아무도 안 볼 때** 한다.

    같은 계정에서 키만 재발급해도 지문이 달라져 초기화된다. 그 대가는 전량 재동기화
    한 번이고(apply 는 rev 가드 걸린 멱등 LWW), 반대쪽 실수는 무음 데이터 누락이다.
    헷갈릴 때 초기화하는 쪽으로 기우는 이유다.

    ⚠ `selected_domains` 는 건드리지 않는다 — 그것은 계정이 아니라 **이 기기에서
    무엇을 내보낼지**에 대한 사용자의 선택이다.
    """
    now = _account_fingerprint(remote)
    if not now:
        return False                      # 키 없는 원격(테스트 스텁) — 판정 보류
    was = revstamp._meta_get(store, _ACCOUNT_KEY, "")
    if was == now:
        return False
    if was:                               # 최초 연결은 초기화할 것이 없다
        for k in _CURSOR_KEYS:
            set_cursor(store, k, 0)
    revstamp._meta_set(store, _ACCOUNT_KEY, now)
    return bool(was)


# ── export ────────────────────────────────────────────────────────────────────
def _scope(where: list[str], owner: str, domains: list[str] | None,
           var: str) -> tuple[list[str], dict]:
    """주제 스코프. `domains=None` 은 **주제를 안 가린다** — 모드 전환(VIN-130)이 이 기기의
    전부를 옮길 때다. 주제가 비어 있는 노드도 그때는 함께 간다(`IN` 은 그것을 못 잡는다)."""
    params: dict = {"owner": owner}
    if domains is not None:
        where = [*where, f"{var}.domain IN $domains"]
        params["domains"] = domains
    return where, params


def _concepts_delta_plan(*, owner: str, domains: list[str] | None, since: int) -> q.QueryPlan:
    """선택 도메인 안에서 `sync_seq > since` 인 Concept. since=0 이면 전량(최초 push)."""
    where, params = _scope(["c.owner_id = $owner"], owner, domains, "c")
    if since > 0:
        where.append("c.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (c:Concept) WHERE {' AND '.join(where)} "
        "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
        "c.tags AS tags, c.topic AS topic, c.one_liner AS one_liner, "
        "c.props AS props, c.updated_at AS updated_at, c.rev AS rev, "
        # D72 두 시간열. 안 실으면 수신 기기가 자기 시각으로 출생을 새로 찍어 같은
        # 노드가 기기마다 다른 생일을 갖는다(2026-08-17 두 스토어 실측: 1초 차이).
        # 문서 경로(api/docsync)는 이미 같은 이유로 둘을 싣고 있었다 — 노드만 빠져 있었다.
        "c.created AS created, c.content_time AS content_time, "
        "c.sync_seq AS sync_seq ORDER BY c.sync_seq"
    )
    return q.QueryPlan("sync_concepts", cypher, params, note="owner+domain scoped delta")


def _episodes_delta_plan(*, owner: str, domains: list[str] | None, since: int) -> q.QueryPlan:
    where, params = _scope(["e.owner_id = $owner"], owner, domains, "e")
    if since > 0:
        where.append("e.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (e:Episode) WHERE {' AND '.join(where)} "
        "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
        "e.date AS date, e.summary AS summary, e.tags AS tags, e.pinned AS pinned, "
        "e.updated_at AS updated_at, e.rev AS rev, e.sync_seq AS sync_seq "
        "ORDER BY e.sync_seq"
    )
    return q.QueryPlan("sync_episodes", cypher, params, note="owner+domain scoped delta")


def _decisions_delta_plan(*, owner: str, domains: list[str] | None, since: int) -> q.QueryPlan:
    """선택 도메인 안에서 `sync_seq > since` 인 Decision(v12). Concept 플랜과 동형."""
    where, params = _scope(["d.owner_id = $owner"], owner, domains, "d")
    if since > 0:
        where.append("d.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (d:Decision) WHERE {' AND '.join(where)} "
        "RETURN d.decision_id AS id, d.title AS title, d.domain AS domain, "
        "d.tags AS tags, d.props AS props, d.updated_at AS updated_at, "
        # `created` 만 — Decision 에는 `content_time` 열이 **없다**(v12 가 라이브 68건
        # 실측으로 빈 열을 안 만들었다; 2026-08-17 재확인). 실으면 Binder 로 죽는다.
        "d.created AS created, "
        "d.rev AS rev, d.sync_seq AS sync_seq ORDER BY d.sync_seq"
    )
    return q.QueryPlan("sync_decisions", cypher, params, note="owner+domain scoped delta")


def _records_delta_plan(*, owner: str, domains: list[str] | None, since: int) -> q.QueryPlan:
    """선택 도메인 안에서 `sync_seq > since` 인 Record. Concept 플랜과 동형이고
    시간 두 열도 같다 — Record 는 `created` 와 `content_time` 을 **둘 다** 갖는다
    (스키마 v7 이 Concept 과 함께 붙였다)."""
    where, params = _scope(["r.owner_id = $owner"], owner, domains, "r")
    if since > 0:
        where.append("r.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (r:Record) WHERE {' AND '.join(where)} "
        "RETURN r.record_id AS id, r.title AS title, r.domain AS domain, "
        "r.tags AS tags, r.props AS props, r.updated_at AS updated_at, "
        "r.created AS created, r.content_time AS content_time, "
        # v15(W13) — 유효구간. SYNC_FIELDS 에 있는데 여기서 안 실으면 _snapshot 이
        # None 을 뽑아 "원격이 이 필드를 비웠다"로 읽힌다(무효화의 조용한 소실).
        "r.valid_from AS valid_from, r.valid_until AS valid_until, "
        "r.rev AS rev, r.sync_seq AS sync_seq ORDER BY r.sync_seq"
    )
    return q.QueryPlan("sync_records", cypher, params, note="owner+domain scoped delta")


def _node_from_concept(r: dict) -> dict:
    # sync 는 **raw props** 만 나른다(topic/one_liner 는 fragment 왕복 불가라 폴딩 안 함).
    props = {}
    if r.get("props"):
        try:
            props = json.loads(r["props"]) or {}
        except (TypeError, ValueError):
            props = {}
    return {"id": r["id"], "type": "concept", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "props": props,
            # 내용 시점은 **본문**으로(3-way 병합 대상), 출생은 **스탬프**로(_ 접두 —
            # 병합하지 않고 `_reconcile_birth` 가 따로 처리한다).
            "content_time": r.get("content_time") or "",
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_created": r.get("created") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def _node_from_decision(r: dict) -> dict:
    props = {}
    if r.get("props"):
        try:
            props = json.loads(r["props"]) or {}
        except (TypeError, ValueError):
            props = {}
    return {"id": r["id"], "type": "decision", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "props": props,
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_created": r.get("created") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def _node_from_record(r: dict) -> dict:
    props = {}
    if r.get("props"):
        try:
            props = json.loads(r["props"]) or {}
        except (TypeError, ValueError):
            props = {}
    return {"id": r["id"], "type": "record", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "props": props,
            "content_time": r.get("content_time") or "",
            # v15(W13) — 유효구간도 본문이다(content_time 과 같은 신분 — 3-way
            # 병합 대상. 출생 _created 처럼 스탬프가 아니다).
            "valid_from": r.get("valid_from") or "",
            "valid_until": r.get("valid_until") or "",
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_created": r.get("created") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def _node_from_episode(r: dict) -> dict:
    return {"id": r["id"], "type": "episode", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "date": r.get("date"), "summary": r.get("summary"),
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def export_delta(*, owner: str, since: int = 0, domains: list[str] | None = None,
                 everything: bool = False, with_tombstones: bool = True,
                 cfg=None, store=None) -> dict:
    """owner + 선택 도메인 스코프의 delta 번들.

    반환 `{executed, since, next_cursor, full, nodes, edges, counts}`. `full=True` 는
    최초 동기화(전량)를 뜻한다. 노드에는 `_rev/_updated_at/_seq` 가 실려 수신측이
    LWW 가드와 커서를 판단한다.

    `everything=True` 는 주제를 가리지 않는다(주제 없는 노드 포함). 모드 전환이 이 기기의
    전부를 옮길 때와, 그것을 받는 허브의 전량 pull 이 쓴다(VIN-130).
    `with_tombstones=False` 는 삭제 표식을 싣지 않는다. 옮기기는 **있는 것을 더하는** 일이지
    이 기기에서 지운 이력으로 계정의 것을 지우는 일이 아니다."""
    sel = None if everything else (domains if domains is not None else get_selected_domains(store))
    base = {"executed": False, "since": since, "next_cursor": since,
            "full": since <= 0, "nodes": [], "edges": [], "counts": {}}
    if everything:
        # 받는 쪽이 "주제 없는 노드까지 왔다"를 알 수 있게. 옛 허브는 플래그를 몰라
        # 이 키 없이 주제 있는 노드만 준다(`pull_everything` 이 그것을 말한다).
        base["everything"] = True
    if not everything and not sel:
        # 선택 도메인 없음 = 아무것도 내보내지 않는다(오프라인 우선 기본값).
        base["executed"] = True
        base["note"] = "no domains selected — nothing leaves the device"
        return base

    c_res = q.execute(_concepts_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)
    if not c_res.get("executed"):
        base["note"] = c_res.get("note", "no reachable store")
        return base
    e_res = q.execute(_episodes_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)
    d_res = q.execute(_decisions_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)
    r_res = q.execute(_records_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)

    c_rows = c_res.get("rows", [])
    e_rows = e_res.get("rows", []) if e_res.get("executed") else []
    d_rows = d_res.get("rows", []) if d_res.get("executed") else []
    r_rows = r_res.get("rows", []) if r_res.get("executed") else []
    nodes = ([_node_from_concept(r) for r in c_rows]
             + [_node_from_episode(r) for r in e_rows]
             + [_node_from_decision(r) for r in d_rows]
             + [_node_from_record(r) for r in r_rows])

    # 엣지: **양끝이 이번 번들 안에 있는 것만**. fragment 는 같은 payload 의 노드로만
    # (from,to) 타입을 해석하므로(fragment.py `_node_types`), 끝점이 빠진 엣지는
    # 수신측에서 조용히 skip 된다 — 애초에 싣지 않는다.
    edges: list[dict] = []
    ids = {n["id"] for n in nodes}
    if ids:
        rel_res = q.execute(q.graph_rows_for_ids_plan(sorted(ids), owner=owner),
                            cfg, store=store)
        # graph_rows_for_ids_plan 은 Concept→Concept 만 본다 → 나머지 조합은 전용
        # 플랜으로 별도 조회(F4 의 Episode ABOUT, v12 의 결정 엣지). apply 측은
        # 무변경 — merge_fragment 가 타입에서 라우팅한다.
        extra_rows: list[dict] = []
        for pair in _EXTRA_EDGE_PAIRS:
            res = q.execute(_extra_edges_plan(sorted(ids), owner=owner, pair=pair),
                            cfg, store=store)
            if res.get("executed"):
                extra_rows.extend(res.get("rows", []))
        # Record 엣지는 타입이 라벨이 아니라 `r.type` 에 있다(_record_edges_plan).
        # 이미 fragment 타입이므로 아래 역맵을 태우지 않고 `ftype` 으로 바로 들어간다.
        for pair in _RECORD_EDGE_PAIRS:
            res = q.execute(_record_edges_plan(sorted(ids), owner=owner, pair=pair),
                            cfg, store=store)
            if res.get("executed"):
                extra_rows.extend(res.get("rows", []))
        seen = set()
        for r in [*rel_res.get("rows", []), *extra_rows]:
            ftype = r.get("ftype") or _REL_TO_FRAGMENT.get(r.get("rel") or "")
            tgt = r.get("target")
            if not ftype or not tgt or tgt not in ids or r["id"] not in ids:
                continue
            key = (r["id"], ftype, tgt)
            if key in seen:
                continue
            seen.add(key)
            e = {"from": r["id"], "to": tgt, "type": ftype}
            if r.get("edge_created"):
                e["created"] = r["edge_created"]
            if ftype == "returns_to":
                # 반려 경로는 근거가 필수다. 동기화가 근거를 버리면 수신측이
                # 동일 엣지를 다시 적재할 수 없으므로 두 필드를 함께 보존한다.
                for field in ("evidence", "confidence"):
                    if r.get(field) is not None:
                        e[field] = r[field]
            edges.append(e)

    # 삭제 표식(S4) — 노드와 같은 커서 공간(sync_seq). 도메인 스코프는 없다(삭제된
    # 노드의 도메인을 알 수 없으니 owner 전체). 수신측이 부활 가드로 안전 적용.
    t_res = (q.execute(_tombstones_delta_plan(owner=owner, since=since), cfg, store=store)
             if with_tombstones else {"executed": False})
    tombstones = [{"node_id": r["node_id"], "label": r["label"],
                   "deleted_at": r.get("deleted_at") or "", "rev": int(r.get("rev") or 0),
                   "_seq": int(r.get("sync_seq") or 0)}
                  for r in (t_res.get("rows", []) if t_res.get("executed") else [])]

    # 엣지 삭제 표식(S4 확장) — 노드 툼스톤과 같은 커서 공간(sync_seq).
    et_res = (q.execute(_edge_tombstones_delta_plan(owner=owner, since=since),
                        cfg, store=store) if with_tombstones else {"executed": False})
    edge_tombstones = [{"src": r["src"], "rel": r["rel"], "dst": r["dst"],
                        "deleted_at": r.get("deleted_at") or "",
                        "_seq": int(r.get("sync_seq") or 0)}
                       for r in (et_res.get("rows", []) if et_res.get("executed") else [])]

    seqs = ([n["_seq"] for n in nodes] + [t["_seq"] for t in tombstones]
            + [t["_seq"] for t in edge_tombstones])
    nxt = max([since] + seqs) if seqs else since
    base.update({"executed": True, "nodes": nodes, "edges": edges,
                 "tombstones": tombstones, "edge_tombstones": edge_tombstones,
                 "next_cursor": nxt,
                 "counts": {"concepts": len(c_rows), "episodes": len(e_rows),
                            "edges": len(edges), "tombstones": len(tombstones),
                            "edge_tombstones": len(edge_tombstones)}})
    return base


# share.py 의 역매핑을 재사용(중복 정의 회피). + F4: ABOUT(Episode→Concept) 편입 —
# _EC_EDGES 는 _CC_EDGES 와 분리 유지(여기 역맵이 Concept 엣지 export 전용이라는
# 전제를 오염시키지 않기 위함), 합류는 이 지점에서만.
_REL_TO_FRAGMENT = {rel: ft for ft, rel in frag._CC_EDGES.items()}
_REL_TO_FRAGMENT.update({rel: ft for ft, rel in frag._EC_EDGES.items()})


#: `graph_rows_for_ids_plan` 이 **못 보는** 끝점 조합 — 그 플랜은 `(c:Concept)-[r]->
#: (d:Concept)` 라 소스가 Concept 이 아니거나 도착이 Concept 이 아니면 안 걸린다.
#: 여기 빠진 조합의 엣지는 번들에 안 실리고, **오류 없이** 기기 간에 사라진다
#: (F4 가 Episode→Concept 에서 겪은 결함이고, v12 의 결정 엣지가 같은 자리다).
_EXTRA_EDGE_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("Episode", "episode_id", "Concept", "concept_id"),      # F4
    ("Episode", "episode_id", "Decision", "decision_id"),    # v12
    ("Decision", "decision_id", "Concept", "concept_id"),
    ("Decision", "decision_id", "Decision", "decision_id"),
    ("Concept", "concept_id", "Decision", "decision_id"),
)


#: Record 참여 엣지는 위 표에 넣을 수 없다. `_extra_edges_plan` 은 `label(r)` 을
#: 뽑아 `_REL_TO_FRAGMENT` 로 되돌리는데, Record 엣지의 라벨은 언제나
#: `ATTACHES_TO` 이고 **원래 관계 타입은 라벨이 아니라 `r.type` 에 실려 있다**
#: (fragment `_merge_edge` 의 Record 분기). 그 표로 보내면 네 갈래가 전부
#: ATTACHES_TO 한 이름으로 뭉개져 수신측에서 되살릴 수 없다. 그래서 전용 플랜이다.
_RECORD_EDGE_PAIRS: tuple[tuple[str, str], ...] = (
    ("Concept", "concept_id"),
    ("Decision", "decision_id"),
)


def _record_edges_plan(ids: list[str], *, owner: str, pair: tuple) -> "q.QueryPlan":
    """번들 안의 Record→(Concept|Decision) ATTACHES_TO 엣지.

    `label(r)` 대신 **`r.type`** 을 fragment 타입으로 돌려준다 — 저장할 때 원 타입을
    거기 보존했으므로 복원도 거기서 한다. 라벨을 명시(`[r:ATTACHES_TO]`)하는 이유는
    `_extra_edges_plan` 처럼 무타입으로 열면 `r.type` 열이 없는 rel 테이블까지 걸려
    Binder 가 죽기 때문이다."""
    dl, dp = pair
    cypher = (
        f"MATCH (a:Record)-[r:ATTACHES_TO]->(b:{dl}) "
        f"WHERE a.record_id IN $ids AND b.{dp} IN $ids "
        "AND a.owner_id = $owner AND b.owner_id = $owner "
        f"RETURN a.record_id AS id, r.type AS ftype, b.{dp} AS target, "
        "r.created AS edge_created"
    )
    return q.QueryPlan(f"sync_edges_Record_{dl}", cypher,
                       {"ids": pkns.keys(owner, ids), "owner": owner},
                       note=f"Record->{dl} ATTACHES_TO edges for sync bundle")


def _extra_edges_plan(ids: list[str], *, owner: str, pair: tuple) -> "q.QueryPlan":
    """번들 내 비-(Concept→Concept) 엣지. 양끝 모두 번들 id 집합 + owner 스코프.

    rel 라벨을 좁히지 않고 `label(r)` 로 뽑는다 — 동기화 대상 여부는 호출부의
    `_REL_TO_FRAGMENT` 가 가린다(IN_DOMAIN 처럼 매핑 없는 것은 거기서 떨어진다).
    삭제 전파는 엣지 툼스톤(_EdgeTombstone, S4 확장)이 담당."""
    sl, sp, dl, dp = pair
    cypher = (
        f"MATCH (a:{sl})-[r]->(b:{dl}) "
        f"WHERE a.{sp} IN $ids AND b.{dp} IN $ids "
        "AND a.owner_id = $owner AND b.owner_id = $owner "
        f"RETURN a.{sp} AS id, label(r) AS rel, b.{dp} AS target, "
        # v14 — 엣지 출생도 나른다. 안 실으면 수신측에서 그 엣지는 신규라
        # **그 기기 시각으로 다시 태어난다** — 노드에서 방금 고친 것과 같은 병이다.
        "r.created AS edge_created"
    )
    return q.QueryPlan(f"sync_edges_{sl}_{dl}", cypher,
                       {"ids": pkns.keys(owner, ids), "owner": owner},
                       note=f"{sl}->{dl} edges for sync bundle")


# ── 청킹 ──────────────────────────────────────────────────────────────────────
def chunk_bundle(bundle: dict, *, limit: int = CHUNK_BYTES) -> list[dict]:
    """번들을 전송 가능한 크기로 쪼갠다. **각 청크는 자기완결** — 엣지는 양끝 노드가
    같은 청크에 있을 때만 싣는다(수신측 fragment 가 payload 내 노드로만 타입 해석).
    끝점이 갈라진 엣지는 마지막 청크에 양끝 노드를 다시 실어 함께 보낸다."""
    nodes = bundle.get("nodes") or []
    edges = bundle.get("edges") or []
    tombs = bundle.get("tombstones") or []
    etombs = bundle.get("edge_tombstones") or []
    if not nodes:
        # 노드가 없어도 엣지·tombstone 은 실어 보낸다(삭제-only sync).
        if not edges and not tombs and not etombs:
            return []
        return [{"nodes": [], "edges": edges, "tombstones": tombs,
                 "edge_tombstones": etombs}]

    by_id = {n["id"]: n for n in nodes}
    chunks: list[dict] = []
    cur: list[dict] = []
    size = 0
    for n in nodes:
        b = len(json.dumps(n, ensure_ascii=False).encode("utf-8"))
        if cur and size + b > limit:
            chunks.append({"nodes": cur, "edges": []})
            cur, size = [], 0
        cur.append(n)
        size += b
    if cur:
        chunks.append({"nodes": cur, "edges": []})

    # 엣지를 양끝이 함께 든 청크에 배치; 갈라진 엣지는 별도 청크(양끝 노드 동봉).
    placed = set()
    for ch in chunks:
        have = {n["id"] for n in ch["nodes"]}
        for e in edges:
            k = (e["from"], e["type"], e["to"])
            if k not in placed and e["from"] in have and e["to"] in have:
                ch["edges"].append(e)
                placed.add(k)
    spill = [e for e in edges if (e["from"], e["type"], e["to"]) not in placed]
    if spill:
        need = {e["from"] for e in spill} | {e["to"] for e in spill}
        chunks.append({"nodes": [by_id[i] for i in sorted(need) if i in by_id],
                       "edges": spill})
    # tombstone(노드·엣지)은 작아 첫 청크에 전부 싣는다(한 번만 적용되게).
    if tombs:
        chunks[0]["tombstones"] = tombs
    if etombs:
        chunks[0]["edge_tombstones"] = etombs
    return chunks


# ── apply (3-way 병합, S3) ────────────────────────────────────────────────────
def _local_snapshot(store, label: str, pk: str, nid: str,
                    owner: str = "") -> dict | None:
    """로컬 노드의 sync 필드 + rev/updated_at. 없으면 None."""
    cols = ", ".join(f"x.{c} AS {c}" for c in SYNC_FIELDS[label])
    rows = store.run(f"MATCH (x:{label}) WHERE x.{pk} = $id "
                     f"RETURN {cols}, x.rev AS rev, x.updated_at AS updated_at",
                     {"id": pkns.key(owner, nid)})
    if not rows:
        return None
    r = rows[0]
    snap = _snapshot(label, r)
    snap["_rev"] = int(r.get("rev") or 0)
    snap["_ua"] = r.get("updated_at") or ""
    return snap


#: 출생(`created`) 열을 가진 동기화 라벨. Episode 에는 그 열이 **없다**(2026-08-17
#: 실엔진 확인) — 넣으면 아래 문장이 Binder 로 죽는다.
_BIRTH_LABELS = ("Concept", "Decision", "Record")


def _reconcile_birth(store, label: str, pk: str, nid: str, born: str, owner: str) -> None:
    """노드 출생 시각을 **가장 이른 값으로** 수렴시킨다.

    왜 3-way 병합이 아닌가: 출생은 편집이 아니라서 "충돌"이 뜻을 갖지 않는다. min 은
    교환·결합·멱등이라 어느 순서로 동기화하든 모든 기기가 같은 값에 닿고 타이브레이크가
    필요 없다. 뜻도 정직하다 — 어느 기기든 이 노드를 **처음 본 시각**이 그 노드가 이
    공간에 들어온 때의 최선 추정이다. 빈 값은 이기지 못하므로, 출생을 모르는 기기는
    들은 값을 받아들인다.

    **모든 인바운드 노드에** 부른다(쓰기가 일어난 것만이 아니라). 병합 결과가 로컬과
    같으면(`skipped_stale`) 쓰기 자체가 없는데, 그때 건너뛰면 이미 갈라진 출생이 영영
    만나지 못한다.

    ⚠ `IS NULL` 은 방어적 군더더기가 아니다: 문서 추출 경로가 추적열을 안 찍던 시절의
    행이 NULL 을 갖고 있다(af7a973 이전). owner 술어도 필수다 — merge_fragment 의
    SEC-3 가드가 교차테넌트 노드를 건너뛰어도 여기서 id 로만 MATCH 하면 남의 행을
    고친다(risk-audit 2026-08-05 §3.2, 위 스탬프 루프와 같은 이유)."""
    if not born or label not in _BIRTH_LABELS:
        return
    store.run(
        f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $owner "
        "AND (x.created IS NULL OR x.created = '' OR x.created > $born) "
        "SET x.created = $born",
        {"id": pkns.key(owner, nid), "owner": owner, "born": born})


def _merged_fragment_node(label: str, nid: str, ntype: str, merged: dict) -> dict:
    """병합 결과(snapshot)를 merge_fragment 가 쓸 수 있는 노드로. props=dict, tags=list."""
    node = {"id": nid, "type": ntype}
    node.update(merged)   # title/domain/tags/props(+date/summary) — 이미 raw 표현
    return node


def apply_delta(*, owner: str, bundle: dict, cfg=None, store=None) -> dict:
    """delta 번들을 **필드단위 3-way 병합**으로 적용(S3 — 발주자 핵심 요구).

    각 노드: base(마지막 동기화값) 대비 local·remote 를 필드별로 병합한다. 서로 다른
    필드를 고쳤으면 **둘 다 보존**, 같은 필드 충돌만 노드 타이브레이크(updated_at→rev→id)
    로 최신승. 병합 결과가 로컬과 달라졌으면 새 HLC 로 스탬프해 push 가 집어가게 하고,
    **base 는 원격값으로** 갱신한다 — 그래야 다음 push 가 로컬-only 필드만 감지한다
    (에코 완전 제거). 스탬프 규칙(§S2)은 그대로: sync_seq 는 수신측 자기 번호.
    """
    out = {"executed": False, "applied": 0, "skipped_stale": 0, "merged": 0,
           "edges": 0, "deleted": 0, "resurrected": 0, "kept_deleted": 0, "warnings": []}
    if store is None:
        out["note"] = "no reachable store"
        return out

    writes: list[dict] = []          # merge_fragment 로 쓸 노드(스탬프 포함)
    base_updates: list[tuple] = []   # (nid, label, base_fields, base_rev, base_ua)
    births: list[tuple] = []         # (label, pk, nid, remote_created) — 병합과 무관
    for n in bundle.get("nodes") or []:
        nid, ntype = n.get("id"), n.get("type")
        label = _SYNC_TYPES.get(ntype or "")
        if not nid or not label:
            out["warnings"].append(f"unsupported node — skipped ({nid or '?'})")
            continue
        pk = frag._NODE_MAP[ntype][1]
        remote_snap = _snapshot(label, n)
        r_rev, r_ua = int(n.get("_rev") or 0), n.get("_updated_at") or ""

        # 부활 가드(S4): 로컬 tombstone 이 인바운드 편집보다 **새롭거나 같으면** 삭제가
        # 이긴다 → upsert 무시(지운 노드가 옛 편집으로 되살아나지 않게). 인바운드가 더
        # 최신이면(삭제 후 재생성) tombstone 을 걷고 적용한다.
        tomb = get_tombstone(store, nid, owner)
        if tomb:
            if tomb["deleted_at"] >= r_ua:
                out["skipped_stale"] += 1
                # 따로도 센다: 이 노드는 받는 쪽에 **없다**. 모드 전환의 대조가 이것을
                # "이미 있다"와 섞으면 사라진 노드를 무손실로 읽는다(VIN-130).
                out["kept_deleted"] += 1
                continue
            del_tombstone(store, nid, owner)
            out["resurrected"] += 1

        # 삭제가 이긴 노드는 위에서 이미 continue 했다 — 지운 노드의 열을 되살리지
        # 않으려고 여기서 모은다(부활한 경우는 살아났으므로 포함이 맞다).
        births.append((label, pk, nid, n.get("_created") or ""))

        local = _local_snapshot(store, label, pk, nid, owner)

        if local is None:                       # 신규 — 원격 그대로, 스탬프 보존
            writes.append({"node": _merged_fragment_node(label, nid, ntype, remote_snap),
                           "ua": r_ua, "rev": r_rev})
            base_updates.append((nid, label, remote_snap, r_rev, r_ua))
            continue

        l_rev, l_ua = local.pop("_rev"), local.pop("_ua")
        base = get_base(store, nid, owner)
        # base 없음 = 공통 조상 없음(양쪽 독립 존재) → 빈 조상: 모든 필드가 '양쪽 변경'
        # 으로 취급돼 값이 다르면 필드별 LWW. dict(local) 로 두면 push 후·pull 전 로컬
        # 편집이 조상에 섞여 유실된다(그 편집이 원격과 충돌 시). 빈 조상이 안전측.
        base_fields = base["fields"] if base else {}
        remote_newer = syncmerge.node_remote_newer(l_rev, l_ua, r_rev, r_ua, nid)
        merged, conflicts = syncmerge.three_way(base_fields, local, remote_snap,
                                                remote_newer=remote_newer)

        if not syncmerge.maps_equal(merged, local):
            # 병합이 로컬을 바꿈 → 새 상태로 스탬프(신규 HLC·rev+1). push 가 집어간다.
            writes.append({"node": _merged_fragment_node(label, nid, ntype, merged),
                           "ua": revstamp.hlc_now(store),
                           "rev": max(l_rev, r_rev) + 1})
            out["merged"] += 1
        else:
            out["skipped_stale"] += 1           # 로컬에 반영할 변화 없음
        # base 는 항상 원격이 가진 값으로 — 다음 push 가 로컬-only 필드만 delta 로 본다.
        base_updates.append((nid, label, remote_snap, r_rev, r_ua))
        revstamp.observe_hlc(store, r_ua)

    if writes or bundle.get("edges"):
        # stamp_edges=False — 원격발 엣지를 끝점 스탬프하면 다음 push 로 무변경 노드가
        # 에코된다(엣지 전파 스탬프는 **원 저작 기기**에서만, fragment 주석 참조).
        res = frag.merge_fragment(
            {"owner": owner, "nodes": [w["node"] for w in writes],
             "edges": bundle.get("edges") or []}, store=store, stamp_edges=False)
        out["executed"] = bool(res.get("executed"))
        out["edges"] = (res.get("merged") or {}).get("edges", {}).get("created", 0)
        out["warnings"] += res.get("warnings") or []
    else:
        out["executed"] = True

    # merge_fragment 가 방금 로컬 스탬프를 찍었으므로, 결정된 스탬프로 덮어쓴다.
    # owner 술어 필수(risk-audit 2026-08-05 §3.2): merge_fragment 의 SEC-3 가드가
    # 교차테넌트 노드를 건너뛰어도 이 루프가 owner 없이 id 로만 MATCH 하면 그 노드의
    # rev/sync_seq 를 그대로 찍어 버려 피해자의 3-way 병합 기준을 조용히 오염시킨다.
    for w in writes:
        node = w["node"]
        label, pk = frag._NODE_MAP[node["type"]]
        store.run(f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $owner "
                  "SET x.updated_at = $ua, x.rev = $rev, x.sync_seq = $seq",
                  {"id": pkns.key(owner, node["id"]), "owner": owner,
                   "ua": w["ua"], "rev": int(w["rev"]),
                   "seq": revstamp.next_seq(store, owner)})
    # 출생 화해는 **스탬프 루프 뒤**다: 신규 노드는 방금 merge_fragment 가 수신측
    # 시각으로 `created` 를 찍었으므로(fragment `_merge_node`), 그 위에 min 을 얹어야
    # 원본의 출생이 남는다.
    for label, pk, nid, born in births:
        _reconcile_birth(store, label, pk, nid, born, owner)
    for nid, label, fields, rev, ua in base_updates:
        set_base(store, nid, label, fields, rev, ua, owner)

    # 삭제 전파(S4): 인바운드 tombstone 을 적용한다. 단 **노드가 삭제보다 나중에
    # 편집됐으면**(updated_at > deleted_at) 삭제를 무시한다(다른 기기의 재편집이 이김).
    # 적용 시 자기 tombstone 을 남겨(put_tombstone) 다른 기기로 재전파한다.
    # owner 술어 필수(risk-audit §3.2): 없으면 유효한 멤버 키 하나로 남의 concept 을
    # 지울 수 있다. deleted_at 은 clamp — 인바운드 값이라 공격자가 미래로 조작해
    # 어떤 재편집도 이기게 만들 수 있으니 서버 클록을 넘지 못하게 한다.
    for t in bundle.get("tombstones") or []:
        tid, tlabel = t.get("node_id"), t.get("label")
        t_da = t.get("deleted_at") or ""
        if t_da:
            t_da = min(t_da, revstamp.hlc_now(store))
        # 라벨→PK 를 손으로 적지 않는다 — 동기화 라벨이 늘 때 여기가 빠지면 그
        # 라벨의 삭제가 조용히 전파되지 않는다(v12 Decision 이 그 자리였다).
        pk = _SYNC_PK.get(tlabel or "")
        if not tid or not pk:
            continue
        cur = store.run(f"MATCH (x:{tlabel}) WHERE x.{pk} = $id AND x.owner_id = $owner "
                        "RETURN x.updated_at AS ua",
                        {"id": pkns.key(owner, tid), "owner": owner})
        if cur and (cur[0].get("ua") or "") > t_da:
            continue                          # 삭제 이후 재편집됨 → 삭제 무시
        if cur:
            store.run(f"MATCH (x:{tlabel}) WHERE x.{pk} = $id AND x.owner_id = $owner "
                      "DETACH DELETE x",
                      {"id": pkns.key(owner, tid), "owner": owner})
            out["deleted"] += 1
        del_base(store, tid, owner)
        put_tombstone(store, tid, tlabel, owner, t_da, int(t.get("rev") or 0))
        revstamp.observe_hlc(store, t_da)

    # 엣지 삭제 전파(S4 확장): 엣지는 타임스탬프가 없어 노드식 재편집 가드가 불가 —
    # 삭제를 무조건 적용한다. '재생성이 삭제를 이긴다'는 별도 경로로 성립:
    # 재생성 기기가 끝점을 스탬프(fragment._stamp_edge_endpoints — 로컬 엣지 툼스톤
    # 상쇄 포함)해 다음 delta 에 엣지가 실려 되돌아온다.
    # owner 술어 필수(risk-audit §3.2): 양 끝 모두 owner 스코프 — 안 그러면 남의
    # 관계를 지울 수 있다. _extra_edges_plan 과 같은 술어 패턴.
    for t in bundle.get("edge_tombstones") or []:
        src, rel, dst = t.get("src"), t.get("rel"), t.get("dst")
        spec = q.sync_edge_spec(rel or "")
        if not spec or not src or not dst:
            continue
        carrier, subtype, ep = spec
        qualifier = " AND r.type = $subtype" if subtype is not None else ""
        extra = {"subtype": subtype} if subtype is not None else {}
        pairs = [(src, dst)]
        if rel == "RELATED_TO":   # 무방향 의미 — 물리 역방향도 함께(delete_edge 동형)
            pairs.append((dst, src))
        # 끝점 라벨 조합 전수(v12 — rel 하나에 조합이 여럿). delete_edge 와 동형.
        for sl, sp, dl, dp in ep:
            for s_, d_ in pairs:
                cnt = store.run(f"MATCH (a:{sl})-[r:{carrier}]->(b:{dl}) "
                                f"WHERE a.{sp} = $s AND b.{dp} = $d "
                                f"AND a.owner_id = $owner AND b.owner_id = $owner{qualifier} "
                                "RETURN count(r) AS c",
                                {"s": pkns.key(owner, s_),
                                 "d": pkns.key(owner, d_), "owner": owner, **extra})
                if cnt and int(cnt[0].get("c") or 0):
                    store.run(f"MATCH (a:{sl})-[r:{carrier}]->(b:{dl}) "
                              f"WHERE a.{sp} = $s AND b.{dp} = $d "
                              f"AND a.owner_id = $owner AND b.owner_id = $owner{qualifier} DELETE r",
                              {"s": pkns.key(owner, s_),
                               "d": pkns.key(owner, d_), "owner": owner, **extra})
                    out["edges_deleted"] = out.get("edges_deleted", 0) + 1
        put_edge_tombstone(store, src, rel, dst, owner, t.get("deleted_at") or "")
        revstamp.observe_hlc(store, t.get("deleted_at") or "")

    out["applied"] = len(writes)
    return out


# ── 오케스트레이터 (S2) ───────────────────────────────────────────────────────
# 로컬 사이드카가 원격(mcp.vincs.io)에 대해 pull/push 를 조율한다. 원격 호출은
# 주입(remote 객체)이라 인프로세스 스텁과 실 HTTP 양쪽으로 검증된다.
#
# 커서 2개(서로 다른 seq 공간):
#   cursor:push — **로컬** sync_seq. 이후 로컬 변경분만 push.
#   cursor:pull — **허브** sync_seq. 이후 허브 변경분만 pull.
#
# echo: pull 로 받은 노드는 로컬 seq 가 새로 매겨져 다음 push 에 실릴 수 있으나,
# updated_at/rev 를 보존하므로 허브 stale 가드가 되돌림을 막는다(무손상, 헛트래픽만).
# 그 헛트래픽은 S3 의 base(로컬≠base 일 때만 push)가 완전히 없앤다.


class RemoteHTTP:
    """syncclient 를 감싼 원격 어댑터(프로덕션). 회원 키·기본 mcp.vincs.io."""

    def __init__(self, token: str, base: str | None = None):
        self.token = token
        self.base = base or "https://mcp.vincs.io"

    def pull(self, since: int, everything: bool = False) -> dict:
        from vinc_mcp import syncclient
        return syncclient.pull(since=since, token=self.token, base=self.base,
                               everything=everything)

    def push(self, chunks: list[dict]) -> dict:
        from vinc_mcp import syncclient
        return syncclient.push(chunks, token=self.token, base=self.base)

    def docs_pull(self, since: int, everything: bool = False) -> dict:
        from vinc_mcp import syncclient
        return syncclient.docs_pull(since=since, token=self.token, base=self.base,
                                    everything=everything)

    def docs_push(self, payload: dict) -> dict:
        from vinc_mcp import syncclient
        return syncclient.docs_push(payload, token=self.token, base=self.base)


def push_local(store, cfg, *, owner: str, remote) -> dict:
    """로컬 delta 를 원격에 push. 커서는 **push 성공 후에만** 전진(중단 시 재전송).

    push 성공 후 보낸 노드의 **base 를 로컬 현재값으로** 갱신한다(S3). 이유: base 는
    '마지막으로 원격과 합의한 값'이라야 3-way 가 이후 로컬 편집만 delta 로 잡는다.
    안 하면 로컬-only 편집이 매 sync 재전송되거나(에코), push 후·pull 전 편집이 조상에
    섞여 유실될 수 있다."""
    since = get_cursor(store, "push")
    bundle = export_delta(owner=owner, since=since, cfg=cfg, store=store)
    if not bundle.get("executed"):
        return {"phase": "push", "executed": False, "note": bundle.get("note")}
    nodes = bundle.get("nodes") or []
    tombs = bundle.get("tombstones") or []
    if not nodes and not (bundle.get("edges") or []) and not tombs:
        return {"phase": "push", "executed": True, "sent": 0, "deletes": 0, "cursor": since}
    res = remote.push(chunk_bundle(bundle))
    for n in nodes:                       # base := 방금 보낸(=원격과 합의된) 값
        label = _SYNC_TYPES.get(n.get("type") or "")
        if label:
            set_base(store, n["id"], label, _snapshot(label, n),
                     int(n.get("_rev") or 0), n.get("_updated_at") or "")
    set_cursor(store, "push", bundle["next_cursor"])
    return {"phase": "push", "executed": True, "sent": len(nodes), "deletes": len(tombs),
            "remote": res, "cursor": bundle["next_cursor"]}


def pull_apply(store, cfg, *, owner: str, remote) -> dict:
    """원격 delta 를 페이지 단위로 받아 apply. 커서는 **apply·전진이 내구된 뒤에만**."""
    since = get_cursor(store, "pull")
    applied = skipped = deleted = pages = 0
    warnings: list[str] = []
    while pages < 1000:                       # 안전 상한(무한루프 방어)
        page = remote.pull(since)
        nodes = page.get("nodes") or []
        tombs = page.get("tombstones") or []
        r = apply_delta(owner=owner, bundle=page, cfg=cfg, store=store)
        applied += r["applied"]
        skipped += r["skipped_stale"]
        deleted += r.get("deleted", 0)
        warnings += r.get("warnings") or []
        pages += 1
        nxt = int(page.get("next_cursor", since))
        advanced = nxt > since
        if advanced:
            since = nxt
            set_cursor(store, "pull", since)   # 페이지별 내구 전진
        if (not nodes and not tombs) or not advanced:
            break
    return {"phase": "pull", "applied": applied, "skipped_stale": skipped,
            "deleted": deleted, "pages": pages, "cursor": since, "warnings": warnings}


# ── 문서 정본 동기화 국면(D45 P3 배선) ────────────────────────────────────────
# docsync(엔진)는 완성돼 있었는데 **아무도 부르지 않았다** — sync.run 은 노드·엣지만
# 다뤘고 허브에는 /sync/docs-* 라우트가 없어, [지금 동기화]가 문서를 한 번도 나르지
# 않았다(에러도 없이). 커서는 노드와 **별도 키**(docs-push/docs-pull)다:
# _DocMeta.sync_seq 는 노드와 같은 owner 카운터에서 나오므로, 노드 커서를 같이 쓰면
# 노드 트래픽이 커서를 먼저 전진시켜 문서 행이 영영 delta 에 안 실린다(과거
# "pull 100% 빈값" 사고와 같은 모양의 함정).


def _chunk_docs(payload: dict, max_bytes: int = 1_500_000) -> list[dict]:
    """docs 를 크기 기준으로 쪼갠다(허브 MAX_BODY 4MB 보호 — 초과는 빈 바디 400 으로
    **조용히** 실패한다). 각 조각은 자기완결 LWW 라 재전송·순서 뒤바뀜에 안전하고,
    tombstones 는 마지막 조각에 싣는다."""
    docs = payload.get("docs") or []
    groups: list[list[dict]] = [[]]
    size = 0
    for d in docs:
        n = len((d.get("text") or "").encode("utf-8")) + 512
        if groups[-1] and size + n > max_bytes:
            groups.append([])
            size = 0
        groups[-1].append(d)
        size += n
    out = [{"docs": g, "tombstones": []} for g in groups]
    out[-1]["tombstones"] = payload.get("tombstones") or []
    return out


def push_docs(store, cfg, *, owner: str, remote) -> dict:
    """문서 정본 delta 를 원격에 push(doc 단위 LWW). 커서는 **전송 성공 후에만** 전진."""
    from . import docsync
    since = get_cursor(store, "docs-push")
    payload = docsync.export_docs(store, owner=owner, since=since)
    docs = payload.get("docs") or []
    tombs = payload.get("tombstones") or []
    if not docs and not tombs:
        return {"phase": "docs-push", "executed": True, "sent": 0, "deletes": 0,
                "cursor": since}
    agg = {"applied": 0, "updated": 0, "skipped": 0, "deleted": 0}
    for ch in _chunk_docs(payload):
        res = remote.docs_push(ch)
        for k in agg:
            agg[k] += int(res.get(k) or 0)
    cursor = int(payload.get("cursor") or since)
    set_cursor(store, "docs-push", cursor)
    return {"phase": "docs-push", "executed": True, "sent": len(docs),
            "deletes": len(tombs), "remote": agg, "cursor": cursor}


def pull_docs(store, cfg, *, owner: str, remote) -> dict:
    """원격 문서 delta 를 받아 적용(LWW). 커서는 **적용 뒤에만** 전진.

    embedding 은 여기서 재생성하지 않는다 — 허브 docs-apply 와 같은 태도다(정본
    텍스트 정합이 우선, FTS 는 즉시·벡터는 후속 재임베딩이 채운다)."""
    from . import docsync
    since = get_cursor(store, "docs-pull")
    page = remote.docs_pull(since)
    r = docsync.import_docs(store, owner=owner, payload=page)
    nxt = int(page.get("cursor") or since)
    if nxt > since:
        set_cursor(store, "docs-pull", nxt)
    return {"phase": "docs-pull", "executed": True,
            "applied": r.get("applied", 0), "updated": r.get("updated", 0),
            "skipped": r.get("skipped", 0), "deleted": r.get("deleted", 0),
            "cursor": max(nxt, since)}


# ── 모드 전환의 데이터 이동(VIN-130) ─────────────────────────────────────────
# 데스크톱에는 계속 도는 동기화가 없다. 데이터는 **모드를 바꿀 때만**, 사람이 확인한 뒤
# 한 번 옮긴다: 로컬 → 계정은 이 기기의 전부를 올리고, 계정 → 로컬은 계정 전부를 새
# 폴더로 받는다. 근거: [[decision:the-desktop-signs-in-and-shows-the-account-graph-live-and-local-mode-is-a-setting]]


def owner_counts(store, owner: str) -> dict:
    """이 소유자의 노드(동기화 라벨별)와 문서 수. 옮기기 전후를 대조하는 숫자다.
    관례 문서(GRAPH.md)는 세지 않는다: 동기화가 일부러 안 나른다(`docsync` 주석)."""
    from . import graphmd
    out: dict = {}
    for label in _SYNC_LABELS:
        rows = store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c",
                         {"o": owner})
        out[label] = int((rows[0].get("c") if rows else 0) or 0)
    rows = store.run("MATCH (d:_DocMeta) WHERE d.owner_id = $o RETURN d.doc_id AS id",
                     {"o": owner})
    out["docs"] = sum(1 for r in rows or []
                      if not graphmd.is_conventions_doc_id(r.get("id") or ""))
    out["nodes"] = sum(out[lbl] for lbl in _SYNC_LABELS)
    # 동기화가 나르지 않는 라벨(개방 타입의 Entity 캐리어·Bridge). 옮기기가 이것들을
    # "무손실"에 섞어 세면 안 된다: 폴더에는 남지만 다른 쪽으로는 안 간다. 따로 알린다.
    left = 0
    for label in _UNSYNCED_LABELS:
        try:
            rows = store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c",
                             {"o": owner})
        except Exception:                    # noqa: BLE001 — 옛 스키마엔 표가 없을 수 있다
            continue
        left += int((rows[0].get("c") if rows else 0) or 0)
    out["left_behind"] = left
    return out


#: owner_id 가 있지만 동기화가 나르지 않는 라벨(`fragment._OWNER_SCOPED_LABELS` 의 나머지).
_UNSYNCED_LABELS = tuple(lbl for lbl in frag._OWNER_SCOPED_LABELS if lbl not in _SYNC_LABELS)


def push_everything(store, cfg, *, owners: list[str], remote) -> dict:
    """로컬 모드 → 계정 모드: 이 기기의 **모든 주제**를 계정으로 올린다.

    델타 동기화(`push_local`)와 다른 점 셋:
      - 주제를 가리지 않고(주제 없는 노드 포함) 순번도 보지 않는다(문서는 전부).
      - 삭제 표식을 싣지 않는다. 옮기기는 더하는 일이지, 이 기기에서 지운 이력으로
        계정의 것을 지우는 일이 아니다.
      - 이 스토어에 **아무것도 쓰지 않는다**(커서·base 그대로). 옮긴 뒤에도 폴더는 손대지
        않은 채 남고, 다시 로컬 모드로 오면 그대로 열린다.

    무손실은 수로 확인한다. 노드 하나는 계정에서 반영되거나(`applied`), 계정 쪽이 이미
    같거나 더 새것이라 건너뛴다(`skipped_stale`). 둘 다 계정에 남아 있다는 뜻이다. 문서도
    같다(`applied`·`updated`·`skipped`). 세어 둔 수·보낸 수·받았다는 수가 어긋나면
    `missing` 에 남고, 부르는 쪽은 모드를 바꾸지 않는다."""
    from . import docsync
    rep: dict = {"phase": "push-all", "executed": False, "owners": {}, "nodes": 0,
                 "edges": 0, "docs": 0, "missing": 0, "kept_deleted": 0, "left_behind": 0,
                 "warnings": []}
    for owner in owners:
        before = owner_counts(store, owner)
        bundle = export_delta(owner=owner, since=0, everything=True, with_tombstones=False,
                              cfg=cfg, store=store)
        if not bundle.get("executed"):
            raise RuntimeError(bundle.get("note") or "could not read this device's graph")
        nodes = bundle.get("nodes") or []
        edges = bundle.get("edges") or []
        chunks = chunk_bundle(bundle)
        # 끝점이 갈라진 엣지의 청크는 노드를 한 번 더 싣는다. 받았다는 수는 청크마다
        # 세므로 대조도 청크에 실린 수로 한다(노드 수로 하면 넘친 만큼 빠진 것을 가린다).
        carried = sum(len(ch.get("nodes") or []) for ch in chunks)
        node_res: dict = {}
        if chunks:
            node_res = remote.push(chunks)
        docs = docsync.export_docs(store, owner=owner, everything=True).get("docs") or []
        doc_res = {"applied": 0, "updated": 0, "skipped": 0}
        if docs:
            for ch in _chunk_docs({"docs": docs, "tombstones": []}):
                res = remote.docs_push(ch)
                for k in doc_res:
                    doc_res[k] += int(res.get(k) or 0)
        # 계정에서 먼저 지워진 노드(삭제가 이 기기의 마지막 편집보다 새것)는 계정의 삭제가
        # 이긴다. 받았다는 수에 들지만 계정에는 없으므로 따로 알린다.
        kept_deleted = int(node_res.get("kept_deleted") or 0)
        acked_nodes = (int(node_res.get("applied") or 0)
                       + int(node_res.get("skipped_stale") or 0) - kept_deleted)
        acked_docs = sum(doc_res.values())
        missing = (max(0, before["nodes"] - len(nodes))
                   + max(0, carried - kept_deleted - acked_nodes)
                   + max(0, before["docs"] - len(docs)) + max(0, len(docs) - acked_docs))
        rep["left_behind"] += int(before.get("left_behind") or 0)
        rep["owners"][owner] = {"before": before, "sent_nodes": len(nodes),
                                "sent_edges": len(edges), "sent_docs": len(docs),
                                "acked_nodes": acked_nodes, "acked_docs": acked_docs,
                                "kept_deleted": kept_deleted, "missing": missing}
        rep["kept_deleted"] += kept_deleted
        rep["nodes"] += len(nodes)
        rep["edges"] += len(edges)
        rep["docs"] += len(docs)
        rep["missing"] += missing
        rep["warnings"] += node_res.get("warnings") or []
    rep["executed"] = True
    return rep


def pull_everything(store, cfg, *, owner: str, remote) -> dict:
    """계정 모드 → 로컬 모드: 계정 그래프 전부를 이 기기의 (새) 폴더로 받는다.

    노드는 페이지로 받고(`everything` — 주제 없는 노드 포함), 문서는 순번과 무관하게 한
    번에 받는다. 삭제 표식은 싣지 않는다(빈 폴더를 채우는 일이다). 옛 허브가 `everything`
    을 모르면 노드는 주제 있는 것만 오고(`nodes_mode: "topics"`), 문서는 델타 페이지로
    물러서서 받는다(그때 순번 0 인 옛 문서는 못 받는다 — `docs_mode: "delta"`). 부르는
    쪽은 둘 중 하나라도 전량이 아니면 "전부 받았다"고 말하면 안 된다. 무손실 대조는
    `push_everything` 과 같다."""
    from . import docsync
    since = pages = received = applied = skipped = kept_deleted = 0
    nodes_mode = "everything"
    warnings: list[str] = []
    while pages < 1000:                       # 안전 상한(무한루프 방어)
        page = remote.pull(since, everything=True)
        if not page.get("everything"):
            nodes_mode = "topics"             # 옛 허브 — 주제 없는 노드는 안 왔다
        nodes = page.get("nodes") or []
        received += len(nodes)
        r = apply_delta(owner=owner, bundle={**page, "tombstones": [], "edge_tombstones": []},
                        cfg=cfg, store=store)
        applied += int(r.get("applied") or 0)
        skipped += int(r.get("skipped_stale") or 0)
        kept_deleted += int(r.get("kept_deleted") or 0)
        warnings += r.get("warnings") or []
        pages += 1
        nxt = int(page.get("next_cursor", since) or since)
        if not nodes or nxt <= since:
            break
        since = nxt
    first = remote.docs_pull(0, everything=True)
    docs = list(first.get("docs") or [])
    docs_mode = "everything"
    if not first.get("everything"):
        docs_mode = "delta"                   # 옛 허브 — 커서가 멈출 때까지 페이지로
        cur = int(first.get("cursor") or 0)
        for _ in range(1000):
            if cur <= 0:
                break
            page = remote.docs_pull(cur)
            more = page.get("docs") or []
            nxt = int(page.get("cursor") or cur)
            docs += more
            if not more or nxt <= cur:
                break
            cur = nxt
    dres = docsync.import_docs(store, owner=owner, payload={"docs": docs, "tombstones": []})
    acked_docs = sum(int(dres.get(k) or 0) for k in ("applied", "updated", "skipped"))
    missing = max(0, received - (applied + skipped)) + max(0, len(docs) - acked_docs)
    return {"phase": "pull-all", "executed": True, "received_nodes": received,
            "applied": applied, "skipped_stale": skipped, "kept_deleted": kept_deleted,
            "pages": pages, "nodes_mode": nodes_mode,
            "received_docs": len(docs), "acked_docs": acked_docs, "docs_mode": docs_mode,
            "missing": missing, "after": owner_counts(store, owner), "warnings": warnings}


#: 일회성 전량 왕복 표식. 다음에 또 한 번이 필요하면 **키 이름을 올린다**(v2) —
#: 이력이 남고, 이미 돈 기기가 다시 돌지 않는다.
_ONESHOT_KEY = "oneshot:birth-resync-v1"


def ensure_birth_resync(store) -> bool:
    """이 스토어에서 **딱 한 번**, 커서 둘을 0 으로 되돌린다. 되돌렸으면 True.

    왜 필요한가: `_reconcile_birth` 는 새 발산을 막을 뿐, **이미 갈라진 행은 못 고친다**
    — delta 는 커서 이후 변경분만 나르는데 그 노드들은 안 변했다. 그래서 한 번은
    전량을 오가게 해야 한다.

    왜 커서 리셋인가(전용 '출생 교환' 프로토콜 대신): 전량 왕복은 **최초 동기화가
    이미 하는 일**이라 코드도 배선도 새로 필요 없다. 값이 실제로 바뀌는 노드가 없으면
    apply 는 3-way 에서 `skipped_stale` 로 흘러가고 쓰기는 `_reconcile_birth` 뿐이다.

    왜 **둘 다** 0 인가: 수렴이 양방향이라야 한다. pull 만 0 이면 이 기기가 허브의
    이른 출생을 받아들이지만, 이 기기가 가진 더 이른 출생은 허브로 안 간다(push 는
    안 변한 노드를 안 싣는다). `run` 이 pull → push 순이므로 한 세션에 둘 다 돈다:
    허브 것을 받아 min 을 취하고, 그 결과를 도로 올린다.

    표식은 **세션이 끝난 뒤** 찍는다(`run`). 중간에 죽으면 커서가 0 인 채 남아 다음
    세션이 그대로 다시 한다 — 멱등이라 재실행이 해롭지 않다."""
    if revstamp._meta_get(store, _ONESHOT_KEY, "") == "done":
        return False
    set_cursor(store, "push", 0)
    set_cursor(store, "pull", 0)
    return True


def run(store, cfg, *, owner: str, remote) -> dict:
    """한 동기화 세션: **pull 먼저**(원격 최신을 받아 병합 후 push — lost-update 창
    최소, 플랜 §6-1) → push. 문서 국면은 노드 국면 **뒤**에 둔다 — put_doc 의
    재링크(MENTIONS)가 방금 받은 개념을 볼 수 있어야 한다. 잡 워커에서 돌아 로컬
    쓰기가 단일 writer 로 직렬화된다.

    문서 국면은 **soft-fail** 이다: 구 허브(문서 라우트 없음)나 doc 전용 오류가
    노드 동기화까지 무너뜨리면 안 된다 — 결과에 executed:False 로 남긴다."""
    switched = reset_cursors_on_account_change(store, remote)
    resynced = ensure_birth_resync(store)
    pu = pull_apply(store, cfg, owner=owner, remote=remote)
    docs_ok = hasattr(remote, "docs_pull") and hasattr(remote, "docs_push")
    if docs_ok:
        try:
            dpu = pull_docs(store, cfg, owner=owner, remote=remote)
        except Exception as e:
            dpu = {"phase": "docs-pull", "executed": False, "error": str(e)}
    else:
        dpu = {"phase": "docs-pull", "executed": False, "note": "remote lacks doc sync"}
    ph = push_local(store, cfg, owner=owner, remote=remote)
    if docs_ok:
        try:
            dph = push_docs(store, cfg, owner=owner, remote=remote)
        except Exception as e:
            dph = {"phase": "docs-push", "executed": False, "error": str(e)}
    else:
        dph = {"phase": "docs-push", "executed": False, "note": "remote lacks doc sync"}
    if resynced:
        # 여기까지 왔으면 전량이 양방향으로 한 번 오갔다 — 다시 할 이유가 없다.
        revstamp._meta_set(store, _ONESHOT_KEY, "done")
    return {"executed": True, "pull": pu, "push": ph,
            "docs_pull": dpu, "docs_push": dph, "birth_resync": resynced,
            "account_switched": switched}
