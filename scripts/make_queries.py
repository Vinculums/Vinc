#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""평가 질의셋 생성 — 문서 **본문 소제목**을 질의로, 그 문서를 정답으로 뽑는다.

**왜 이 파일이 존재하는가.** [[vinc.plan.embedding-pipeline]] Step 4 의 모델 비교
(bge vs e5, 한영 60질의)를 만든 스크립트 다섯이 세션 스크래치패드에만 있었고 그대로
소실됐다. 그 문서는 스스로 "재현 절차가 세 문서를 순환 참조하고 실제 스크립트는 없는
전례"를 경고해 놓고 데이터는 같은 함정에 남겨 뒀다. 이 파일과 `fixtures/evalset.json`
이 그 구멍을 메운다 — 생성 규칙을 **코드로** 고정하고, 산출물을 리포에 커밋한다.

**생성 규칙 (넷 다 실패에서 나왔다).**

1. 질의는 **본문 소제목**(`##`/`###`)이다. 제목·파일명이 아니다.
   ⚠ 1차 측정이 이것 때문에 통째로 무효였다. 한국어 코퍼스로 라벨한 Vinc-docs 의
   파일명이 ASCII(CONVENTIONS·DESIGN·README)라 "한국어 질의"에 한글이 **0자**였다.
   한국어를 잰 적이 없는데 한국어 점수를 읽고 있었다. 반대 방향도 있다 — 영어 팩은
   파일명이 한국어(`01-뉴턴역학.md`)인데 본문이 영어다. 어느 쪽이든 **파일명은 언어의
   증거가 아니다.**
2. 소제목의 문자 계열이 **그 문서의 우세 언어와 같을 때만** 쓴다. 한국어 문서 안의
   영어 소제목을 한국어 질의로 세면 같은 오염이 다시 난다.
3. **여러 문서에 나오는 소제목은 버린다.** 정답이 모호해지기 때문이다. 실제로 영어
   스타터팩은 문서마다 `## One-line essence`·`## Physical picture` 같은 템플릿
   소제목을 공유해서, 이 규칙이 없으면 질의 대부분이 정답 여럿을 갖게 된다.
4. 너무 짧거나 순번뿐인 소제목은 버린다(`## 1.`·`## 요약` 류). 검색 신호가 없다.

**편향을 정직하게 적는다.** 소제목은 본문에 그대로 나오는 어휘라 **FTS 에 유리하다**
— 벡터의 이점을 과소평가하는 보수적 편향이다. "이 조건에서도 벡터가 이긴다"면 믿을 수
있고, 벡터가 져도 그것만으로 벡터가 쓸모없다는 결론은 못 낸다. 같은 경고가
`eval_search.py` 모듈 독스트링에도 있다.

사용:
    python scripts/make_queries.py                      # 기본 경로로 fixtures/evalset.json 갱신
    python scripts/make_queries.py --per-lang 40 --stdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

#: 워크스페이스 루트. 기본은 이 파일 기준(…/vinc-project)인데, **워크트리에서 돌리면
#: 그 계산이 스크래치패드를 가리켜 코퍼스가 통째로 안 보인다** — 코퍼스 둘(Vinc-docs)은
#: 리포 밖에만 있다. 그때는 VINC_EVAL_ROOT 로 실제 워크스페이스를 가리킬 것.
_ROOT = Path(os.environ.get("VINC_EVAL_ROOT")
             or Path(__file__).resolve().parents[2])
_OUT = Path(__file__).resolve().parents[1] / "rag_eval" / "fixtures" / "evalset.json"

#: 코퍼스 둘. 영어는 스타터팩 본문, 한국어는 Vinc-docs 정본이다.
#: (레이블은 기대 언어일 뿐이고 실제 판정은 본문으로 한다 — 규칙 2.)
_CORPORA = [
    ("en", "Vinc/starter-packs/work"),
    ("ko", "Vinc-docs/vinc"),
    ("ko", "Vinc-docs/plans"),
]

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
#: 소제목에서 걷어낼 마크다운 장식 — 굵게·기울임·인라인 코드·링크.
_DECOR = re.compile(r"(\*\*|__|\*|`|\[|\]\([^)]*\))")
#: 순번만 있는 머리(`1.` · `§3` · `Step 2 —`)를 떼어 본문만 남긴다.
_ORDINAL = re.compile(r"^\s*(?:§?\d+(?:[.\-]\d+)*[.)]?|Step\s+\d+|부록\s*[A-Z]?)\s*[·—:\-]?\s*")

