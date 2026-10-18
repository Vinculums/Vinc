#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""색인 유지 모드 프로브(VIN-236 → VIN-662) — 프로브 브랜치에만 있다. main 에 올리지 않는다.

묻는 것: `VINC_KEEP_VECTOR_INDEX=on`(임베딩 적용이 청크 벡터 색인을 드롭하지 않고, 벡터 질의가
문장마다 배타로 돈다)이 허브 조건에서 동시 검색과 함께 세그폴트 없이 도는가. 그리고 검색이
얼마나 기다리는가.

허브 조건(VIN-662, 2026-10-18): 2 vCPU · 4GiB 컨테이너, ladybug 0.21.2, 체크포인트 임계 4MB(기본),
WAL 관용 재생, 실제 bge-m3 int8 벡터(이 리포의 문서를 워커와 같은 조각내기·접두로 인코딩), 16k 베이스.
첫 판(VIN-236, 2026-10-03)은 1 vCPU · 2GiB · ladybug 0.20.4 였다.

  bank   코퍼스를 워커 경로(`_split_for_embed` → `_prefix` → `_Embedder.encode`)로 인코딩
  base   bank 를 ~16k 벡터까지 불려(실벡터 + 작은 잡음) 드롭 모드로 채우고 색인을 한 번 세운다
  run    베이스 사본에서 시행마다 자식 하나:
           keep       제품 경로 그대로(플래그 켬)
           unguarded  플래그는 켜되 질의 가드만 끈 대조 — 같은 조건에서 죽는지 보는 양성 대조

시행 안: 라이터가 실제 문서를 put_doc → apply_embeddings(실벡터)로 넣고, 여섯 번에 한 번
지우고, 여덟 번에 한 번 같은 문서를 재적용한다. 리더 스레드 둘이 각자 리더 커넥션에서
vector_search · fulltext_search · 평범한 MATCH 를 쉬지 않고 돈다(허브의 요청 스레드 모양).
세그폴트는 예외가 아니라 프로세스 사망이라 자식의 종료 코드로 센다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OWNER = "user:probe"
DIM = 1024


def _open(data: str):
    from store.backend import open_store
    from store.config import load_config
    from store.embedded_backend import EmbeddedBackend
    s = open_store(load_config({"data_dir": data}))
    if s is None:
        print("RESULT " + json.dumps({"open_error": EmbeddedBackend.last_open_error}), flush=True)
        sys.exit(3)
    s.provision(dim=DIM)
    for n in ("heal_fts_index", "heal_vector_index"):          # 허브 기동과 같은 순서
        getattr(s, n)(dim=DIM)
    return s


def _chunks(s, doc: str) -> list[dict]:
    return s.run("MATCH (c:Chunk) WHERE c.doc_id = $d "
                 "RETURN c.chunk_id AS id, c.text AS text ORDER BY c.chunk_index", {"d": doc}) or []


def _put(s, doc: str, text: str, title: str) -> list[dict]:
    from api import docstore as ds
    r = ds.put_doc(s, owner=OWNER, text=text, doc_id=doc, title=title, link=False)
    if not r.get("executed"):
        raise RuntimeError(f"put_doc: {r}")
    return _chunks(s, doc)


def _apply(s, doc: str, cids: list[str], pieces: list) -> None:
    from api import docstore as ds
    r = ds.apply_embeddings(s, doc, {c: [v.tolist() for v in p] for c, p in zip(cids, pieces)})
    if not r.get("executed"):
        raise RuntimeError(f"apply_embeddings: {r}")


class Bank:
    """문서별 (본문, 청크별 조각 벡터 목록). 벡터는 float32 단위벡터."""

    def __init__(self, path: str):
        import numpy as np
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        self.vecs = z["vecs"]
        self.docs = []
        at = 0
        for d in meta["docs"]:
            per = []
            for n in d["pieces"]:
                per.append(self.vecs[at:at + n])
                at += n
            self.docs.append({"name": d["name"], "text": d["text"], "pieces": per})


# ── bank ─────────────────────────────────────────────────────────────────────

