"""동결 바이너리의 새 로컬 저장소에서 쓰기·검색·진단·재개방을 검증한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--network-enforcement", required=True,
                        choices=("docker-none", "windows-program-firewall", "macos-sandbox", "none"))
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve()
    home = root / "home"
    home.mkdir()
    cwd = root / "empty-working-directory"
    cwd.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VINC_", "MEMORY_", "PYTHON")) and k not in ("HOME", "USERPROFILE")}
    env.update(HOME=str(home), USERPROFILE=str(home), MEMORY_DATA_DIR=str(root / "db"),
               VINC_TARGET="local", VINC_EMBED_APPLY_PAUSED="1",
               VINC_BUFFER_POOL_MB="128", VINC_MAX_DB_SIZE=str(1024**3))
    if sys.platform == "win32":
        windows = next((value for key, value in env.items() if key.upper() == "SYSTEMROOT"), "C:/Windows")
        env["PATH"] = str(Path(windows) / "System32")
    text = "offline sentinel storage checkpoint recovery evidence\n"
    source = root / "sentinel.txt"
    source.write_bytes(text.encode("utf-8"))
    owner = "user:local"
    common = ["--target", "local", "--format", "json"]
    payload = {"owner": owner, "nodes": [
        {"id": "concept:offline-a", "type": "concept", "title": "Offline sentinel", "domain": "vinc/probe"},
        {"id": "concept:offline-b", "type": "concept", "title": "Recovery evidence", "domain": "vinc/probe"}],
        "edges": [{"from": "concept:offline-a", "to": "concept:offline-b", "type": "relates_to"}]}
    steps = [("version", ["--version"]),
             ("document_write", common + ["doc", "put", "--id", "offline-sentinel", "--owner", owner,
                                           "--title", "Offline sentinel", "--file", str(source)]),
             ("document_reopen", common + ["doc", "get", "--id", "offline-sentinel", "--owner", owner]),
             ("graph_write", common + ["call", "vinc_ingest", "--args", json.dumps(payload)]),
             ("fts_first", common + ["call", "vinc_search", "--args", json.dumps({"owner": owner, "query": "sentinel"})]),
             ("fts_reopen", common + ["call", "vinc_search", "--args", json.dumps({"owner": owner, "query": "sentinel"})]),
             ("components_reopen", common + ["call", "vinc_diagnose", "--args", json.dumps({"owner": owner, "kind": "components"})])]
    prefix = [str(binary)]
    if args.network_enforcement == "macos-sandbox":
        prefix = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)", str(binary)]
    results = []
    for name, command in steps:
        process = subprocess.run(prefix + command, cwd=cwd, env=env, capture_output=True,
                                 text=True, encoding="utf-8", timeout=90)
        (root / f"{name}.stdout").write_text(process.stdout, encoding="utf-8")
        (root / f"{name}.stderr").write_text(process.stderr, encoding="utf-8")
        value = None if name == "version" else json.loads(process.stdout) if process.returncode == 0 else None
        passed = process.returncode == 0
        if name == "version":
            passed = passed and process.stdout.startswith("vinc ")
        elif name == "document_write":
            passed = passed and value.get("executed") is True and not value.get("warnings")
        elif name == "document_reopen":
            passed = passed and value.get("verified") is True and value.get("text") == text
        elif name == "graph_write":
            passed = passed and not value.get("warnings") and not value.get("partial") and value.get("executed") is True
            passed = passed and value.get("merged", {}).get("nodes", {}).get("created") == 2
            passed = passed and value.get("merged", {}).get("edges", {}).get("created") == 1
        elif name.startswith("fts_"):
            passed = passed and not value.get("error") and any(
                r.get("doc_id") == "offline-sentinel" and "sentinel" in r.get("snippet", "")
                for r in value.get("rows", []))
        else:
            passed = passed and value.get("executed") is True and value.get("component_count") == 1
            passed = passed and value.get("orphan_count") == 0 and {
                member for c in value.get("components", []) for member in c.get("members", [])
            } == {"concept:offline-a", "concept:offline-b"}
        results.append({"step": name, "exit_code": process.returncode, "passed": passed})
        if not passed:
            break
    receipt = {"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "platform": sys.platform, "source_sha": os.environ.get("GITHUB_SHA"),
               "network_enforcement": args.network_enforcement, "steps": results,
               "passed": len(results) == len(steps) and all(r["passed"] for r in results),
               "limits": ["Disposable local storage; not operating-share or native desktop acceptance.",
                          "Normal subprocess reopen; forced-termination qualification is separate."]}
    (root / "result.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
