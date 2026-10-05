"""동결 사이드카를 네트워크 없는 Linux 컨테이너의 새 저장소에서 검증한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--image", default="ubuntu:22.04")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    # 기존 저장소와 실패 자료를 건드리지 않는다. 모든 입력은 합성 데이터다.
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    state = output / "state"
    state.mkdir()
    inputs = output / "input"
    inputs.mkdir()
    text = "offline sentinel storage checkpoint recovery evidence\n"
    (inputs / "sentinel.txt").write_text(text, encoding="utf-8")
    environment = dict(os.environ)
    for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(key, None)
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    try:
        inspection = subprocess.run(docker + ["image", "inspect", "--format", "{{.Id}}", args.image],
                                    env=environment, text=True, capture_output=True, timeout=30)
        image_id = inspection.stdout.strip()
        if inspection.returncode or not image_id:
            raise RuntimeError(inspection.stderr.strip() or "local image unavailable")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        (output / "result.json").write_text(json.dumps({"passed": False, "stage": "image_inspection",
            "error": str(exc), "network": "not_started"}, indent=2) + "\n", encoding="utf-8")
        return 1
    run = docker + ["run", "--rm", "--pull=never", "--network=none",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "--mount", f"type=bind,src={binary.parent},dst=/probe,readonly",
                    "--mount", f"type=bind,src={inputs},dst=/input,readonly",
                    "--mount", f"type=bind,src={state},dst=/state",
                    "-e", "HOME=/state/home", "-e", "MEMORY_DATA_DIR=/state/data",
                    "-e", "VINC_TARGET=local", "-e", "VINC_EMBED_APPLY_PAUSED=1",
                    image_id, f"/probe/{binary.name}"]
    common = ["--target", "local", "--format", "json"]
    steps = [
        ("version", ["--version"]),
        ("document_write", common + ["doc", "put", "--id", "offline-sentinel", "--owner", "user:local",
                                     "--title", "Offline sentinel", "--file", "/input/sentinel.txt"]),
        ("document_reopen", common + ["doc", "get", "--id", "offline-sentinel", "--owner", "user:local"]),
        ("graph_write", common + ["call", "vinc_ingest", "--args", json.dumps({"owner": "user:local", "nodes": [
            {"id": "concept:offline-a", "type": "concept", "title": "Offline sentinel", "domain": "vinc/probe"},
            {"id": "concept:offline-b", "type": "concept", "title": "Recovery evidence", "domain": "vinc/probe"}],
            "edges": [{"from": "concept:offline-a", "to": "concept:offline-b", "type": "relates_to"}]})]),
        ("fts_first", common + ["call", "vinc_search", "--args", json.dumps({"owner": "user:local", "query": "sentinel"})]),
        ("fts_reopen", common + ["call", "vinc_search", "--args", json.dumps({"owner": "user:local", "query": "sentinel"})]),
        ("components_reopen", common + ["call", "vinc_diagnose", "--args", json.dumps({"owner": "user:local", "kind": "components"})]),
    ]
    results = []
    for name, command in steps:
        container = f"vinc-offline-probe-{uuid.uuid4().hex}"
        # 클라이언트 timeout만으로 컨테이너가 종료되지는 않는다.
        invocation = run[:3] + ["--name", container] + run[3:]
        cleanup_error = None
        timed_out = False
        try:
            process = subprocess.run(invocation + command, env=environment, text=True, capture_output=True, timeout=90)
            stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            code, timed_out = 124, True
        except OSError as exc:
            stdout, stderr, code = "", str(exc), 127
        (output / f"{name}.stdout").write_text(stdout, encoding="utf-8")
        (output / f"{name}.stderr").write_text(stderr, encoding="utf-8")
        if timed_out:
            try:
                cleanup = subprocess.run(docker + ["container", "rm", "--force", container], env=environment,
                                         capture_output=True, text=True, timeout=30, check=False)
                if cleanup.returncode:
                    cleanup_error = cleanup.stderr.strip() or f"cleanup exit {cleanup.returncode}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                cleanup_error = str(exc)
        value = None
        try:
            value = json.loads(stdout) if name != "version" else None
        except ValueError:
            pass
        passed = code == 0
        if name == "version":
            passed = passed and stdout.startswith("vinc ") and bool(stdout.removeprefix("vinc ").strip())
        elif name == "document_write":
            passed = passed and isinstance(value, dict) and value.get("executed") is True
        elif name == "document_reopen":
            passed = passed and isinstance(value, dict) and value.get("verified") is True and value.get("text") == text
        elif name == "graph_write":
            passed = passed and isinstance(value, dict) and value.get("executed") is True and not value.get("warnings") and not value.get("partial")
            if passed:
                passed = (value.get("merged", {}).get("nodes", {}).get("created") == 2
                          and value.get("merged", {}).get("edges", {}).get("created") == 1
                          and {n["id"] for n in value.get("stored", [])} == {"concept:offline-a", "concept:offline-b"})
        elif name.startswith("fts_"):
            passed = passed and isinstance(value, dict) and not value.get("error")
            if passed:
                passed = any(row.get("doc_id") == "offline-sentinel" and "sentinel" in row.get("snippet", "")
                             for row in value.get("rows", []))
        elif name == "components_reopen":
            passed = passed and isinstance(value, dict) and value.get("executed") is True and not value.get("error")
            if passed:
                passed = (value.get("component_count") == 1 and value.get("orphan_count") == 0
                          and {m for c in value.get("components", []) for m in c.get("members", [])}
                          == {"concept:offline-a", "concept:offline-b"})
        results.append({"step": name, "exit_code": code, "passed": passed, "cleanup_error": cleanup_error})
        if not passed:
            break
    receipt = {"schema": "vinc.offline-sidecar-probe.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
               "image": image_id, "network": "none", "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "steps": results, "passed": len(results) == len(steps) and all(r["passed"] for r in results),
               "limits": ["Linux CLI subprocesses on disposable local storage", "not native desktop or SMB acceptance",
                          "not Windows/macOS acceptance", "does not establish crash recovery or native algo execution"]}
    (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
