"""VIN-343 보정 준비: 열린 적 없는 핀 사본에서 raw 검색 근거를 수집한다.

운영 정책이나 brief 문턱은 변경하지 않는다. 결과는 검토할 실험 자료다.
질의 해시로 사전에 분리한 calibration/holdout을 유지한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag_eval.evalkit import runtime
from store.backend import open_store
from store.config import load_config
from store.encoder_runtime import fingerprint
from vinc_rag.embed.encoder import Encoder
from vinc_rag import retrieval
from vinc_rag.search_relevance import classify_rows


def split(query):
    return "calibration" if int(hashlib.sha256(query.encode()).hexdigest(), 16) % 2 == 0 else "holdout"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--store-file", type=Path, required=True)
    ap.add_argument("--onnx", type=Path, required=True)
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--allow-unstamped-lock-provenance", action="store_true",
                    help="락 해시와 런타임이 일치하는 구 핀의 도장 부재를 명시하고 raw 근거만 조사")
    args = ap.parse_args()
    entry = runtime.lock_entry(args.store)
    fp = runtime.fingerprint()
    if runtime.runtime_mismatch(entry, fp):
        raise ValueError("query runtime does not match the pinned store")
    if Path(str(args.store_file) + ".wal").exists():
        raise ValueError("WAL beside source pin")
    digest = runtime.file_sha256(args.store_file)
    if digest != entry["sha256"]:
        raise ValueError("source pin hash mismatch")
    encoder_sha = runtime.file_sha256(args.onnx)
    if encoder_sha != entry["encoder_sha256"]:
        raise ValueError("encoder artifact hash mismatch")
    inputs = json.loads(args.inputs.read_text())
    if len({r["query"] for r in inputs}) != len(inputs):
        raise ValueError("duplicate input queries")
    rows_out = []
    with tempfile.TemporaryDirectory(prefix="vinc-relevance-") as folder:
        copy = Path(folder) / "store.db"
        shutil.copyfile(args.store_file, copy)
        copy.chmod(0o600)
        cfg = load_config({"data_dir": str(copy), "embedding_model": entry["encoder"]})
        store = open_store(cfg)
        if store is None:
            raise RuntimeError("cannot open pinned copy")
        try:
            store.provision(dim=cfg.embedding_dim)
            encoder = Encoder(entry["encoder"], onnx_path=str(args.onnx), batch=8)
            for item in inputs:
                qv = encoder.encode([item["query"]], kind="query")[0]
                # 검색 응답이 아니라 순수 raw 근거를 기록한다. 운영 프로필은 사용하지 않는다.
                res = retrieval.search(item["query"], query_embedding=qv, top_k=20,
                                       query_runtime=fingerprint(), owner=entry["owner"],
                                       cfg=cfg, store=store)
                legacy = (args.allow_unstamped_lock_provenance
                          and res.get("encoder_runtime_warning") is True
                          and res.get("vector_note") == retrieval.UNSTAMPED_RUNTIME_NOTE)
                if res.get("error") or ((res.get("vector_note") or res.get("encoder_runtime_warning")) and not legacy):
                    failure = {"status": "failed_not_a_measurement", "runtime": fp,
                               "store_sha256": digest, "encoder_sha256": encoder_sha,
                               "completed_queries": len(rows_out),
                               "diagnostics": {k: res[k] for k in ("error", "vector_note", "encoder_runtime_warning") if k in res}}
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.with_suffix(".failure.json").write_text(json.dumps(failure, indent=2))
                    raise RuntimeError("degraded search is not a calibration measurement")
                fields = ("id", "doc_id", "distance", "arms", "matched_in")
                record = {**item, "split": split(item["query"]),
                          "stored_runtime_stamp_verified": not bool(res.get("encoder_runtime_warning")),
                          "encoder_runtime_note": res.get("vector_note") or ""}
                for key in ("rows", "node_hits"):
                    record[key] = [{k: r[k] for k in fields if k in r} for r in res[key]]
                rows_out.append(record)
        finally:
            store.close()
    if runtime.file_sha256(args.store_file) != digest:
        raise RuntimeError("source pin changed")
    result = {"status": "experimental", "activation": "off", "runtime": fp,
              "runtime_provenance": "immutable lock and actual query packages; per-vector stamps reported separately",
              "eligible_for_production_activation": False,
              "store_sha256": digest, "encoder_sha256": encoder_sha,
              "inputs_sha256": runtime.file_sha256(args.inputs), "per_query": rows_out}
    # calibration의 검색 가능한 정답을 모두 보존하는 최소 node 한도만 제안한다.
    # lexical/graph 근거는 거리에 상관없이 유지한다. brief의 unanswerable은
    # 검색 no-match와 같은 라벨이 아니므로 negative로 사용하지 않는다.
    positive_distances = []
    for item in rows_out:
        if item["split"] != "calibration" or not item.get("answerable"):
            continue
        for hit in item["node_hits"]:
            if hit["id"] != item.get("gold_node_id"):
                continue
            candidate = classify_rows([hit], {"id": "probe", "max_distance": {"node": 0}}, kind="node")[0]
            if candidate["relevance"]["basis"] == "calibrated_vector":
                positive_distances.append(hit["distance"])
    if positive_distances:
        limit = max(positive_distances)
        result["proposal"] = {"node_max_distance": limit, "chunk_limit": "not calibrated",
                              "status": "experimental", "positive_vector_calibration": len(positive_distances)}
        counts = {}
        for name in ("calibration", "holdout"):
            sample = [r for r in rows_out if r["split"] == name]
            negative = [r for r in sample if r.get("search_no_match_control")]
            positive = [r for r in sample if r.get("answerable")]
            def classified(item):
                return classify_rows(item["node_hits"], {"id": "probe", "max_distance": {"node": limit}}, kind="node")
            baseline_found = [r for r in positive if any(h["id"] == r["gold_node_id"] for h in r["node_hits"])]
            lost = [r["query"] for r in baseline_found if not any(h["id"] == r["gold_node_id"]
                    and h["relevance"]["level"] != "weak" for h in classified(r))]
            counts[name] = {"negative_queries": len(negative),
                            "negative_with_primary": sum(any(h["relevance"]["level"] != "weak" for h in classified(r)) for r in negative),
                            "baseline_gold_found": len(baseline_found), "gold_lost": lost}
        result["counterfactual_node_labels"] = counts
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "per_query"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
