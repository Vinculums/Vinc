#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E-G1 — Chunk↔Concept 이부 그래프 링커(:MENTIONS 결정론 생성).

리서치 ①(다중 홉 가교)·HippoRAG 이부 그래프를 **LLM 없이 결정론**으로 번역한다.
Chunk(벡터/FTS 검색 노드)를 그것이 언급하는 Concept 에 잇는 규칙:

  1. **source_file basename 일치**(가장 강함, method='source') — 같은 원본 문서에서
     파생된 Chunk 와 Concept. Chunk.source_file 은 절대경로, Concept.source_file 은
     hub-상대경로라 basename(파일명)으로 비교한다.
  2. **title 토큰 매칭**(method='title') — Concept.title 의 유의 토큰(길이≥2, 상한 6)이
     **전부** Chunk.text(소문자)에 포함. "이 청크가 그 개념을 논한다"의 결정론 근사.
  3. **stem 매칭**(method='stem') — Concept.stem 이 Chunk.text 에 토큰으로 포함(파일
     stem 이 식별자 역할 하는 코퍼스 보완). 1·2 가 잡으면 3 은 생략(우선순위).

`_node_fallback_search`(api/query.py)의 토큰화 규약과 동형(소문자 split). owner 를
주면 Concept 를 그 소유자로 스코프한다(호스티드 — 남의 개념에 링크 금지).

⚠ 재적재 순서: 인덱스가 걸린 embedding 열은 수정 불가하나, MENTIONS 는 별도 rel
테이블이라 인덱스와 무관하다 — embed 잡의 build_search_indexes **뒤/앞** 어디서든
안전하다. 멱등(MERGE) — 재실행 시 method 만 갱신.

사용:
    python -m engines.mentions_link [--owner <id>] [--dry-run]
    (또는 embed_pipeline 이 적재 후 link_mentions(store) 로 호출)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

# 토큰: 소문자 영숫자/한글 낱말(길이≥2). 불용어는 두지 않는다 — title 은 이미
# 유의어 밀도가 높고, "전부 포함" AND 규칙이 노이즈를 걸러낸다.
_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+")
_MAX_TITLE_TOKENS = 6


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2]


def _basename(path: str) -> str:
    return Path((path or "").replace("\\", "/")).name


def compute_links(chunks: list[dict], concepts: list[dict]) -> list[dict]:
    """(chunk_id, concept_id, method) 링크를 결정론적으로 산출(순수 — I/O 없음).

    ``chunks``: [{chunk_id, source_file, text}]. ``concepts``: [{concept_id,
    title, stem, source_file}]. 각 (chunk, concept) 쌍에 대해 우선순위 규칙으로
    최대 1개 method 를 부여한다(source > title > stem). 반환은 정렬 리스트(결정론)."""
    # concept 전처리: basename·title 토큰셋·stem 소문자.
    prepped = []
    for c in concepts:
        cid = c.get("concept_id")
        if not cid:
            continue
        title_toks = _tokens(c.get("title") or "")[:_MAX_TITLE_TOKENS]
        prepped.append({
            "id": cid,
            "base": _basename(c.get("source_file") or ""),
            "title_toks": set(title_toks),
            "has_title": bool(title_toks),
            "stem": (c.get("stem") or "").lower().strip(),
        })
    out: list[dict] = []
    for ch in chunks:
        chid = ch.get("chunk_id")
        if not chid:
            continue
        ch_base = _basename(ch.get("source_file") or "")
        ch_tokset = set(_tokens(ch.get("text") or ""))
        for c in prepped:
            method = None
            if ch_base and c["base"] and ch_base == c["base"]:
                method = "source"
            elif c["has_title"] and c["title_toks"] <= ch_tokset:
                method = "title"
            elif c["stem"] and len(c["stem"]) >= 2 and c["stem"] in ch_tokset:
                method = "stem"
            if method:
                out.append({"chunk_id": chid, "concept_id": c["id"], "method": method})
    out.sort(key=lambda r: (r["chunk_id"], r["concept_id"]))
    return out


def _fetch_chunks(store) -> list[dict]:
    rows = store.run("MATCH (c:Chunk) RETURN c.chunk_id AS chunk_id, "
                     "c.source_file AS source_file, c.text AS text")
    return rows or []


def _fetch_concepts(store, owner: str | None) -> list[dict]:
    where = "WHERE c.owner_id = $owner " if owner else ""
    params = {"owner": owner} if owner else None
    rows = store.run("MATCH (c:Concept) " + where +
                     "RETURN c.concept_id AS concept_id, c.title AS title, "
                     "c.stem AS stem, c.source_file AS source_file", params)
    return rows or []


def link_mentions(store, *, owner: str | None = None, dry_run: bool = False) -> dict:
    """스토어의 Chunk↔Concept 를 스캔해 :MENTIONS 를 MERGE(멱등). 반환 통계
    {chunks, concepts, links, by_method}. ``dry_run`` 이면 MERGE 없이 계산만."""
    chunks = _fetch_chunks(store)
    concepts = _fetch_concepts(store, owner)
    links = compute_links(chunks, concepts)
    by_method: dict[str, int] = {}
    for lk in links:
        by_method[lk["method"]] = by_method.get(lk["method"], 0) + 1
    if not dry_run:
        for lk in links:
            store.run(
                "MATCH (ch:Chunk {chunk_id: $cid}), (co:Concept {concept_id: $koid}) "
                "MERGE (ch)-[m:MENTIONS]->(co) SET m.method = $method",
                {"cid": lk["chunk_id"], "koid": lk["concept_id"], "method": lk["method"]},
            )
    return {"chunks": len(chunks), "concepts": len(concepts),
            "links": len(links), "by_method": by_method}


def main() -> None:
    ap = argparse.ArgumentParser(description="Chunk↔Concept :MENTIONS 결정론 링커(E-G1)")
    ap.add_argument("--owner", default=None, help="Concept 소유자 스코프(호스티드)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패.", file=sys.stderr)
        sys.exit(1)
    try:
        stats = link_mentions(store, owner=args.owner, dry_run=args.dry_run)
    finally:
        store.close()
    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"{tag}Chunk {stats['chunks']} · Concept {stats['concepts']} → "
          f":MENTIONS {stats['links']}개 {stats['by_method']}")


if __name__ == "__main__":
    main()