#: 소제목이라기보다 문서 틀인 것들. 규칙 3(중복 제거)이 대부분 걸러 주지만,
#: 한 문서에만 있어도 검색 질의로는 무의미한 것들이 남아서 명시 제외한다.
_BOILERPLATE = {
    "요약", "개요", "배경", "결론", "참고", "부록", "목차", "한계", "남은 일", "메모",
    "summary", "overview", "background", "conclusion", "references", "appendix",
    "notes", "todo", "see also", "index",
}


def _strip(raw: str) -> str:
    """소제목 원문 → 질의 문자열."""
    s = _DECOR.sub("", raw)
    s = _ORDINAL.sub("", s)
    return unicodedata.normalize("NFC", s).strip()


def hangul_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "가" <= c <= "힣") / len(letters)


#: 문서 전체의 언어 판정 임계. **측정으로 고른 값이다**(2026-08-07, 두 코퍼스 전수):
#:
#:     starter-packs/work   123문서 · 한글 비율 min=max=**0.0000**
#:     Vinc-docs/vinc        42문서 · min 0.2127 (vinc.d.packs.md)
#:     Vinc-docs/plans       11문서 · min 0.3693
#:
#: 즉 0.0 과 0.21 사이가 통째로 비어 있어 그 구간의 어떤 값도 두 코퍼스를 완전 분리한다.
#: 0.10 은 그 빈 구간의 가운데다. ⚠ 처음엔 소제목과 같은 0.3 을 썼는데, 그러면 기술
#: 용어가 빽빽한 한국어 문서(`vinc/AZURE-ARCHITECTURE.md`·`vinc.d.packs.md`)가 **영어
#: 문서로 분류돼** 그 안의 ASCII 소제목이 "영어 질의"로 실린다 — 규칙 2 가 막으려던
#: 1차 측정의 오염이 방향만 뒤집혀 재발한다.
_DOC_KO_THRESHOLD = 0.10
#: 소제목 하나의 언어 판정 임계. 문서보다 높게 잡는다 — 소제목은 짧아서 고유명사 하나로
#: 비율이 크게 흔들리고, 애매한 것은 **채택하지 않는 쪽**이 안전하다(후보가 2,400개라
#: 보수적으로 걸러도 60개를 채우고 남는다).
_HEAD_KO_THRESHOLD = 0.30


def lang_of(text: str, threshold: float = _HEAD_KO_THRESHOLD) -> str:
    """문자 계열 판정. `eval_search.py.lang_of` 와 같은 기준(한글 비율)이되, 여기서는
    **알파벳 문자만** 분모로 삼는다 — 소제목은 짧아서 공백·기호가 비율을 흔든다.
    문서와 소제목은 임계가 다르다(위 두 상수의 근거 주석 참조)."""
    return "ko" if hangul_ratio(text) > threshold else "en"


def _body_without_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def candidates_in_text(text: str, doc_id: str, label: str, source: str) -> list[dict]:
    """본문 하나에서 (질의, 정답) 후보를 뽑는다(규칙 1·2). 문서 출처(파일이냐 스토어냐)와
    무관 — `collect`(파일)와 `make_queries_from_store.py`(스토어)가 함께 쓴다."""
    body = _body_without_frontmatter(text)
    # 규칙 2 — 본문이 판정 기준이다. 파일명/doc_id 는 언어의 증거가 아니다(영어 팩의
    # 파일명은 한국어이고, 한국어 정본의 파일명은 ASCII 다).
    doc_lang = lang_of(body, _DOC_KO_THRESHOLD)
    out: list[dict] = []
    for _, raw in _HEADING.findall(body):
        q = _strip(raw)
        if len(q) < 6 or q.casefold() in _BOILERPLATE:   # 규칙 4
            continue
        if lang_of(q) != doc_lang:                       # 규칙 2
            continue
        out.append({"query": q, "doc_id": doc_id, "lang": doc_lang,
                    "corpus": label, "source": source})
    return out


