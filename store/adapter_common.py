# -*- coding: utf-8 -*-
"""두 스토어 어댑터(임베디드·Neo4j)가 같이 쓰는 엔진 무관 조각.

여기 있는 것은 두 엔진에서 뜻이 같다: 청크 검색의 과추출 배수, 스코프 필터 술어(표준
openCypher 비교와 매개변수뿐), neo4j Result 모양의 결과 묶음, 자식 조각을 부모 청크로 접기.
과추출이나 접기를 바꾸면 이 파일 하나만 고친다. 두 어댑터가 같이 따라온다.

⚠ 엔진 전용 구문(색인 호출·카탈로그·DDL)은 여기에 두지 않는다. 방언은 어댑터 파일에만
산다(`scripts/gate_engine_dialect.py` 의 허용 목록이 비어 있다). 이 모듈은 `api`·`vinc_mcp`·
`vinc_rag` 를 import 하지 않는다(`scripts/gate_ownership.py` 의 LAYER_BANS).
"""
from __future__ import annotations

#: 자식 과추출 배수. 한 부모의 조각들이 상위 k 를 다 차지하지 않게 자식을 k 의 이 배수만큼
#: 뽑은 뒤 :func:`fold_children` 으로 접는다.
CHILD_FANOUT = 4
#: pre-filter 과추출 배수(§4-2). 스코프 필터가 걸리면 색인 상위 k 의 이 배수를 뽑아 필터
#: 뒤에도 k 개가 남게 한다.
OVERSAMPLE = 5

#: 청크 검색 필터가 받는 키. Chunk 표에 실제로 있는 열이다.
CHUNK_FILTER_KEYS = ("owner_id", "layer", "doc_id")


def filter_clause(var: str, filters: dict | None, params: dict, *,
                  keyword: str = "WHERE") -> str:
    """스코프 필터를 술어로 렌더한다(pre-filter, §4-2). params 를 in-place 로 채운다.
    색인 호출의 YIELD 와 RETURN 사이에 붙인다.

    ⚠ `keyword` 는 **붙는 자리에 이미 WHERE 가 있는가**로 정한다. 자식에서 부모로 되돌리는
    MATCH 가 WHERE 를 이미 가진 질의에 기본값을 쓰면 `WHERE … WHERE …` 가 되어 파서가
    질의를 통째로 거부한다. 그 자리에서는 `keyword="AND"` 를 준다.

    받는 키는 Chunk 의 실제 열(:data:`CHUNK_FILTER_KEYS`)뿐이고, 모르는 키는 던진다.
    조용히 버리면 `/search?doc=X` 가 같은 소유자의 다른 문서를 섞어 내고, Chunk 에 없는
    열을 받으면 엔진이 바인더 오류로 질의를 죽인다."""
    if not filters:
        return ""
    unknown = sorted(set(filters) - set(CHUNK_FILTER_KEYS))
    if unknown:
        raise ValueError(f"unsupported chunk search filter: {', '.join(unknown)} "
                         f"(Chunk columns: {', '.join(CHUNK_FILTER_KEYS)})")
    preds = []
    for key in CHUNK_FILTER_KEYS:
        val = filters.get(key)
        if val is not None:
            preds.append(f"{var}.{key} = ${key}")
            params[key] = val
    return (f"{keyword} " + " AND ".join(preds) + " ") if preds else ""


class Result:
    """neo4j Result 동형: 반복 가능하고 `.single()`·`.data()` 를 준다. 레코드는 dict(컬럼명 키)."""

    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None

    def data(self) -> list[dict]:
        return list(self._records)


def fold_children(rows, top_k) -> list[dict]:
    """자식 조각 행을 부모 청크 단위로 접어 앞에서 `top_k` 개를 돌려준다.

    ⚠ `rows` 는 distance 오름차순이어야 한다. 같은 `id` 의 첫 행(가장 가까운 자식)이 그
    부모를 대표하고 뒤의 행은 버린다. 순서는 첫 등장 순서 그대로다. `id` 가 없는 행은
    `None` 하나로 접힌다(행을 고치지 않는다)."""
    best: dict[str, dict] = {}
    for r in rows:
        best.setdefault(r.get("id"), r)
    return list(best.values())[:int(top_k)]
