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
앞부분을 붙인다. Record·Decision 은 제목만이다. 넓히는 것은 측정 없이 하지 않는다
([[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]]).

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
#: 부가 텍스트를 이만큼만 쓴다. 문단 전체를 넣으면 제목이 묻힌다.
EXTRA_CHARS = 300
#: CREATE 한 문장에 실을 행 수. 파라미터 목록이 너무 길면 엔진이 준비 단계에서 샌다.
INSERT_BATCH = 256


def mirror_text(title: str, extra: str) -> str:
    """미러 한 행의 텍스트. 제목이 먼저다 — 그것이 이 노드의 이름이다."""
    t = " ".join(str(title or "").split())
    e = " ".join(str(extra or "").split())[:EXTRA_CHARS]
    return f"{t}. {e}".strip(". ") if e else t


def collect(store, owner: str | None = None) -> list[dict]:
    """미러에 담을 행. `owner=None` 이면 스토어의 **모든** 소유자다.

    ⚠ owner 를 거는 쪽(CLI)과 안 거는 쪽(허브 배포 경계)이 같은 함수를 쓴다. 걸 때는
    반드시 걸어야 한다 — 안 그러면 남의 노드가 미러에 섞이고 의미 팔이 그것을 후보로
    올린다(SEC-1). 그래서 `owner_id` 는 **언제나 행에 실어 돌려준다**: 지우고 넣는
    쪽이 스코프를 다시 지어내지 않게."""
    out: list[dict] = []
    for label, pk, kind, extra_col in SPEC:
        proj = f"c.{extra_col} AS extra" if extra_col else "'' AS extra"
        where = "WHERE c.owner_id = $o " if owner else ""
        rows = store.run(
            f"MATCH (c:{label}) {where}"
            f"RETURN c.{pk} AS id, c.title AS title, c.owner_id AS owner, {proj}",
            {"o": owner} if owner else None) or []
        for r in rows:
            title = " ".join(str(r.get("title") or "").split())
            text = mirror_text(title, r.get("extra"))
            if text:
                out.append({"id": r["id"], "kind": kind, "owner": r.get("owner") or "",
                            "title": title, "text": text})
    return out


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
            "UNWIND $rows AS r CREATE (:NodeVec {node_id: r.id, kind: r.kind, "
            "owner_id: r.owner, title: r.title, text: r.text, model: $m, "
            "embedding: r.emb})",
            {"rows": [{"id": r["id"], "kind": r["kind"], "owner": r.get("owner") or "",
                       "title": r["title"], "text": r["text"], "emb": list(r["emb"])}
                      for r in rows[i:i + INSERT_BATCH]],
             "m": model})
    store.create_node_vector_index()
    return {"kind": "node-mirror.replace", "rows": len(rows), "owners": owners,
            "model": model}