def collect(root: Path, label: str) -> list[dict]:
    """한 코퍼스 루트(파일트리)에서 (문서, 소제목) 후보를 모은다."""
    out: list[dict] = []
    for path in sorted(root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        doc_lang = lang_of(_body_without_frontmatter(text), _DOC_KO_THRESHOLD)
        rel = path.relative_to(root).as_posix()
        doc_id = f"eval/{doc_lang}/{root.name}/{rel}"
        out.extend(candidates_in_text(text, doc_id, label, f"{root.name}/{rel}"))
    return out


def select(cands: list[dict], per_lang: int, corpus_meta: list[dict],
          generated_by: str = "scripts/make_queries.py") -> dict:
    """후보 목록 → 평가셋(규칙 3 dedup·문서당 상한·언어별 상한·출력 형태). 후보의
    출처(파일트리 또는 스토어)와 무관 — 여기서부터는 순수 함수다."""
    # 규칙 3 — 두 문서 이상에 나오는 소제목은 정답이 모호하다. 버린다.
    seen: dict[str, set[str]] = {}
    for c in cands:
        seen.setdefault(c["query"].casefold(), set()).add(c["doc_id"])
    ambiguous = {k for k, v in seen.items() if len(v) > 1}
    uniq = [c for c in cands if c["query"].casefold() not in ambiguous]

    # 한 문서가 질의를 독식하지 않게 문서당 상한을 둔다. 그렇게 하지 않으면
    # 소제목이 많은 긴 문서 두세 개가 평가셋 전체를 대표해 버린다.
    per_doc: dict[str, int] = {}
    picked: list[dict] = []
    for c in sorted(uniq, key=lambda x: (x["lang"], x["doc_id"], x["query"])):
        if per_doc.get(c["doc_id"], 0) >= 2:
            continue
        per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], 0) + 1
        picked.append(c)

    by_lang: dict[str, list[dict]] = {}
    for c in picked:
        by_lang.setdefault(c["lang"], []).append(c)
    queries: list[dict] = []
    for lang in sorted(by_lang):
        queries.extend(by_lang[lang][:per_lang])

    return {
        "version": 1,
        "generated_by": generated_by,
        "note": ("소제목 질의는 본문 어휘와 겹쳐 FTS 에 유리하다 — 벡터를 과소평가하는 "
                 "보수적 편향이다. 절대 점수가 아니라 조건 간 차이를 읽는 용도다."),
        "rules": [
            "질의는 본문 소제목(## / ###). 제목·파일명은 쓰지 않는다 (1차 측정 무효 원인).",
            "소제목의 문자 계열이 그 문서의 우세 언어와 같을 때만 채택한다.",
            "두 문서 이상에 나오는 소제목은 정답이 모호하므로 제외한다.",
            "6자 미만이거나 문서 틀(요약·개요·Summary 등)인 소제목은 제외한다.",
            "문서당 최대 2개까지만 뽑아 긴 문서가 평가셋을 독식하지 않게 한다.",
        ],
        "corpora": corpus_meta,
        "counts": {
            "candidates": len(cands),
            "dropped_ambiguous": len(cands) - len(uniq),
            "selected": len(queries),
            "by_lang": {lg: sum(1 for q in queries if q["lang"] == lg)
                        for lg in sorted({q["lang"] for q in queries})},
            "docs": len({q["doc_id"] for q in queries}),
        },
        "queries": queries,
    }


def build(per_lang: int) -> dict:
    """파일트리 코퍼스(`_CORPORA`) 전용 진입점 — collect + select."""
    cands: list[dict] = []
    corpus_meta = []
    for label, rel in _CORPORA:
        root = _ROOT / rel
        if not root.exists():
            print(f"⚠ 코퍼스 없음, 건너뜀: {root}", file=sys.stderr)
            continue
        got = collect(root, label)
        cands.extend(got)
        corpus_meta.append({"path": rel, "expected_lang": label,
                            "docs": len({c['doc_id'] for c in got}),
                            "heading_candidates": len(got)})
    return select(cands, per_lang, corpus_meta)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-lang", type=int, default=30,
                    help="언어별 질의 수 상한(기본 30 — 원 측정의 en 21 · ko 39 와 같은 규모)")
    ap.add_argument("--out", default=str(_OUT))
    ap.add_argument("--stdout", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()

    data = build(args.per_lang)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.stdout:
        sys.stdout.write(text)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    c = data["counts"]
    print(f"질의 {c['selected']}개 ({c['by_lang']}) · 문서 {c['docs']}개 · "
          f"후보 {c['candidates']} 중 모호 {c['dropped_ambiguous']} 제외 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
