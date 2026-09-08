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
    python scripts/eval_rerank.py --data work/db.full.ko --ko-strip ...
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

from eval_rerank import ko_stripped_text      # noqa: E402

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

    # 열이 이미 있으면 ALTER 가 실패한다 — 사본이므로 그때는 사람 실수다(멱등 아님).
    store.run(f"ALTER TABLE Chunk ADD {_COL} STRING DEFAULT ''")

    rows = store.run("MATCH (c:Chunk) RETURN c.id AS id, c.text AS text")
    n = 0
    for r in rows:
        stripped = ko_stripped_text(r.get("text") or "")
        store.run(f"MATCH (c:Chunk) WHERE c.id = $i SET c.{_COL} = $s",
                  {"i": r["id"], "s": stripped})
        n += 1

    # 두 열을 한 색인에 넣는다. 원문 열을 빼지 않는 것이 요점이다 — 영어는 원문 열의
    # 영어 어간 처리로 계속 맞아야 하고, 실측에서 그것이 유지된다.
    idx = getattr(store, "_FTS_INDEX", None) or "chunk_ft"
    try:
        store.run(f"CALL DROP_FTS_INDEX('Chunk','{idx}')")
    except Exception:
        pass
    store.run(f"CALL CREATE_FTS_INDEX('Chunk','{idx}',['text','{_COL}'])")
    store.close()
    return {"src": src, "out": out, "chunks_backfilled": n,
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
