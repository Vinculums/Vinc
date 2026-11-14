# -*- coding: utf-8 -*-
"""로컬 스토어의 옛 소유자 `user:local` 을 `tenant:local` 로 접는다(VIN-131).

이 기기 스토어의 소유자는 하나여야 한다. 앱·fragment 기본값·profile.yml·PK 정규화 표는
모두 `tenant:local` 인데 로컬 MCP 브리지만 `user:local` 로 썼다. 읽기는 전부
`owner_id = $owner` 로 거르므로, MCP 로 쓴 노드와 문서는 같은 스토어에 있으면서 앱
화면에는 영영 0건이었다(2026-09-28 재현). 브리지 기본값은 고쳤고, 이미 쓰인 행은 여기서
옮긴다. 근거: [[vinc.plan.desktop-account-mode §6.3]]

무엇을 옮기나:
  - `owner_id` 열을 가진 노드 표 전부(스키마 파생, purge 와 같은 목록)와 소유 메타 넷
    (`_Tombstone`·`_EdgeTombstone`·`_DocMeta`·`_DocTombstone`): 값만 바꾼다
  - `_MergeRejection`: 키(`pair_key`)에 소유자가 들어 있어 새 키로 옮겨 적고 옛 행을 지운다
  - `seq:<owner>` 카운터: 둘 중 큰 값을 `tenant:local` 에 남긴다. 옮겨 온 행의 sync_seq
    보다 새 쓰기가 늘 위에 오게 하려는 것이다
  - **빈 owner(''·NULL) 노드 행**(VIN-234): SEC-3 가드(`api.fragment._merge_node`)가 보는
    라벨 여섯에서만. 가드는 빈 owner 행을 남의 것으로 보고 명시 owner 쓰기를 건너뛰므로,
    이 기기 사용자의 레거시 행(v2 이전 Bridge 등, 백필 안 됨)은 여기서 접지 않으면 영영
    못 고친다. 값만 바꾼다(스탬프는 그대로 — 위의 옮기기와 같다)

무엇을 안 옮기나:
  - `NodeVec`(노드 미러): 키(`vec_id`)에 소유자가 들어 있고, 지우고 다시 넣는 것은 HNSW
    삽입이라 세그폴트 자리다. 데스크탑에는 인코더가 없어 미러가 비어 있으므로 옮길 것도
    없다. 손으로 채운 미러가 있으면 수만 보고한다(`fill_node_mirror.py --owner tenant:local`
    로 다시 채운다)

🔴 **이 기기의 스토어에서만 돈다.** PK 네임스페이싱이 켜진 스토어(허브)에서는 소유자가
PK 의 일부라 이 접기가 뜻이 없고, 허브의 `user:local` 행(있다면)은 남의 것일 수 있다.
부르는 쪽(`VincApp.open`)도 `--parent-pid` 로 뜬 사이드카에서만 부른다. 이중으로 막는다.

멱등이다: 옮길 행이 없으면 개수만 세고 아무것도 쓰지 않는다. 그래서 기동마다 돌려도 되고,
벡터 색인 잔해 때문에 한 번 건너뛴 표는 다음 기동이 마저 옮긴다.
"""
from __future__ import annotations

from store import pkns
from store.backend import flag

LEGACY = "user:local"
CANON = "tenant:local"

#: 소유 메타(스키마 파생 목록 밖의 owner_id 보유 표). `api.query._purge_labels` 와 같은 넷에서
#: `_MergeRejection` 만 뺐다: 그 표는 키가 소유자를 담아 값만 바꾸면 안 된다.
_META_LABELS = ("_Tombstone", "_EdgeTombstone", "_DocMeta", "_DocTombstone")


def _labels() -> list[str]:
    from store.embedded_schema import owner_scoped_labels
    return [lbl for lbl in owner_scoped_labels() if lbl != "NodeVec"] + list(_META_LABELS)


def _count(store, label: str) -> int:
    rows = store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c",
                     {"o": LEGACY})
    return int(rows[0].get("c") or 0) if rows else 0


#: 빈 owner 술어. 스키마 v2 가 ALTER 로 연 열은 '' 이고, 열을 안 쓰고 만든 행은 NULL 이다.
_OWNERLESS = "(n.owner_id IS NULL OR n.owner_id = '')"


def _ownerless_labels() -> tuple[str, ...]:
    """빈 owner 행을 접는 라벨 — SEC-3 가드가 보는 목록 그대로(한 벌)."""
    from api.fragment import _OWNER_SCOPED_LABELS
    return _OWNER_SCOPED_LABELS


def _count_ownerless(store, label: str) -> int:
    rows = store.run(f"MATCH (n:{label}) WHERE {_OWNERLESS} RETURN count(n) AS c")
    return int(rows[0].get("c") or 0) if rows else 0


