#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Option C 측정 — `VINC_BRIEF_CHOOSE_MATERIAL` 을 켠 brief 가 choose 에서
첫 후보 둘레 재료를 싣는가. 호스트 모델은 부르지 않는다.

N2 팔 (b) 는 이미 같은 되묻기를 하네스 쪽에서 한다. 이 스크립트가 재는 것은
**제품 표면**: 플래그를 켜면 choose 응답에 `candidate_material` 이 붙고 status 는
choose 로 남는가, 그리고 그 재료가 정답 노드인가. 정답이 1위가 아닌 choose
목록에서 landed 가 뜨는 횟수는 틀린 노드에 재료를 붙이는 횟수다
([[record:most-wrong-first-choose-lists-never-had-the-gold-as-a-candidate-2026-09-13]]).
기본값을 켜는 판정은 이 숫자가 나온 뒤에 한다
([[decision:option-c-assembly-is-built-behind-a-flag-left-off-2026-09-13]]).

플래그는 이 프로세스가 켠다. 허브 env 는 건드리지 않는다.

사용:
    python scripts/eval_choose_material.py --data <store> --owner <owner> \\
        --gold scripts/fixtures/brief_gold.jsonl --out report.json

종료코드 1: 채점할 것이 없다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from api import query as q                      # noqa: E402
from store.backend import open_store            # noqa: E402
from store.config import load_config            # noqa: E402

import eval_answer_material as n2               # noqa: E402
import eval_brief_resolution as n1              # noqa: E402

_FLAG = "VINC_BRIEF_CHOOSE_MATERIAL"


def _bytes(out: dict) -> str:
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


def strip_material(out: dict) -> dict:
    got = dict(out)
    got.pop("candidate_material", None)
    return got


def classify(off: dict, on: dict, gold: str, answerable: bool) -> dict:
    """플래그 끄고/켠 brief 한 쌍. 질문은 담지 않는다."""
    status = off.get("status") or "none"
    first = ((off.get("candidates") or [{}])[0].get("id") or "") if status == "choose" else ""
    gold_first = bool(gold) and first == gold
    mat = on.get("candidate_material") if isinstance(on.get("candidate_material"), dict) else {}
    landed = bool(mat.get("landed"))
    landed_id = ((mat.get("node") or {}).get("id") or "") if landed else ""
    identical = _bytes(off) == _bytes(strip_material(on))
    return {
        "answerable": answerable,
        "gold": gold or "",
        "status": status,
        "first": first,
        "gold_first": gold_first,
        "has_material": "candidate_material" in on,
        "landed": landed,
        "landed_id": landed_id,
        "landed_is_gold": bool(gold) and landed_id == gold,
        "would_attach_wrong": bool(answerable) and status == "choose" and landed and (
            (not gold_first) or (landed_id != gold)),
        "identical_except_material": identical,
        "bytes_off": n2.nbytes(off),
        "bytes_on": n2.nbytes(on),
    }


def summarize(rows: list[dict]) -> dict:
    choose = [r for r in rows if r["status"] == "choose"]
    gold_first = [r for r in choose if r["gold_first"]]
    wrong_first = [r for r in choose if r["answerable"] and not r["gold_first"]]
    non_choose = [r for r in rows if r["status"] != "choose"]
    return {
        "n": len(rows),
        "by_status": dict(Counter(r["status"] for r in rows)),
        "choose": {
            "n": len(choose),
            "landed": sum(r["landed"] for r in choose),
            "gold_first": {
                "n": len(gold_first),
                "landed": sum(r["landed"] for r in gold_first),
                "landed_is_gold": sum(r["landed_is_gold"] for r in gold_first),
            },
            "wrong_first": {
                "n": len(wrong_first),
                "landed": sum(r["landed"] for r in wrong_first),
                "would_attach_wrong": sum(r["would_attach_wrong"] for r in wrong_first),
            },
        },
        "non_choose_identical": all(r["identical_except_material"] for r in non_choose),
        "choose_identical_except_material": all(r["identical_except_material"] for r in choose),
        "bytes_delta_mean": (
            sum(r["bytes_on"] - r["bytes_off"] for r in rows) / len(rows) if rows else 0.0),
        "commit": n2.tree_ref(),
    }


def brief_pair(query: str, *, owner: str, store, query_embedding=None) -> tuple[dict, dict]:
    env = os.environ
    prev = env.get(_FLAG)
    try:
        env.pop(_FLAG, None)
        off = q.brief(query, owner=owner, store=store, query_embedding=query_embedding)
        env[_FLAG] = "on"
        on = q.brief(query, owner=owner, store=store, query_embedding=query_embedding)
    finally:
        if prev is None:
            env.pop(_FLAG, None)
        else:
            env[_FLAG] = prev
    return off, on


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(사본을 쓸 것 — 단일 라이터)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--gold", default=str(n2.GOLD))
    ap.add_argument("--out", help="보고서 JSON 경로")
    ap.add_argument("--encoder", help="이름=onnx. 없으면 제목 팔만")
    a = ap.parse_args(argv)

    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1

    emb = None
    if a.encoder:
        from encoder import Encoder                                    # noqa: PLC0415
        name, path = a.encoder.split("=", 1)
        enc = Encoder(name, path)
        enc.encode(["warmup"], kind="query")
        emb = lambda t: enc.encode([t], kind="query")[0]               # noqa: E731

    gold_rows = n1.load_gold([a.gold])
    rows = []
    for r in gold_rows:
        qv = emb(r["query"]) if emb else None
        off, on = brief_pair(r["query"], owner=a.owner, store=store, query_embedding=qv)
        rec = classify(off, on, r.get("gold_node_id") or "",
                       bool(r.get("answerable", True)))
        rec["gold_kind"] = r.get("gold_kind") or ""
        rec["axis"] = r.get("language_axis") or ""
        rows.append(rec)
    store.close()

    if not rows:
        print("채점할 행이 0건이다", file=sys.stderr)
        return 1
    report = summarize(rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.out:
        pathlib.Path(a.out).write_text(
            json.dumps({"report": report, "rows": rows}, ensure_ascii=False) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
