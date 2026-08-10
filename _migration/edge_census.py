# -*- coding: utf-8 -*-
"""edge_census.py — 라이브 엣지를 다시 센다(P5 이후).

STATE.md 의 "판단 대상 223건"은 **2026-08-06 인벤토리** 기준이다. 그 뒤 P5 가 문서
54 · doc-concept 39 · 학습자료 53 · 노드 6 을 지웠으므로 판단 표면이 줄었다. 특히
그 223 중 66건이 "한쪽이 문서 스텁"인 `relates_to` 였는데, 그 스텁들이 바로 P5 에서
지운 doc-concept 이다.

낡은 수를 근거로 "발주자 결정 대기"라고 적어 두면 실제로는 없는 일이 블로커로 남는다.
그래서 다시 센다.

열거는 `vinc_graph`(depth=1)를 개념 전수에 돌려 엣지를 모은다. 개념 목록은
원장 + diagnose(components·communities)를 합쳐 만든다 — 라이브 diagnose 는 members 를
50개로 조용히 자르므로 그것만 믿으면 안 된다(F6 미배포).

    python -X utf8 edge_census.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "Vinc"))

#: 판단이 필요한 관계 — 사람이 "이게 맞나"를 물어야 하는 것들.
#: 구조 엣지(ATTACHES_TO·ABOUT·CONNECTS)는 노드가 정해지면 따라오므로 제외한다.
JUDGED = {"RELATED_TO", "DERIVES_FROM", "PREREQUISITE_OF", "INHERITS_FROM"}


def _ok(r: dict, what: str) -> dict:
    if r.get("isError") or r.get("error") or not r.get("executed", True):
        raise SystemExit(f"[ABORT] {what} 실패: {str(r)[:200]}")
    return r


def main() -> int:
    from vinc_mcp import remote
    import csv

    ids: set[str] = set()
    with (HERE / "ledger-nodes.tsv").open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["disposition"] != "drop" and r["kind"] == "concept":
                ids.add(r["id"])
    for kind in ("components", "communities"):
        d = _ok(remote.call("vinc_diagnose", {"kind": kind, "member_limit": 1000}), kind)
        ids |= set(d.get("orphans") or [])
        for c in (d.get("components") or []) + (d.get("communities") or []):
            ids |= set(c.get("members") or [])
    seeds = sorted(ids)
    print(f"개념 시드 {len(seeds)}건 — 엣지를 센다", flush=True)

    edges: dict[tuple, str] = {}
    kinds: dict[str, str] = {}
    for i, cid in enumerate(seeds, 1):
        g = remote.call("vinc_graph", {"seed_id": cid, "depth": 1})
        if g.get("isError") or g.get("error"):
            continue
        for n in g.get("nodes") or []:
            kinds[n["id"]] = n.get("kind") or ""
        for e in g.get("edges") or []:
            edges[(e["source"], e["target"], e["type"])] = e.get("role") or ""
        if i % 40 == 0:
            print(f"  {i}/{len(seeds)} · 엣지 {len(edges)}", flush=True)
        time.sleep(0.04)

    by_type = Counter(t for _, _, t in edges)
    print(f"\n라이브 엣지 {len(edges)}건")
    for t, n in by_type.most_common():
        mark = " ← 판단 대상" if t in JUDGED else ""
        print(f"  {t:<18}{n}{mark}")

    judged = [(s, d, t) for (s, d, t) in edges if t in JUDGED]
    print(f"\n판단 대상 {len(judged)}건 (STATE.md 의 08-06 기준 수치는 223)")

    def side(x: str) -> str:
        return "doc" if x.startswith("concept:doc.") else kinds.get(x, "?")
    pairs = Counter(tuple(sorted((side(s), side(d)))) for s, d, _ in judged)
    print("\n  양 끝 성격:")
    for p, n in pairs.most_common():
        print(f"    {p[0]:<10}↔ {p[1]:<10}{n}")
    (HERE / "edge-census.json").write_text(
        json.dumps({"total": len(edges), "by_type": dict(by_type),
                    "judged": [list(x) for x in judged]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
