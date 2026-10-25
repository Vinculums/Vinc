# -*- coding: utf-8 -*-
"""결정 문 평가 — 심어 둔 위반과 대조군으로 정밀도·재현율을 잰다.

    python -X utf8 scripts/eval_decision_gate.py [--fixture tests/fixtures/decision_gate_eval.json]

픽스처의 노드·엣지를 새 임베디드 스토어(임시 폴더)에 넣고, 경로마다 `api.query.decide_paths`
를 부른 뒤 (경로, id) 쌍으로 센다. 기대한 쌍을 낸 것이 TP, 기대하지 않은 쌍이 FP, 못 낸
기대 쌍이 FN 이다. 대조군(`controls`)은 FP 로 셀 이름을 미리 적어 둔 것이고, 맞춤 종류
(exact·directory·related)가 기대와 같은지도 따로 센다. 결과는 JSON 한 줄이다.

모델은 없고 결과는 결정론이다. 그래서 이 수는 "심어 둔 경우를 규칙이 다 맞히나"를 말할 뿐,
실제 그래프에서 이 목록이 쓸모 있는지는 말하지 않는다(그것은 라이브 관측의 몫이다).
근거: [[vinc.plan.agent-harness-work-order-2026-10-25 §2]]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "decision_gate_eval.json"


def load(store, fixture: dict) -> list[str]:
    """픽스처를 스토어에 넣는다. 돌려주는 것은 쓰기 경고(빠진 노드·엣지가 있으면 여기 보인다)."""
    from api import fragment as frag
    warnings: list[str] = []
    for part in fixture["fragments"]:
        out = frag.merge_fragment({"owner": part["owner"], "nodes": part["nodes"],
                                   "edges": part["edges"]}, store=store)
        warnings.extend(str(w) for w in out.get("warnings") or [])
    return warnings


def score(store, fixture: dict) -> dict:
    """경로마다 결정 문을 돌려 (경로, id) 쌍으로 센다."""
    from api import query as q
    owner = fixture["owner"]
    paths = [c["path"] for c in fixture["cases"]]
    got = q.decide_paths(paths, owner=owner, store=store, limit=1000)
    if not got.get("executed"):
        return {"executed": False, "error": got.get("error") or got.get("note")}
    by_path = {p["path"]: {i["id"]: i["match"] for i in p["governed_by"]} for p in got["paths"]}
    tp = fp = fn = label_ok = 0
    misses, extras, mislabels, control_hits = [], [], [], []
    for case in fixture["cases"]:
        seen = by_path.get(case["path"], {})
        expect = case["expect"]
        for nid, match in seen.items():
            if nid in expect:
                tp += 1
                if match == expect[nid]:
                    label_ok += 1
                else:
                    mislabels.append({"path": case["path"], "id": nid, "got": match,
                                      "want": expect[nid]})
            else:
                fp += 1
                extras.append({"path": case["path"], "id": nid, "match": match})
        for nid in expect:
            if nid not in seen:
                fn += 1
                misses.append({"path": case["path"], "id": nid})
        control_hits.extend({"path": case["path"], "id": c} for c in case["controls"] if c in seen)
    planted = sum(len(c["expect"]) for c in fixture["cases"])
    controls = sum(len(c["controls"]) for c in fixture["cases"])
    return {"executed": True, "cases": len(fixture["cases"]), "planted": planted,
            "controls": controls, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "label_accuracy": round(label_ok / tp, 4) if tp else None,
            "control_hits": control_hits, "misses": misses, "extras": extras,
            "mislabels": mislabels, "verdict": got["verdict"],
            "retired_excluded": got["retired_excluded"],
            "results_excluded": got["results_excluded"],
            "unqualified_paths": got["unqualified_paths"]}


def run(fixture_path: Path = FIXTURE) -> dict:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    tmp = tempfile.mkdtemp(prefix="vinc-gate-eval-")
    saved = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["MEMORY_DATA_DIR"] = os.path.join(tmp, "store")
    store = None
    try:
        from store.backend import open_store
        from store.config import load_config
        store = open_store(load_config())
        store.provision(dim=4)
        warnings = load(store, fixture)
        out = score(store, fixture)
        out["load_warnings"] = warnings
        out["fixture"] = str(fixture_path.relative_to(ROOT)).replace("\\", "/") \
            if fixture_path.is_relative_to(ROOT) else str(fixture_path)
        return out
    finally:
        if store is not None:
            store.close()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args(argv)
    out = run(args.fixture.resolve())
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("executed") and not out["fn"] and not out["fp"] else 1


if __name__ == "__main__":
    sys.exit(main())
