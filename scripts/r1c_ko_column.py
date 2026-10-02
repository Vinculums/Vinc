#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1c 2단계 계측기 — 스토어 **사본**에 조사 절단 열을 더하고 전문 색인을 두 열로 다시 만든다.

**왜 필요한가.** 한국어 전문 검색이 지는 원인은 어간 처리가 아니다. `stemmer := 'none'`
으로 바꿔도 한글 결과는 한 글자도 안 바뀌고 영어 형태론만 잃는다(실측). 진짜 원인은
색인이 조사를 붙인 채로 토큰을 만든다는 것이다 — `영속성을` 이 색인되면 질의 `영속성`
은 **한 건도** 못 맞춘다. 근거: [[record:korean-fts-analyser-2026-09-08]]

**왜 사본인가.** 이 도구는 스키마를 바꾸고 색인을 다시 만든다. 원본을 고치면 그 스토어로
잰 옛 수치와 비교가 불가능해지고, 되돌릴 방법도 없다. 그래서 `--src` 는 읽기만 하고
`--out` 에 복사본을 만든 뒤 그것만 건드린다.

⚠ **프로덕션 경로가 아니다.** 여기서 재는 것이 이기면 그때 `store/embedded_backend.py`
의 색인 생성 세 곳과 `fulltext_search` 를 함께 고치는 것이 프로덕션 변경이고, 그것은
스토어 재구축을 뜻한다. 이 스크립트는 그 변경을 **정당화할 수치를 만드는 것**까지다.

사용:
    python scripts/r1c_ko_column.py --src work/db.full --out work/db.full.ko
    python -m rag_eval.rerank --data work/db.full.ko --ko-strip ...
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402

from rag_eval.rerank import ko_stripped_text      # noqa: E402

_COL = "stripped"
_BATCH = 200


def prepare(src: str, out: str) -> dict:
    if os.path.exists(out):
        raise SystemExit(f"이미 있다: {out} — 지우고 다시 부를 것(덮어쓰지 않는다)")
    t0 = time.perf_counter()
    shutil.copy(src, out)
    store = open_store(load_config({"data_dir": out}))
    if store is None:
        raise SystemExit(f"사본을 열 수 없다: {out}")

    # 🔴 **색인을 먼저 내린다.** 색인이 붙은 채로 Chunk 에 SET 을 걸면 프로세스가
    # **예외 없이 죽는다** — stdout 이 그 줄에서 끊기고 종료코드는 0 이며 열은 빈 채로
    # 남는다. 백필 0건을 "쿼리가 안 맞았나" 로 두 번 오독했다. 이 리포가 이미 아는
    # 고장 종류다(`embedded_backend` 가 벡터 삽입 때 색인을 통째로 드롭하는 이유).
    #
    # ⚠ 날 Cypher 로 `DROP_FTS_INDEX` 를 부르지 말 것. 확장이 이 커넥션에 로드돼
    # 있지 않으면 "function DROP_FTS_INDEX is not defined" 로 **실패하고**, 그것을
    # try/except 로 삼키면 색인이 남은 채 백필로 들어가 위의 무음 사망이 난다.
    # `drop_search_indexes()` 가 확장 로드까지 해 준다.
    store.drop_search_indexes()

    # 열이 이미 있으면 ALTER 가 실패한다 — 사본이므로 그때는 사람 실수다(멱등 아님).
    store.run(f"ALTER TABLE Chunk ADD {_COL} STRING DEFAULT ''")

    rows = store.run("MATCH (c:Chunk) RETURN c.chunk_id AS cid, c.text AS text")
    n = 0
    for r in rows:
        store.run(f"MATCH (c:Chunk) WHERE c.chunk_id = $i SET c.{_COL} = $s",
                  {"i": r["cid"], "s": ko_stripped_text(r.get("text") or "")})
        n += 1

    # 스스로 확인한다 — 위의 무음 사망이 정확히 "0건인데 성공으로 보이는" 모양이었다.
    filled = store.run(f"MATCH (c:Chunk) WHERE c.{_COL} <> '' RETURN count(c) AS c")
    filled = int(filled[0]["c"]) if filled else 0
    if filled == 0:
        store.close()
        raise SystemExit(f"백필이 한 건도 안 들어갔다({n}건 시도) — 사본을 지우고 다시 볼 것")

    # 두 열을 한 색인에 넣는다. 원문 열을 빼지 않는 것이 요점이다 — 영어는 원문 열의
    # 영어 어간 처리로 계속 맞아야 하고, 실측에서 그것이 유지된다. 벡터 색인은 원래
    # 모양 그대로 되돌린다(`build_search_indexes` 는 FTS 를 `['text']` 한 열로만
    # 만들기 때문에 여기서는 안 쓴다).
    idx = getattr(store, "_FTS_INDEX", None) or "chunk_ft"
    vidx = getattr(store, "_VEC_INDEX", None) or "chunk_vec"
    store.run(f"CALL CREATE_VECTOR_INDEX('ChunkVec','{vidx}','embedding')")
    store.run(f"CALL CREATE_FTS_INDEX('Chunk','{idx}',['text','{_COL}'])")
    store.close()
    return {"src": src, "out": out, "chunks_backfilled": n, "chunks_nonempty": filled,
            "wall_s": round(time.perf_counter() - t0, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, help="원본 스토어(읽기만 한다)")
    ap.add_argument("--out", required=True, help="만들 사본 — 이미 있으면 거부한다")
    a = ap.parse_args(argv)
    print(json.dumps(prepare(a.src, a.out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
