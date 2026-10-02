#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""한영 혼합 평가 코퍼스를 **스크래치 스토어**에 심는다 — 모델·검색 비교의 전제.

[[vinc.plan.embedding-pipeline]] Step 4 의 실측(bge vs e5, 한국어 nDCG 0.347 →
0.670)은 "영어 스타터팩 25문서 + 한국어 Vinc-docs 25문서" 코퍼스 위에서 잰 것인데,
그 코퍼스를 만든 스크립트가 세션과 함께 사라졌다. 이 파일이 그것을 되살린다.

**doc_id 는 `make_queries.py` 와 같은 규칙으로 만든다** — 두 곳에 따로 적으면 언젠가
어긋나고, 어긋나면 평가셋의 정답이 스토어에 없어서 전 질의가 miss 가 된다(그리고 그건
Recall 0.000 이라는 **정상 모양의 출력**으로 나온다). 그래서 규칙을 여기에 복제하지 않고
그 모듈에서 직접 가져온다.

⚠ **기존 스토어에 심지 말 것.** 이 코퍼스는 평가용이고, 실제 그래프에 섞이면 검색·
MENTIONS·주제 유도가 전부 오염된다. `--data` 에 반드시 **새 경로**를 준다.

사용:
    python scripts/seed_bilingual.py --data /tmp/evalstore.db --owner eval
    python -m rag_eval.search --data /tmp/evalstore.db --owner eval --evalset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import docstore                                   # noqa: E402
from store.backend import open_store                       # noqa: E402
from store.config import load_config                       # noqa: E402

from make_queries import (                                 # noqa: E402
    _CORPORA, _DOC_KO_THRESHOLD, _ROOT, _body_without_frontmatter, lang_of,
)


def _title_of(body: str, fallback: str) -> str:
    """본문 첫 `# ` 제목. 없으면 파일명 — 다만 **파일명은 언어의 증거가 아니다**
    (영어 팩의 파일명이 한국어다). 제목은 표시용일 뿐 평가 라벨에 쓰이지 않는다."""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return fallback


def iter_corpus(per_lang: int | None):
    """(doc_id, title, text, lang, origin) 을 낸다. 순회 순서는 결정론이다."""
    seen: dict[str, int] = {}
    for label, rel in _CORPORA:
        root = _ROOT / rel
        if not root.exists():
            print(f"⚠ 코퍼스 없음, 건너뜀: {root}", file=sys.stderr)
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            body = _body_without_frontmatter(text)
            lang = lang_of(body, _DOC_KO_THRESHOLD)
            if per_lang is not None and seen.get(lang, 0) >= per_lang:
                continue
            seen[lang] = seen.get(lang, 0) + 1
            relp = path.relative_to(root).as_posix()
            yield (f"eval/{lang}/{root.name}/{relp}", _title_of(body, path.stem),
                   text, lang, f"{rel}/{relp}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True,
                    help="스크래치 스토어 **파일 경로**(디렉터리 아님). 새 경로를 줄 것")
    ap.add_argument("--owner", default="eval")
    ap.add_argument("--per-lang", type=int, default=None,
                    help="언어별 문서 수 상한(기본: 전량). 원 측정은 25+25 였다")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data})
    store = open_store(cfg)
    if store is None:
        print("스토어를 열 수 없다", file=sys.stderr)
        return 1
    # 새 파일이면 테이블이 없다 — 멱등이므로 기존 스토어에도 무해하다.
    # (없이 돌리면 첫 put_doc 이 "Table _DocMeta does not exist" 로 죽는다.)
    store.provision(dim=cfg.embedding_dim)

    n, skipped, by_lang = 0, 0, {}
    for doc_id, title, text, lang, origin in iter_corpus(args.per_lang):
        res = docstore.put_doc(store, owner=args.owner, text=text, doc_id=doc_id,
                               title=title, origin=origin, link=True)
        if res.get("skipped"):
            skipped += 1
        n += 1
        by_lang[lang] = by_lang.get(lang, 0) + 1
    store.close()
    print(f"심음 {n}문서 {by_lang} (무변경 스킵 {skipped}) → {args.data}")
    print("다음: python -m rag_eval.search --data "
          f"{args.data} --owner {args.owner} --evalset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
