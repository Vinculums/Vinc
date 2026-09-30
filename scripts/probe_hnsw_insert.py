#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HNSW 삽입 프로브 — 벡터 색인을 **드롭하지 않고** 임베딩을 적용하면 엔진이 죽는가.

묻는 것 하나: 쓰기 경로의 벡터 색인 드롭(`api.docstore._drop_vector_index_if_any`)이 지금
엔진에서도 필요한가. 드롭의 근거는 2026-07-31 ladybug 0.18.x 실측(색인이 걸린 채 없던 벡터가
생기면 수백 건에서 세그폴트)이고, 그 대가로 임베딩 대기가 있는 동안 청크 의미 검색이 꺼진다.
근거: [[vinc.plan.embedding-pipeline §1]] ·
[[record:a-soft-requeue-keeps-semantic-search-off-because-every-apply-rebuilds-the-vector-index-2026-09-30]]

시행 하나가 **자식 프로세스 하나**다. 세그폴트는 예외가 아니라 프로세스 사망이라 종료 코드로만
센다. 자식은 드롭만 끄고(`_drop_vector_index_if_any` 를 무동작으로 바꾼다) 나머지는 제품 경로
(`put_doc` → `apply_embeddings`)를 그대로 탄다. 열 때는 허브 기동과 같이 기동 수리를 돈다.

시나리오:
  fill        빈 스토어에 청크 N개(기본 2,000) — 문서마다 put_doc → apply
  setnull     옛 모양 그대로 — 색인 걸린 ChunkVec 에 벡터 없는 행을 넣고 나중에 SET
  big         벡터 16k 베이스 사본에 문서 1,000개
  interleave  fill 과 같되 리더 커넥션 스레드가 쉬지 않고 QUERY_VECTOR_INDEX
  churn       베이스 사본에서 삭제 후 재삽입과 같은 문서 재적용(소프트 재큐)을 번갈아
  crash       베이스 사본에 쓰다가 kill -9 → 재오픈(WAL 재생) → 기동 수리 → 검사 → 더 쓰기
  crashctl    crash 의 대조군 — 드롭을 켠 제품 기본 그대로 같은 kill 을 한다

정확성: 새 벡터로 자기를 질의해 top-1 이 자기인지(self@1), 무작위 질의의 recall@10 을 numpy
전수 계산과 대조하고, 끝에 색인을 새로 세워 같은 질의의 recall 과 견준다. 지운·바뀐 벡터가
여전히 거리 0 으로 잡히면 stale 로 센다. 크래시만 안 나고 색인이 틀리면 안전이 아니다.

사용(리눅스, 의존은 ladybug·numpy 둘):
  python scripts/probe_hnsw_insert.py run --work /tmp/p --trials 5 --out r.json
  python scripts/probe_hnsw_insert.py run --work /tmp/p --scenarios fill,setnull --dim 8

