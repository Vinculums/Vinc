# -*- coding: utf-8 -*-
"""load_decisions.py — `concept:dec-*` 68건을 `decision:*` 로 옮긴다(스키마 v12).

**전제: 허브가 v12 로 배포돼 있어야 한다.** 그 전에는 `type:"decision"` 이 개방층
(Entity) 으로 라우팅돼 domain·props 가 조용히 사라진다(경고는 뜬다). 확인:

    vinc_ingest 로 decision 하나 넣고 vinc_neighbors 로 domain 이 살아 있는지 본다.

왜 옮기는가: 결정은 "그때 내린 판단"이라 개념(지금 참인 것)과 다른 종류인데, 여태
`concept:dec-*` 라는 **id 접두 관례**로만 표현돼 있었다. 그 관례를 코드가 알아야 해서
병합 후보 탐지에 구조 가드가 박혀 있었다 — 관례가 타입 노릇을 하고 있었다는 뜻이다.

단계 (각각 따로 돌린다):

  nodes   ledger-decisions.tsv 의 67건을 `decision:*` 로 **신규 생성**한다(구 노드는
          그대로 둔 채). 제목·노트는 영어 재작성본이다.
  edges   ledger-decision-edges.tsv 의 판정대로 엣지를 새 id 로 다시 만든다.
          ⚠ 엣지는 **같은 페이로드의 nodes 안에서만** 해석되므로 양 끝 노드를
          스텁으로 동봉한다. 스텁이 제목을 덮지 않도록 title 을 싣지 않는다.
  drop    구 `concept:dec-*` 68건을 삭제한다(DETACH DELETE — 구 엣지가 함께 간다).
          **edges 검증 후에만** 돈다.
  verify  라이브를 조회해 개수를 대조한다.

    python -X utf8 load_decisions.py nodes  [--apply]
    python -X utf8 load_decisions.py edges  [--apply]
    python -X utf8 load_decisions.py drop   --apply
    python -X utf8 load_decisions.py verify

기본은 dry-run 이다. `--apply` 없이는 아무것도 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VINC = HERE.parents[1] / "Vinc"
sys.path.insert(0, str(VINC))

OWNER = "user:019fae48-cc62-7984-b2b0-1698f4de55d4"   # 계정 그래프. 빠뜨리면 user:local 로 샌다
BATCH = 25
DEC_PREFIX = "concept:dec-"


def _remote():
    from vinc_mcp import remote
    return remote


def _rows(name: str) -> list[dict]:
    with (HERE / name).open(encoding="utf-8", newline="") as fh:
        return [r for r in csv.DictReader(fh, delimiter="\t")
                if r.get("old_id") and not r["old_id"].startswith("#")
                or r.get("verdict") and not r["verdict"].startswith("#")]


def _moves() -> list[dict]:
    """이관 대상(DROP 제외)."""
    return [r for r in _rows("ledger-decisions.tsv")
            if r["new_id"] and r["new_id"] != "DROP"]


def _node_payload(r: dict) -> dict:
    n = {"id": r["new_id"], "type": "decision", "title": r["title_en"],
         "domain": r["domain"]}
    if r.get("note_en"):
        n["props"] = {"note": r["note_en"]}
    return n


def _stub(nid: str) -> dict:
    """엣지 해석용 최소 노드(끝점은 같은 payload 안에서만 해석된다).

    ⚠ 2026-08-10 사고: 여기 원래 "title 을 싣지 않는다 — 부분 재전송이 제목을
    덮어쓴다"고 적혀 있었는데 **방향을 거꾸로 읽은 것이었다.** 구 쓰기 경로는
    `title` 을 `node.get("title") or nid` 로 **무조건 SET** 했으므로, 안 싣는 것이
    보존이 아니라 **id 로 덮어쓰기**였다. 이 스크립트로 107개 노드의 제목이 id 가 됐고
    같은 원장으로 되돌렸다.

    서버 쪽은 고쳤다(미지정 시 보존, 신규일 때만 id 로 채움). 그래도 여기서 제목을
    싣지 않는 이유는 그대로다 — 이 스크립트는 제목의 정본이 아니다."""
    kind = nid.split(":", 1)[0] if ":" in nid else "concept"
    return {"id": nid, "type": kind if kind in
            ("concept", "decision", "record", "episode", "bridge") else "concept"}


def cmd_nodes(apply: bool) -> int:
    rows = _moves()
    print(f"신규 생성 {len(rows)}건 (구 노드는 이 단계에서 건드리지 않는다) · 배치 {BATCH}")
    if not apply:
        print(json.dumps(_node_payload(rows[0]), ensure_ascii=False, indent=2)[:500])
        print("\n(dry-run — 쓰려면 --apply)")
        return 0
    remote = _remote()
    done = warn = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        res = remote.call("vinc_ingest", {"owner": OWNER,
                                          "nodes": [_node_payload(r) for r in chunk]})
        if not res.get("executed"):
            print(f"  ✗ 배치 {i//BATCH+1} 실패: {str(res)[:200]}", file=sys.stderr)
            return 1
        m = res.get("merged", {}).get("nodes", {})
        done += m.get("created", 0) + m.get("matched", 0)
        for w in res.get("warnings") or []:
            warn += 1
            print(f"  ⚠ {w[:180]}")
        print(f"  배치 {i//BATCH+1}: created {m.get('created')} · matched {m.get('matched')}")
    print(f"\n적재 {done}/{len(rows)} · 경고 {warn}")
    if warn:
        print("⚠ 경고가 있으면 개방층(Entity)으로 샜을 수 있다 — 허브가 v12 인지 확인할 것",
              file=sys.stderr)
    return 0 if done == len(rows) and not warn else 1


def _live_edges(remote) -> list[dict]:
    """구 DEC 엣지를 라이브에서 읽어 판정표에 따라 새 엣지로 옮긴 목록을 만든다.

    파일에 엣지를 통째로 박아 두지 않는 이유: 원장은 **판정**(어떤 종류로 옮기는가)의
    정본이고, 어떤 엣지가 실제로 있는지는 그래프가 정본이다. 박아 두면 그 사이에
    생긴 엣지가 조용히 빠진다."""
    ids = [r["old_id"] for r in _rows("ledger-decisions.tsv") if r["old_id"]]
    out: list[dict] = []
    seen = set()
    for i in range(0, len(ids), 20):
        res = remote.call("vinc_graph", {"owner": OWNER, "ids": ids[i:i + 20],
                                         "depth": 1, "limit": 500})
        for e in (res.get("edges") or []):
            k = (e.get("source"), e.get("rel"), e.get("target"))
            if k in seen or not all(k):
                continue
            seen.add(k)
            out.append({"from": k[0], "rel": k[1], "to": k[2]})
    return out


def _remap(nid: str, drops: set[str]) -> str | None:
    if nid in drops:
        return None
    return ("decision:" + nid[len(DEC_PREFIX):]) if nid.startswith(DEC_PREFIX) else nid


def _retype(rel: str, a_old: str, b_old: str) -> str | None:
    """판정표(ledger-decision-edges.tsv)의 규칙을 코드로. 반환 = fragment 엣지 타입."""
    a_dec, b_dec = a_old.startswith(DEC_PREFIX), b_old.startswith(DEC_PREFIX)
    if rel == "MENTIONS":
        return None                       # REGEN — 링커가 다시 만든다
    if rel == "RELATED_TO":
        if a_dec and not b_dec:
            return "about"                # 결정 → 개념: 무엇을 두고 내렸는가
        if b_dec and not a_dec:
            return "derives_from"         # 개념 → 결정: 그 결정에서 나왔다
        if a_dec and b_dec:
            return "derives_from"         # 결정끼리의 막연한 "관련"은 계보로
    return {"ABOUT": "about", "DERIVES_FROM": "derives_from",
            "PREREQUISITE_OF": "prerequisite_of", "INHERITS_FROM": "inherits_from",
            "ATTACHES_TO": "relates_to",  # Record 참여 엣지는 서버가 ATTACHES_TO 로 받는다
            "CONNECTS": "connects_via"}.get(rel)


def _planned_edges(remote) -> list[dict]:
    drops = {r["old_id"] for r in _rows("ledger-decisions.tsv") if r["new_id"] == "DROP"}
    plan, skipped = [], []
    for e in _live_edges(remote):
        a, b, rel = e["from"], e["to"], e["rel"]
        if not (a.startswith(DEC_PREFIX) or b.startswith(DEC_PREFIX)):
            continue                       # DEC 와 무관한 엣지는 건드리지 않는다
        na, nb = _remap(a, drops), _remap(b, drops)
        et = _retype(rel, a, b)
        if na is None or nb is None or et is None:
            skipped.append((rel, a, b, "DROP" if (na is None or nb is None) else "REGEN"))
            continue
        # ⚠ 방향 뒤집기 금지 — "개념 → 결정" 을 결정발 ABOUT 으로 돌리면 뜻이 바뀐다
        # ("AP-1 은 릴리스에 관한 결정"). 방향은 그대로 두고 종류만 바꾼다.
        plan.append({"from": na, "to": nb, "type": et, "_was": rel})
    for rel, a, b, why in skipped:
        print(f"  · {why:<5} {rel:<16} {a} -> {b}")
    return plan


def cmd_edges(apply: bool) -> int:
    remote = _remote()
    plan = _planned_edges(remote)
    by_type: dict[str, int] = {}
    for e in plan:
        by_type[f"{e['_was']}→{e['type']}"] = by_type.get(f"{e['_was']}→{e['type']}", 0) + 1
    print(f"\n옮길 엣지 {len(plan)}건")
    for k, v in sorted(by_type.items()):
        print(f"  {k:<34} {v}")
    if not apply:
        print("\n(dry-run — 쓰려면 --apply)")
        return 0
    done = warn = 0
    for i in range(0, len(plan), BATCH):
        chunk = plan[i:i + BATCH]
        ids = {x for e in chunk for x in (e["from"], e["to"])}
        res = remote.call("vinc_ingest", {
            "owner": OWNER,
            "nodes": [_stub(x) for x in sorted(ids)],
            "edges": [{"from": e["from"], "to": e["to"], "type": e["type"]}
                      for e in chunk]})
        if not res.get("executed"):
            print(f"  ✗ 배치 {i//BATCH+1} 실패: {str(res)[:200]}", file=sys.stderr)
            return 1
        m = res.get("merged", {}).get("edges", {})
        done += m.get("created", 0) + m.get("skipped_duplicate", 0)
        for w in res.get("warnings") or []:
            warn += 1
            print(f"  ⚠ {w[:180]}")
        print(f"  배치 {i//BATCH+1}: created {m.get('created')}")
    print(f"\n엣지 {done}/{len(plan)} · 경고 {warn}")
    return 0 if not warn else 1


def cmd_drop(apply: bool) -> int:
    ids = [r["old_id"] for r in _rows("ledger-decisions.tsv") if r["old_id"]]
    print(f"삭제 대상 {len(ids)}건 (DETACH DELETE — 구 엣지가 함께 간다)")
    if not apply:
        for i in ids[:5]:
            print("  " + i)
        print(f"  … 외 {len(ids)-5}건\n\n(dry-run — 지우려면 --apply)")
        return 0
    remote = _remote()
    gone = 0
    for i in ids:
        res = remote.call("vinc_delete", {"owner": OWNER, "node_id": i})
        ok = bool(res.get("found"))
        gone += ok
        if not ok:
            print(f"  · 없음: {i}")
    print(f"\n삭제 {gone}/{len(ids)}")
    return 0


def cmd_verify(_apply: bool) -> int:
    remote = _remote()
    want = len(_moves())
    left = remote.call("vinc_search", {"owner": OWNER, "query": "dec-", "limit": 100})
    old = [h.get("id") for h in (left.get("hits") or [])
           if str(h.get("id", "")).startswith(DEC_PREFIX)]
    got = remote.call("vinc_graph", {"owner": OWNER, "kind": "decision", "limit": 200})
    nodes = [n for n in (got.get("nodes") or []) if n.get("kind") == "decision"]
    print(f"decision 노드   {len(nodes)}/{want}")
    print(f"구 concept:dec- {len(old)} (0 이어야 한다)")
    for i in old:
        print("  남음:", i)
    return 0 if len(nodes) == want and not old else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["nodes", "edges", "drop", "verify"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"nodes": cmd_nodes, "edges": cmd_edges,
            "drop": cmd_drop, "verify": cmd_verify}[a.stage](a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
