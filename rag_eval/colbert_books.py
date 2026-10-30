"""ColBERT pq64 코드북을 한 번 맞춘다. 결과 파일은 인코더와 사이드카가 함께 싣는다.

연구와 같은 절차다(`vinc-rag-assets` `node-set/bundle-fix/colbert.py`): 노드 300개를 시드 7로 뽑아
토큰 벡터를 만들고, 그중 20,000개를 시드 7로 뽑아 부분공간마다 k-means(중심 256, 20회, 시드 7).
`--exclude` 에 준 노드는 뽑지 않는다 — 수용 측정(계획 §4)에 쓰는 후보·정답 노드를 코드북에서
빼야 그 측정이 깨끗하다.

    python -X utf8 -m rag_eval.colbert_books --store <pin hub.db> --encoder <onnx> \
        --linear <colbert_linear.pt> --out <books.npy> [--exclude ids.json] [--owner <id>]

근거: [[vinc.plan.vin845-colbert-node-rescoring-implementation-2026-10-30]]
"""
from __future__ import annotations

import argparse
import json
import os
import random

import numpy as np

from vinc_rag import colbert as cb

TABLES = (("Concept", "concept_id", "c.one_liner", "''"), ("Record", "record_id", "''", "''"),
          ("Decision", "decision_id", "''", "''"), ("Episode", "episode_id", "''", "c.summary"))


def node_texts(store, owner: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, pk, one, summ in TABLES:
        where = "WHERE c.owner_id = $o " if owner else ""
        props = "c.props" if label != "Episode" else "''"
        rows = store.run(f"MATCH (c:{label}) {where}RETURN c.{pk} AS id, c.title AS t, {one} AS one, "
                         f"{summ} AS s, {props} AS p", {"o": owner} if owner else None) or []
        for r in rows:
            try:
                props_v = json.loads(r.get("p") or "{}")
            except ValueError:
                props_v = {}
            text = cb.token_text(r.get("t") or "", r.get("s") or "", r.get("one") or "", props_v)
            if text:
                out[r["id"]] = text
    return out


def kmeans(x: np.ndarray, k: int, it: int = 20, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    c = x[rng.choice(len(x), k, replace=False)].copy()
    for _ in range(it):
        a = np.argmin((c ** 2).sum(1)[None] - 2 * x @ c.T, 1)
        for j in range(k):
            m = x[a == j]
            if len(m):
                c[j] = m.mean(0)
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--encoder", required=True)
    ap.add_argument("--linear", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude")
    ap.add_argument("--owner")
    ap.add_argument("--nodes", type=int, default=300)
    ap.add_argument("--tokens", type=int, default=20000)
    a = ap.parse_args()
    os.environ.setdefault("VINC_TARGET", "local")
    from store.backend import open_store
    from store.config import load_config
    from vinc_rag.embed.encoder import Encoder

    st = open_store(load_config({"data_dir": a.store}))
    try:
        texts = node_texts(st, a.owner)
    finally:
        st.close()
    skip = set(json.load(open(a.exclude, encoding="utf-8"))) if a.exclude else set()
    pool = sorted(i for i in texts if i not in skip)
    random.Random(7).shuffle(pool)
    fit = pool[:a.nodes]
    w, b = cb.load_linear(a.linear)
    enc = Encoder("BAAI/bge-m3", a.encoder)
    toks = []
    for h in enc.hidden([texts[i] for i in fit]):
        v = h[1:].astype(np.float32) @ w.T + b
        toks.append(v / np.linalg.norm(v, axis=1, keepdims=True))
    x = np.concatenate(toks).astype(np.float32)
    rng = np.random.default_rng(7)
    x = x[rng.choice(len(x), min(a.tokens, len(x)), replace=False)]
    books = np.stack([kmeans(x[:, s * cb.SUB_DIM:(s + 1) * cb.SUB_DIM], cb.CENTROIDS)
                      for s in range(cb.SUBSPACES)]).astype(np.float32)
    np.save(a.out, books)
    print(json.dumps({"nodes_in_store": len(texts), "excluded": len(skip & set(texts)), "fit_nodes": len(fit),
                      "fit_tokens": len(x), "books_id": cb.books_id(books), "out": a.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
