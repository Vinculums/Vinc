"""동결 사이드카의 로컬 HTTP ACK 직후 급사와 재개방을 합성 데이터로 측정한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error


TOKEN = "synthetic-frozen-recovery-probe"
OWNER = "user:local"


def kill_tree(process, *, abrupt):
    """이 검증기가 시작한 동결 프로세스와 그 자식만 종료한다."""
    if sys.platform == "win32":
        # PyInstaller 부모만 종료하면 실제 DB를 연 자식이 살아 남는다.
        killed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True, text=True, timeout=10)
        if killed.returncode and process.poll() is None:
            raise RuntimeError(f"owned process tree kill failed: {killed.stderr}")
    else:
        os.killpg(process.pid, signal.SIGKILL if abrupt else signal.SIGTERM)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--source-sha", help="이 바이너리를 빌드한 검증 소스의 정확한 SHA")
    parser.add_argument("--request-timeout", type=float, default=20,
                        help="HTTP 관찰 제한(초). 늘린 실행은 지연 진단으로 따로 해석")
    parser.add_argument("--durable-ack", action="store_true",
                        help="새 합성 저장소의 별도 로컬 영속 경로만 활성화")
    parser.add_argument("--persistent-root", type=Path,
                        help="영속 경로를 별도 파일시스템의 새 합성 폴더에 둔다. 기존 폴더는 거절")
    parser.add_argument("--lose-local-disk", action="store_true",
                        help="급사 뒤 로컬 DB 파일을 보존 경로로 옮겨 영속본만으로 복구")
    parser.add_argument("--check-retry-fencing", action="store_true",
                        help="복원 뒤 같은 요청 재시도와 합성 리비전 표식의 ACK 차단 검증")
    args = parser.parse_args()
    if args.source_sha and not re.fullmatch(r"[0-9a-f]{40}", args.source_sha):
        parser.error("--source-sha requires an exact lowercase 40-character SHA")
    if not 5 <= args.request_timeout <= 180:
        parser.error("--request-timeout must be between 5 and 180 seconds")
    if not 1 <= args.trials <= 50:
        parser.error("trials must be between 1 and 50")
    if args.lose_local_disk and not args.durable_ack:
        parser.error("--lose-local-disk requires --durable-ack")
    if args.check_retry_fencing and not args.durable_ack:
        parser.error("--check-retry-fencing requires --durable-ack")
    if args.persistent_root and not args.durable_ack:
        parser.error("--persistent-root requires --durable-ack")
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    persistent_root = None
    if args.persistent_root:
        args.persistent_root.mkdir(exist_ok=False)
        persistent_root = args.persistent_root.resolve(strict=True)
    results = []

    for index in range(args.trials):
        trial = output / f"trial-{index + 1:02}"
        trial.mkdir()
        home = trial / "home"
        home.mkdir()
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(("VINC_", "MEMORY_", "PYTHON", "AZURE_", "ACTIONS_ID_TOKEN"))
                       and k not in {"AZ_KEY", "GH_TOKEN", "GITHUB_TOKEN"}}
        environment.update(HOME=str(home), USERPROFILE=str(home), XDG_DATA_HOME=str(home / "data"),
                           XDG_CONFIG_HOME=str(home / "config"), MEMORY_DATA_DIR=str(trial / "db"),
                           VINC_TARGET="local", VINC_SIDECAR_TOKEN=TOKEN,
                           VINC_EMBED_APPLY_PAUSED="1", VINC_BUFFER_POOL_MB="128",
                           VINC_CHECKPOINT_THRESHOLD_MB="4",
                           VINC_MAX_DB_SIZE=str(1024**3))
        # 영속 경로도 이 시도만 소유하는 합성 폴더다. 실제 공유에서도 DB는 로컬에 연다.
        if args.durable_ack:
            share = persistent_root / trial.name if persistent_root else trial / "persistent"
            share.mkdir()
            environment.update(VINC_DURABLE_ACK="on", VINC_STORE_SNAPSHOT=str(share / "db"),
                               VINC_STORE_SNAPSHOT_EVERY="3600")
            if args.check_retry_fencing:
                environment["CONTAINER_APP_REVISION"] = "vinc-probe--0000001"
        servers = []
        record = {"trial": index + 1, "passed": False,
                  "durable_ack_requested": args.durable_ack,
                  "local_disk_loss_requested": args.lose_local_disk}
        phase = "initial_boot"

        def start(label):
            outpath, errpath = trial / f"{label}.stdout", trial / f"{label}.stderr"
            out, err = outpath.open("w", encoding="utf-8"), errpath.open("w", encoding="utf-8")
            boot_environment = dict(environment)
            if args.durable_ack and label == "before-kill":
                # 비어 있음을 아는 합성 첫 기동만 허용한다. 복원 기동에는 절대 넘기지 않는다.
                boot_environment["VINC_STORE_ALLOW_EMPTY"] = "1"
            process = subprocess.Popen([str(binary), "--target", "local", "serve", "--port", "0"],
                                       env=boot_environment, stdout=out, stderr=err, start_new_session=True)
            servers.append((process, out, err))
            deadline = time.monotonic() + 45
            while process.poll() is None and time.monotonic() < deadline:
                for line in outpath.read_text(encoding="utf-8").splitlines():
                    try:
                        info = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(info, dict) and info.get("port") and info.get("driver"):
                        return process, f"http://127.0.0.1:{info['port']}", info
                time.sleep(0.05)
            raise RuntimeError(f"{label} did not open a real store; see stdout/stderr")

        def request(base, path, payload=None, query=None):
            if query:
                path += "?" + urllib.parse.urlencode(query)
            req = urllib.request.Request(base + path,
                data=None if payload is None else json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
            # loopback만 쓴다. 프록시를 통해 외부로 요청을 보내지 않는다.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=args.request_timeout) as response:
                value = json.load(response)
                return response.status, value

        try:
            process, base, info = start("before-kill")
            record["initial_driver"] = info["driver"]
            if info["driver"] != "ladybug":
                raise RuntimeError("unexpected initial store driver")
            text = f"frozen recovery sentinel {index + 1}\n"
            phase = "document_ack"
            ack_start = time.monotonic()
            status, written = request(base, "/doc/put", {"owner": OWNER, "id": "recovery-sentinel",
                                                       "title": "Recovery sentinel", "text": text})
            record["document_ack_seconds"] = time.monotonic() - ack_start
            (trial / "document-ack.json").write_text(json.dumps(written, indent=2) + "\n")
            if status != 200 or written.get("executed") is not True:
                raise RuntimeError("document was not acknowledged")
            phase = "graph_ack"
            status, merged = request(base, "/ingest", {"owner": OWNER, "nodes": [
                {"id": "concept:recovery-a", "type": "concept", "title": "Recovery source", "domain": "vinc/probe"},
                {"id": "concept:recovery-b", "type": "concept", "title": "Recovery target", "domain": "vinc/probe"}],
                "edges": [{"from": "concept:recovery-a", "to": "concept:recovery-b", "type": "relates_to"}]})
            acknowledged = time.monotonic()
            # ACK 검증이나 receipt 쓰기보다 먼저 급사시켜 관찰자가 복구 창을 늘리지 않는다.
            kill_tree(process, abrupt=True)
            record["ack_to_kill_ms"] = (time.monotonic() - acknowledged) * 1000
            record["killed_exit_code"] = process.wait(timeout=5)
            (trial / "graph-ack.json").write_text(json.dumps(merged, indent=2) + "\n")
            abruptly_killed = record["killed_exit_code"] != 0 if sys.platform == "win32" else record["killed_exit_code"] == -signal.SIGKILL
            if (not abruptly_killed or status != 200 or merged.get("executed") is not True
                    or merged.get("warnings") or merged.get("partial")
                    or merged.get("merged", {}).get("nodes", {}).get("created") != 2
                    or merged.get("merged", {}).get("edges", {}).get("created") != 1):
                raise RuntimeError("full graph write/abrupt termination not established")
            record["files_after_kill"] = [{"name": p.name, "bytes": p.stat().st_size}
                                           for p in trial.glob("db*") if p.is_file()]
            if not args.durable_ack and (not (trial / "db.wal").is_file()
                    or (trial / "db.wal").stat().st_size == 0):
                raise RuntimeError("no nonempty WAL to replay")
            # 재개방은 WAL을 바꾼다. 재생 전 바이트를 별도로 남겨 실패도 다시 조사할 수 있다.
            preserved = trial / "pre-replay"
            preserved.mkdir()
            record["pre_replay_files"] = []
            for source in trial.glob("db*"):
                if source.is_file():
                    copied = preserved / source.name
                    shutil.copy2(source, copied)
                    record["pre_replay_files"].append({"name": copied.name,
                        "bytes": copied.stat().st_size, "sha256": hashlib.sha256(copied.read_bytes()).hexdigest()})
            if args.durable_ack:
                generation_root = share / "db.generations"
                if not generation_root.is_dir():
                    raise RuntimeError("ACK did not leave persistent generations")
                record["persistent_files_before_restore"] = [
                    {"name": str(p.relative_to(share)), "bytes": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in sorted(share.rglob("*")) if p.is_file()]
            if args.lose_local_disk:
                lost = trial / "lost-local-disk"
                lost.mkdir()
                for source in trial.glob("db*"):
                    if source.is_file():
                        if source.resolve().parent != trial.resolve():
                            raise RuntimeError("local disk-loss target escaped the owned trial")
                        shutil.move(str(source), str(lost / source.name))
                if (trial / "db").exists() or (trial / "db.wal").exists():
                    raise RuntimeError("local disk-loss simulation failed")
            started = time.monotonic()
            phase = "restore_boot"
            _, base, info = start("after-kill")
            record["reopen_seconds"] = time.monotonic() - started
            record["reopened_driver"] = info["driver"]
            if info["driver"] != "ladybug":
                raise RuntimeError("unexpected reopened store driver")
            if args.lose_local_disk and "restored-generation:" not in (
                    trial / "after-kill.stderr").read_text(encoding="utf-8"):
                raise RuntimeError("reopen did not disclose persistent generation restoration")
            if args.durable_ack:
                _, health = request(base, "/health", query={"live": "1"})
                record["reopened_durable_ack"] = health.get("durable_ack")
                if not isinstance(record["reopened_durable_ack"], dict):
                    raise RuntimeError("durable ACK was not active in the frozen product")
            phase = "restored_read_checks"
            _, document = request(base, "/doc", query={"owner": OWNER, "id": "recovery-sentinel"})
            (trial / "document-reopened.json").write_text(json.dumps(document, indent=2) + "\n")
            if document.get("verified") is not True or document.get("text") != text:
                raise RuntimeError("acknowledged document content not preserved")
            _, components = request(base, "/diagnose", query={"owner": OWNER, "kind": "components"})
            (trial / "components-reopened.json").write_text(json.dumps(components, indent=2) + "\n")
            members = {m for c in components.get("components", []) for m in c.get("members", [])}
            if (components.get("executed") is not True or components.get("component_count") != 1
                    or components.get("orphan_count") != 0
                    or members != {"concept:recovery-a", "concept:recovery-b"}):
                raise RuntimeError("acknowledged nodes/connection not preserved")
            _, edges = request(base, "/node-edges", query={"owner": OWNER, "node": "concept:recovery-a",
                                                         "type": "RELATED_TO", "dir": "out"})
            (trial / "edges-reopened.json").write_text(json.dumps(edges, indent=2) + "\n")
            actual = {(e["from"], e["to"], e["type"]) for e in edges.get("edges", [])}
            if (edges.get("executed") is not True or edges.get("missing_nodes")
                    or edges.get("edges_truncated") or edges.get("count") != 1
                    or actual != {("concept:recovery-a", "concept:recovery-b", "RELATED_TO")}):
                raise RuntimeError("acknowledged directed relation not preserved")
            _, search = request(base, "/search", query={"owner": OWNER, "query": "sentinel"})
            (trial / "search-reopened.json").write_text(json.dumps(search, indent=2) + "\n")
            if search.get("error") or not any(row.get("doc_id") == "recovery-sentinel"
                    and "sentinel" in row.get("snippet", "") for row in search.get("rows", [])):
                raise RuntimeError("acknowledged document not searchable after WAL replay")
            if args.check_retry_fencing:
                phase = "retry_and_fencing"
                _, retried_doc = request(base, "/doc/put", {"owner": OWNER, "id": "recovery-sentinel",
                    "title": "Recovery sentinel", "text": text})
                _, retried_graph = request(base, "/ingest", {"owner": OWNER, "nodes": [
                    {"id": "concept:recovery-a", "type": "concept", "title": "Recovery source", "domain": "vinc/probe"},
                    {"id": "concept:recovery-b", "type": "concept", "title": "Recovery target", "domain": "vinc/probe"}],
                    "edges": [{"from": "concept:recovery-a", "to": "concept:recovery-b", "type": "relates_to"}]})
                _, retried_edges = request(base, "/node-edges", query={"owner": OWNER,
                    "node": "concept:recovery-a", "type": "RELATED_TO", "dir": "out"})
                record["retry"] = {"document": retried_doc, "graph": retried_graph,
                                   "edge_count": retried_edges.get("count")}
                if (retried_doc.get("executed") is not True or retried_graph.get("executed") is not True
                        or retried_graph.get("warnings") or retried_graph.get("partial")
                        or retried_graph.get("merged", {}).get("nodes", {}).get("created") != 0
                        or retried_graph.get("merged", {}).get("nodes", {}).get("matched") != 2
                        or retried_edges.get("count") != 1):
                    raise RuntimeError("identical request retry duplicated data or failed")
                marker = share / "db.writer"
                if marker.read_text().strip() != "vinc-probe--0000001":
                    raise RuntimeError("synthetic initial writer did not claim its marker")
                def generation_hashes():
                    return {str(p.relative_to(generation_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(generation_root.rglob("*")) if p.is_file()}
                before_fence = generation_hashes()
                marker.write_text("vinc-probe--0000002", encoding="utf-8")
                try:
                    request(base, "/doc/put", {"owner": OWNER, "id": "fenced-write",
                        "title": "Synthetic fenced write", "text": "uncertain commit probe"})
                except urllib.error.HTTPError as exc:
                    fenced = json.load(exc)
                    record["fencing"] = {"status": exc.code, "response": fenced,
                        "persistent_generations_unchanged": generation_hashes() == before_fence}
                    if (exc.code != 500 or "commit uncertain" not in fenced.get("error", "")
                            or not record["fencing"]["persistent_generations_unchanged"]):
                        raise RuntimeError("newer synthetic writer did not fence durable ACK")
                else:
                    raise RuntimeError("old writer acknowledged a write after being fenced")
            record["passed"] = True
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["failure_phase"] = phase
            if phase == "document_ack":
                record["document_ack_wait_seconds"] = time.monotonic() - ack_start
        finally:
            cleanup_errors = []
            for process, out, err in servers:
                try:
                    if process.poll() is None:
                        kill_tree(process, abrupt=False)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            kill_tree(process, abrupt=True)
                            process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    cleanup_errors.append({"pid": process.pid, "error": f"{type(exc).__name__}: {exc}"})
                finally:
                    out.close()
                    err.close()
            record["cleanup_errors"] = cleanup_errors
            if not record["passed"]:
                # 출력은 이 하네스가 만든 합성 프로세스에 한정한다. 운영 로그는 안 읽는다.
                record["synthetic_process_logs"] = {
                    path.name: path.read_text(encoding="utf-8", errors="replace")[-6000:]
                    for path in trial.glob("*.stderr")}
            if cleanup_errors:
                record["passed"] = False
            (trial / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        results.append(record)
        print(json.dumps(record), flush=True)
        if not record["passed"]:
            break

    receipt = {"schema": "vinc.frozen-recovery-probe.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "requested_trials": args.trials,
               "platform": sys.platform, "source_sha": args.source_sha,
               "request_timeout_seconds": args.request_timeout,
               "checkpoint_threshold_mb": 4, "external_persistent_root": bool(persistent_root),
               "trials": results, "passed": len(results) == args.trials and all(r["passed"] for r in results),
               "limits": ["Frozen HTTP ACK and owned process tree termination on synthetic paths", "no native desktop acceptance; external filesystem type requires workflow mount evidence",
                          "does not approve production engine pin or durable ACK", "failed stores/logs retained"]}
    (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
