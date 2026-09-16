#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concept.one_liner 을 **이미 있는 문장으로** 채운다. 생성은 없다.

Record/Decision 미러에 개념 one_liner 를 조인하는 것은 재 봤고 졌다
([[record:the-one-liner-join-improves-no-item-and-costs-four-2026-09-16]]).
같은 스냅샷에서 Concept one_liner 는 80/530(15%) 이라, 조인이 손 댈 수 있는 행이
절반뿐이었다. 그래서 다음 수는 검색 코드가 아니라 **칸을 채우는 것**이다.

재료는 하나뿐이다: props.one_liner 또는 props.note. 쓰기 경로의 승격
(`api/fragment.py` `_merge_node`)과 같은 규칙·같은 값이며, 승격이 되살아나기 전에
쓰인 옛 행만 대상이 된다. 문서 발췌는 쓰지 않는다. 발췌는 청크 앞머리라
프런트매터·표 조각이 칸에 영구히 남는다
([[record:korean-titled-nodes-answer-with-provenance-excerpts-2026-09-14]]).
이미 칸이 채워진 행은 건너뛴다. 기본은 dry-run.

사용:
    python scripts/fill_concept_one_liners.py --data <store> --owner <owner>
    python scripts/fill_concept_one_liners.py --data <store> --owner <owner> \\
        --apply --out written.json

`--apply` 는 `--out` 을 요구한다. 쓴 행이 파일에 남아야 되돌릴 수 있다.

종료코드 1: 스토어에 도달할 수 없다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api import fragment as frag          # noqa: E402
from store import pkns                    # noqa: E402
from store.backend import open_store      # noqa: E402
from store.config import load_config      # noqa: E402


def props_obj(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            got = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def pick_quote(*, one_liner, props) -> dict:
    """한 행의 판정: filled(칸이 있다) · fillable(인용할 문장) · empty. 생성하지 않는다.

    고르는 규칙은 쓰기 경로의 승격과 같다: `props.one_liner or props.note`, strip."""
    if str(one_liner or "").strip():
        return {"status": "filled"}
    p = props_obj(props)
    key = "one_liner" if p.get("one_liner") else "note"
    v = p.get(key)
    if isinstance(v, str) and v.strip():
        return {"status": "fillable", "text": v.strip(), "cite": key}
    return {"status": "empty"}


def collect(store, owner: str) -> list[dict]:
    rows = store.run(
        "MATCH (c:Concept) WHERE c.owner_id = $o "
        "RETURN c.concept_id AS id, c.title AS title, c.one_liner AS ol, "
        "c.props AS props, c.domain AS domain",
        {"o": owner}) or []
    out = []
    for r in rows:
        nid = pkns.strip(str(r.get("id") or ""))
        if not nid:
            continue
        out.append({"id": nid, "title": r.get("title") or nid,
                    "one_liner": r.get("ol") or "", "props": r.get("props"),
                    "domain": r.get("domain") or ""})
    return out


def plan_one(row: dict) -> dict:
    """한 개념의 채움 계획. 쓰지 않는다."""
    return {"id": row["id"], "title": row.get("title") or row["id"],
            "domain": row.get("domain") or "",
            **pick_quote(one_liner=row.get("one_liner"), props=row.get("props"))}


def apply_one(store, owner: str, rec: dict) -> dict:
    """fillable 한 행만 칸에 싣는다. props 는 보내지 않는다(통째 교체)."""
    out = frag.merge_fragment(
        {"owner": owner, "nodes": [
            {"id": rec["id"], "type": "concept", "one_liner": rec["text"]}]},
        store=store)
    return {"id": rec["id"], "merged": out.get("merged"),
            "warnings": out.get("warnings") or [],
            "partial": bool(out.get("partial"))}


def summarize(rows: list[dict]) -> dict:
    c = Counter(r["status"] for r in rows)
    cite = Counter(r.get("cite") for r in rows if r.get("status") == "fillable")
    n = len(rows)
    filled = c.get("filled", 0)
    return {
        "n": n,
        "filled": filled,
        "fillable": c.get("fillable", 0),
        "empty": c.get("empty", 0),
        "filled_rate": (filled / n) if n else 0.0,
        "fillable_from": dict(cite),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(사본을 쓸 것 — 단일 라이터)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="기본은 계획만. 이 깃발을 켜야 칸을 쓴다. --out 필수.")
    ap.add_argument("--limit", type=int, default=0, help="시험용 상한(0=전량)")
    ap.add_argument("--out", help="행 JSON 경로")
    a = ap.parse_args(argv)
    if a.apply and not a.out:
        ap.error("--apply 는 --out 이 필요하다: 쓴 행이 남아야 되돌릴 수 있다")

    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1

    rows = collect(store, a.owner)
    if a.limit:
        rows = rows[:a.limit]

    planned = [plan_one(r) for r in rows]
    applied = []
    if a.apply:
        for rec in planned:
            if rec["status"] == "fillable":
                applied.append(apply_one(store, a.owner, rec))
    store.close()

    summary = summarize(planned)
    summary["applied"] = len(applied)
    summary["apply_warnings"] = sum(1 for x in applied if x.get("warnings") or x.get("partial"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if a.out:
        pathlib.Path(a.out).write_text(
            json.dumps({"summary": summary, "rows": planned, "applied": applied},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
