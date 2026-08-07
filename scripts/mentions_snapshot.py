#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""문서별 MENTIONS 스냅샷과 diff — 재청킹 회귀 감시(E3).

**왜 필요한가.** [[vinc.plan.embedding-retrieval]] 이 "이행 전후로 MENTIONS 수를 세어
회귀를 감시할 것"이라 적어 뒀는데 **세는 절차도 스크립트도 없었다.** 계획 문장만 있고
수단이 없으면 감시는 일어나지 않는다 — 이 리포가 이미 한 번 당한 모양이다(모델 비교
스크립트 5종이 "세션 스크래치패드에 있었다"로 남고 소실됐다). 그래서 도구를 리포에 둔다.

**무엇을 재는가.** `(Chunk)-[:MENTIONS]->(Concept)` 을 문서 단위로 집계한다.
`api.docstore.mention_tally` 와 **같은 쿼리**를 쓴다(집계 규칙을 두 벌로 두지 않는다).
문서마다 링크 수(`mentions`) · 서로 다른 개념 수(`concepts`) · 청크 수(`chunks`)를 남긴다.
청크 수를 함께 남기는 이유: MENTIONS 가 줄었을 때 **재청킹 때문인지** 아닌지를 한
파일 안에서 판별할 수 있어야 하기 때문이다.

**이 스냅샷이 실제로 검증하는 것.** 발주자가 고른 안은 부모-자식(parent-child)이라
재청킹이 일어나지 않는다. 따라서 임베딩 이행 전후로 MENTIONS 는 **기준선 그대로여야
한다**. 즉 이것은 손실 대비가 아니라 그 주장의 검증이다. 코드로도 같은 결론이 나온다:
MENTIONS 재링크는 `docstore._relink_mentions` 뿐이고 그것은 `put_doc` 에서만 불린다.
임베딩 워커는 `apply_embeddings` 만 부르므로 청크도 링크도 건드리지 않는다. **차이가
0 이 아니면 부모-자식 구현이 청크를 건드리고 있다는 뜻이다** — 즉시 보고 대상이다.

사용:
    # 기준선 뜨기
    python scripts/mentions_snapshot.py --data ~/.vinc/db --out mentions-baseline.json

    # 이행 후 대조(차이가 있으면 종료코드 1)
    python scripts/mentions_snapshot.py --data ~/.vinc/db --diff mentions-baseline.json

⚠ 허브(호스티드) 그래프는 Azure Files 볼륨에 있어 밖에서 열 수 없다. 허브 기준선은
컨테이너 안에서 돌리거나 스토어 사본에 대고 돌린다. `--owner` 를 주면 그 소유자 것만
센다(호스티드는 단일 스토어 다중 소유자다).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402


def collect(store, *, owner: str | None = None) -> dict:
    """문서별 MENTIONS 집계. `api.docstore.mention_tally` 와 같은 술어를 쓴다.

    owner 술어가 청크 쪽에서 `ch.owner_id = $o OR ch.owner_id = ''` 인 것도 그대로
    옮긴다. 빈 문자열을 함께 받는 것은 owner 스탬핑 이전에 들어온 행 때문이고, 그
    관용을 여기서 빼면 같은 스토어를 두 도구가 다르게 세게 된다."""
    where = ("WHERE c.owner_id = $o AND (ch.owner_id = $o OR ch.owner_id = '') "
             if owner else "")
    note = ""
    try:
        rows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
            + where
            + "RETURN ch.doc_id AS doc_id, c.concept_id AS cid, count(*) AS n",
            {"o": owner} if owner else None) or []
    except Exception as exc:
        # 프로비저닝 안 된 스토어(테이블 부재)는 "링크 0" 과 **다른 사건**이다.
        # 조용히 0 으로 적으면 그 스냅샷을 기준선 삼아 나중에 "변화 없음" 이라는
        # 거짓 통과가 나온다. 사유를 스냅샷 안에 남긴다.
        rows, note = [], f"{type(exc).__name__}: {exc}"

    docs: dict[str, dict] = {}
    for r in rows:
        d = docs.setdefault(str(r.get("doc_id") or ""),
                            {"mentions": 0, "concepts": 0, "chunks": 0})
        d["mentions"] += int(r.get("n") or 0)
        d["concepts"] += 1

    # 청크 수는 따로 센다 — MENTIONS 가 0 인 문서도 스냅샷에 남아야 한다. 링크가
    # 하나도 없는 문서를 목록에서 빼면 "0 이 됐다"와 "원래 없었다"를 구분 못 한다.
    cwhere = "WHERE ch.owner_id = $o OR ch.owner_id = '' " if owner else ""
    try:
        crows = store.run(
            "MATCH (ch:Chunk) " + cwhere
            + "RETURN ch.doc_id AS doc_id, count(*) AS n",
            {"o": owner} if owner else None) or []
    except Exception as exc:
        crows = []
        note = note or f"{type(exc).__name__}: {exc}"
    for r in crows:
        did = str(r.get("doc_id") or "")
        d = docs.setdefault(did, {"mentions": 0, "concepts": 0, "chunks": 0})
        d["chunks"] = int(r.get("n") or 0)

    out = {
        "taken_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "owner": owner or "",
        "totals": {
            "docs": len(docs),
            "mentions": sum(d["mentions"] for d in docs.values()),
            "links": sum(d["concepts"] for d in docs.values()),
            "chunks": sum(d["chunks"] for d in docs.values()),
        },
        "docs": dict(sorted(docs.items())),
    }
    if note:
        out["error"] = note
    return out


