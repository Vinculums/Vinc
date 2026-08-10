# -*- coding: utf-8 -*-
"""load_inheritance.py — 문서 계보를 그래프에 물린다(발주자 선택 ①, 2026-08-08).

**무엇을 푸는가.** 신 코퍼스 54건 중 53건이 frontmatter 로 `inherits_from` 을
선언하는데 라이브 `INHERITS_FROM` 은 0건이었다. `vinc_doc_put` 은 청크와 MENTIONS 만
만들고 doc-stub 개념을 안 만들기 때문이다. MENTIONS 는 "문서가 개념을 언급한다"를
텍스트에서 파생하지만 `inherits_from` 은 **문서와 문서 사이** 관계라 그것으로는
표현이 안 된다 — 즉 "이 문서가 무엇 위에 서 있나"를 `vinc_path` 로 못 따라갔다.

**어떻게.** 문서마다 `concept:doc.<doc_id>` 스텁을 만들고 그 사이에 계보를 건다.
정본은 `inherits_map.tsv`(P4 산출, 계층 역전 10건을 끊고 출처는 derives_from 으로
분리한 복원본)이고, frontmatter 와 일치함을 실행 전에 대조한다.

**급소 둘**(CLAUDE.md §1):

- 엣지는 **같은 페이로드의 `nodes` 안에서만** 해석된다. 그래서 배치를 나누지 않고
  54개 노드를 매 호출에 전부 동봉한다. 나누면 배치 간 엣지가 조용히 skip 된다.
- `title` 에는 가드가 없다. 스텁 제목은 문서 frontmatter 의 `title` 을 쓰되, 이미
  같은 id 의 개념이 있으면 **건드리지 않는다**(구작업 스텁과 충돌 방지).

    python -X utf8 load_inheritance.py          # dry-run + 원장↔frontmatter 대조
    python -X utf8 load_inheritance.py --apply
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "Vinc"))
STUB = "concept:doc."


def _ok(r: dict, what: str) -> dict:
    if r.get("isError") or r.get("error") or not r.get("executed", True):
        raise SystemExit(f"[ABORT] {what} 실패: {str(r)[:200]}")
    return r


def _front(path: Path) -> dict:
    """frontmatter 의 doc_id·title·domain·inherits_from 만 뽑는다."""
    out: dict = {}
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k in ("doc_id", "title", "domain", "layer"):
            out[k] = v
        elif k == "inherits_from":
            out[k] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    docs = {}
    for p in sorted((HERE / "rewrite").glob("*.md")):
        f = _front(p)
        if f.get("doc_id"):
            docs[f["doc_id"]] = f
    print(f"재작성 문서 {len(docs)}건")

    ledger = {}
    with (HERE / "inherits_map.tsv").open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            ledger[r["doc_id"]] = {
                "inherits": [x for x in (r.get("inherits_from") or "").split(",") if x],
                "derives": [x for x in (r.get("derives_from") or "").split(",") if x]}

    # 원장 ↔ frontmatter 대조. 어긋나면 멈춘다 — 둘 중 어느 쪽이 맞는지 사람이 정해야 한다.
    drift = []
    for d, f in docs.items():
        led = set(ledger.get(d, {}).get("inherits", []))
        fm = set(f.get("inherits_from") or [])
        if led != fm:
            drift.append((d, sorted(fm), sorted(led)))
    if drift:
        print(f"\n[ABORT] 원장과 frontmatter 가 {len(drift)}건 어긋난다:", file=sys.stderr)
        for d, fm, led in drift[:10]:
            print(f"  {d}: fm={fm} 원장={led}", file=sys.stderr)
        return 2
    print("원장 ↔ frontmatter 일치 확인")

    nodes = [{"id": STUB + d, "type": "concept", "title": f.get("title") or d,
              "domain": f.get("domain") or "", "tags": ["doc", f.get("layer") or ""]}
             for d, f in sorted(docs.items())]
    edges = []
    for d in sorted(docs):
        for parent in ledger.get(d, {}).get("inherits", []):
            if parent in docs:
                edges.append({"from": STUB + d, "to": STUB + parent, "type": "inherits_from"})
            else:
                print(f"  ! {d} 의 상속 대상 {parent} 이 코퍼스 밖 — 건너뜀")
        for src in ledger.get(d, {}).get("derives", []):
            if src in docs:
                edges.append({"from": STUB + d, "to": STUB + src, "type": "derives_from"})

    print(f"\n스텁 {len(nodes)}개 · 계보 엣지 {len(edges)}개 "
          f"(inherits {sum(1 for e in edges if e['type']=='inherits_from')} · "
          f"derives {sum(1 for e in edges if e['type']=='derives_from')})")
    roots = [d for d in docs if not ledger.get(d, {}).get("inherits")]
    print(f"뿌리(상속 없음) {len(roots)}건: {', '.join(roots)}")
    if not a.apply:
        print("\n(dry-run — 쓰려면 --apply)")
        return 0

    from vinc_mcp import remote
    # ⚠ 엣지는 같은 페이로드의 nodes 로만 해석된다. 54 노드는 한 호출에 들어가므로
    #   나누지 않는다 — 나누면 배치 간 엣지가 **경고 없이** skip 된다.
    r = _ok(remote.call("vinc_ingest", {"nodes": nodes, "edges": edges}), "ingest")
    m = r.get("merged", {})
    print(f"\n노드 created {m.get('nodes',{}).get('created')} · "
          f"matched {m.get('nodes',{}).get('matched')} · "
          f"엣지 created {m.get('edges',{}).get('created')}")
    for w in r.get("warnings") or []:
        print(f"  ⚠ {w[:180]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