def cmd_bank(a) -> int:
    import numpy as np
    from vinc_rag.embed import worker as ew      # 워커는 packaging/ 에서 여기로 옮겨졌다
    files = sorted(f for g in a.corpus.split(",") for f in glob.glob(str(ROOT / g), recursive=True))
    files = files[::a.stride]          # 크기 분포는 그대로 두고 인코딩 시간만 줄인다
    s = _open(a.work)
    emb = ew._Embedder()
    docs, vecs, t0 = [], [], time.monotonic()
    for i, f in enumerate(files):
        text = Path(f).read_text(encoding="utf-8", errors="replace").strip()
        if len(text) < 200:
            continue
        rows = _put(s, f"c{i:04d}", text, Path(f).name)
        per_chunk = [ew._split_for_embed(r["text"] or "") for r in rows]
        flat = [ew._prefix(p, "passage") for parts in per_chunk for p in parts]
        out = np.asarray(emb.encode(flat), dtype=np.float32)
        out /= np.linalg.norm(out, axis=1, keepdims=True)
        vecs.append(out)
        docs.append({"name": str(Path(f).relative_to(ROOT)), "text": text,
                     "pieces": [len(p) for p in per_chunk]})
        print(f"bank {len(docs)} {sum(len(v) for v in vecs)} {time.monotonic() - t0:.0f}s", flush=True)
    s.close()
    meta = {"docs": docs, "model": ew.MODEL, "secs": round(time.monotonic() - t0, 1)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.out, vecs=np.concatenate(vecs), meta=np.array(json.dumps(meta)))
    print(json.dumps({"docs": len(docs), "vectors": int(sum(len(v) for v in vecs)),
                      "chars": sum(len(d["text"]) for d in docs), "secs": meta["secs"]}))
    return 0


# ── base ─────────────────────────────────────────────────────────────────────

def cmd_base(a) -> int:
    """드롭을 켠 제품 기본으로 채우고 끝에 한 번 세운다. 사본마다 실벡터에 작은 잡음을 얹는다
    (같은 벡터가 여러 벌이면 HNSW 에 병적인 동률이 생긴다)."""
    import numpy as np
    bank = Bank(a.bank)
    rng = np.random.default_rng(7)
    s = _open(a.data)
    n, rep = 0, 0
    while n < a.base_vectors:
        for j, d in enumerate(bank.docs):
            rows = _put(s, f"base.{rep:02d}.{j:04d}", d["text"], d["name"])
            if len(rows) != len(d["pieces"]):
                raise RuntimeError(f"chunking drifted on {d['name']}: {len(rows)} vs {len(d['pieces'])}")
            noisy = []
            for p in d["pieces"]:
                v = p + rng.normal(0, a.noise, p.shape).astype(np.float32)
                noisy.append(v / np.linalg.norm(v, axis=1, keepdims=True))
            _apply(s, f"base.{rep:02d}.{j:04d}", [r["id"] for r in rows], noisy)
            n += sum(len(p) for p in d["pieces"])
            if n >= a.base_vectors:
                break
        rep += 1
        print(f"base {n}", flush=True)
    t0 = time.monotonic()
    s.ensure_search_indexes(dim=DIM)
    build = round(time.monotonic() - t0, 1)
    s.run("CHECKPOINT")
    s.close()
    print(json.dumps({"vectors": n, "build_s": build}))
    return 0


# ── trial child ──────────────────────────────────────────────────────────────

