# -*- coding: utf-8 -*-
"""스토어 **사본**의 노드 미러에 ColBERT 토큰을 채운다 — 재채점을 켠 측정(`run_nodes --fill-colbert`).

고정 스토어는 토큰 열(스키마 v24)보다 먼저 만들어져 열도 값도 없고, 하네스는 기동 잡을 돌리지 않아
열이 생기지 않는다. 허브의 `_node_mirror_colbert` 와 같은 순서(토큰 텍스트 → 할 일 → 한 줄씩 인코딩
→ 투영 → pq64 → `set_colbert`)를 이 프로세스의 인코더로 밟는다. 핀 자체에는 쓰지 않는다.
근거: [[vinc.plan.vin845-colbert-node-rescoring-implementation-2026-10-30]]
"""
from __future__ import annotations

import time

_COLS = ("colbert", "colbert_books", "colbert_hash")


def fill(store, enc, cb, *, batch: int = 64) -> dict:
    """`store` 는 사본을 연 백엔드, `enc` 는 `vinc_rag.embed.encoder.Encoder`, `cb` 는 `vinc_rag.colbert.Colbert`."""
    from api import node_mirror as nm             # noqa: PLC0415
    from vinc_rag.colbert import pack_codes        # noqa: PLC0415
    added = []
    for col in _COLS:
        try:
            store.run(f"MATCH (n:NodeVec) RETURN n.{col} AS c LIMIT 1")
        except Exception:
            store.run(f"ALTER TABLE NodeVec ADD {col} STRING DEFAULT ''")
            added.append(col)
    rows, _ = nm.collect(store, None, with_tokens=True)
    todo = nm.colbert_todo(rows, nm.colbert_state(store, None), cb.id)
    t0, buf, done = time.monotonic(), [], 0
    for r in todo:
        hidden = enc.hidden([r["ctext"]], kind="passage")[0]
        buf.append({"vid": nm.vec_id(r.get("owner") or "", r["id"]),
                    "codes": pack_codes(cb.encode_codes(cb.project(hidden))),
                    "hash": nm.colbert_hash(r["ctext"])})
        if len(buf) >= batch:
            done += nm.set_colbert(store, buf, cb.id)
            buf = []
    if buf:
        done += nm.set_colbert(store, buf, cb.id)
    return {"rows": len(rows), "filled": done, "books": cb.id, "columns_added": added,
            "seconds": round(time.monotonic() - t0, 1)}
