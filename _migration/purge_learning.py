# -*- coding: utf-8 -*-
"""purge_learning.py — 롤백으로 되살아난 학습자료 문서를 다시 지운다.

P2(2026-08-06)가 학습자료 53건을 지웠는데, 08-07 FTS 손상 복구가 그 이전 시점의 DB
사본으로 되돌리면서 **전부 되살아났다**. 이행 원장의 "P2 완료"는 그 시점에는 참이었고
지금은 아니다 — 재해 복구가 이행 단계를 되감았다는 사실이 원장에 안 적혀 있었다.

식별은 `origin` 이 `WORKSPACES/learning/` 으로 시작하는 것. doc_id 가 해시라
접두로는 못 가른다.

    python -X utf8 purge_learning.py          # dry-run
    python -X utf8 purge_learning.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Vinc"))

PREFIX = "WORKSPACES/learning/"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from vinc_mcp import remote
    docs, cur = [], None
    while True:
        args = {"limit": 200, "detail": "full"}
        if cur:
            args["cursor"] = cur
        r = remote.call("vinc_doc_list", args)
        # 무음 실패 금지 — remote.call 은 실패를 예외가 아니라 {error,isError} 로 준다.
        # 그걸 그냥 `or []` 로 흘리면 "호출 실패"가 "문서 0건"으로 둔갑하고, 그 상태로
        # 삭제 루프를 돌면 아무 일도 안 일어난 것을 성공으로 보고한다.
        if r.get("isError") or not r.get("executed", True) or r.get("error"):
            print(f"[ABORT] doc_list 실패: {str(r)[:200]}", file=sys.stderr)
            return 3
        docs += r.get("docs") or []
        cur = r.get("next_cursor")
        if not cur or not r.get("has_more"):
            break

    targets = [d for d in docs if str(d.get("origin") or "").startswith(PREFIX)]
    keep = len(docs) - len(targets)
    print(f"전체 {len(docs)}건 · 학습자료 {len(targets)}건 · 남길 것 {keep}건")
    for d in targets[:5]:
        print(f"  {d['doc_id']}  {d.get('title')}")
    if len(targets) > 5:
        print(f"  … 외 {len(targets)-5}건")
    if not targets:
        print("\n지울 것 없음")
        return 0
    if not a.apply:
        print("\n(dry-run — 지우려면 --apply)")
        return 0

    gone = 0
    for d in targets:
        res = remote.call("vinc_doc_delete", {"doc_id": d["doc_id"]})
        if res.get("executed") and res.get("found"):
            gone += 1
        else:
            print(f"  ✗ {d['doc_id']}: {str(res)[:120]}", file=sys.stderr)
    print(f"\n삭제 {gone}/{len(targets)}")
    return 0 if gone == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
