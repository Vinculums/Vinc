#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E-G1 — Chunk↔Concept 이부 그래프 링커(:MENTIONS 결정론 생성).

리서치 ①(다중 홉 가교)·HippoRAG 이부 그래프를 **LLM 없이 결정론**으로 번역한다.
Chunk(벡터/FTS 검색 노드)를 그것이 언급하는 Concept 에 잇는 규칙:

  1. **title 토큰 매칭**(method='title') — Concept.title 의 유의 토큰(길이≥2, 상한 6)이
     **전부** Chunk.text(소문자)에 포함되고, 토큰이 둘 이상이면 그 전부가 **연속
     `_TITLE_WINDOW` 토큰 안에 함께** 나와야 한다. "이 청크가 그 개념을 논한다"의
     결정론 근사. 창 조건이 없던 때는 "Vinc Design Hub" 같은 짧은 제목이 vinc·design·
     hub 를 문단 곳곳에 하나씩만 담은 2000자 청크에도 걸려, 디자인과 무관한 문서
     8건의 대표 개념이 그 개념이 됐다(2026-09-05 실측). 근거·창 크기 선택:
     [[decision:mention-title-tokens-within-a-window]]
  2. **source_file basename 일치**(method='source') — 같은 원본 문서에서 파생된 Chunk
     와 Concept. Chunk.source_file 은 절대경로, Concept.source_file 은 hub-상대경로라
     basename(파일명)으로 비교한다.
  3. **stem 매칭**(method='stem') — Concept.stem 이 Chunk.text 에 토큰으로 포함(파일
     stem 이 식별자 역할 하는 코퍼스 보완). 1·2 가 잡으면 3 은 생략(우선순위).

⚠ **1 과 2 의 순서가 곧 발췌 순서다.** 쌍 하나에 method 는 하나뿐이라(아래 if/elif),
출처를 공유하는 개념은 청크가 제 제목을 **글자 그대로 말하고 있어도** 'source' 로
찍혔다. 그런데 발췌 정렬(`api/query.py`)은 title·stem·source 순이므로, 한 문서에서
통째로 뽑힌 개념은 창 전체가 'source' 라 올릴 것이 없었다. 순서를 맞바꿔도 링크는
하나도 늘거나 줄지 않는다(3858 → 3858, 라벨만 142건 이동). 실측:
[[record:title-before-source-clears-every-all-source-korean-window-while-suffix-stripping-buys-nothing-2026-09-14]]

규칙이 바뀌면 `MENTIONS_RULE` 을 올린다. 재링크는 `put_doc` 에서만 일어나므로 규칙만
바꾸면 **이미 적재된 문서의 링크는 옛 규칙 그대로 남는다** — `ensure_rule` 이 기동 시
`_SchemaMeta` 의 표식과 대조해 다르면 전량 재링크한다(임베딩 모델 전환과 같은 형태,
`api.docstore.switch_embed_model`).

`_node_fallback_search`(api/query.py)의 토큰화 규약과 동형(소문자 split). owner 를
주면 Concept 를 그 소유자로 스코프한다(호스티드 — 남의 개념에 링크 금지).

⚠ 재적재 순서: 인덱스가 걸린 embedding 열은 수정 불가하나, MENTIONS 는 별도 rel
테이블이라 인덱스와 무관하다 — embed 잡의 build_search_indexes **뒤/앞** 어디서든
안전하다. 멱등(MERGE) — 재실행 시 method 만 갱신.

사용:
    python -m engines.mentions_link [--owner <id>] [--dry-run] [--reset]
    (또는 embed_pipeline 이 적재 후 link_mentions(store) 로 호출)
    --reset: 기존 :MENTIONS 를 지우고 현재 규칙으로 다시 만든다(relink_all)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import pkns  # noqa: E402  (PK 네임스페이싱)
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

