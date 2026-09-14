# -*- coding: utf-8 -*-
"""노드 미러(NodeVec) — 의미 팔이 찾는 표면. **전량 재구축만 있다.**

노드 층에는 색인이 하나도 없었다. 제목 검색은 CONTAINS 전수 스캔이고 점수는 맞은
토큰을 세는 것이라, 질문과 노드가 낱말을 하나도 안 나누면 그 노드는 **어떤 팔로도
후보가 못 된다**. 미러는 그 구멍을 메우는 사본이다.

🔴 **순서가 안전장치다: 드롭 → 벡터와 함께 전량 삽입 → 생성.**
"없던 벡터가 생기는" 것이 HNSW 삽입이고 **그것만** 세그폴트를 낸다(exit 139 — 같은
행의 값 교체와 벡터 없는 행 삽입은 각각 2,000건까지 무사하다는 실측이
`store/embedded_backend.py::drop_vector_index` 주석에 있다). 그래서 증분 갱신이 없다.
중간에 죽으면 인덱스 없는 미러가 남는데, 그 상태에서 의미 팔은 빈 목록을 낼 뿐이라
검색은 멀쩡하다 — 다시 돌리면 된다.
근거: [[decision:the-node-mirror-is-rebuilt-whole-or-not-at-all]]

**찾는 표면은 좁게 담는다.** 제목이 기본이고 Concept 은 one_liner, Episode 는 summary
앞부분을 붙인다. Record·Decision 은 title 옆에 자기 텍스트 열이 없다 — `api/fragment.py`
의 `_KEPT_FIELDS` 계약을 직접 읽어 확인했다
([[record:node-mirror-record-decision-vector-is-title-only-2026-09-14]]). 그래서 이 둘은
자기 열 대신, 이미 갖고 있는 자기 관계로 처음 닿는 Concept 의 one_liner 를 조인해 온다 —
Record 는 `ATTACHES_TO`, Decision 은 `ABOUT`. 이 조인은 **아직 측정 전**이고, 넓히는
것은 측정 없이 하지 않는다는 규율은 그대로다
([[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]] 가 넓혔다가
되돌린 전례). 배포에는 아직 걸리지 않는다 — `POST /node-mirror/rebuild` 는 릴리스에서만
트리거되고, 이 변경은 병합 전 브랜치에 머문다.

이 모듈은 **스토어 연산만** 한다. 인코딩은 부르는 쪽이 한다 — 허브는 사이드카가
원격 인코더에 묻고(`api/server.py`), 로컬은 `scripts/fill_node_mirror.py` 가 onnx 를
직접 든다. 인코딩이 여기 들어오면 두 경로가 서로의 의존성을 물게 된다.
"""
from __future__ import annotations

#: 미러에 담을 라벨과 그 텍스트. (라벨, PK, kind, 부가 텍스트 열 또는 None)
SPEC = [
    ("Concept", "concept_id", "concept", "one_liner"),
    ("Record", "record_id", "record", None),
    ("Decision", "decision_id", "decision", None),
    ("Episode", "episode_id", "episode", "summary"),
]
#: Record·Decision 에는 SPEC 의 네 번째 자리(부가 텍스트 열)가 없다. 대신 이 관계로
#: 처음 닿는 Concept 의 one_liner 를 조인해 붙인다. 라벨에 없으면 SPEC 의 열 그대로다.
_JOIN_EXTRA = {
    "Record": "ATTACHES_TO",
    "Decision": "ABOUT",
}
#: 부가 텍스트를 이만큼만 쓴다. 문단 전체를 넣으면 제목이 묻힌다.
EXTRA_CHARS = 300
#: CREATE 한 문장에 실을 행 수. 파라미터 목록이 너무 길면 엔진이 준비 단계에서 샌다.
INSERT_BATCH = 256


def vec_id(owner: str, node_id: str) -> str:
    """미러 한 행의 기본키. **소유자를 함께 잡는다** — 같은 논리 id 가 스페이스마다
    살기 때문이다(새 스페이스는 전부 같은 네 역할로 시작한다). `ChunkVec` 이 같은
    자리에서 이미 이 모양이다."""
    return f"{owner}#{node_id}"


def mirror_text(title: str, extra: str) -> str:
    """미러 한 행의 텍스트. 제목이 먼저다 — 그것이 이 노드의 이름이다."""
    t = " ".join(str(title or "").split())
    e = " ".join(str(extra or "").split())[:EXTRA_CHARS]
    return f"{t}. {e}".strip(". ") if e else t


