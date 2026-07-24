#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""허브 자체호스팅 임베딩 워커(B) — needs_embed 문서의 청크를 **로컬 모델**로
임베딩해 사이드카에 돌려준다. 원문은 사이드카 루프백 API 로만 오가고 **서버 밖으로
안 나간다**(no cloud/telemetry 약속 유지 — Voyage 등 외부 API 미사용).

배경: 사이드카 바이너리는 임베딩 스택(sentence-transformers/torch)을 번들에서
제외한다(2.7GB 회피 — packaging/sidecar.spec). 그래서 앱 편집·팩 임포트로 들어온
문서는 needs_embed=1 로 FTS 만 되고 벡터가 빈다. 이 워커는 **허브에만** 두는 별도
venv(sentence-transformers 설치)에서 그 벡터를 채운다. 데스크탑은 오프라인 FTS 유지.

경로(전부 사이드카 루프백 8787):
  GET  /embed/pending?limit=N   → [{doc_id, chunk_id, text}]  (needs_embed 문서 doc 단위)
  POST /embed/apply {doc_id, embeddings:{chunk_id:[...]}} → 저장 + 전 청크 완료 시 needs_embed=0

모델 = BAAI/bge-base-en-v1.5 (768d) — 스토어 provision dim(local-en=768)과 일치하므로
재프로비전 불요. 최초 1회 HuggingFace 에서 캐시로 다운로드.

  VINC_SIDECAR=http://127.0.0.1:8787 VINC_TOKEN=<사이드카 토큰> \
    python embed_worker.py [--once] [--interval 30]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

SIDECAR = os.environ.get("VINC_SIDECAR", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("VINC_TOKEN") or os.environ.get("TOKEN") or ""
MODEL = os.environ.get("VINC_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
DOC_BATCH = int(os.environ.get("VINC_EMBED_DOC_BATCH", "16"))   # 라운드당 문서 수


def _call(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        SIDECAR + path, data=data,
        method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="pending 을 한 번 비우고 종료(systemd timer 용)")
    ap.add_argument("--interval", type=float, default=30.0,
                    help="상주 모드에서 pending 이 빌 때 대기 초")
    args = ap.parse_args()

    model = None   # 지연 로드 — 일감 있을 때만 torch/모델을 올린다(idle timer 절약).

    while True:
        pending = _call(f"/embed/pending?limit={DOC_BATCH}").get("chunks", [])
        if not pending:
            if args.once:
                break
            time.sleep(args.interval)
            continue
        if model is None:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(MODEL)   # 최초 1회 다운로드 → 캐시
        by_doc: dict[str, list[dict]] = {}
        for c in pending:
            by_doc.setdefault(c["doc_id"], []).append(c)
        for doc_id, chunks in by_doc.items():
            texts = [c["text"] for c in chunks]
            vecs = model.encode(texts, normalize_embeddings=True)
            embeddings = {c["chunk_id"]: [float(x) for x in v]
                          for c, v in zip(chunks, vecs)}
            res = _call("/embed/apply", {"doc_id": doc_id, "embeddings": embeddings})
            print(f"[embed] doc={doc_id} chunks={res.get('embedded')} "
                  f"cleared={res.get('cleared')}", flush=True)
        # 이번 라운드가 doc 배치보다 적으면 대기열이 얕아진 것 — once 면 종료.
        if args.once and len(by_doc) < DOC_BATCH:
            break


if __name__ == "__main__":
    main()
