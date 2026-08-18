#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source excerpt gain — one_liner 단독과 one_liner+발췌 중 어느 쪽이 정답 섹션과
더 겹치는가(vinc.plan.reinforcement-points 우선순위 3, unified-kg-rag-comparison §2/§7).

**왜 필요한가.** AWS 의 GraphRAG 는 엔티티 요약 옆에 원문을 붙이면 (생성) 답변의
token-F1 이 0.11 오른다고 측정했다 — Chunk=SSoT 논지를 남이 실측한 것이다. Vinc 는
그 우위를 자기 코퍼스에서 한 번도 잰 적이 없었다. Vinc 는 생성을 안 하므로(no-llm-chat
연구 §8, 인용만 허용) "생성 답변의 F1" 대신 **인용될 재료 자체와 정답 섹션의 어휘
겹침(token-F1)**을 잰다 — 결정론이고 모델이 필요 없다.

**두 조건**은 이미 있는 answer material 그대로다:
    A  one_liner 단독              (node_context 의 one_liner)
    B  one_liner + 발췌            (node_context 의 excerpts 를 붙인다)

**정답(gold)**은 그 질의가 유래한 헤딩의 본문 전체(다음 헤딩 전까지) — `evalset.json`
의 질의가 바로 그 헤딩 제목이므로, `extract_concepts` 와 같은 헤딩 파서를 재사용해
정확히 그 경계를 다시 잘라낸다. LLM 판정자를 쓰지 않는다.

전제: `scripts/seed_bilingual.py` 로 심은 코퍼스가 타깃 스토어에 있어야 한다
(evalset 의 doc_id 가 그 코퍼스를 가리킨다). 문서마다 `extract_doc(apply=True)`
를 돌려 헤딩당 개념을 만든다(멱등 — 이미 있으면 matched 로 스킵, one_liner 는
안 덮는다). 헤딩 제목이 make_queries.py 의 파서와 docstore 의 파서 사이에서 정확히
일치하지 않는 질의는 **미매칭으로 세어 보고**한다(조용한 0점 금지, eval_search.py
와 같은 규율).

사용:
    python scripts/eval_excerpt_gain.py --data ~/.vinc/db --owner u
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import docstore as ds        # noqa: E402
from api import query as q            # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402
from make_queries import _strip       # noqa: E402 — evalset 의 질의는 이 함수를 거친
                                       # 제목이다(순번·장식 제거). docstore._headings 는
                                       # 원문 그대로 돌려주므로 같은 함수로 맞춰야 매칭된다.

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[가-힣]+")


def tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def token_f1(candidate: str, gold: str) -> float:
    """어휘 집합 겹침 F1 — 생성 없이 답변 재료와 정답 섹션의 근접도를 잰다."""
    c, g = tokens(candidate), tokens(gold)
    overlap = len(c & g)
    if not overlap:
        return 0.0
    precision, recall = overlap / len(c), overlap / len(g)
    return 2 * precision * recall / (precision + recall)


def load_evalset(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"query": str(x["query"]), "doc_id": str(x["doc_id"]), "lang": x.get("lang") or "en"}
            for x in (data.get("queries") or []) if x.get("query") and x.get("doc_id")]


def gold_section(text: str, title: str) -> str:
    """제목이 일치하는(순번·장식 제거 후) 헤딩의 본문 — 다음 헤딩 전까지
    (extract_concepts 와 동일 경계)."""
    lines = text.splitlines()
    heads = ds._headings(lines)
    want = _strip(title)
    for k, (_, body, t) in enumerate(heads):
        if _strip(t) == want:
            stop = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
            return "\n".join(lines[body:stop]).strip()
    return ""