# 토큰: 소문자 영숫자/한글 낱말(길이≥2). 불용어는 두지 않는다 — title 은 이미
# 유의어 밀도가 높고, "전부 포함" AND 규칙이 노이즈를 걸러낸다.
_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+")
_MAX_TITLE_TOKENS = 6
#: 여러 토큰짜리 제목이 한 청크에 "함께" 나왔다고 치는 연속 토큰 창. 제목 상한(6)의
#: 두 배 — "namespacing node primary keys by owner" 같은 어순 바꾼 인용은 들어오고,
#: 문단마다 한 단어씩 흩어진 우연은 빠진다. 값의 근거는 위 decision 노드.
_TITLE_WINDOW = 12
#: 링크 규칙의 식별자. 규칙을 바꾸면 이 값을 바꿔야 `ensure_rule` 이 재링크한다 —
#: 값이 같으면 코드를 바꿔도 기존 링크는 그대로다.
MENTIONS_RULE = "3:title-before-source"
_RULE_KEY = "mentions_rule"


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2]


def _basename(path: str) -> str:
    return Path((path or "").replace("\\", "/")).name


def _within_window(positions: dict[str, list[int]], toks: set[str],
                   window: int = _TITLE_WINDOW) -> bool:
    """``toks`` 전부가 연속 ``window`` 토큰 안에 함께 나오는 자리가 하나라도 있는가.

    ``positions`` 는 토큰 → 청크 안 등장 위치(오름차순). 토큰 하나짜리 제목은 창 조건이
    없다(포함이 곧 인접). 각 토큰의 등장 위치를 합쳐 정렬한 뒤 최소 창 두 포인터 —
    청크 길이에 선형이고 청크마다 한 번만 토큰화한다."""
    if len(toks) <= 1:
        return True
    seq = sorted((p, t) for t in toks for p in positions.get(t, ()))
    need = len(toks)
    seen: dict[str, int] = {}
    left = 0
    for right_pos, right_tok in seq:
        seen[right_tok] = seen.get(right_tok, 0) + 1
        while len(seen) == need:
            left_pos, left_tok = seq[left]
            if right_pos - left_pos < window:
                return True
            seen[left_tok] -= 1
            if not seen[left_tok]:
                del seen[left_tok]
            left += 1
    return False


def compute_links(chunks: list[dict], concepts: list[dict]) -> list[dict]:
    """(chunk_id, concept_id, method) 링크를 결정론적으로 산출(순수 — I/O 없음).

    ``chunks``: [{chunk_id, source_file, text}]. ``concepts``: [{concept_id,
    title, stem, source_file}]. 각 (chunk, concept) 쌍에 대해 우선순위 규칙으로
    최대 1개 method 를 부여한다(title > source > stem). 반환은 정렬 리스트(결정론)."""
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
        # 토큰 → 등장 위치. 집합 포함 검사는 키로, 창 검사는 위치로 — 한 번 토큰화.
        positions: dict[str, list[int]] = {}
        for i, tok in enumerate(_tokens(ch.get("text") or "")):
            positions.setdefault(tok, []).append(i)
        ch_tokset = set(positions)
        for c in prepped:
            method = None
            # 순서 주의 — 쌍마다 method 는 하나다. title 이 source 보다 먼저인 이유는
            # 모듈 docstring ⚠ 절에 있다(바꾸면 MENTIONS_RULE 도 올릴 것).
            if (c["has_title"] and c["title_toks"] <= ch_tokset
                    and _within_window(positions, c["title_toks"])):
                method = "title"
            elif ch_base and c["base"] and ch_base == c["base"]:
                method = "source"
            elif c["stem"] and len(c["stem"]) >= 2 and c["stem"] in ch_tokset:
                method = "stem"
            if method:
                out.append({"chunk_id": chid, "concept_id": c["id"], "method": method})
    out.sort(key=lambda r: (r["chunk_id"], r["concept_id"]))
    return out


