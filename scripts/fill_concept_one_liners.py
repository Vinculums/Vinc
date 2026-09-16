#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Concept.one_liner 을 **이미 있는 문장으로** 채운다. 생성은 없다.

Record/Decision 미러에 개념 one_liner 를 조인하는 것은 재 봤고 졌다
([[record:the-one-liner-join-improves-no-item-and-costs-four-2026-09-16]]).
같은 스냅샷에서 Concept one_liner 는 80/530(15%) 이라, 조인이 손 댈 수 있는 행이
절반뿐이었다. 그래서 다음 수는 검색 코드가 아니라 **칸을 채우는 것**이다.

채우는 재료는 둘뿐이다. 둘 다 그 개념에 이미 붙어 있는 문장이다.

    1. props.one_liner 또는 props.note (쓰기 승격이 놓친 옛 행)
    2. node_context 발췌 중 링크 방법이 title 또는 stem 인 것

`source`(파일 제공처) 와 `fts`(제목 폴백) 는 쓰지 않는다. 한국어 제목 노드의
발췌 81% 가 제공처라 제목과 토큰을 거의 안 나누고, 그걸 칸에 넣으면 조인이
진 것과 같은 오염이 난다
([[record:korean-titled-nodes-answer-with-provenance-excerpts-2026-09-14]]).
이미 칸이 채워진 행은 건너뛴다. 기본은 dry-run.

사용:
    python scripts/fill_concept_one_liners.py --data <store> --owner <owner>
    python scripts/fill_concept_one_liners.py --data <store> --owner <owner> --apply
    python scripts/fill_concept_one_liners.py --data <store> --owner <owner> \\
        --ids-file scripts/fixtures/brief_gold.jsonl

종료코드 1: 스토어에 도달할 수 없다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from api import fragment as frag          # noqa: E402
from api import query as q                # noqa: E402
from store import pkns                    # noqa: E402
from store.backend import open_store      # noqa: E402
from store.config import load_config      # noqa: E402

#: docstore 추출과 같은 상한. 툴팁 한 문단 분량.
ONE_LINER_MAX = 400
#: 제목·어간으로 이 개념에 묶인 발췌만. source 는 파일 전체, fts 는 폴백.
QUOTE_METHODS = ("title", "stem")


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


def clip(text: str) -> str:
    return " ".join(str(text or "").split())[:ONE_LINER_MAX]


def pick_quote(*, one_liner, props, excerpts) -> dict | None:
    """이미 있는 문장 하나. 칸이 채워져 있으면 None (건너뛴다). 생성하지 않는다."""
    if clip(one_liner):
        return None
    p = props_obj(props)
    for key in ("one_liner", "note"):
        v = p.get(key)
        if isinstance(v, str) and clip(v):
            return {"text": clip(v), "src": "props", "cite": key}
    for e in excerpts or []:
        if str(e.get("method") or "") not in QUOTE_METHODS:
            continue
        text = clip(e.get("excerpt") or "")
        if text:
            where = str(e.get("doc_id") or "")
            return {"text": text, "src": "excerpt",
                    "cite": (where + " · " + str(e.get("method") or "")).strip(" ·")}
    return None


def load_id_filter(path: str | None) -> set[str] | None:
    """정답 파일에서 concept id 만. 질문은 읽되 어디에도 다시 쓰지 않는다."""
    if not path:
        return None
    raw = pathlib.Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    rows = json.loads(raw) if raw.lstrip().startswith("[") else [
        json.loads(l) for l in raw.splitlines() if l.strip()]
    return {str(r.get("gold_node_id") or "") for r in rows
            if str(r.get("gold_node_id") or "").startswith("concept:")}


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


def plan_one(store, owner: str, row: dict) -> dict:
    """한 개념의 채움 계획. store 를 읽어 발췌를 붙인다. 쓰지 않는다."""
    nid = row["id"]
    filled = bool(clip(row.get("one_liner")))
    ctx = q.node_context(owner=owner, node=nid, limit=3, store=store)
    quote = pick_quote(one_liner=row.get("one_liner"), props=row.get("props"),
                       excerpts=(ctx.get("excerpts") if ctx.get("executed") else None)
                       or [])
    status = ("filled" if filled
              else "fillable" if quote
              else "empty")
    rec = {"id": nid, "title": row.get("title") or nid,
           "domain": row.get("domain") or "", "status": status}
    if quote:
        rec["src"] = quote["src"]
        rec["cite"] = quote["cite"]
        rec["text"] = quote["text"]
    return rec


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
    src = Counter(r.get("src") for r in rows if r.get("status") == "fillable")
    n = len(rows)
    filled = c.get("filled", 0)
    return {
        "n": n,
        "filled": filled,
        "fillable": c.get("fillable", 0),
        "empty": c.get("empty", 0),
        "filled_rate": (filled / n) if n else 0.0,
        "fillable_from": dict(src),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(사본을 쓸 것 — 단일 라이터)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--apply", action="store_true",
                    help="기본은 계획만. 이 깃발을 켜야 칸을 쓴다.")
    ap.add_argument("--ids-file", help="JSONL/JSON 정답 파일. concept gold_node_id 만 대상")
    ap.add_argument("--limit", type=int, default=0, help="시험용 상한(0=전량)")
    ap.add_argument("--out", help="행 JSON 경로")
    a = ap.parse_args(argv)

    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1

    wanted = load_id_filter(a.ids_file)
    rows = collect(store, a.owner)
    if wanted is not None:
        rows = [r for r in rows if r["id"] in wanted]
    if a.limit:
        rows = rows[:a.limit]

    planned = [plan_one(store, a.owner, r) for r in rows]
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
