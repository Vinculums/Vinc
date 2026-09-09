#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""노드 미러(NodeVec)를 채운다 — 의미 팔이 쓸 표면.

**왜 있는가.** 노드 층에는 색인이 하나도 없었다. 제목 검색은 Cypher CONTAINS 전수
스캔이고 점수는 맞은 토큰을 세는 것이라, 질문과 노드가 낱말을 하나도 안 나누면 그 노드는
**어떤 팔로도 후보가 못 된다**. 실측(2026-09-09, 정답 66문항): 답 가능 60건 중 39건이
그 상태였고, 제목을 임베딩해 찾으니 그중 22건이 상위 5에, 14건이 1위로 돌아왔다.
근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]

🔴 **순서가 안전장치다: 드롭 → 벡터와 함께 전량 삽입 → 생성.**
"없던 벡터가 생기는" 것이 HNSW 삽입이고 **그것만** 세그폴트를 낸다(exit 139 — 같은 행의
값 교체와 벡터 없는 행 삽입은 각각 2,000건까지 무사하다는 실측이
`store/embedded_backend.py::drop_vector_index` 주석에 있다). 그래서 부분 채우기가 없다.
중간에 죽으면 인덱스 없는 미러가 남는데, 그 상태에서 의미 팔은 빈 목록을 낼 뿐이라
검색은 멀쩡하다 — 다시 돌리면 된다.

**찾는 표면은 좁게 담는다.** 제목이 기본이고, Concept 은 one_liner, Episode 는 summary
앞부분을 붙인다. Record·Decision 은 제목만이다. 오늘 Episode 요약을 **어휘** 검색 표면에
넣었다가 답 불가 질문을 확정하게 만든 적이 있다
([[record:the-episode-summary-is-too-wide-a-search-surface-2026-09-09]]). 의미 팔은 그
표면과 다르지만, 같은 이유로 넓히기 전에 재는 것이 맞다.

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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402

#: 미러에 담을 라벨과 그 텍스트. (라벨, PK, kind, 부가 텍스트 열 또는 None)
#: 부가 열을 늘리는 것은 **찾는 표면을 넓히는 것**이라 측정 없이 하지 않는다.
SPEC = [
    ("Concept", "concept_id", "concept", "one_liner"),
    ("Record", "record_id", "record", None),
    ("Decision", "decision_id", "decision", None),
    ("Episode", "episode_id", "episode", "summary"),
]
#: 부가 텍스트를 이만큼만 쓴다. 문단 전체를 넣으면 제목이 묻힌다.
EXTRA_CHARS = 300


def mirror_text(title: str, extra: str) -> str:
    """미러 한 행의 텍스트. 제목이 먼저다 — 그것이 이 노드의 이름이다."""
    t = " ".join(str(title or "").split())
    e = " ".join(str(extra or "").split())[:EXTRA_CHARS]
    return f"{t}. {e}".strip(". ") if e else t


def collect(store, owner: str) -> list[dict]:
    """미러에 담을 행. owner 를 안 걸면 남의 노드가 미러에 섞인다(SEC-1)."""
    out: list[dict] = []
    for label, pk, kind, extra_col in SPEC:
        proj = f"c.{extra_col} AS extra" if extra_col else "'' AS extra"
        rows = store.run(
            f"MATCH (c:{label}) WHERE c.owner_id = $o "
            f"RETURN c.{pk} AS id, c.title AS title, {proj}",
            {"o": owner}) or []
        for r in rows:
            title = " ".join(str(r.get("title") or "").split())
            text = mirror_text(title, r.get("extra"))
            if text:
                out.append({"id": r["id"], "kind": kind, "title": title, "text": text})
    return out


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

    rows = collect(store, a.owner)
    if a.limit:
        rows = rows[:a.limit]
    if not rows:
        print("미러에 담을 노드가 0건이다", file=sys.stderr)
        store.close()
        return 1

    from encoder import Encoder                                    # noqa: PLC0415
    name, path = a.encoder.split("=", 1) if "=" in a.encoder else (a.encoder, None)
    enc = Encoder(name, path, batch=max(1, a.batch))
    enc.encode(["warmup"], kind="query")     # 적재 비용을 첫 배치 밖으로

    # ① 드롭 — 채우기 전에. 이 줄이 없으면 HNSW 삽입이 프로세스를 죽인다.
    store.drop_node_vector_index()
    # 미러는 통째로 다시 만든다. 부분 갱신은 "없던 벡터가 생기는" 자리를 남긴다.
    store.run("MATCH (n:NodeVec) WHERE n.owner_id = $o DELETE n", {"o": a.owner})

    t0 = time.perf_counter()
    done = 0
    for i in range(0, len(rows), max(1, a.batch)):
        batch = rows[i:i + max(1, a.batch)]
        vecs = enc.encode([b["text"] for b in batch], kind="passage")
        # ② 벡터와 **함께** 삽입한다.
        store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {node_id: r.id, kind: r.kind, "
            "owner_id: $o, title: r.title, text: r.text, model: $m, embedding: r.emb})",
            {"rows": [{"id": b["id"], "kind": b["kind"], "title": b["title"],
                       "text": b["text"], "emb": list(v)} for b, v in zip(batch, vecs)],
             "o": a.owner, "m": name})
        done += len(batch)
    wall = time.perf_counter() - t0

    # ③ 인덱스는 마지막에.
    store.create_node_vector_index()
    n = (store.run("MATCH (n:NodeVec) WHERE n.owner_id = $o RETURN count(n) AS n",
                   {"o": a.owner}) or [{}])[0].get("n")
    store.close()

    summary = {"kind": "node-mirror.fill", "store": a.data, "owner": a.owner,
               "encoder": a.encoder, "rows": done, "in_store": n,
               "wall_s": round(wall, 1),
               "per_s": round(done / wall, 2) if wall else None}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
