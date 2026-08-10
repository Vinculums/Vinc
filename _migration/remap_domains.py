# -*- coding: utf-8 -*-
"""remap_domains.py — 원장 밖 노드의 구 도메인을 신 7종으로 옮긴다.

P5 는 원장 198건만 다뤘다. 원장은 2026-08-06 에 만들어졌으므로 **그 뒤에 생긴 노드**는
구 도메인 문자열을 그대로 달고 있다(실측: vinc/mcp 17 · vinc/architecture 12 ·
vinc/web 4 = 33건).

매핑은 지어내지 않고 **원장의 실적을 따른다**:

    vinc/architecture → vinc/engineering   (원장 20/20 만장일치)
    vinc/mcp          → vinc/engineering   (원장 18/18 만장일치)
    vinc/web          → 갈린다(product 7 · engineering 7 · infra 4) → 사람이 판정

그래서 web 은 자동 매핑하지 않고 목록만 낸다.

열거 방법: `vinc_graph`(depth=1)를 모든 개념에 대해 돌린다. depth 1 응답은 시드와
이웃을 **도메인과 함께** 주므로, 개념 전수를 돌면 붙어 있는 Record·Episode 도 함께
걷힌다. 개념 목록은 `vinc_diagnose`(components + orphans)가 전수로 준다.
depth 2 이상은 결과가 72k 예산을 넘어 못 쓴다(실측).

    python -X utf8 remap_domains.py scan            # 열거만
    python -X utf8 remap_domains.py apply --apply   # 자동 매핑분 적용
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Vinc"))

#: 원장이 실제로 쓴 매핑. 만장일치인 것만 자동으로 옮긴다.
AUTO = {"vinc/architecture": "vinc/engineering",
        "vinc/mcp": "vinc/engineering"}
#: 원장에서 갈린 것 — 사람이 노드별로 판정한다.
MANUAL = {"vinc/web"}
NEW_DOMAINS = {"vinc/engineering", "vinc/governance", "vinc/infra",
               "vinc/product", "vinc/security", "vinc/design", "vinc/ai"}
CACHE = Path(__file__).resolve().parent / "domain-scan.json"


def _ok(r: dict, what: str) -> dict:
    """무음 실패 금지 — remote.call 은 실패를 {error,isError} 로 준다."""
    if r.get("isError") or r.get("error") or not r.get("executed", True):
        raise SystemExit(f"[ABORT] {what} 실패: {str(r)[:200]}")
    return r


def _seed_ids() -> list[str]:
    """순회 시드. **원장 + diagnose** 를 합친다.

    ⚠ diagnose 만으로는 안 된다. 라이브 허브는 아직 F6(무음 절단 제거) 이전 코드라
    `member_limit` 을 무시하고 members 를 50개로 자르면서 `members_truncated` 도 안
    준다 — 실측: 최대 컴포넌트가 `size: 185` 인데 members 는 50건이었다. 그걸 모르고
    돌리면 개념 189 중 54 만 보고 "전수 순회했다"고 착각한다(실제로 그랬다).
    원장(ledger-nodes.tsv)은 파일이라 잘리지 않으므로 그것을 바닥으로 깐다.
    """
    import csv
    ids: set[str] = set()
    led = Path(__file__).resolve().parent / "ledger-nodes.tsv"
    with led.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="	"):
            if r["disposition"] != "drop" and r["kind"] == "concept":
                ids.add(r["id"])
    from vinc_mcp import remote
    diag = _ok(remote.call("vinc_diagnose",
                           {"kind": "components", "member_limit": 1000}), "diagnose")
    ids |= set(diag.get("orphans") or [])
    for c in diag.get("components") or []:
        ids |= set(c.get("members") or [])
    # 커뮤니티 축도 합친다. WCC 컴포넌트는 하나가 거대해서(size 144) 50 상한에 걸리지만
    # Louvain 군집은 12개로 잘게 나뉘어(최대 18) **상한에 안 닿는다** — 같은 절단
    # 버그를 우회하는 두 번째 창이다. 둘을 합쳐야 개념 전수에 가까워진다.
    com = _ok(remote.call("vinc_diagnose",
                          {"kind": "communities", "member_limit": 1000}), "communities")
    for c in com.get("communities") or []:
        ids |= set(c.get("members") or [])
    return sorted(ids)


def scan() -> dict[str, dict]:
    from vinc_mcp import remote
    concepts = _seed_ids()
    print(f"개념 시드 {len(concepts)}건(원장 + diagnose) — depth1 로 순회한다", flush=True)

    seen: dict[str, dict] = {}
    for i, cid in enumerate(concepts, 1):
        g = remote.call("vinc_graph", {"seed_id": cid, "depth": 1})
        if g.get("isError") or g.get("error"):
            print(f"  ! {cid}: {str(g)[:90]}", flush=True)
            continue
        for n in g.get("nodes") or []:
            seen[n["id"]] = {"domain": n.get("domain") or "",
                             "kind": n.get("kind") or "", "label": n.get("label") or ""}
        if i % 25 == 0:
            print(f"  {i}/{len(concepts)} · 수집 {len(seen)}", flush=True)
        time.sleep(0.05)
    CACHE.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["scan", "apply"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if a.stage == "scan" or not CACHE.exists():
        seen = scan()
    else:
        seen = json.loads(CACHE.read_text(encoding="utf-8"))

    stale = {i: v for i, v in seen.items()
             if v["domain"] and v["domain"] not in NEW_DOMAINS}
    print(f"\n수집 {len(seen)}건 · 구 도메인 {len(stale)}건")
    by_dom: dict[str, list] = {}
    for i, v in stale.items():
        by_dom.setdefault(v["domain"], []).append((i, v))
    for d in sorted(by_dom):
        tgt = AUTO.get(d)
        tag = f"→ {tgt}" if tgt else "→ ?? (사람 판정)"
        print(f"\n  [{d}] {len(by_dom[d])}건 {tag}")
        for i, v in sorted(by_dom[d]):
            print(f"    {i:<52} {v['label'][:40]}")

    if a.stage != "apply":
        return 0
    auto = [(i, v, AUTO[v["domain"]]) for i, v in stale.items() if v["domain"] in AUTO]
    print(f"\n자동 매핑 대상 {len(auto)}건 · 사람 판정 대기 "
          f"{sum(1 for _, v in stale.items() if v['domain'] in MANUAL)}건")
    if not a.apply:
        print("(dry-run — 쓰려면 --apply)")
        return 0
    from vinc_mcp import remote
    nodes = [{"id": i, "type": v["kind"], "title": v["label"], "domain": t}
             for i, v, t in auto]
    for k in range(0, len(nodes), 30):
        r = _ok(remote.call("vinc_ingest", {"nodes": nodes[k:k + 30]}), "ingest")
        m = r.get("merged", {}).get("nodes", {})
        print(f"  배치 {k//30+1}: matched {m.get('matched')} created {m.get('created')}")
    print(f"\n적용 {len(nodes)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
