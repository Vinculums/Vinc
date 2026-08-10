# -*- coding: utf-8 -*-
"""prune_doc_concepts.py — 사라진 문서를 가리키는 doc-concept 잔여를 지운다.

문서를 적재하면 `concept:doc.<doc_id>` 개념이 함께 생긴다(문서를 그래프에 걸기 위한
손잡이). P5 에서 구 문서 54건을 지웠지만 **그 손잡이는 남는다** — 문서 삭제가
doc-concept 까지 캐스케이드하지 않기 때문이다. 남으면 가리키는 것이 없는 개념이라
검색·이웃·진단에 잡히면서 아무 데도 안 닿는다.

판정은 단순하다: `concept:doc.X` 인데 문서 `X` 가 없으면 잔여다.

    python -X utf8 prune_doc_concepts.py          # dry-run
    python -X utf8 prune_doc_concepts.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "Vinc"))
CACHE = HERE / "domain-scan.json"
PREFIX = "concept:doc."


def _ok(r: dict, what: str) -> dict:
    if r.get("isError") or r.get("error") or not r.get("executed", True):
        raise SystemExit(f"[ABORT] {what} 실패: {str(r)[:200]}")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not CACHE.exists():
        print("domain-scan.json 이 없다 — 먼저 remap_domains.py scan", file=sys.stderr)
        return 3

    from vinc_mcp import remote
    live, cur = set(), None
    while True:
        args = {"limit": 200, "detail": "summary"}
        if cur:
            args["cursor"] = cur
        r = _ok(remote.call("vinc_doc_list", args), "doc_list")
        live |= {d["doc_id"] for d in (r.get("docs") or [])}
        cur = r.get("next_cursor")
        if not cur or not r.get("has_more"):
            break

    seen = json.loads(CACHE.read_text(encoding="utf-8"))
    handles = {i: v for i, v in seen.items() if i.startswith(PREFIX)}
    orphan = {i: v for i, v in handles.items() if i[len(PREFIX):] not in live}
    print(f"살아 있는 문서 {len(live)} · doc-concept {len(handles)} · "
          f"가리키는 문서가 없는 것 {len(orphan)}")
    for i, v in sorted(orphan.items()):
        print(f"  {i:<48} [{v['domain'] or '-'}]")
    if not orphan:
        return 0
    if not a.apply:
        print("\n(dry-run — 지우려면 --apply)")
        return 0
    gone = 0
    for i in sorted(orphan):
        res = remote.call("vinc_delete", {"node_id": i})
        if res.get("executed") and res.get("found"):
            gone += 1
        else:
            print(f"  ✗ {i}: {str(res)[:120]}", file=sys.stderr)
    print(f"\n삭제 {gone}/{len(orphan)}")
    return 0 if gone == len(orphan) else 1


if __name__ == "__main__":
    raise SystemExit(main())
