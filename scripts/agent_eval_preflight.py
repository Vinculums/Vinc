"""기존 비과금 검증을 재현하고 모델 품질 완료 조건과 분리해 출력한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "client": ["integrations/client/python/tests"],
    "langgraph": ["integrations/langgraph/python/tests"],
    "agent-framework": ["integrations/agent-framework/python/tests"],
    "parity": ["integrations/parity"],
    "core": ["tests/core/test_package_contract.py", "tests/core/test_citation_ids.py"],
}
ACCEPTANCE = {
    "VIN-301": {"experiment": "exp11", "remaining": "Package vs transcript: 10/10 claims, zero reversals."},
    "VIN-309": {"experiment": "exp09", "remaining": "Existing node IDs cited without extra citation prompting."},
    "VIN-312": {"experiment": "exp05", "remaining": "Zero role collapse, unrecorded side effects and empty manifests."},
    "VIN-313": {"experiment": "exp11", "remaining": "All three original tasks: 10/10, zero reversals, shipped package APIs."},
    "VIN-314": {"experiment": "exp01", "remaining": "All six original base answers cite at least one existing node ID."},
    "VIN-317": {"experiment": "exp01", "remaining": "R3/C3, mini and strong, no hints: interrupt and no extra model call on resume."},
    "VIN-318": {"experiment": "exp02", "remaining": "Original R2: same tool call counts and citation format in both frameworks."},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # 실패 로그를 덮어쓰지 않는다. 재실행은 별도 출력 디렉터리를 쓴다.
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    xml = output / "pytest.xml"
    command = [sys.executable, "-m", "pytest", "-q",
               *SUITES[args.suite], f"--junitxml={xml}"]
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    if args.suite == "core" and sys.platform.startswith("linux"):
        environment.setdefault("VINC_LBUG_EXT_DIR", str(ROOT / "packaging/lbug_extensions"))
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        process = subprocess.run(command, cwd=ROOT, env=environment, stdout=log,
                                 stderr=subprocess.STDOUT, check=False)
    counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    cases = []
    if xml.exists():
        tree = ET.parse(xml)
        for suite in tree.findall(".//testsuite"):
            for key in counts:
                counts[key] += int(suite.get(key, "0"))
        for case in tree.findall(".//testcase"):
            status = next((kind for kind in ("failure", "error", "skipped")
                           if case.find(kind) is not None), "passed")
            cases.append({"class": case.get("classname"), "name": case.get("name"),
                          "status": status})
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    complete = (process.returncode == 0 and counts["passed"] > 0
                and counts["failures"] == counts["errors"] == counts["skipped"] == 0)
    exit_code = process.returncode or (0 if complete else 1)
    versions = {}
    for package in ("ladybug", "pytest", "vinc-client", "vinc-langgraph",
                    "vinc-agent-framework", "agent-framework-core", "langgraph", "langchain-core"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    fixtures = [ROOT / "integrations/fixtures/output_gate/hand-labelled.json",
                ROOT / "integrations/fixtures/output_gate/proposed-role-checks.json",
                ROOT / "tests/fixtures/agent_lab_exp05_dag.json"]
    result = {
        "schema": "vinc.agent-eval-preflight.v1", "suite": args.suite,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_tree": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines(),
        "python": sys.version, "versions": versions, "command": command,
        "exit_code": exit_code, "pytest_exit_code": process.returncode, "counts": counts, "cases": cases,
        "deterministic_checks_passed": complete,
        "all_collected_cases_executed": counts["tests"] > 0 and counts["skipped"] == 0,
        "fixture_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in fixtures},
        "runner_sha256": sha256(Path(__file__)),
        "test_source_sha256": {str(path.relative_to(ROOT)): sha256(path)
                               for directory in SUITES[args.suite]
                               for path in ([ROOT / directory] if (ROOT / directory).is_file()
                                            else sorted((ROOT / directory).rglob("*.py")))},
        "acceptance": {issue: {**criteria, "fresh_model_quality": "not_run"}
                       for issue, criteria in ACCEPTANCE.items()},
        "public_package_install": "not_verified_by_local_source_tests",
        "original_task_inputs": "exp09/11 inputs and original scoring are not in this repository",
        "logs": {"pytest": str(output / "pytest.log"), "junit": str(xml)},
    }
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(output / "result.json"), "exit_code": exit_code,
                      "counts": counts}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
