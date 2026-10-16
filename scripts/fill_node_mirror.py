#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""노드 미러(NodeVec)를 채운다 — 의미 팔이 쓸 표면.

**왜 있는가.** 노드 층에는 색인이 하나도 없었다. 제목 검색은 Cypher CONTAINS 전수
스캔이고 점수는 맞은 토큰을 세는 것이라, 질문과 노드가 낱말을 하나도 안 나누면 그 노드는
**어떤 팔로도 후보가 못 된다**. 실측(2026-09-09, 정답 66문항): 답 가능 60건 중 39건이
그 상태였고, 제목을 임베딩해 찾으니 그중 22건이 상위 5에, 14건이 1위로 돌아왔다.
근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]

스토어 연산은 `api/node_mirror.py` 한 벌이다 — 허브가 배포 경계에서 부르는 것과
**같은 코드**를 쓴다. 드롭 → 벡터와 함께 전량 삽입 → 생성이라는 순서(HNSW 삽입만
세그폴트를 낸다)와 찾는 표면을 좁게 담는 이유는 거기 적혀 있다. 이 스크립트가 따로
하는 일은 하나뿐이다: **인코더를 직접 든다**(허브는 원격 인코더에 묻는다).

사용:
    python scripts/fill_node_mirror.py --data <store> --owner <owner> \\
        --encoder 이름=onnx [--batch 32] [--limit 0]

종료코드 1: 채울 것이 없거나 인코더가 없다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from api import node_mirror                   # noqa: E402
from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(사본을 쓸 것 — 단일 라이터)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--encoder", required=True, metavar="이름=onnx")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0, help="시험용 상한(0=전량)")
    ap.add_argument("--out", help="요약 JSON 경로")
    a = ap.parse_args(argv)

    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1

    # 미러는 v17 에서 생긴 표라 그 전에 만들어진 스토어에는 없다. `migrate` 의 base DDL 이
    # 멱등이므로 여기서 부르면 신규·구 설치가 같은 자리에 선다(그 함수의 계약 그대로).
    # dim 은 설정이 정한다 — 여기서 지어내면 Chunk 쪽 벡터 폭과 갈릴 수 있다.
    cfg = load_config({"data_dir": a.data})
    try:
        store.migrate(dim=int(getattr(cfg, "embedding_dim", 0) or 768))
    except Exception as exc:
        print(f"미러 표를 만들지 못했다: {type(exc).__name__}: {exc}", file=sys.stderr)
        store.close()
        return 1

    rows, collisions = node_mirror.collect(store, a.owner)
    if a.limit:
        rows = rows[:a.limit]
    if not rows:
        print("미러에 담을 노드가 0건이다", file=sys.stderr)
        store.close()
        return 1

    from vinc_rag.embed.encoder import Encoder                                    # noqa: PLC0415
    name, path = a.encoder.split("=", 1) if "=" in a.encoder else (a.encoder, None)
    enc = Encoder(name, path, batch=max(1, a.batch))
    enc.encode(["warmup"], kind="query")     # 적재 비용을 첫 배치 밖으로

    t0 = time.perf_counter()
    for i in range(0, len(rows), max(1, a.batch)):
        batch = rows[i:i + max(1, a.batch)]
        for r, v in zip(batch, enc.encode([b["text"] for b in batch], kind="passage")):
            r["emb"] = list(v)
    # 드롭 → 벡터와 함께 삽입 → 생성. 그 순서의 이유는 `api/node_mirror.py` 에 있다.
    from store.encoder_runtime import fingerprint                 # noqa: PLC0415
    node_mirror.replace(store, rows, name, fingerprint())
    wall = time.perf_counter() - t0
    done = len(rows)

    n = (store.run("MATCH (n:NodeVec) WHERE n.owner_id = $o RETURN count(n) AS n",
                   {"o": a.owner}) or [{}])[0].get("n")
    store.close()

    summary = {"kind": "node-mirror.fill", "store": a.data, "owner": a.owner,
               "encoder": a.encoder, "rows": done, "in_store": n, "id_collisions": collisions,
               "wall_s": round(wall, 1),
               "per_s": round(done / wall, 2) if wall else None}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