def _meta_int(store, key: str) -> int | None:
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key = $k RETURN m.value AS v", {"k": key})
    if not rows:
        return None
    try:
        return int(rows[0].get("v"))
    except (TypeError, ValueError):
        return 0


def _fold_seq(store) -> bool:
    """`seq:user:local` 를 `seq:tenant:local` 에 합친다(큰 값). 옮겼으면 True."""
    old_key, new_key = f"seq:{LEGACY}", f"seq:{CANON}"
    old = _meta_int(store, old_key)
    if old is None:
        return False
    cur = _meta_int(store, new_key) or 0
    store.run("MERGE (m:_SyncMeta {key: $k}) SET m.value = $v",
              {"k": new_key, "v": str(max(old, cur))})
    store.run("MATCH (m:_SyncMeta) WHERE m.key = $k DELETE m", {"k": old_key})
    return True


def _fold_merge_rejections(store) -> int:
    """기각 기억을 새 키로 옮긴다. 같은 쌍이 이미 `tenant:local` 에 있으면 그것을 남긴다.
    키는 기각을 적는 쪽과 같은 함수로 짓는다(정렬 규칙까지 한 벌)."""
    from api.query import rejection_pair_key
    rows = store.run(
        "MATCH (r:_MergeRejection) WHERE r.owner_id = $o RETURN r.pair_key AS k, "
        "r.a_id AS a, r.b_id AS b, r.rejected_at AS at, r.note AS note", {"o": LEGACY}) or []
    for r in rows:
        store.run(
            "MERGE (x:_MergeRejection {pair_key: $k}) "
            "ON CREATE SET x.owner_id = $o, x.a_id = $a, x.b_id = $b, "
            "x.rejected_at = $at, x.note = $note",
            {"k": rejection_pair_key(CANON, r.get("a"), r.get("b")), "o": CANON, "a": r.get("a"),
             "b": r.get("b"), "at": r.get("at") or "", "note": r.get("note") or ""})
        store.run("MATCH (x:_MergeRejection) WHERE x.pair_key = $k DELETE x", {"k": r.get("k")})
    return len(rows)


def fold_legacy_owner(store) -> dict:
    """`user:local` 행과 빈 owner 노드 행을 `tenant:local` 로 옮긴다. 결과는 표별로 옮긴
    수다(`moved` 는 `user:local` 에서, `ownerless` 는 빈 owner 에서).

    잡 워커(단일 writer)에서 불러야 한다. 스토어를 못 읽으면 예외를 그대로 올린다:
    부르는 쪽이 로그에 남기고 기동은 계속한다(다음 기동이 다시 한다)."""
    out: dict = {"kind": "owner.fold", "executed": False, "from": LEGACY, "to": CANON,
                 "moved": {}, "ownerless": {}}
    if pkns.enabled():
        out["skipped"] = "namespaced store"
        return out
    # 빈 owner 노드 행은 벡터 색인이 없는 표뿐이라 아래 색인 순서와 무관하다. 먼저 접는다.
    for lbl in _ownerless_labels():
        n = _count_ownerless(store, lbl)
        if n:
            store.run(f"MATCH (n:{lbl}) WHERE {_OWNERLESS} SET n.owner_id = $to", {"to": CANON})
            out["ownerless"][lbl] = n
    counts = {lbl: _count(store, lbl) for lbl in _labels()}
    rejections = _count(store, "_MergeRejection")
    has_seq = _meta_int(store, f"seq:{LEGACY}") is not None
    mirror = _count(store, "NodeVec")
    if mirror:
        out["node_mirror_left"] = mirror
    if not any(counts.values()) and not rejections and not has_seq:
        out["executed"] = True
        return out

    # 벡터 색인이 걸린 표를 고치기 전에 색인을 내린다(문서 쓰기 경로와 같은 순서 —
    # `drop_vector_index` 주석의 세그폴트 실측). 다시 세우는 것은 다음 검색이 한다.
    # 잔해가 있으면 그 표에 쓰는 것 자체가 세그폴트라 건드리지 않는다: 기동이 잔해를
    # 고친 뒤 다음 접기가 마저 옮긴다.
    if counts.get("ChunkVec"):
        if flag(store, "vector_index_remnant"):
            out["deferred"] = {"ChunkVec": counts.pop("ChunkVec")}
        else:
            store.drop_vector_index()

    for lbl, n in counts.items():
        if n:
            store.run(f"MATCH (n:{lbl}) WHERE n.owner_id = $o SET n.owner_id = $to",
                      {"o": LEGACY, "to": CANON})
            out["moved"][lbl] = n
    if rejections:
        out["moved"]["_MergeRejection"] = _fold_merge_rejections(store)
    if has_seq and _fold_seq(store):
        out["moved"]["seq"] = 1
    out["executed"] = True
    return out