⚠ WSL 에서 돌리면 세그폴트 한 번이 코어 덤프 8~11GB 로 C 를 채운다(ulimit 무효). 그래서 자식은
첫 줄에 prctl(PR_SET_DUMPABLE, 0) 을 부른다.
"""
from __future__ import annotations

import argparse
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
BASED = ("big", "churn", "crash", "crashctl")   # 베이스 사본에서 시작하는 시나리오
ALL = ("fill", "setnull", "big", "interleave", "churn", "crash", "crashctl")


# ── 자식 ─────────────────────────────────────────────────────────────────────

def _harden() -> None:
    """코어 덤프를 끄고(prctl) 죽는 순간의 네이티브 스택을 stderr 로 남긴다."""
    if sys.platform.startswith("linux"):
        import ctypes
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)          # PR_SET_DUMPABLE = 4
    import faulthandler
    faulthandler.enable()


def _open(data: str, dim: int):
    from store.backend import open_store
    from store.config import load_config
    from store.embedded_backend import EmbeddedBackend
    s = open_store(load_config({"data_dir": data}))
    if s is None:
        print("RESULT " + json.dumps({"open_error": EmbeddedBackend.last_open_error}), flush=True)
        sys.exit(3)
    s.provision(dim=dim)
    heals = {n: getattr(s, n)(dim=dim).get("state")
             for n in ("heal_fts_index", "heal_vector_index")}   # 허브 기동과 같은 순서
    return s, heals


def _no_drop() -> None:
    from api import docstore as ds
    ds._drop_vector_index_if_any = lambda store: None


class Probe:
    """한 자식의 스토어·진실표(vec_id → 벡터)·검사 계수."""

    def __init__(self, s, dim: int, seed: int):
        import numpy as np
        self.np, self.s, self.dim = np, s, dim
        self.rng = np.random.default_rng(seed)
        self.centers = self.rng.standard_normal((64, dim)).astype(np.float32)
        self.idx = s._VEC_INDEX
        self.truth: dict = {}
        self.order: list = []              # 리더 스레드용 추가 전용 목록
        self.st = {"docs": 0, "self_n": 0, "self1": 0, "stale_n": 0, "stale": 0,
                   "recall_n": 0, "recall_sum": 0.0}
        self.queries = [self.vec() for _ in range(40)]

    def vec(self):
        np = self.np
        v = self.centers[self.rng.integers(64)] + 0.6 * self.rng.standard_normal(self.dim)
        return (v / np.linalg.norm(v)).astype(np.float32)

    def text(self, n: int) -> str:
        # 문단 하나 ≈ 1,800자 — 청커 상한(2,000자) 안이라 문단 하나가 청크 하나다.
        return "\n\n".join(" ".join(f"t{w}" for w in self.rng.integers(0, 5000, 300))
                           for _ in range(n))

    def knn(self, emb, k: int = 10, conn=None) -> list[dict]:
        return (conn or self.s).run(
            f"CALL QUERY_VECTOR_INDEX('ChunkVec','{self.idx}', $e, $k) "
            "RETURN node.vec_id AS id, distance ORDER BY distance",
            {"e": [float(x) for x in emb], "k": k}) or []

    def load_truth(self) -> None:
        for r in self.s.run("MATCH (v:ChunkVec) WHERE v.embedding IS NOT NULL "
                            "RETURN v.vec_id AS id, v.embedding AS e") or []:
            self.truth[r["id"]] = self.np.asarray(r["e"], dtype=self.np.float32)
        self.order = list(self.truth)

    def put(self, doc: str, n: int, per_chunk: int = 1) -> list[str]:
        """제품 경로 하나: put_doc(벡터 없는 청크) → apply_embeddings(워커가 채우는 자리)."""
        from api import docstore as ds
        r = ds.put_doc(self.s, owner=OWNER, text=self.text(n), doc_id=doc, title=doc, link=False)
        if not r.get("executed"):
            raise RuntimeError(f"put_doc: {r}")
        cids = [x["id"] for x in self.s.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id", {"d": doc}) or []]
        embs = {c: [self.vec() for _ in range(per_chunk)] for c in cids}
        r = ds.apply_embeddings(self.s, doc, {c: [v.tolist() for v in vs] for c, vs in embs.items()})
        if not r.get("executed"):
            raise RuntimeError(f"apply_embeddings: {r}")
        vids = []
        for c, vs in embs.items():
            for i, v in enumerate(vs):
                vid = f"{c}#{i:03d}"
                if vid not in self.truth:
                    self.order.append(vid)
                self.truth[vid] = v
                vids.append(vid)
        self.st["docs"] += 1
        return vids

    def self_hit(self, vids) -> None:
        for vid in vids:
            rows = self.knn(self.truth[vid])
            self.st["self_n"] += 1
            self.st["self1"] += bool(rows and rows[0]["id"] == vid)

    def gone(self, old: dict) -> None:
        """지웠거나 값이 바뀐 벡터가 색인에 **옛 값으로** 남았는가(거리 0 으로 잡히면 stale)."""
        for vid, v in old.items():
            rows = self.knn(v, 5)
            self.st["stale_n"] += 1
            self.st["stale"] += any(r["id"] == vid and r["distance"] < 1e-3 for r in rows)

    def recall(self, qs=None) -> float:
        np = self.np
        ids = list(self.truth)
        m = np.stack([self.truth[i] for i in ids])
        tot, k = 0.0, min(10, len(ids))
        for q in (qs or self.queries[:5]):
            exact = {ids[j] for j in np.argsort(-(m @ q))[:k]}
            tot += len(exact & {r["id"] for r in self.knn(q, k)}) / k
        n = len(qs or self.queries[:5])
        self.st["recall_n"] += n
        self.st["recall_sum"] += tot
        return tot / n

    def finish(self) -> dict:
        """증분으로 유지된 색인과 새로 세운 색인의 recall 을 같은 질의로 견준다."""
        out = dict(self.st, vectors=len(self.truth))
        out["recall_incremental"] = round(self.recall(self.queries), 4)
        t0 = time.monotonic()
        self.s.drop_vector_index()
        self.s.ensure_search_indexes(dim=self.dim)
        out["rebuild_s"] = round(time.monotonic() - t0, 2)
        out["recall_rebuilt"] = round(self.recall(self.queries), 4)
        return out


def _progress(p: Probe) -> None:
    print(f"P {p.st['docs']} {len(p.truth)}", flush=True)


def child_fill(a, p: Probe, *, setnull: bool = False, reader: bool = False) -> dict:
    per = a.per_doc
    stop, rst = threading.Event(), {"q": 0, "self1": 0, "err": 0, "err_last": ""}
    th = threading.Thread(target=_reader_loop, args=(p, stop, rst), daemon=True)
    for i in range(a.chunks // per):
        doc = f"probe.{i:05d}"
        if setnull:
            vids = _setnull(p, doc, per)
        else:
            vids = p.put(doc, per)
        # 기동 수리가 빈 표에도 색인을 세우므로 첫 적용부터 HNSW 삽입이다(드롭이 막던 자리).
        p.self_hit(vids[:2])
        if i == 0 and reader:
            th.start()
        if i % a.recall_every == 0:
            p.recall()
        _progress(p)
    stop.set()
    if reader:
        th.join(30)
    out = p.finish()
    if reader:
        out["reader"] = rst
    return out


def _setnull(p: Probe, doc: str, n: int) -> list[str]:
    """2026-07-31 의 모양: 벡터 없는 행이 먼저 들어가고 워커가 나중에 빈 열을 채운다."""
    vids = [f"{doc}#{j:05d}#000" for j in range(n)]
    for vid in vids:
        p.s.run("CREATE (v:ChunkVec {vec_id: $v, chunk_id: $c, doc_id: $d, owner_id: $o, seq: 0})",
                {"v": vid, "c": vid[:-4], "d": doc, "o": OWNER})
    for vid in vids:
        v = p.vec()
        p.s.run("MATCH (v:ChunkVec) WHERE v.vec_id = $v SET v.embedding = $e",
                {"v": vid, "e": v.tolist()})
        p.truth[vid] = v
        p.order.append(vid)
    p.st["docs"] += 1
    return vids


def _reader_loop(p: Probe, stop, st: dict) -> None:
    """허브의 읽기 경로처럼 **별도 커넥션**에서 쉬지 않고 질의한다."""
    rv = p.s.reader_view()
    rng = p.np.random.default_rng(99)
    while not stop.is_set():
        vid = p.order[int(rng.integers(len(p.order)))]
        try:
            rows = p.knn(p.truth[vid], 10, conn=rv)
            st["q"] += 1
            st["self1"] += bool(rows and rows[0]["id"] == vid)
        except Exception as exc:        # noqa: BLE001 — 세는 것이 목적이다
            st["err"] += 1
            st["err_last"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    rv.close()


def child_big(a, p: Probe, *, forever: bool = False) -> dict:
    p.load_truth()
    base = len(p.truth)
    i = 0
    while forever or i < a.docs:
        vids = p.put(f"probe.big.{a.seed}.{i:05d}", a.big_per_doc)
        if not a.drop:                  # 드롭 모드(대조군)에는 질의할 색인이 없다
            p.self_hit(vids[:1])
            if i % a.recall_every == 0:
                p.recall()
        _progress(p)
        i += 1
    return dict(p.finish(), base_vectors=base)


def child_churn(a, p: Probe) -> dict:
    """삭제 후 재삽입(짝수 회)과 같은 문서 재적용(홀수 회 — 소프트 재큐의 apply)."""
    from api import docstore as ds
    p.load_truth()
    docs = sorted({vid.split("#")[0] for vid in p.truth})
    for i in range(a.iters):
        doc = docs[int(p.rng.integers(len(docs)))]
        old = {v: p.truth.pop(v) for v in [k for k in p.truth if k.split("#")[0] == doc]}
        if i % 2 == 0:
            ds.delete_doc(p.s, doc, owner=OWNER)
            p.gone(dict(list(old.items())[:2]))            # 지운 직후
            vids = p.put(doc, int(p.rng.integers(1, 5)))
        else:
            cids = sorted({v.rsplit("#", 1)[0] for v in old})
            embs = {c: [p.vec(), p.vec()] for c in cids}
            r = ds.apply_embeddings(p.s, doc, {c: [v.tolist() for v in vs] for c, vs in embs.items()})
            if not r.get("executed"):
                raise RuntimeError(f"apply_embeddings: {r}")
            vids = []
            for c, vs in embs.items():
                for j, v in enumerate(vs):
                    p.truth[f"{c}#{j:03d}"] = v
                    vids.append(f"{c}#{j:03d}")
            p.st["docs"] += 1
        p.gone({v: x for v, x in list(old.items())[:2]})  # 새 값이 들어간 뒤
        p.self_hit(vids[:2])
        if i % a.recall_every == 0:
            p.recall()
        _progress(p)
    return p.finish()


def child_verify(a, p: Probe, heals: dict) -> dict:
    """kill -9 뒤 재오픈 — WAL 재생·기동 수리 뒤에도 색인이 맞고 쓰기가 사는가."""
    p.load_truth()
    out = {"heals": heals, "remnant": bool(p.s.vector_index_remnant),
           "vectors_after_replay": len(p.truth)}
    sample = [p.order[int(p.rng.integers(len(p.order)))] for _ in range(50)] if p.order else []
    p.self_hit(sample)
    p.recall(p.queries[:20])
    out["self1_after_replay"] = f"{p.st['self1']}/{p.st['self_n']}"
    for i in range(a.verify_docs):
        vids = p.put(f"probe.after.{i:05d}", a.big_per_doc)
        if not a.drop:
            p.self_hit(vids[:1])
    return dict(p.finish(), **out)


def child_base(a) -> dict:
    """벡터 16k 베이스(드롭을 **켠 채** 제품 경로로 채우고 끝에 한 번 세운다)."""
    s, heals = _open(a.data, a.dim)
    p = Probe(s, a.dim, 12345)
    for i in range(a.base_docs):
        p.put(f"base.{i:05d}", a.base_chunks, per_chunk=2)
    t0 = time.monotonic()
    s.ensure_search_indexes(dim=a.dim)
    out = {"vectors": len(p.truth), "build_s": round(time.monotonic() - t0, 2), "heals": heals}
    s.run("CHECKPOINT")
    s.close()
    return out


def cmd_child(a) -> int:
    _harden()
    if a.scenario == "base":
        print("RESULT " + json.dumps(child_base(a)), flush=True)
        return 0
    if not a.drop:
        _no_drop()
    s, heals = _open(a.data, a.dim)
    p = Probe(s, a.dim, a.seed)
    fn = {"fill": lambda: child_fill(a, p),
          "setnull": lambda: child_fill(a, p, setnull=True),
          "interleave": lambda: child_fill(a, p, reader=True),
          "big": lambda: child_big(a, p),
          "writer": lambda: child_big(a, p, forever=True),
          "churn": lambda: child_churn(a, p),
          "verify": lambda: child_verify(a, p, heals)}[a.scenario]
    out = fn()
    s.close()
    print("RESULT " + json.dumps(out), flush=True)
    return 0


# ── 부모 ─────────────────────────────────────────────────────────────────────

def _kind(rc) -> str:
    if rc == 0:
        return "ok"
    if rc in (-11, 139, 3221225477):     # 마지막 것은 Windows 의 access violation
        return "segv"
    if rc in (-6, 134):
        return "abort"
    if rc in (-9, 137):
        return "killed"
    return f"rc{rc}"


#: 부모와 자식이 같이 받는 수치 인자(이름 · 기본값). 자식에게는 부모 값을 그대로 넘긴다.
_SHARED = (("dim", 1024), ("chunks", 2000), ("per_doc", 20), ("docs", 1000),
           ("big_per_doc", 2), ("verify_docs", 100), ("iters", 300),
           ("base_docs", 500), ("base_chunks", 16),   # × 2벡터 = 16k(허브 규모)
           ("recall_every", 25))


def _child_args(a, scenario: str, data: Path, seed: int) -> list[str]:
    args = [sys.executable, "-X", "utf8", __file__, "child", scenario, "--data", str(data),
            "--seed", str(seed)]
    for name, _ in _SHARED:
        args += [f"--{name.replace('_', '-')}", str(getattr(a, name))]
    return args


def _parse(stdout: str) -> dict:
    res, last = {}, ""
    for line in stdout.splitlines():
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
        elif line.startswith("P "):
            last = line[2:]
    if last:
        res.setdefault("progress", last)      # "문서 수 벡터 수" — 죽은 자리
    return res


def _spawn(args: list[str], timeout: float) -> dict:
    t0 = time.monotonic()
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as exc:
        rc, out, err = "timeout", exc.stdout or "", exc.stderr or ""
        out = out.decode() if isinstance(out, bytes) else out
        err = err.decode() if isinstance(err, bytes) else err
    rec = {"rc": rc, "kind": "timeout" if rc == "timeout" else _kind(rc),
           "secs": round(time.monotonic() - t0, 1), **_parse(out)}
    if rec["kind"] != "ok":
        rec["stderr_tail"] = err[-1500:]
    return rec


def _crash_trial(a, data: Path, seed: int, *, drop: bool = False) -> dict:
    """쓰는 자식을 무작위 시점에 SIGKILL 하고(체크포인트 사이), 새 자식으로 다시 연다.

    ``drop``: 대조군(crashctl) — 제품 기본(드롭 켬)으로 같은 kill 을 한다. kill -9 자체가
    WAL 을 못 여는 상태로 남기는지와, 드롭을 끈 것이 새로 더하는 고장을 가르려고 둔다."""
    import random
    extra = ["--drop"] if drop else []
    proc = subprocess.Popen(_child_args(a, "writer", data, seed) + extra, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    lines: list[str] = []
    threading.Thread(target=lambda: [lines.append(x) for x in proc.stdout], daemon=True).start()
    threading.Thread(target=lambda: proc.stderr.read(), daemon=True).start()
    t0 = time.monotonic()
    while not any(x.startswith("P ") for x in lines) and proc.poll() is None \
            and time.monotonic() - t0 < 600:
        time.sleep(0.2)
    delay = random.Random(seed).uniform(1.0, a.kill_max)
    time.sleep(delay)
    writer_rc = proc.poll()
    if writer_rc is None:
        proc.kill()
        proc.wait()
    prog = [x[2:].strip() for x in lines if x.startswith("P ")]
    rec = {"writer": "killed" if writer_rc is None else _kind(writer_rc),
           "killed_after_s": round(delay, 1), "writer_progress": prog[-1] if prog else ""}
    v = _spawn(_child_args(a, "verify", data, seed + 1000) + extra, a.timeout)
    rec.update({f"verify_{k}" if k in ("rc", "secs", "stderr_tail", "progress") else k: val
                for k, val in v.items()})
    rec["kind"] = v["kind"] if rec["writer"] == "killed" else f"writer_{rec['writer']}"
    return rec


def _env() -> dict:
    import hashlib
    import platform
    try:
        import ladybug
        ver = getattr(ladybug, "__version__", "?")
    except Exception as exc:        # noqa: BLE001
        ver = f"unavailable: {exc}"
    ext = os.environ.get("VINC_LBUG_EXT_DIR", "")
    sha = {}
    for name in ("libvector.lbug_extension", "libfts.lbug_extension"):
        f = Path(ext) / name if ext else None
        if f and f.exists():
            sha[name] = hashlib.sha256(f.read_bytes()).hexdigest()
    return {"ladybug": ver, "python": sys.version.split()[0], "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "ext_dir": ext, "ext_sha256": sha}


def _summary(env: dict, a, base: dict | None, rows: list[dict]) -> str:
    out = [f"### HNSW insert probe · ladybug {env['ladybug']} · dim {a.dim}",
           f"`{env['platform']}` · base {json.dumps(base) if base else '-'}", "",
           "| scenario | trials | ok | segv | other | self@1 | stale | recall inc/rebuilt | rebuild s |",
           "|---|---|---|---|---|---|---|---|---|"]
    for sc in dict.fromkeys(r["scenario"] for r in rows):
        rs = [r for r in rows if r["scenario"] == sc]
        ok = sum(r["kind"] == "ok" for r in rs)
        segv = sum("segv" in r["kind"] for r in rs)
        s1 = sum(r.get("self1", 0) for r in rs), sum(r.get("self_n", 0) for r in rs)
        st = sum(r.get("stale", 0) for r in rs), sum(r.get("stale_n", 0) for r in rs)
        inc = [r["recall_incremental"] for r in rs if "recall_incremental" in r]
        reb = [r["recall_rebuilt"] for r in rs if "recall_rebuilt" in r]
        rb = [r["rebuild_s"] for r in rs if "rebuild_s" in r]
        mean = (lambda xs: f"{sum(xs) / len(xs):.3f}" if xs else "-")
        out.append(f"| {sc} | {len(rs)} | {ok} | {segv} | {len(rs) - ok - segv} | "
                   f"{s1[0]}/{s1[1]} | {st[0]}/{st[1]} | {mean(inc)} / {mean(reb)} | "
                   f"{mean(rb)} |")
    return "\n".join(out)


def cmd_run(a) -> int:
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    scen = [x for x in a.scenarios.split(",") if x]
    env = _env()
    print(json.dumps(env), flush=True)
    base = None
    if any(x in BASED for x in scen):
        bdir = work / "base"
        shutil.rmtree(bdir, ignore_errors=True)
        bdir.mkdir()
        base = _spawn(_child_args(a, "base", bdir / "db", 0), a.timeout)
        print("base " + json.dumps(base), flush=True)
        if base["kind"] != "ok":
            scen = [x for x in scen if x not in BASED]
    rows: list[dict] = []
    for sc in scen:
        for t in range(a.trials):
            d = work / f"{sc}-{t}"
            shutil.rmtree(d, ignore_errors=True)
            if sc in BASED:
                shutil.copytree(work / "base", d)
            else:
                d.mkdir()
            if sc in ("crash", "crashctl"):
                rec = _crash_trial(a, d / "db", t, drop=sc == "crashctl")
            else:
                rec = _spawn(_child_args(a, sc, d / "db", t), a.timeout)
            rec["scenario"] = sc
            rows.append(rec)
            print(json.dumps(rec), flush=True)
            shutil.rmtree(d, ignore_errors=True)
            if a.out:       # 시행마다 쓴다 — 잡이 시간 초과로 잘려도 거기까지는 남는다
                Path(a.out).write_text(json.dumps({"env": env, "args": vars(a), "base": base,
                                                   "trials": rows}, indent=1), encoding="utf-8")
    text = _summary(env, a, base, rows)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "child"):
        p = sub.add_parser(name)
        if name == "child":
            p.add_argument("scenario")
            p.add_argument("--data", required=True)
            p.add_argument("--seed", type=int, default=0)
            p.add_argument("--drop", action="store_true", help="드롭을 켠 채(제품 기본)")
        else:
            p.add_argument("--work", required=True)
            p.add_argument("--scenarios", default=",".join(ALL))
            p.add_argument("--trials", type=int, default=5)
            p.add_argument("--out", default="")
            p.add_argument("--timeout", type=float, default=1800)
            p.add_argument("--kill-max", type=float, default=30.0)
        for arg, default in _SHARED:
            p.add_argument(f"--{arg.replace('_', '-')}", type=int, default=default)
    a = ap.parse_args()
    return cmd_child(a) if a.cmd == "child" else cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
