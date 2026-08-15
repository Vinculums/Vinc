#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""평가 질의셋 생성 — `make_queries.py` 와 같은 규칙(본문 소제목, 언어 필터, 중복
제거, 문서당 상한)을 **파일트리 대신 스토어의 문서**에 적용한다.

**왜 필요한가.** [[vinc.ops.mcp-hardening-eval]]가 2026-08-08에 실제 계정 그래프
위에서 검색 품질을 측정했을 때, `scripts/fixtures/evalset.json`의 정답 문서가
그 스토어에 **하나도 없어서**(합성 코퍼스용이라) 게이트가 거부했고, 결국 문서
제목을 질의로 쓰는 부트스트랩으로 되돌아갔다 — 어휘 매칭에 유리한 보수적 편향이
있는 방식이다. "이 코퍼스에 속한 평가셋이 없다"는 것이 남은 과제로 적혔다
(같은 문서, vinc.research.unified-kg-rag-comparison §5/§7 item 2도 같은 결론).

이 스크립트는 그 프로그램만 만든다. **실제 운영 그래프에 접근하지는 않는다** —
`--data`로 가리키는 스토어가 로컬 개발 스토어든, 실 그래프의 공유 스냅샷 사본이든
가리지 않는다(그 스냅샷을 만드는 절차는 [[vinc.ops.mcp-hardening-eval]] 끝의
재현 절차 그대로이고, 프로덕션에 손대는 일이라 이 스크립트가 대신 결정하지 않는다).

사용:
    python scripts/make_queries_from_store.py --data ~/.vinc/db --owner u
    python scripts/make_queries_from_store.py --data <snapshot copy> --owner u \\
        --out scripts/fixtures/evalset_live.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import docstore as ds        # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402
from make_queries import candidates_in_text, select  # noqa: E402


def iter_doc_texts(store, owner: str, page_size: int = 100):
    """(doc_id, text) 을 문서 전량에 대해 낸다 — `vinc_doc_list`(summary)로 id 를 모으고
    `get_doc`으로 무손실 복원한다. 페이지네이션은 `list_docs` 의 커서 규약 그대로."""
    cursor = None
    while True:
        page = ds.list_docs(store, owner=owner, limit=page_size, cursor=cursor, detail="summary")
        for row in page.get("docs") or []:
            did = row.get("doc_id")
            if not did:
                continue
            got = ds.get_doc(store, did, owner=owner)
            if got.get("executed"):
                yield did, got["text"]
        if not page.get("has_more"):
            break
        cursor = page.get("next_cursor")
        if not cursor:
            break


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="스토어 데이터 경로(미지정 시 기본 해석)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--per-lang", type=int, default=30)
    ap.add_argument("--out", default=str(
        Path(__file__).resolve().parent / "fixtures" / "evalset_live.json"))
    ap.add_argument("--stdout", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data} if args.data else None)
    store = open_store(cfg)
    if store is None:
        print("스토어에 도달할 수 없다", file=sys.stderr)
        return 1

    cands: list[dict] = []
    n_docs = 0
    for doc_id, text in iter_doc_texts(store, args.owner):
        n_docs += 1
        cands.extend(candidates_in_text(text, doc_id, "live", doc_id))
    store.close()

    if n_docs == 0:
        print(f"이 owner({args.owner})의 문서가 스토어에 없다 — 스냅샷·owner 를 확인할 것.",
              file=sys.stderr)
        return 1

    data = select(cands, args.per_lang,
                 [{"path": f"<store:{args.owner}>", "expected_lang": "mixed",
                   "docs": n_docs, "heading_candidates": len(cands)}],
                 generated_by="scripts/make_queries_from_store.py")
    text_out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if args.stdout:
        sys.stdout.write(text_out)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text_out, encoding="utf-8")
    c = data["counts"]
    print(f"질의 {c['selected']}개 ({c['by_lang']}) · 문서 {c['docs']}개 · "
          f"후보 {c['candidates']} 중 모호 {c['dropped_ambiguous']} 제외 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
