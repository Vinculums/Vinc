"""허브 모양 스토어에서 버퍼 풀 상한별 메모리 고점 — 2GiB cgroup 컨테이너 안에서 돈다.

인자: 버퍼 풀 MB(0 = ladybug 기본값 = 호스트 물리 메모리의 80%).
출력: 단계마다 JSON 한 줄(peak RSS, peak cgroup memory.current, 소요 시간, 오류).
"""
import json, os, sys, threading, time
from array import array

sys.path.insert(0, "/w")
POOL = int(sys.argv[1])
os.environ["MEMORY_STORE_BACKEND"] = "embedded"
os.environ["MEMORY_DATA_DIR"] = "/tmp/s/store"
os.environ["VINC_LBUG_EXT_DIR"] = "/w/packaging/lbug_extensions"
if POOL:
    os.environ["VINC_BUFFER_POOL_MB"] = str(POOL)

import numpy as np  # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402
from api import node_mirror  # noqa: E402

DIM, NODES, CHUNKVECS, OWNERS = 1024, 15025, 20300, 19


def rss_kb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


def cg_bytes():
    try:
        return int(open("/sys/fs/cgroup/memory.current").read())
    except OSError:
        return 0


class Peak:
    def __enter__(self):
        self.stop, self.rss, self.cg, self.t0 = False, rss_kb(), cg_bytes(), time.monotonic()
        def run():
            while not self.stop:
                self.rss, self.cg = max(self.rss, rss_kb()), max(self.cg, cg_bytes())
                time.sleep(0.05)
        self.th = threading.Thread(target=run, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *exc):
        self.stop = True
        self.th.join()
        self.s = time.monotonic() - self.t0


def report(phase, p, **kw):
    print(json.dumps({"pool_mb": POOL, "phase": phase, "s": round(p.s, 1),
                      "peak_rss_mb": round(p.rss / 1024), "peak_cgroup_mb": round(p.cg / 2**20),
                      "after_rss_mb": round(rss_kb() / 1024), **kw}), flush=True)


rng = np.random.default_rng(0)


def vec():
    v = rng.standard_normal(DIM).astype(np.float32)
    return array("f", (v / np.linalg.norm(v)).tobytes())


def node_rows(tag=""):
    return [{"id": f"concept:n{i}", "kind": "concept", "owner": f"user:o{i % OWNERS}",
             "title": f"Node {i}{tag}", "text": f"Node {i}{tag}. a short one liner about it",
             "emb": vec()} for i in range(NODES)]


def phase(name, fn, **kw):
    err = ""
    with Peak() as p:
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001 — 실패도 결과다
            out, err = None, f"{type(exc).__name__}: {str(exc)[:200]}"
    report(name, p, error=err, **kw)
    return out


s = open_store(load_config())
s.provision(dim=DIM)


def build():
    rows = node_rows()
    node_mirror.replace(s, rows, "bge", "rt")
    for i in range(0, CHUNKVECS, 256):
        s.run("UNWIND $rows AS r CREATE (:ChunkVec {vec_id: r.v, chunk_id: r.c, doc_id: r.d, "
              "owner_id: r.o, seq: 0, embedding: r.e, encoder_runtime: 'rt'})",
              {"rows": [{"v": f"c{j}#000", "c": f"c{j}", "d": f"d{j // 2}", "o": f"user:o{j % OWNERS}",
                         "e": list(vec())} for j in range(i, min(i + 256, CHUNKVECS))]})
    s.heal_vector_index(dim=DIM)
    s.run("CHECKPOINT")
    return len(rows)


phase("build", build)
s.close()


def boot():
    global s
    s = open_store(load_config())
    a = s.heal_vector_index(dim=DIM)
    b = s.heal_node_vector_index(dim=DIM)
    return a, b


phase("boot", boot)
rows = node_rows()
have = node_mirror.existing(s)
for r in rows[:5]:
    r["text"] += " changed"


def small_swap():
    todo, stale = node_mirror.plan(rows, have, "bge", "rt")
    return node_mirror.replace(s, todo, "bge", "rt", stale=stale)["index_ok"]


phase("swap_5_rows", small_swap)
phase("snapshot", lambda: s.snapshot("/tmp/snap"))


def full_swap():
    fresh = node_rows(" v2")
    todo, stale = node_mirror.plan(fresh, node_mirror.existing(s), "bge", "rt")
    return node_mirror.replace(s, todo, "bge", "rt", stale=stale)["index_ok"]


phase("swap_all_rows", full_swap)
s.close()