def _fetch_chunks(store, owner: str | None = None) -> list[dict]:
    """owner 를 주면 그 소유자의 청크만(호스티드 단일 스토어 — 남의 청크에 링크 금지).
    None 이면 전부(로컬 단일 사용자 — embed_pipeline 이 그렇게 부른다)."""
    where = "WHERE c.owner_id = $o " if owner else ""
    rows = store.run("MATCH (c:Chunk) " + where + "RETURN c.chunk_id AS chunk_id, "
                     "c.source_file AS source_file, c.text AS text",
                     {"o": owner} if owner else None)
    return rows or []


def backfill_chunk_owners(store) -> dict:
    """owner 가 빈 청크에 **그 문서의 owner** 를 찍는다. 반환 {filled, remaining}.

    빈 owner 는 전량 재링크의 사각지대다. `link_mentions(scope_chunks=True)` 는
    `WHERE c.owner_id = $o` 로 청크를 좁히고 `ensure_rule` 은 빈 버킷을 아예 건너뛰므로,
    그런 청크는 **삭제에도 재생성에도 안 잡힌다.** 그런데 `put_doc` 의 재링크는 doc_id
    로만 걸러 늘 성공한다 — 그 비대칭이 "문서 하나씩은 되는데 전량은 한 번도 안 되는"
    상태를 만들었다. 재현·근거:
    [[record:the-startup-relink-skips-ownerless-chunks-and-then-stamps-the-marker-2026-09-06]]

    고치는 방향을 **스코프 넓히기가 아니라 데이터 채우기**로 잡은 이유: 빈 owner 를
    현재 owner 로 싸잡아 링크하면 그것이 곧 교차 테넌트 링크이고, 그 가드는
    `ensure_rule` 이 지키려던 바로 그것이다. 청크의 owner 는 짐작할 값이 아니라
    **그 청크가 속한 문서가 이미 알고 있는 값**이다.

    문서 메타가 없는 고아 청크는 남긴다(귀속할 owner 가 없다). 그 수를 `remaining`
    으로 돌려 호출부가 조용히 넘어가지 않게 한다."""
    try:
        store.run("MATCH (c:Chunk), (d:_DocMeta) WHERE c.owner_id = '' "
                  "AND d.doc_id = c.doc_id AND d.owner_id <> '' "
                  "SET c.owner_id = d.owner_id")
        rows = store.run("MATCH (c:Chunk) WHERE c.owner_id = '' RETURN count(c) AS n") or []
    except Exception:
        return {"filled": 0, "remaining": -1}   # 구스토어 — 강등(실패 아님)
    return {"filled": 0, "remaining": int((rows[0].get("n") if rows else 0) or 0)}


def _chunk_owners(store) -> list[str]:
    """스토어에 청크를 가진 소유자 전부(빈 문자열 = owner 없이 적재된 로컬 청크)."""
    rows = store.run("MATCH (c:Chunk) RETURN DISTINCT c.owner_id AS o") or []
    return sorted({(r.get("o") or "") for r in rows})


#: 링크 대상 라벨 → (PK 열, stem 열 유무). v12 — 결정도 문서가 언급하는 대상이다.
#: 안 넣으면 `Chunk→Decision` MENTIONS 가 **재생성되지 않는다**: 이 링커가 유일한
#: 생성 경로라, 결정이 Concept 을 떠나는 순간 "이 값이 왜 이런가 → 문서 → 결정" 의
#: 마지막 한 칸이 조용히 끊긴다(기존 엣지는 남아 있어 한동안 티도 안 난다).
_LINK_TARGETS: tuple[tuple[str, str, bool], ...] = (
    ("Concept", "concept_id", True),
    ("Decision", "decision_id", False),   # Decision 에는 stem·source_file 열이 없다
)