def mean(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="스토어 데이터 경로(미지정 시 기본 해석)")
    ap.add_argument("--owner", required=True, help="extract_doc 이 owner 일치를 요구한다(SEC-1)")
    ap.add_argument("--evalset", default=str(
        Path(__file__).resolve().parent / "fixtures" / "evalset.json"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data} if args.data else None)
    store = open_store(cfg)
    if store is None:
        print("스토어에 도달할 수 없다", file=sys.stderr)
        return 1

    qs = load_evalset(args.evalset)
    doc_ids = sorted({x["doc_id"] for x in qs})

    # 문서마다 결정론 추출(apply=True) — 헤딩당 concept 1개 + MENTIONS 재링크.
    # 재실행은 멱등: 기존 concept 은 matched 로 스킵되고 one_liner 를 덮지 않는다.
    text_by_doc: dict[str, str] = {}
    cand_by_doc: dict[str, dict[str, dict]] = {}
    for did in doc_ids:
        got = ds.get_doc(store, did, owner=args.owner)
        if not got.get("executed"):
            continue
        text_by_doc[did] = got["text"]
        ex = ds.extract_doc(store, did, owner=args.owner, apply=True)
        if ex.get("executed"):
            cand_by_doc[did] = {_strip(c["title"]): c for c in ex.get("candidates") or []}

    if not text_by_doc:
        raise SystemExit(
            f"평가셋의 문서가 이 스토어에 하나도 없다({len(doc_ids)}개 찾음). "
            "scripts/seed_bilingual.py 로 코퍼스를 먼저 심을 것.")

    rows = {"a": [], "b": []}
    by_lang: dict[str, dict[str, list]] = {}
    unmatched = no_excerpt = 0

    for item in qs:
        did, title, lang = item["doc_id"], item["query"], item["lang"]
        cand = (cand_by_doc.get(did) or {}).get(_strip(title))
        gold = gold_section(text_by_doc.get(did, ""), title) if cand else ""
        if not cand or not gold:
            unmatched += 1
            continue
        ctx = q.node_context(owner=args.owner, node=f"concept:{cand['slug']}", store=store)
        if not ctx.get("executed"):
            unmatched += 1
            continue
        one_liner = ctx.get("one_liner") or ""
        excerpt_text = " ".join(e.get("excerpt") or "" for e in ctx.get("excerpts") or [])
        if not excerpt_text:
            no_excerpt += 1
        a = token_f1(one_liner, gold)
        b = token_f1((one_liner + " " + excerpt_text).strip(), gold)
        rows["a"].append(a)
        rows["b"].append(b)
        by_lang.setdefault(lang, {"a": [], "b": []})
        by_lang[lang]["a"].append(a)
        by_lang[lang]["b"].append(b)

    out = {
        "kind": "eval.excerpt_gain",
        "queries": len(qs), "matched": len(rows["a"]), "unmatched": unmatched,
        "no_excerpt": no_excerpt,
        "overall": {"one_liner_only": round(mean(rows["a"]), 4),
                    "one_liner_plus_excerpt": round(mean(rows["b"]), 4)},
        "by_lang": {lg: {"n": len(v["a"]), "one_liner_only": round(mean(v["a"]), 4),
                         "one_liner_plus_excerpt": round(mean(v["b"]), 4)}
                    for lg, v in by_lang.items()},
    }
    store.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"질의 {out['queries']}개 · 매칭 {out['matched']} · 미매칭 {out['unmatched']} "
          f"· 발췌 없음 {out['no_excerpt']}")
    print(f"\n{'조건':<24}{'token-F1':>10}")
    print(f"{'one_liner only':<24}{out['overall']['one_liner_only']:>10.4f}")
    print(f"{'one_liner + excerpt':<24}{out['overall']['one_liner_plus_excerpt']:>10.4f}")
    if len(out["by_lang"]) > 1:
        print("\n언어별")
        for lg, m in sorted(out["by_lang"].items()):
            print(f"  {lg:<4}n={m['n']:<4}one_liner {m['one_liner_only']:.4f}  "
                  f"+excerpt {m['one_liner_plus_excerpt']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