def _lat(xs: list[float]) -> dict:
    if not xs:
        return {}
    xs = sorted(xs)
    return {"n": len(xs), "p50_ms": round(1000 * xs[len(xs) // 2], 1),
            "p95_ms": round(1000 * xs[int(len(xs) * 0.95)], 1), "max_ms": round(1000 * xs[-1], 1)}


def _reader(s, bank, seed: int, stop, st: dict) -> None:
    import numpy as np
    rng = np.random.default_rng(seed)
    rv = s.reader_view()
    vl, fl = [], []
    words = [w for d in bank.docs[:40] for w in d["text"].split()[:200] if len(w) > 3]
    while not stop.is_set():
        d = bank.docs[int(rng.integers(len(bank.docs)))]
        p = d["pieces"][int(rng.integers(len(d["pieces"])))]
        q = p[int(rng.integers(len(p)))]
        for kind, fn, lat in (
                ("vector", lambda: rv.vector_search([float(x) for x in q], 10), vl),
                ("fts", lambda: rv.fulltext_search(" ".join(rng.choice(words, 3)), 10), fl),
                ("match", lambda: rv.run("MATCH (c:Chunk) RETURN count(c) AS c"), None)):
            t0 = time.perf_counter()
            try:
                fn()
                if lat is not None:
                    lat.append(time.perf_counter() - t0)
                st[kind] = st.get(kind, 0) + 1
            except Exception as exc:        # noqa: BLE001 — 세는 것이 목적이다
                st["err"] = st.get("err", 0) + 1
                st["err_last"] = f"{kind}: {type(exc).__name__}: {str(exc)[:160]}"
    rv.close()
    st["vector_lat"], st["fts_lat"] = _lat(vl), _lat(fl)


def cmd_trial(a) -> int:
    import faulthandler
    import numpy as np
    if sys.platform.startswith("linux"):
        import ctypes
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)          # PR_SET_DUMPABLE=0 — 코어 덤프 끔
    faulthandler.enable()
    from contextlib import nullcontext
    from api import docstore as ds
    from store import embedded_backend as eb
    if a.mode == "unguarded":
        eb._vector_query_guard = nullcontext            # 질의 가드만 끈다(양성 대조)
    bank = Bank(a.bank)
    s = _open(a.data)
    out = {"keep": eb.keep_vector_index(), "index_at_start": s._has_index("ChunkVec", s._VEC_INDEX)}
    if not out["keep"]:
        print("RESULT " + json.dumps(dict(out, error="keep_vector_index() is false")), flush=True)
        return 4
    rng = np.random.default_rng(a.seed)
    stop = threading.Event()
    rst = [{}, {}]
    ths = [threading.Thread(target=_reader, args=(s, bank, a.seed * 10 + k, stop, rst[k]), daemon=True)
           for k in range(2)]
    for t in ths:
        t.start()
    # 가장 큰 문서를 반드시 한 번 넣는다 — 긴 쓰기가 검색을 얼마나 막는지가 여기서 나온다.
    order = [max(range(len(bank.docs)), key=lambda j: len(bank.docs[j]["text"]))]
    order += [int(rng.integers(len(bank.docs))) for _ in range(a.docs - 1)]
    live: list[tuple[str, int]] = []
    st = {"put": 0, "deleted": 0, "requeued": 0, "self_n": 0, "self1": 0, "stale_n": 0, "stale": 0}
    wl = []
    for i, j in enumerate(order):
        d, doc = bank.docs[j], f"t.{a.seed}.{i:03d}"
        t0 = time.perf_counter()
        rows = _put(s, doc, d["text"], d["name"])
        _apply(s, doc, [r["id"] for r in rows], d["pieces"])
        wl.append(time.perf_counter() - t0)
        st["put"] += 1
        live.append((doc, j))
        # 같은 문서가 한 시행에 두 번 들어갈 수 있다(벡터가 똑같다) — 상위 10 안에 있으면 된다.
        hit = s.vector_search([float(x) for x in d["pieces"][0][0]], 10)
        st["self_n"] += 1
        st["self1"] += rows[0]["id"] in [h["id"] for h in hit]
        if i % 6 == 5 and len(live) > 2:
            gone, gj = live.pop(int(rng.integers(len(live) - 1)))
            vid = _chunks(s, gone)[0]["id"] + "#000"
            ds.delete_doc(s, gone, owner=OWNER)
            st["deleted"] += 1
            # 부모(Chunk)로 되돌리면 지운 행은 어차피 걸러진다 — 색인 자체를 본다.
            with eb._vector_query_guard():
                hit = s.run(f"CALL QUERY_VECTOR_INDEX('ChunkVec','{s._VEC_INDEX}', $e, 10) "
                            "RETURN node.vec_id AS v",
                            {"e": [float(x) for x in bank.docs[gj]["pieces"][0][0]]}) or []
            st["stale_n"] += 1
            st["stale"] += any(h["v"] == vid for h in hit)
        if i % 8 == 7 and live:
            again, aj = live[int(rng.integers(len(live)))]
            ds.reembed_doc(s, again, owner=OWNER)
            _apply(s, again, [r["id"] for r in _chunks(s, again)], bank.docs[aj]["pieces"])
            st["requeued"] += 1
        print(f"P {i + 1}", flush=True)
    stop.set()
    for t in ths:
        t.join(60)
    # 증분으로 유지된 색인과 새로 세운 색인이 같은 질의에 같은 상위 10 을 주는가.
    qs = [bank.docs[int(rng.integers(len(bank.docs)))]["pieces"][0][0] for _ in range(20)]
    inc = [[h["id"] for h in s.vector_search([float(x) for x in q], 10)] for q in qs]
    t0 = time.perf_counter()
    s.drop_vector_index()
    s.ensure_search_indexes(dim=DIM)
    rebuild_s = round(time.perf_counter() - t0, 1)
    reb = [[h["id"] for h in s.vector_search([float(x) for x in q], 10)] for q in qs]
    overlap = sum(len(set(x) & set(y)) for x, y in zip(inc, reb)) / max(1, sum(len(y) for y in reb))
    s.close()
    out.update(st, write_lat=_lat(wl), readers=rst, overlap_at_10=round(overlap, 3),
               rebuild_s=rebuild_s)
    print("RESULT " + json.dumps(out), flush=True)
    return 0


# ── parent ───────────────────────────────────────────────────────────────────

def _kind(rc) -> str:
    return {0: "ok", -11: "segv", 139: "segv", -6: "abort", 134: "abort",
            -9: "killed", 137: "killed"}.get(rc, f"rc{rc}")


def cmd_run(a) -> int:
    work = Path(a.work)
    rows = []
    env = dict(os.environ, VINC_KEEP_VECTOR_INDEX="on", VINC_WAL_REPLAY_TOLERANT="on")
    try:
        import ladybug
        engine = ladybug.__version__
    except Exception as exc:        # noqa: BLE001
        engine = f"unavailable: {exc}"
    for t in range(a.trials):
        seed = a.seed0 + t
        d = work / f"t{seed}"
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(a.base, d)
        args = [sys.executable, "-X", "utf8", __file__, "trial", "--mode", a.mode, "--bank", a.bank,
                "--data", str(d / "db"), "--seed", str(seed), "--docs", str(a.docs)]
        t0 = time.monotonic()
        try:
            cp = subprocess.run(args, capture_output=True, text=True, timeout=a.timeout, env=env)
            rc, so, se = cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired as exc:
            rc, so, se = "timeout", str(exc.stdout or ""), str(exc.stderr or "")
        rec = {"mode": a.mode, "seed": seed, "rc": rc,
               "kind": "timeout" if rc == "timeout" else _kind(rc),
               "secs": round(time.monotonic() - t0, 1)}
        for line in so.splitlines():
            if line.startswith("RESULT "):
                rec.update(json.loads(line[7:]))
            elif line.startswith("P "):
                rec["progress"] = line[2:]
        if rec["kind"] != "ok":
            i = se.find("Fatal Python error")
            rec["stderr"] = se[i:i + 2500] if i >= 0 else se[-2500:]
        rows.append(rec)
        print(json.dumps(rec), flush=True)
        shutil.rmtree(d, ignore_errors=True)
        Path(a.out).write_text(json.dumps({"engine": engine, "trials": rows}, indent=1), encoding="utf-8")
    ok = sum(r["kind"] == "ok" for r in rows)
    segv = sum(r["kind"] == "segv" for r in rows)
    vmax = max((r.get("readers", [{}])[k].get("vector_lat", {}).get("max_ms", 0)
                for r in rows for k in range(len(r.get("readers", [])))), default=0)
    line = (f"| {a.mode} | {engine} | {len(rows)} | {ok} | {segv} | {len(rows) - ok - segv} | "
            f"{vmax} |")
    print(line)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("| mode | engine | trials | ok | segv | other | vector max ms |\n"
                    "|---|---|---|---|---|---|---|\n" + line + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bank")
    b.add_argument("--work", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--corpus", default="_migration/**/*.md")
    b.add_argument("--stride", type=int, default=1)
    b = sub.add_parser("base")
    b.add_argument("--bank", required=True)
    b.add_argument("--data", required=True)
    b.add_argument("--base-vectors", type=int, default=16000)
    b.add_argument("--noise", type=float, default=0.008)
    b = sub.add_parser("trial")
    b.add_argument("--mode", choices=("keep", "unguarded"), required=True)
    b.add_argument("--bank", required=True)
    b.add_argument("--data", required=True)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--docs", type=int, default=30)
    b = sub.add_parser("run")
    b.add_argument("--mode", choices=("keep", "unguarded"), required=True)
    b.add_argument("--bank", required=True)
    b.add_argument("--base", required=True)
    b.add_argument("--work", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--trials", type=int, default=10)
    b.add_argument("--seed0", type=int, default=0)
    b.add_argument("--docs", type=int, default=30)
    b.add_argument("--timeout", type=float, default=1200)
    a = ap.parse_args()
    return {"bank": cmd_bank, "base": cmd_base, "trial": cmd_trial, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