def _fetch_concepts(store, owner: str | None) -> list[dict]:
    """링크 대상 노드(개념·결정)를 한 목록으로. 키 이름은 `concept_id` 를 유지한다 —
    순수 계산부(compute_links)와 MERGE 가 그 키로 짜여 있고, 여기서 이름을 바꾸면
    링크 규칙까지 손대야 한다. 실제 라벨은 `label` 로 함께 나른다."""
    where = "WHERE c.owner_id = $owner " if owner else ""
    params = {"owner": owner} if owner else None
    rows: list[dict] = []
    for label, pk, has_stem in _LINK_TARGETS:
        proj = f"c.{pk} AS concept_id, c.title AS title, "
        proj += ("c.stem AS stem, c.source_file AS source_file"
                 if has_stem else "'' AS stem, '' AS source_file")
        got = store.run(f"MATCH (c:{label}) " + where + "RETURN " + proj, params)
        for r in got or []:
            rows.append({**r, "label": label})
    return rows


def merge_links(store, links: list[dict], concepts: list[dict],
                owner: str | None = None) -> int:
    """계산된 링크를 :MENTIONS 로 MERGE(멱등). 대상 **라벨**은 concepts 행이 정한다.

    쓰기를 여기 한 벌로 두는 이유: 이 MERGE 가 `api.docstore._relink_mentions` 에도
    복제돼 있었고 거기에는 `(co:Concept)` 이 박혀 있었다. 그러면 결정으로 향하는
    링크가 **오류 없이 0건**이 된다(그 함수는 예외를 삼키기까지 한다). 규칙만이
    아니라 쓰기도 SSoT 하나여야 그 형태가 안 생긴다."""
    label_of = {c["concept_id"]: (c.get("label") or "Concept") for c in concepts}
    pk_of = {lbl: pk for lbl, pk, _ in _LINK_TARGETS}
    # (청크, 라벨, method) 로 묶어 한 청크의 링크를 쿼리 하나로 MERGE 한다. 링크마다
    # 쿼리 하나였을 때 전량 재링크(ensure_rule)가 473청크·3086링크에 19.7초였고, 묶으면
    # 470쿼리·3.1초다(2026-09-05 실측, 같은 링크 집합). 허브에서는 그 시간만큼 뒤의
    # 쓰기가 잡 큐에서 기다리므로 배수가 곧 정지 창이다.
    groups: dict[tuple[str, str, str], list[str]] = {}
    for lk in links:
        lbl = label_of.get(lk["concept_id"], "Concept")
        groups.setdefault((lk["chunk_id"], lbl, lk["method"]), []).append(lk["concept_id"])
    n = 0
    for (cid, lbl, method), ids in groups.items():
        store.run(
            f"MATCH (ch:Chunk {{chunk_id: $cid}}), (co:{lbl}) WHERE co.{pk_of[lbl]} IN $ids "
            "MERGE (ch)-[m:MENTIONS]->(co) SET m.method = $method",
            # PK 네임스페이싱 — $ids 는 노드 PK 바인딩이라 물리 키다($cid 는 Chunk 로,
            # 마이그레이션 대상 라벨이 아니라 논리 그대로). 근거: [[vinc.plan.node-pk-namespacing]] §7
            {"cid": cid, "ids": [pkns.key(owner, i) for i in ids], "method": method})
        n += len(ids)
    return n


def link_mentions(store, *, owner: str | None = None, dry_run: bool = False,
                  scope_chunks: bool = False) -> dict:
    """스토어의 Chunk↔Concept 를 스캔해 :MENTIONS 를 MERGE(멱등). 반환 통계
    {chunks, concepts, links, by_method}. ``dry_run`` 이면 MERGE 없이 계산만.

    ``scope_chunks`` 가 참이면 청크도 owner 로 좁힌다. 기본이 거짓인 이유: 로컬
    적재(embed_pipeline)는 owner 를 개념에만 걸어 왔고, 구 스토어의 청크는
    owner_id 가 빈 값이라 좁히면 통째로 빠진다. 호스티드 전량 재링크(relink_all)만
    켠다."""
    chunks = _fetch_chunks(store, owner if scope_chunks else None)
    concepts = _fetch_concepts(store, owner)
    links = compute_links(chunks, concepts)
    by_method: dict[str, int] = {}
    for lk in links:
        by_method[lk["method"]] = by_method.get(lk["method"], 0) + 1
    if not dry_run:
        merge_links(store, links, concepts, owner)
    return {"chunks": len(chunks), "concepts": len(concepts),
            "links": len(links), "by_method": by_method}