def diff(base: dict, now: dict) -> dict:
    """기준선 대비 변화. 사라진 문서 · 새 문서 · 수치가 바뀐 문서를 각각 낸다.

    셋을 나누는 이유: "문서를 지워서 줄었다"와 "링크가 끊겨 줄었다"는 전혀 다른
    사건인데 총합만 보면 같아 보인다."""
    b, n = base.get("docs") or {}, now.get("docs") or {}
    changed = []
    for did in sorted(set(b) & set(n)):
        if b[did].get("mentions") != n[did].get("mentions") \
                or b[did].get("chunks") != n[did].get("chunks"):
            changed.append({
                "doc_id": did,
                "mentions": [b[did].get("mentions"), n[did].get("mentions")],
                "chunks": [b[did].get("chunks"), n[did].get("chunks")],
            })
    bm = base.get("totals", {}).get("mentions", 0)
    nm = now.get("totals", {}).get("mentions", 0)
    return {
        "removed": sorted(set(b) - set(n)),
        "added": sorted(set(n) - set(b)),
        "changed": changed,
        "total_mentions": [bm, nm],
        "delta_pct": round((nm - bm) / bm * 100, 2) if bm else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="스토어 데이터 경로(미지정 시 기본 해석)")
    ap.add_argument("--owner", help="소유자 스코프(호스티드는 단일 스토어 다중 소유자)")
    ap.add_argument("--out", help="스냅샷을 쓸 파일(미지정 시 stdout)")
    ap.add_argument("--diff", dest="base", help="이 기준선과 대조한다")
    args = ap.parse_args()

    store = open_store(load_config({"data_dir": args.data} if args.data else None))
    if store is None:
        print("스토어에 도달할 수 없다", file=sys.stderr)
        return 2

    snap = collect(store, owner=args.owner or None)

    if args.base:
        with open(args.base, encoding="utf-8") as fh:
            base = json.load(fh)
        # 못 읽은 스냅샷을 기준선 삼으면 "변화 없음"이 거짓으로 통과한다. 거부한다.
        for label, snapshot in (("기준선", base), ("현재", snap)):
            if snapshot.get("error"):
                print("{0} 스냅샷이 집계에 실패한 것이라 대조할 수 없다: {1}".format(
                    label, snapshot["error"]), file=sys.stderr)
                return 2
        d = diff(base, snap)
        print(json.dumps(d, ensure_ascii=False, indent=2))
        drifted = bool(d["removed"] or d["added"] or d["changed"])
        # 차이가 있으면 종료코드 1. 부모-자식 이행에서는 차이가 0 이어야 하므로
        # 이 스크립트는 게이트로도 쓸 수 있다(CI · 배포 후 확인).
        print(("MENTIONS 변화 감지: 총 {0} → {1} ({2:+.2f}%)" if drifted
               else "변화 없음: 총 {0} → {1} ({2:+.2f}%)").format(
                   d["total_mentions"][0], d["total_mentions"][1], d["delta_pct"]),
              file=sys.stderr)
        return 1 if drifted else 0

    text = json.dumps(snap, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        t = snap["totals"]
        print("스냅샷: 문서 {0} · MENTIONS {1} · 청크 {2} → {3}".format(
            t["docs"], t["mentions"], t["chunks"], args.out), file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
