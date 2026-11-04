"""VIN-921 — 패키지(PyInstaller) 사이드카를 쓰기 도중 강제 종료하고 다시 열어 본다.

데스크톱이 사이드카를 띄우는 방식을 그대로 흉내 낸다(`--target local serve --port 0`,
`VINC_SIDECAR_TOKEN`, `VINC_LEX_BACKFILL=background`, WAL 관용 재생은 제품이 안 켜므로 기본
엄격). 시도마다 새 일회용 저장소에서 문서·노드·벡터를 끊임없이 쓰다가 무작위 시점에
SIGKILL(Windows 는 taskkill /F = TerminateProcess)로 죽이고, 다시 띄워 열리는지, 복구 뒤
쓰기와 검색(FTS 포함 — PR688 회귀)이 되는지, 중복 청크 키(VIN-237)가 나오는지 본다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = "synthetic-native-reopen-probe"
OWNER = "user:local"
DIM = 768
WORDS = ("quartz", "basalt", "granite", "marble", "obsidian", "pumice", "schist", "slate",
         "feldspar", "gypsum", "halite", "jasper", "kyanite", "limonite", "mica", "olivine")
DUP_KEY = re.compile(r"duplicat(ed)? (primary )?key", re.IGNORECASE)


def kill_tree(process, *, abrupt):
    """이 검증기가 띄운 동결 프로세스 트리만 죽인다."""
    if sys.platform == "win32":
        # PyInstaller onefile 은 부모 부트스트랩 + 실제 파이썬 자식이다. /T 로 둘 다 TerminateProcess.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=15)
    else:
        os.killpg(process.pid, signal.SIGKILL if abrupt else signal.SIGTERM)


class Sidecar:
    def __init__(self, binary: Path, trial: Path, env: dict, label: str):
        self.outpath, self.errpath = trial / f"{label}.stdout", trial / f"{label}.stderr"
        self.out = self.outpath.open("w", encoding="utf-8")
        self.err = self.errpath.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(binary), "--target", "local", "serve", "--port", "0"], env=env,
            stdout=self.out, stderr=self.err, start_new_session=(sys.platform != "win32"))
        self.base = None
        self.info = None

    def wait_ready(self, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.outpath.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    info = json.loads(line)
                except ValueError:
                    continue
                if isinstance(info, dict) and info.get("port"):
                    self.info = info
                    self.base = f"http://127.0.0.1:{info['port']}"
                    return info
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError(f"sidecar did not come up (exit={self.process.poll()}): "
                           + self.errpath.read_text(encoding="utf-8", errors="replace")[-3000:])

    def close(self):
        try:
            if self.process.poll() is None:
                kill_tree(self.process, abrupt=False)
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    kill_tree(self.process, abrupt=True)
                    self.process.wait(timeout=10)
        finally:
            self.out.close()
            self.err.close()


def request(base, path, payload=None, query=None, timeout=30):
    if query:
        path += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(base + path,
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + TOKEN,
                                          "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except Exception:  # noqa: BLE001
            return exc.code, {"error": str(exc)}


def doc_text(doc_index: int, revision: int) -> str:
    rng = random.Random(doc_index * 1000 + revision)
    words = [rng.choice(WORDS) for _ in range(30)] + [unique_word(doc_index)] + [rng.choice(WORDS) for _ in range(30)]
    # 검색할 단어는 문서마다 고유하고 줄 가운데 둔다(FTS 토크나이저가 줄바꿈에서 안 끊는다 — release.yml 참조).
    return f"native reopen probe document {doc_index} revision {revision}: " + " ".join(words) + " end.\n"


def unique_word(doc_index: int) -> str:
    return f"nrpword{doc_index}x"


class Writer(threading.Thread):
    """끊임없이 문서·노드·벡터를 쓴다. ACK 받은 것만 센다."""

    def __init__(self, base):
        super().__init__(daemon=True)
        self.base = base
        self.stop = threading.Event()
        self.acked = {"doc": 0, "ingest": 0, "embed": 0}
        self.failed = []
        self.in_flight = None
        self.acked_docs = {}      # doc_id -> revision
        self.last_error = None
        self.rng = random.Random()

    def run(self):
        doc_index = 0
        while not self.stop.is_set():
            op = self.rng.choice(("doc", "doc", "doc", "ingest", "embed"))
            try:
                if op == "doc":
                    if self.acked_docs and self.rng.random() < 0.3:
                        doc_index = self.rng.choice(sorted(int(d.split("-")[1]) for d in self.acked_docs))
                    else:
                        doc_index = len(self.acked_docs)
                    doc_id = f"doc-{doc_index}"
                    revision = self.acked_docs.get(doc_id, -1) + 1
                    self.in_flight = ("doc", doc_id, revision)
                    status, res = request(self.base, "/doc/put", {
                        "owner": OWNER, "id": doc_id, "title": f"Probe {doc_index}",
                        "text": doc_text(doc_index, revision)})
                    if status == 200 and res.get("executed"):
                        self.acked["doc"] += 1
                        self.acked_docs[doc_id] = revision
                    else:
                        self.failed.append(("doc", doc_id, status, res))
                elif op == "ingest":
                    n = self.acked["ingest"]
                    self.in_flight = ("ingest", n)
                    status, res = request(self.base, "/ingest", {"owner": OWNER, "nodes": [
                        {"id": f"concept:probe-{n}-a", "type": "concept", "title": f"Probe {n} source",
                         "domain": "vinc/probe"},
                        {"id": f"concept:probe-{n}-b", "type": "concept", "title": f"Probe {n} target",
                         "domain": "vinc/probe"}],
                        "edges": [{"from": f"concept:probe-{n}-a", "to": f"concept:probe-{n}-b",
                                   "type": "relates_to"}]})
                    if status == 200 and res.get("executed"):
                        self.acked["ingest"] += 1
                    else:
                        self.failed.append(("ingest", n, status, res))
                else:
                    status, pending = request(self.base, "/embed/pending", query={"limit": "8"})
                    chunks = pending.get("chunks") or []
                    if not chunks:
                        continue
                    by_doc = {}
                    for c in chunks:
                        by_doc.setdefault((c["doc_id"], c["content_hash"]), {})[c["chunk_id"]] = \
                            [self.rng.random() for _ in range(DIM)]
                    for (doc_id, content_hash), embeddings in by_doc.items():
                        self.in_flight = ("embed", doc_id)
                        status, res = request(self.base, "/embed/apply", {
                            "doc_id": doc_id, "embeddings": embeddings, "content_hash": content_hash})
                        if status == 200 and res.get("executed"):
                            self.acked["embed"] += 1
                        elif res.get("error") not in ("content_changed",):
                            self.failed.append(("embed", doc_id, status, res))
            except Exception as exc:  # noqa: BLE001 — 죽은 뒤의 연결 오류는 정상
                self.last_error = f"{type(exc).__name__}: {exc}"
                if self.stop.is_set():
                    return
                time.sleep(0.05)


def scan_dup_keys(*texts) -> list[str]:
    hits = []
    for text in texts:
        for line in str(text).splitlines():
            if DUP_KEY.search(line):
                hits.append(line.strip()[:300])
    return hits


def run_trial(args, binary: Path, trial: Path, index: int) -> dict:
    trial.mkdir()
    home = trial / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VINC_", "MEMORY_", "PYTHON", "AZURE_", "ACTIONS_ID_TOKEN"))
           and k not in {"AZ_KEY", "GH_TOKEN", "GITHUB_TOKEN"}}
    # 데스크톱(app/src-tauri/src/main.rs spawn_sidecar)이 넘기는 것: 토큰 + 백필 모드. 그 외 없음.
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_DATA_HOME=str(home / "data"),
               XDG_CONFIG_HOME=str(home / "config"), MEMORY_DATA_DIR=str(trial / "db"),
               VINC_SIDECAR_TOKEN=TOKEN, VINC_LEX_BACKFILL="background",
               VINC_BUFFER_POOL_MB="128", VINC_MAX_DB_SIZE=str(1024 ** 3),
               VINC_CHECKPOINT_THRESHOLD_MB=str(args.checkpoint_mb))
    if args.wal_tolerant:
        env["VINC_WAL_REPLAY_TOLERANT"] = "1"
    record = {"trial": index, "passed": False, "phase": "boot", "wal_tolerant": args.wal_tolerant,
              "duplicate_key_hits": []}
    servers = []
    try:
        first = Sidecar(binary, trial, env, "before-kill")
        servers.append(first)
        info = first.wait_ready()
        if info.get("driver") != "ladybug":
            raise RuntimeError(f"unexpected driver at first boot: {info}")
        record["phase"] = "writing"
        writer = Writer(first.base)
        writer.start()
        # 최소 몇 개는 ACK 돼야 '쓰기 도중'이다.
        deadline = time.monotonic() + 60
        while sum(writer.acked.values()) < 5 and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(random.uniform(args.min_write_seconds, args.max_write_seconds))
        in_flight = writer.in_flight
        kill_at = time.monotonic()
        kill_tree(first.process, abrupt=True)
        exit_code = first.process.wait(timeout=15)
        writer.stop.set()
        writer.join(timeout=10)
        record.update(killed_exit_code=exit_code, in_flight_at_kill=in_flight,
                      acked_before_kill=dict(writer.acked), acked_docs=len(writer.acked_docs),
                      writer_failures_before_kill=[f[:3] for f in writer.failed][:10])
        abrupt = exit_code != 0 if sys.platform == "win32" else exit_code == -signal.SIGKILL
        if not abrupt:
            raise RuntimeError(f"process was not killed abruptly: exit={exit_code}")
        if writer.failed:
            # 죽기 전 거절된 쓰기는 결함 후보다(연결 오류는 failed 에 안 들어온다).
            raise RuntimeError(f"writes were rejected before the kill: {writer.failed[:3]}")
        files = {p.name: p.stat().st_size for p in trial.glob("db*") if p.is_file()}
        record["files_after_kill"] = files
        record["wal_bytes_after_kill"] = files.get("db.wal", 0)
        pre = trial / "pre-replay"
        pre.mkdir()
        for p in trial.glob("db*"):
            if p.is_file() and p.stat().st_size < 64 * 1024 * 1024:
                (pre / p.name).write_bytes(p.read_bytes())
        record["pre_replay_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in pre.iterdir()}
        # ── 재개방 ────────────────────────────────────────────────────────────
        record["phase"] = "reopen"
        started = time.monotonic()
        second = Sidecar(binary, trial, env, "after-kill")
        servers.append(second)
        info = second.wait_ready()
        record["reopen_seconds"] = round(time.monotonic() - started, 3)
        if info.get("driver") != "ladybug":
            raise RuntimeError(f"store did not reopen: {info}")
        _, health = request(second.base, "/health")
        record["health_after_reopen"] = health.get("status")
        if health.get("status") != "ok":
            raise RuntimeError(f"health after reopen: {health}")
        boot_log = second.errpath.read_text(encoding="utf-8", errors="replace")
        record["boot_log_fts_lines"] = [l for l in boot_log.splitlines() if "full-text" in l or "FTS" in l][:5]
        record["fts_replay_marker_left"] = (trial / "db.fts-replay-pending").exists()
        # ── 복구 뒤 읽기: 죽기 전 ACK 된 문서가 전문 검색으로 나와야 한다(PR688) ──
        record["phase"] = "post_recovery_search"
        acked_docs = writer.acked_docs
        if not acked_docs:
            raise RuntimeError("no acknowledged document to verify")
        missing = []
        # 죽는 순간 다시 쓰이던 문서는 제외한다: 문서 교체는 청크를 하나씩 바꾸므로 재생 뒤
        # "mid-replacement" 로 읽히는 것이 문서화된 창이고, 치유는 재-put 이다(아래서 확인).
        in_flight_doc = in_flight[1] if in_flight and in_flight[0] == "doc" else None
        if in_flight_doc in acked_docs:
            _, state = request(second.base, "/doc", query={"owner": OWNER, "id": in_flight_doc})
            record["in_flight_doc_state_after_replay"] = (
                "intact" if state.get("text") == doc_text(int(in_flight_doc.split("-")[1]), acked_docs[in_flight_doc])
                else (state.get("error") or "other"))
        verify = [(d, r) for d, r in sorted(acked_docs.items()) if d != in_flight_doc][: args.verify_docs]
        for doc_id, revision in verify:
            _, doc = request(second.base, "/doc", query={"owner": OWNER, "id": doc_id})
            if doc.get("text") != doc_text(int(doc_id.split("-")[1]), revision):
                missing.append((doc_id, "text", doc.get("error") or doc.get("verified")))
                continue
            word = unique_word(int(doc_id.split("-")[1]))
            _, search = request(second.base, "/search", query={"owner": OWNER, "query": word, "top_k": "20"})
            record["duplicate_key_hits"] += scan_dup_keys(json.dumps(search))
            if search.get("error") or not any(r.get("doc_id") == doc_id and "keyword" in (r.get("arms") or [])
                                              for r in search.get("rows", [])):
                missing.append((doc_id, "fts", search.get("error") or len(search.get("rows", []))))
        record["pre_kill_docs_verified"] = len(verify) - len(missing)
        if missing:
            raise RuntimeError(f"acknowledged documents not preserved/searchable after replay: {missing[:5]}")
        # ── 복구 뒤 쓰기: 죽을 때 날아가던 요청을 다시 보내고(재시도), 새 문서·노드·벡터 ──
        record["phase"] = "post_recovery_write"
        post = {"doc": 0, "ingest": 0, "embed": 0}
        if in_flight and in_flight[0] == "doc":
            doc_id, revision = in_flight[1], in_flight[2]
            status, res = request(second.base, "/doc/put", {"owner": OWNER, "id": doc_id,
                                  "title": "retry", "text": doc_text(int(doc_id.split("-")[1]), revision)})
            record["retry_in_flight_doc"] = {"status": status, "executed": res.get("executed"),
                                             "error": res.get("error"), "warnings": res.get("warnings")}
            record["duplicate_key_hits"] += scan_dup_keys(json.dumps(res))
            if status != 200 or not res.get("executed"):
                raise RuntimeError(f"retry of the in-flight document failed: {res}")
            _, healed = request(second.base, "/doc", query={"owner": OWNER, "id": doc_id})
            _, found = request(second.base, "/search", query={"owner": OWNER,
                               "query": unique_word(int(doc_id.split("-")[1])), "top_k": "20"})
            record["duplicate_key_hits"] += scan_dup_keys(json.dumps(healed), json.dumps(found))
            if (healed.get("text") != doc_text(int(doc_id.split("-")[1]), revision) or found.get("error")
                    or not any(r.get("doc_id") == doc_id and "keyword" in (r.get("arms") or [])
                               for r in found.get("rows", []))):
                raise RuntimeError(f"re-put did not heal the interrupted document: {healed.get('error')} / {found.get('error')}")
        for k in range(args.post_writes):
            doc_id = f"post-{k}"
            text = f"post recovery document {k}: the zircon line {k} stays in the middle here.\n"
            status, res = request(second.base, "/doc/put", {"owner": OWNER, "id": doc_id,
                                                            "title": f"Post {k}", "text": text})
            record["duplicate_key_hits"] += scan_dup_keys(json.dumps(res))
            if status != 200 or not res.get("executed") or res.get("warnings"):
                raise RuntimeError(f"post-recovery document write failed: {res}")
            post["doc"] += 1
            # 같은 문서를 한 번 더 덮어써 청크 키 재사용 경로를 탄다.
            status, res = request(second.base, "/doc/put", {"owner": OWNER, "id": doc_id,
                                                            "title": f"Post {k}", "text": text + "revised zircon again.\n"})
            record["duplicate_key_hits"] += scan_dup_keys(json.dumps(res))
            if status != 200 or not res.get("executed"):
                raise RuntimeError(f"post-recovery document re-put failed: {res}")
            post["doc"] += 1
        status, res = request(second.base, "/ingest", {"owner": OWNER, "nodes": [
            {"id": "concept:post-a", "type": "concept", "title": "Post source", "domain": "vinc/probe"},
            {"id": "concept:post-b", "type": "concept", "title": "Post target", "domain": "vinc/probe"}],
            "edges": [{"from": "concept:post-a", "to": "concept:post-b", "type": "relates_to"}]})
        if status != 200 or not res.get("executed") or res.get("partial"):
            raise RuntimeError(f"post-recovery ingest failed: {res}")
        post["ingest"] += 1
        _, pending = request(second.base, "/embed/pending", query={"limit": "64"})
        by_doc = {}
        for c in pending.get("chunks") or []:
            by_doc.setdefault((c["doc_id"], c["content_hash"]), {})[c["chunk_id"]] = \
                [random.random() for _ in range(DIM)]
        for (doc_id, content_hash), embeddings in by_doc.items():
            status, res = request(second.base, "/embed/apply", {"doc_id": doc_id, "embeddings": embeddings,
                                                                "content_hash": content_hash})
            record["duplicate_key_hits"] += scan_dup_keys(json.dumps(res))
            if status != 200 or not res.get("executed"):
                raise RuntimeError(f"post-recovery embed apply failed for {doc_id}: {res}")
            post["embed"] += 1
        record["post_recovery_writes"] = post
        # 새로 쓴 문서도 FTS 에 나와야 한다.
        _, search = request(second.base, "/search", query={"owner": OWNER, "query": "zircon", "top_k": "50"})
        record["duplicate_key_hits"] += scan_dup_keys(json.dumps(search))
        found = {r.get("doc_id") for r in search.get("rows", []) if "keyword" in (r.get("arms") or [])}
        if search.get("error") or not {f"post-{k}" for k in range(args.post_writes)} <= found:
            raise RuntimeError(f"post-recovery documents not searchable: error={search.get('error')} found={sorted(found)}")
        _, components = request(second.base, "/diagnose", query={"owner": OWNER, "kind": "components"})
        if components.get("executed") is not True:
            raise RuntimeError(f"diagnose failed after reopen: {components}")
        record["components_after"] = components.get("component_count")
        # ── 정상 종료 뒤 세 번째 열기(체크포인트 뒤에도 열려야 한다) ──
        record["phase"] = "third_open"
        second.close()
        third = Sidecar(binary, trial, env, "third-open")
        servers.append(third)
        info = third.wait_ready()
        if info.get("driver") != "ladybug":
            raise RuntimeError(f"third open failed: {info}")
        _, search = request(third.base, "/search", query={"owner": OWNER, "query": "zircon", "top_k": "5"})
        if search.get("error") or not search.get("rows"):
            raise RuntimeError(f"search after clean reopen failed: {search}")
        record["phase"] = "done"
        record["passed"] = True
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for s in servers:
            try:
                s.close()
            except Exception as exc:  # noqa: BLE001
                record.setdefault("cleanup_errors", []).append(str(exc))
        logs = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in trial.glob("*.stderr")}
        record["duplicate_key_hits"] += scan_dup_keys(*logs.values())
        if record["duplicate_key_hits"]:
            record["passed"] = False
        if not record["passed"]:
            record["logs_tail"] = {k: v[-4000:] for k, v in logs.items()}
        (trial / "result.json").write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--source-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--wal-tolerant", action="store_true", help="VINC_WAL_REPLAY_TOLERANT=1 (제품 데스크톱은 안 켠다)")
    parser.add_argument("--checkpoint-mb", type=int, default=4, help="VIN-237 측정과 같은 4MB 체크포인트 임계")
    parser.add_argument("--min-write-seconds", type=float, default=1.0)
    parser.add_argument("--max-write-seconds", type=float, default=5.0)
    parser.add_argument("--verify-docs", type=int, default=12)
    parser.add_argument("--post-writes", type=int, default=5)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    # 절대 경로여야 한다: HOME 이 상대 경로면 Windows 의 LoadLibrary 가 확장(.lbug_extension)을
    # 못 찾는다(실측 2026-11-04, 20/20 "Failed to load library"). 데스크톱은 늘 절대 HOME 을 준다.
    args.output = args.output.resolve()
    results = []
    for index in range(1, args.trials + 1):
        record = run_trial(args, binary, args.output / f"trial-{index:02}", index)
        results.append(record)
        print(json.dumps({k: record.get(k) for k in ("trial", "passed", "phase", "error", "in_flight_at_kill",
                                                     "acked_before_kill", "wal_bytes_after_kill", "reopen_seconds",
                                                     "post_recovery_writes", "duplicate_key_hits")},
                         default=str), flush=True)
    summary = {
        "schema": "vinc.native-reopen-probe.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": sys.platform, "source_sha": args.source_sha,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "wal_tolerant": args.wal_tolerant, "checkpoint_threshold_mb": args.checkpoint_mb,
        "trials": len(results),
        "reopen_failures": sum(1 for r in results if r.get("phase") in ("reopen",) and not r["passed"]),
        "failures": sum(1 for r in results if not r["passed"]),
        "duplicate_key_hits": sum(len(r["duplicate_key_hits"]) for r in results),
        "trials_with_nonempty_wal": sum(1 for r in results if r.get("wal_bytes_after_kill")),
        "acked_before_kill_total": {k: sum(r.get("acked_before_kill", {}).get(k, 0) for r in results)
                                    for k in ("doc", "ingest", "embed")},
        "passed": bool(results) and all(r["passed"] for r in results),
        "results": results,
    }
    (args.output / "result.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}), flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
