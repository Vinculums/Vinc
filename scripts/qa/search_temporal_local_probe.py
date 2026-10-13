"""Synthetic-only comparison against a revision-pinned retrieval module.

Creates fresh temporary stores; never opens an operating store or an eval fixture.
The timings cover FTS/title retrieval and metadata joins without a query encoder.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class CountedStore:
    def __init__(self, store):
        self.store, self.calls = store, 0

    def run(self, *args, **kwargs):
        self.calls += 1
        return self.store.run(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.store, name)


def seed(store, count, *, dense=False):
    from store import pkns
    owner = "user:synthetic-temporal-benchmark"
    records = []
    for i in range(count):
        bucket = i % 10
        vf = "2099" if bucket in (2, 3) else "" if bucket in (4, 5) else "2000"
        vu = "2001" if bucket in (0, 1) else ""
        records.append({"id": pkns.key(owner, f"record:synthetic-{i:06}"),
                        "title": f"Marker Item{i:06}", "vf": vf, "vu": vu,
                        "created": "" if i % 7 == 0 else "2020-01-01T00:00:00Z",
                        "props": json.dumps({"entity": "same-service" if dense else f"service:{i}",
                                             "predicate": "release", "scope": "synthetic",
                                             "value": "stable" if not dense else ("a" if i % 2 else "b")})})
    store.run("UNWIND $rows AS r CREATE (:Record {record_id:r.id, title:r.title, "
              "owner_id:$owner, domain:'vinc/synthetic', props:r.props, created:r.created, "
              "valid_from:r.vf, valid_until:r.vu, updated_at:'2099-01-01T00:00:00Z'})",
              {"rows": records, "owner": owner})
    # A second owner uses identical logical keys and titles under namespacing.
    if pkns.enabled():
        other = "team:synthetic-other"
        foreign = [{**r, "id": pkns.key(other, f"record:synthetic-{i:06}")} for i, r in enumerate(records[:20])]
        store.run("UNWIND $rows AS r CREATE (:Record {record_id:r.id, title:r.title, "
                  "owner_id:$owner, domain:'vinc/synthetic', props:'{}', valid_until:'2099'})",
                  {"rows": foreign, "owner": other})
    docs = []
    for i in range(max(5, count // 10)):
        text = f"Marker synthetic document {i:06}. Original body only."
        docs.append({"id": f"synthetic-doc-{i:06}", "chunk": f"synthetic-doc-{i:06}#00000",
                     "text": text, "hash": hashlib.sha256(text.encode()).hexdigest(),
                     "vu": "2001" if i % 5 == 0 else "", "vf": "2099" if i % 5 == 1 else "2000",
                     "created": "" if i % 11 == 0 else "2020-01-01T00:00:00Z"})
    store.run("UNWIND $rows AS r CREATE (:Chunk {chunk_id:r.chunk, doc_id:r.id, owner_id:$owner, "
              "text:r.text, lex:r.text, char_start:0, char_end:size(r.text), chunk_index:0})",
              {"rows": docs, "owner": owner})
    store.run("UNWIND $rows AS r CREATE (:_DocMeta {doc_id:r.id, owner_id:$owner, title:'Marker document', "
              "content_hash:r.hash, chunk_count:1, valid_from:r.vf, valid_until:r.vu, "
              "created_at:r.created, updated_at:'2099-01-01T00:00:00Z'})", {"rows": docs, "owner": owner})
    store.ensure_search_indexes(dim=4)
    return owner, len(docs)


def behavior(result):
    row_keys = ("id", "doc_id", "text", "snippet", "score", "rrf_score", "distance", "truncated")
    hit_keys = ("id", "title", "kind", "domain", "matched_in", "arms", "distance", "rrf_score",
                "hit_tokens", "title_tokens", "sem_lead", "sem_decisive")
    return {"rows": [{k: r[k] for k in row_keys if k in r} for r in result.get("rows", [])],
            "node_hits": [{k: r[k] for k in hit_keys if k in r} for r in result.get("node_hits", [])],
            **{k: result.get(k) for k in ("arms", "error", "vector_note", "total_count", "has_more",
                                         "validity_filtered", "known_at_filtered", "known_at_unknown")}}


def measure(call, store, repeats):
    call()
    samples, queries = [], []
    result = None
    for _ in range(repeats):
        store.calls = 0
        start = time.perf_counter()
        result = call()
        samples.append((time.perf_counter() - start) * 1000)
        queries.append(store.calls)
    return {"median_ms": round(statistics.median(samples), 3), "max_ms": round(max(samples), 3),
            "samples_ms": [round(s, 3) for s in samples], "run_queries": queries,
            "rows": len(result.get("rows", [])), "node_hits": len(result.get("node_hits", [])),
            "partial": bool(result.get("partial")), "error": result.get("error"),
            "temporal_error": result.get("temporal_error"), "node_error": result.get("node_error")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 5000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dense", action="store_true")
    parser.add_argument("--threads", type=int, help="Control this temporary connection only; omit for native default")
    args = parser.parse_args()
    if sys.platform == "win32":
        package = Path(importlib.util.find_spec("ladybug").origin).parent
        dll_handle = os.add_dll_directory(str(package))
    import ladybug
    from store.backend import open_store
    from store.config import load_config
    from vinc_rag import retrieval
    from api import temporal_read
    assert ladybug.__version__ == "0.21.2"
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["VINC_PK_NAMESPACED"] = "on"
    spec = importlib.util.spec_from_file_location("temporal_baseline", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    receipt = {"engine_distribution": importlib.metadata.version("ladybug"), "engine_native": ladybug.__version__,
               "baseline_source_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
               "workload": "synthetic FTS/title only, no query encoder", "dense_claim_group": args.dense,
               "repeats": args.repeats, "execution_threads": args.threads,
               "results": [], "compatibility": [], "baseline_stability": []}
    for count in args.sizes:
        with tempfile.TemporaryDirectory(prefix="vinc-temporal-benchmark-") as directory:
            cfg = load_config({"data_dir": str(Path(directory) / "db")})
            st = open_store(cfg)
            if args.threads is not None:
                st._conn.set_max_threads_for_exec(args.threads)
            st.provision(dim=4)
            try:
                owner, documents = seed(st, count, dense=args.dense)
                counted = CountedStore(st)
                common = dict(store=counted, cfg=cfg, owner=owner, top_k=5)
                timings = {}
                for name, call in (
                    ("baseline_default", lambda: baseline.search("Marker", **common)),
                    ("candidate_default", lambda: retrieval.search("Marker", **common)),
                    ("candidate_current", lambda: retrieval.search("Marker", mode="current", **common)),
                    ("candidate_historical", lambda: retrieval.search("Marker", mode="historical", **common)),
                ):
                    timings[name] = measure(call, counted, args.repeats)
                receipt["results"].append({"records": count, "documents": documents, "timings": timings})
                control = []
                for _ in range(20):
                    control.append(behavior(baseline.search("Marker", **common)))
                    full_control = behavior(baseline.search("Marker", detail="full", **common))
                    full_control["rows"] = control[-1]["rows"]
                    control.append(full_control)
                    # Use only main code, with neutral reads interleaved to vary execution order.
                    st.run("MATCH (r:Record) WHERE r.owner_id = $owner RETURN count(r) AS n", {"owner": owner})
                receipt["baseline_stability"].append({"records": count,
                    "stable": all(r == control[0] for r in control),
                    "node_ids": [[h["id"] for h in r["node_hits"]] for r in control]})
                for kw in ({}, {"detail": "full"}, {"historical": True}, {"as_of": "2000"},
                           {"known_at": "2001"}, {"as_of": "2000", "known_at": "2020"}):
                    known = kw.get("known_at")
                    old = baseline.search("Marker", **common, **{k: v for k, v in kw.items() if k != "known_at"})
                    if known:
                        old = temporal_read.apply("search", old, store=st, owner=owner, known_at=known)
                    new = retrieval.search("Marker", **common, **kw)
                    receipt["compatibility"].append({"records": count, "params": kw,
                                                      "behavior_equal": behavior(old) == behavior(new),
                                                      "known_scope_equal": (old.get("time_scope") or {}).get("known_at") == (new.get("time_scope") or {}).get("known_at")})
                    if behavior(old) != behavior(new):
                        receipt["compatibility"][-1]["difference"] = {"baseline": behavior(old), "candidate": behavior(new)}
                print(json.dumps(receipt["results"][-1]), flush=True)
            finally:
                st.close()
        args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("compatibility", all(c["behavior_equal"] and c["known_scope_equal"] for c in receipt["compatibility"]), flush=True)


if __name__ == "__main__":
    main()
