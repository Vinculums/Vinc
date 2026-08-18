#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Coverage recall for the heading-only extractor (unified-kg-rag-comparison §5
and §7 item 4) — "the heading only extractor's weakness [is] known only as
prose ([[record:fact-builtin-extraction-is-headings]]); coverage recall turns
it into a single number."

**What "should have been extracted" means here.** AWS's coverage recall checks
extraction against a dataset's labeled entities and relations. Vinc's extractor,
`docstore.extract_concepts`, does not attempt relations at all (it emits one
concept candidate per heading, nothing else), so a relation-coverage number
would be measuring something that was never built, not a gap. What is a fair,
in-domain ground truth for entities: **the document's own heading structure**,
at every depth the author wrote, not only the two levels the extractor reads.
`docstore._headings()` is hard-capped to ATX levels 2-3 by its own regex
(`_ATX_RE`) — an H4 is invisible to it by construction, not filtered out after
being seen. That is the same standard the extractor already applies at H2/H3,
just not stopped early, so it costs no new assumption to use it as gold.

**Two independent causes of loss, kept separate.** A heading can go uncounted
for two different reasons: it is deeper than H3 (structurally invisible), or it
is H2/H3 but past the 24-candidate cap (`extract_concepts(max_candidates=24)`).
Collapsing them into one number would hide which fix — reading deeper, or
raising the cap — actually helps.

No store, no model: this is a pure function of the document text, run over the
same file-tree corpus `make_queries.py` reads (so numbers here compose with the
retrieval and label-set work in scripts/eval_search.py and
scripts/make_queries_from_store.py).

사용:
    python scripts/eval_coverage_recall.py
    python scripts/eval_coverage_recall.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.docstore import extract_concepts, _slug  # noqa: E402
from make_queries import _CORPORA, _ROOT, _body_without_frontmatter  # noqa: E402

#: ATX 헤딩 임의 깊이(H2~H6) — docstore._headings 의 _ATX_RE(H2-H3 전용)보다 넓게,
#: "얼마나 더 깊이 읽으면 얼마나 더 잡히는가"를 재기 위해서다. H1 은 문서 제목이라
#: extract_concepts 와 같은 이유로 제외.
_ANY_ATX = re.compile(r"^(#{2,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


def all_headings(text: str) -> list[tuple[int, str]]:
    """(깊이, slug) — 프론트매터 제외, 첫 등장만(extract_concepts 와 같은 멱등 규칙)."""
    body = _body_without_frontmatter(text)
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    for hashes, raw in _ANY_ATX.findall(body):
        slug = _slug(raw)
        if slug and slug not in seen:
            seen.add(slug)
            out.append((len(hashes), slug))
    return out


def coverage(text: str) -> dict:
    """문서 하나의 커버리지 — 전체 헤딩 대비 실제로 잡힌 것, 원인별로 갈라서."""
    heads = all_headings(text)
    caught = {c["slug"] for c in extract_concepts(text)}
    shallow = [s for d, s in heads if d in (2, 3)]
    deep = [s for d, s in heads if d >= 4]
    missed_to_cap = [s for s in shallow if s not in caught]   # H2/H3인데도 못 잡힘 = 24개 상한
    return {
        "total": len(heads), "caught": len(caught),
        "shallow": len(shallow), "deep": len(deep),
        "missed_to_depth": len(deep),          # 구조적으로 안 보임(H4+)
        "missed_to_cap": len(missed_to_cap),    # H2/H3인데 24개 넘어서 잘림
        "recall": len(caught) / len(heads) if heads else 1.0,
    }


def iter_corpus_texts():
    """make_queries.py 와 같은 코퍼스 정의를 그대로 걷는다(§4 파일트리 재사용)."""
    for _, rel in _CORPORA:
        root = _ROOT / rel
        if not root.exists():
            print(f"⚠ 코퍼스 없음, 건너뜀: {root}", file=sys.stderr)
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                yield path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue


def mean(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    per_doc = [coverage(text) for text in iter_corpus_texts()]
    per_doc = [c for c in per_doc if c["total"] > 0]   # 헤딩 없는 문서는 정의상 제외
    if not per_doc:
        print("코퍼스에서 헤딩 있는 문서를 못 찾았다", file=sys.stderr)
        return 1

    total_headings = sum(c["total"] for c in per_doc)
    total_caught = sum(c["caught"] for c in per_doc)
    out = {
        "kind": "eval.coverage_recall",
        "docs": len(per_doc),
        "macro_recall": round(mean([c["recall"] for c in per_doc]), 4),
        "micro_recall": round(total_caught / total_headings, 4) if total_headings else 1.0,
        "total_headings": total_headings,
        "total_caught": total_caught,
        "lost_to_depth": sum(c["missed_to_depth"] for c in per_doc),
        "lost_to_cap": sum(c["missed_to_cap"] for c in per_doc),
        "docs_over_cap": sum(1 for c in per_doc if c["missed_to_cap"] > 0),
    }

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"문서 {out['docs']}개 · 헤딩 {out['total_headings']}개 중 {out['total_caught']}개 포착")
    print(f"macro recall(문서별 평균) {out['macro_recall']:.4f}")
    print(f"micro recall(헤딩 총수 기준)  {out['micro_recall']:.4f}")
    print(f"깊이(H4+)로 놓친 것   {out['lost_to_depth']}")
    print(f"24개 상한으로 놓친 것 {out['lost_to_cap']} ({out['docs_over_cap']}개 문서에서)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
