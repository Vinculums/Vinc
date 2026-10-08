"""동결 사이드카의 로컬 HTTP ACK 직후 급사와 재개방을 합성 데이터로 측정한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request


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
    args = parser.parse_args()
    if not 1 <= args.trials <= 50:
        parser.error("trials must be between 1 and 50")
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    results = []

    for index in range(args.trials):
        trial = output / f"trial-{index + 1:02}"
        trial.mkdir()
        home = trial / "home"
        home.mkdir()
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(("VINC_", "MEMORY_", "PYTHON"))}
        environment.update(HOME=str(home), USERPROFILE=str(home), XDG_DATA_HOME=str(home / "data"),
                           XDG_CONFIG_HOME=str(home / "config"), MEMORY_DATA_DIR=str(trial / "db"),
                           VINC_TARGET="local", VINC_SIDECAR_TOKEN=TOKEN,
                           VINC_EMBED_APPLY_PAUSED="1", VINC_BUFFER_POOL_MB="128",
                           VINC_MAX_DB_SIZE=str(1024**3))
        # 운영 스냅샷·durable ACK 설정은 가져오지 않는다. 각 시도는 새 로컬 저장소다.
        servers = []
        record = {"trial": index + 1, "passed": False, "durable_ack_requested": False}

        def start(label):
            outpath, errpath = trial / f"{label}.stdout", trial / f"{label}.stderr"
            out, err = outpath.open("w", encoding="utf-8"), errpath.open("w", encoding="utf-8")
            process = subprocess.Popen([str(binary), "--target", "local", "serve", "--port", "0"],
                                       env=environment, stdout=out, stderr=err, start_new_session=True)
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
            with opener.open(req, timeout=20) as response:
                value = json.load(response)
                return response.status, value

        try:
            process, base, info = start("before-kill")
            record["initial_driver"] = info["driver"]
            if info["driver"] != "ladybug":
                raise RuntimeError("unexpected initial store driver")
            text = f"frozen recovery sentinel {index + 1}\n"
            status, written = request(base, "/doc/put", {"owner": OWNER, "id": "recovery-sentinel",
                                                       "title": "Recovery sentinel", "text": text})
            (trial / "document-ack.json").write_text(json.dumps(written, indent=2) + "\n")
            if status != 200 or written.get("executed") is not True:
                raise RuntimeError("document was not acknowledged")
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
            if not (trial / "db.wal").is_file() or (trial / "db.wal").stat().st_size == 0:
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
            started = time.monotonic()
            _, base, info = start("after-kill")
            record["reopen_seconds"] = time.monotonic() - started
            record["reopened_driver"] = info["driver"]
            if info["driver"] != "ladybug":
                raise RuntimeError("unexpected reopened store driver")
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
            record["passed"] = True
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
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
            if cleanup_errors:
                record["passed"] = False
            (trial / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        results.append(record)
        print(json.dumps(record), flush=True)
        if not record["passed"]:
            break

    receipt = {"schema": "vinc.frozen-recovery-probe.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "requested_trials": args.trials,
               "platform": sys.platform, "source_sha": os.environ.get("GITHUB_SHA"),
               "trials": results, "passed": len(results) == args.trials and all(r["passed"] for r in results),
               "limits": ["Frozen local HTTP ACK and owned process tree termination only", "not SMB or native desktop acceptance",
                          "does not approve production engine pin or durable ACK", "failed stores/logs retained"]}
    (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