def relink_all(store, owner: str | None = None) -> dict:
    """owner 의 :MENTIONS 를 전부 지우고 현재 규칙으로 다시 만든다.

    MERGE 만으로는 옛 규칙이 만든 링크가 남는다 — 규칙이 **좁아졌을** 때 필요한 것은
    삭제다. owner None 은 무스코프(로컬 단일 사용자). 청크·개념 둘 다 owner 로 좁혀
    남의 청크에 내 개념이 걸리지 않게 한다."""
    where = "WHERE c.owner_id = $o " if owner else ""
    store.run("MATCH (c:Chunk)-[m:MENTIONS]->() " + where + "DELETE m",
              {"o": owner} if owner else None)
    stats = link_mentions(store, owner=owner, scope_chunks=bool(owner))
    stats["owner"] = owner or ""
    return stats


def ensure_rule(store) -> dict:
    """`_SchemaMeta` 의 링크 규칙 표식이 `MENTIONS_RULE` 과 다르면 전량 재링크(멱등).

    표식이 **없을 때도 다른 것으로 친다** — 그 링크가 어느 규칙으로 만들어졌는지
    모르기 때문이다(임베딩 모델 전환과 같은 판단, `switch_embed_model`). 새 스토어는
    청크가 없어 no-op 이고, 재기동은 표식이 남아 두 번 돌지 않는다. 표식은 재링크가
    **끝난 뒤** 남긴다 — 중간에 죽으면 다음 기동이 처음부터 다시 한다.

    호스티드 스토어에서 owner 가 빈 청크는 건너뛴다: 소유자를 모르는 청크에 전 계정의
    개념을 걸면 그것이 곧 교차 테넌트 링크다. 로컬(소유자 그룹이 하나뿐)은 무스코프로
    돈다."""
    out: dict = {"kind": "mentions.rule", "executed": False, "rule": MENTIONS_RULE}
    rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                     {"k": _RULE_KEY}) or []
    stored = (rows[0].get("v") or "") if rows else ""
    out["previous"] = stored
    if stored == MENTIONS_RULE:
        out.update(executed=True, changed=False)
        return out
    # 좁히기 **전에** 데이터를 고친다 — 빈 owner 청크는 아래 루프가 통째로 건너뛴다.
    back = backfill_chunk_owners(store)
    owners = _chunk_owners(store)
    per: dict[str, int] = {}
    skipped = 0
    for o in owners:
        if o == "" and len(owners) > 1:
            skipped += 1
            continue
        per[o or "(local)"] = int(relink_all(store, owner=o or None).get("links") or 0)
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _RULE_KEY, "v": MENTIONS_RULE})
    out.update(executed=True, changed=True, owners=per, skipped_ownerless=skipped,
               ownerless_remaining=back.get("remaining", -1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Chunk↔Concept :MENTIONS 결정론 링커(E-G1)")
    ap.add_argument("--owner", default=None, help="Concept 소유자 스코프(호스티드)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="기존 :MENTIONS 를 지우고 현재 규칙으로 다시 만든다")
    args = ap.parse_args()
    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패.", file=sys.stderr)
        sys.exit(1)
    try:
        if args.reset and not args.dry_run:
            stats = relink_all(store, owner=args.owner)
        else:
            stats = link_mentions(store, owner=args.owner, dry_run=args.dry_run)
    finally:
        store.close()
    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"{tag}Chunk {stats['chunks']} · Concept {stats['concepts']} → "
          f":MENTIONS {stats['links']}개 {stats['by_method']}")


if __name__ == "__main__":
    main()
