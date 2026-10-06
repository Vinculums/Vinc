"""로컬 배포 아카이브를 설치한 외부 venv에서만 비과금 fixture 검증을 실행한다."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (("vinc-client", "vinc_client"), ("vinc-langgraph", "vinc_langgraph"),
            ("vinc-agent-framework", "vinc_agent_framework"))
TESTS = (
    "integrations/client/python/tests/test_output_gate.py::test_original_frozen_hand_labels",
    "integrations/client/python/tests/test_output_gate_original.py",
    "integrations/parity/test_framework_parity.py",
    "integrations/parity/test_memory_workflow.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--kind", choices=("wheel", "sdist"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    assert sys.prefix != sys.base_prefix, "Use a clean external venv"
    assert not Path(sys.prefix).resolve().is_relative_to(ROOT), "The venv must be outside the checkout"
    installed = {}
    for name, module in PACKAGES:
        distribution = importlib.metadata.distribution(name)
        assert distribution.version == args.version, (name, distribution.version)
        provenance = json.loads(distribution.read_text("direct_url.json") or "null")
        assert provenance and not provenance.get("dir_info"), "Source and editable installs are not artifact evidence"
        extension = ".whl" if args.kind == "wheel" else ".tar.gz"
        artifact = args.artifacts / (module + "-" + args.version +
                                    ("-py3-none-any" if args.kind == "wheel" else "") + extension)
        assert provenance["url"] == artifact.resolve().as_uri(), (name, provenance)
        assert provenance["archive_info"]["hashes"]["sha256"] == digest(artifact)
        files = {}
        for entry in distribution.files or ():
            relative = str(entry)
            if relative.startswith(module + "/") and relative.endswith(".py"):
                imported = importlib.import_module(relative[:-3].replace("/", ".").removesuffix(".__init__"))
                path = Path(imported.__file__).resolve()
                assert path.is_relative_to(Path(sys.prefix).resolve()), path
                assert digest(path) == digest(ROOT / "integrations" / {
                    "vinc-client": "client", "vinc-langgraph": "langgraph",
                    "vinc-agent-framework": "agent-framework"}[name] / "python/src" / relative)
                files[relative] = {"path": str(path), "sha256": digest(path)}
        assert files, name
        installed[name] = {"version": distribution.version, "provenance": provenance, "modules": files}
    environment = dict(os.environ)
    for key in list(environment):
        if key in ("PYTHONHOME", "PYTHONPATH") or key.startswith((
                "OPENAI_", "AZURE_OPENAI_", "ANTHROPIC_", "LANGSMITH_", "LANGCHAIN_",
                "VINC_API_", "VINC_REMOTE_MCP_")):
            environment.pop(key)
    environment.update(LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    command = [sys.executable, "-I", "-m", "pytest", "-q", "--import-mode=importlib",
               *[str(ROOT / test) for test in TESTS], f"--junitxml={output / 'pytest.xml'}"]
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        run = subprocess.run(command, cwd=tempfile.gettempdir(), env=environment,
                             stdout=log, stderr=subprocess.STDOUT, check=False)
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    if (output / "pytest.xml").exists():
        for suite in ET.parse(output / "pytest.xml").findall(".//testsuite"):
            for key in counts:
                counts[key] += int(suite.get(key, "0"))
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    complete = run.returncode == 0 and counts["passed"] > 0 and not any(
        counts[key] for key in ("failures", "errors", "skipped"))
    exit_code = run.returncode or (0 if complete else 1)
    result = {"schema": "vinc.local-distribution-probe.v1", "kind": args.kind,
              "installed": installed, "command": command, "counts": counts,
              "exit_code": exit_code, "local_artifact_checks_passed": complete,
              "public_install_verified": False, "fresh_model_quality": "not_run",
              "fixture_sha256": digest(ROOT / "integrations/fixtures/output_gate/hand-labelled.json")}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(output / "result.json"), "counts": counts, "exit_code": exit_code}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
