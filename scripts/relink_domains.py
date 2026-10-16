#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`domain` 열과 `IN_DOMAIN` 엣지가 어긋난 노드를 찾아 다시 잇는다.

**왜 있는가.** `vinc_domains` 는 노드의 `domain` 열을 세고 `vinc_neighbors domain:…`
는 `IN_DOMAIN` 엣지를 걷는다. 문서 쓰기(미러의 주제·`extract_doc`)가 열만 쓰고 엣지를
안 옮기던 동안 둘이 갈렸고, 부팅 백필은 스키마가 v11 아래일 때 한 번만 돈다 — 그래서
이미 생긴 어긋남은 다음 기동에도 안 낫는다. 쓰기 쪽은 고쳤고 이 스크립트는 **이미 생긴
것**을 치운다. 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S9.

기본은 **드라이런**이다(세기만 한다). ``--apply`` 가 있을 때만 고친다.

    python -X utf8 scripts/relink_domains.py --data <store> --owner <owner>
    python -X utf8 scripts/relink_domains.py --data <store> --owner <owner> --apply
    python -X utf8 scripts/relink_domains.py --data <store> --owner <owner> --emit-ingest

⚠ 스토어는 단일 writer 다. 사이드카·허브가 쥔 DB 는 이 프로세스가 못 연다. 라이브
허브는 공유 스냅샷 사본에서 드라이런으로 **세기만** 하고, 수리는 ``--emit-ingest`` 가
내는 `vinc_ingest` 배치(도메인만 실은 재전송 — 제목·props·태그는 보존된다)로 호스티드
MCP 를 거친다. 그 경로의 `fragment._merge_node` 가 `link_node` 를 부른다.

종료코드: 0 = 어긋남 없음 또는 수리 완료, 1 = 스토어를 못 열었다, 2 = 드라이런에서
어긋남이 있다(CI·셸에서 "고칠 것이 남았다"를 읽을 수 있게).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401

from engines import domain_hierarchy as dh    # noqa: E402
from store import pkns                        # noqa: E402

#: `vinc_ingest` 한 번에 실을 노드 수. `stored` 되읽기가 72,000자 결과 상한에 걸리지
#: 않게 작게 둔다(라이브에서 19 이하로 돌렸다).
INGEST_BATCH = 19


def summarize(rows: list[dict]) -> dict:
    return {"total": len(rows),
            "by_label": dict(sorted(Counter(r["label"] for r in rows).items())),
            "by_kind": dict(sorted(Counter(r["kind"] for r in rows).items()))}


def ingest_batches(rows: list[dict]) -> list[dict]:
    """도메인만 실은 `vinc_ingest` 인자. id 는 **논리 id** 다(물리 키를 벗긴다)."""
    nodes = [{"id": pkns.strip(r["key"]), "type": r["label"].lower(),
              "domain": r["domain"]} for r in rows]
    return [{"nodes": nodes[i:i + INGEST_BATCH]}
            for i in range(0, len(nodes), INGEST_BATCH)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(허브라면 스냅샷 사본)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--apply", action="store_true", help="실제로 다시 잇는다")
    ap.add_argument("--emit-ingest", action="store_true",
                    help="호스티드 vinc_ingest 로 보낼 배치를 JSON 으로 출력")
    ap.add_argument("--show", type=int, default=20, help="표본으로 보일 행 수")
    a = ap.parse_args(argv)

    from store.backend import open_store      # noqa: PLC0415
    from store.config import load_config      # noqa: PLC0415
    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1
    try:
        rows = dh.drift(store, owner=a.owner)
        if a.emit_ingest:
            print(json.dumps(ingest_batches(rows), ensure_ascii=False, indent=1))
            return 0 if not rows else 2
        report = {"owner": a.owner, "mode": "apply" if a.apply else "dry-run",
                  **summarize(rows),
                  "sample": [{k: r[k] for k in ("label", "column", "edges", "kind")}
                             | {"id": pkns.strip(r["key"])} for r in rows[:a.show]]}
        if a.apply and rows:
            report["relinked"] = dh.relink(store, rows)
            report["remaining"] = len(dh.drift(store, owner=a.owner))
        print(json.dumps(report, ensure_ascii=False, indent=1))
        if a.apply:
            return 0 if not report.get("remaining") else 2
        return 0 if not rows else 2
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