def collect(store, owner: str | None = None) -> tuple[list[dict], list[dict]]:
    """미러에 담을 행과, **id 가 겹친 것들**. `owner=None` 이면 모든 소유자다.

    ⚠ owner 를 거는 쪽(CLI)과 안 거는 쪽(허브 배포 경계)이 같은 함수를 쓴다. 걸 때는
    반드시 걸어야 한다 — 안 그러면 남의 노드가 미러에 섞이고 의미 팔이 그것을 후보로
    올린다(SEC-1). 그래서 `owner_id` 는 **언제나 행에 실어 돌려준다**: 지우고 넣는
    쪽이 스코프를 다시 지어내지 않게.

    🔴 **겹침은 소유자까지 함께 봐야 한다.** 같은 논리 id 가 스페이스마다 사는 것은
    정상이다(새 스페이스는 전부 같은 네 역할로 시작한다) — 그것은 겹침이 아니라
    멀티테넌시이고, 미러의 키가 `vec_id`(소유자+id)인 이유다. 진짜 겹침은 **한 소유자
    안에서** 같은 id 가 두 표에 있는 경우다: 각 표는 자기 pk 로 유일하지만 표 사이에는
    아무 제약이 없어 한 논리 id 가 Concept 이면서 Decision 일 수 있다.

    ⚠ 2026-09-10 에 이 둘을 헷갈려 스페이스 간 중복을 그래프 결함으로 읽었다. 실제
    결함은 미러의 키였다. 그래도 걸러 내기 자체는 남긴다 — 한 소유자 안의 진짜 겹침은
    여전히 삽입을 통째로 죽이고, 한 노드의 결함이 팔 전체를 끄게 둘 이유가 없다.
    SPEC 순서로 먼저 온 것을 남기고, 무엇이 겹쳤는지 함께 돌려준다(조용히 버리면
    그래프의 진짜 결함이 미러 뒤에 숨는다)."""
    out: list[dict] = []
    seen: dict[tuple[str, str], str] = {}
    collisions: list[dict] = []
    for label, pk, kind, extra_col in SPEC:
        where = "WHERE c.owner_id = $o " if owner else ""
        rel = _JOIN_EXTRA.get(label)
        if rel:
            # Record·Decision 에는 자기 텍스트 열이 없다 — 처음 닿는 Concept 의
            # one_liner 를 조인한다. 관계가 없거나 대상이 one_liner 를 안 가지면
            # extra 는 null 이고 mirror_text 가 그것을 빈 문자열로 취급한다.
            query = (
                f"MATCH (c:{label}) {where}"
                f"OPTIONAL MATCH (c)-[:{rel}]->(x:Concept) "
                "WITH c, collect(x.one_liner)[1] AS extra "
                f"RETURN c.{pk} AS id, c.title AS title, c.owner_id AS owner, extra"
            )
        else:
            proj = f"c.{extra_col} AS extra" if extra_col else "'' AS extra"
            query = (
                f"MATCH (c:{label}) {where}"
                f"RETURN c.{pk} AS id, c.title AS title, c.owner_id AS owner, {proj}"
            )
        rows = store.run(query, {"o": owner} if owner else None) or []
        for r in rows:
            title = " ".join(str(r.get("title") or "").split())
            text = mirror_text(title, r.get("extra"))
            if not text:
                continue
            key = (r.get("owner") or "", r["id"])
            if key in seen:
                collisions.append({"id": r["id"], "owner": key[0],
                                   "kept": seen[key], "dropped": kind})
                continue
            seen[key] = kind
            out.append({"id": r["id"], "kind": kind, "owner": key[0],
                        "title": title, "text": text})
    return out, collisions


def replace(store, rows: list[dict], model: str) -> dict:
    """미러를 통째로 갈아 끼운다. 각 행에 `emb`(벡터)가 이미 있어야 한다.

    지우는 범위는 **실린 행들의 소유자**다. 전량 재구축을 한 소유자에게 돌려도 다른
    소유자의 미러가 살아남고, 인덱스는 그 남은 벡터들 위에서 다시 만들어진다(빌드는
    삽입이 아니라 일괄 구축이라 위의 세그폴트 자리가 아니다)."""
    owners = sorted({str(r.get("owner") or "") for r in rows})
    store.drop_node_vector_index()
    store.run("MATCH (n:NodeVec) WHERE n.owner_id IN $o DELETE n", {"o": owners})
    for i in range(0, len(rows), INSERT_BATCH):
        store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {vec_id: r.vid, node_id: r.id, "
            "kind: r.kind, owner_id: r.owner, title: r.title, text: r.text, "
            "model: $m, embedding: r.emb})",
            {"rows": [{"vid": vec_id(r.get("owner") or "", r["id"]), "id": r["id"],
                       "kind": r["kind"], "owner": r.get("owner") or "",
                       "title": r["title"], "text": r["text"], "emb": list(r["emb"])}
                      for r in rows[i:i + INSERT_BATCH]],
             "m": model})
    # ⚠ 인덱스 생성은 예외를 삼킨다(멱등이라 그래야 한다). 그래서 **삼킨 사유를 받아
    # 두고 결과도 확인한다** — 행은 들어갔는데 인덱스가 없으면 의미 팔은 예외 없이 빈
    # 목록만 내고, 밖에서는 "안 켠 것"과 구분되지 않는다. 2026-09-10 라이브에서 정확히
    # 그랬고, 사유가 없어서 원인을 두 배포에 걸쳐 못 짚었다.
    # 확인은 방금 넣은 벡터로 되묻는 것이다: 그 행이 자기 자신을 못 찾으면 인덱스가 없다.
    index_error = store.create_node_vector_index() or ""
    index_ok = bool(store.node_vector_search(rows[0]["emb"], 1, owner=rows[0]["owner"])
                    ) if rows else True
    return {"kind": "node-mirror.replace", "rows": len(rows), "owners": owners,
            "model": model, "index_ok": index_ok, "index_error": index_error}
