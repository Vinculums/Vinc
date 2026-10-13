"""공개 원문과 실제 SQLite 실행을 후보 작성 전에 동결한다."""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import data_contracts as D


def now():
    return datetime.now(timezone.utc).isoformat()


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Vinc-Laya-source-preparation/1.0 (https://github.com/Vinculums/Vinc)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def observe(case):
    # 외부 코드를 실행하지 않는다. 로컬 계획에 있는 SQL만 일회용 메모리 DB에서 관측한다.
    code = ("import sqlite3,json; c=sqlite3.connect(':memory:'); "
            f"c.executescript({case['setup']!r}); "
            f"print(json.dumps(c.execute({case['query']!r}).fetchall(),ensure_ascii=False)); "
            "print(sqlite3.sqlite_version)")
    started = now()
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                            text=True, encoding="utf-8", timeout=10)
    D.require(result.returncode == 0, f"observation failed: {case['key']}")
    lines = result.stdout.splitlines()
    D.require(len(lines) == 2, "unexpected observation output")
    return {"case_id": case["key"], "setup": case["setup"], "query": case["query"],
            "code": code, "stdout": result.stdout, "stderr": result.stderr,
            "returncode": result.returncode, "rows": json.loads(lines[0]),
            "sqlite_version": lines[1], "started_at": started, "completed_at": now(),
            "python": sys.version, "platform": platform.platform(),
            "executable_sha256": D.digest(Path(sys.executable).read_bytes()),
            "origin": "locally_observed", "code_author": "OpenAI Codex assistant",
            "exact_served_model": None}


def freeze(plan, out):
    D.require(plan["schema"] == "vinc.laya.development-source-plan.v1", "unknown source plan")
    D.require(all(len(plan[k]) == 8 for k in ("documents", "observations", "stations")), "development needs eight sources per category")
    D.require(not out.exists(), "source freeze destination already exists")
    out.mkdir(parents=True)

    def put(name, data):
        target = D.safe_file(out, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        return D.digest(data)

    put("source-plan.json", D.encoded(plan))
    licenses = {}
    for item in plan["rights"]:
        raw = fetch(item["uri"])
        licenses[item["key"]] = {**item, "path": f"rights/{item['key']}.txt",
                                  "sha256": put(f"rights/{item['key']}.txt", raw), "captured_at": now()}
    sources = []
    for category, items in (("technical_specification", plan["documents"]),
                            ("dated_engineering_measurement", plan["observations"]),
                            ("general_factual_evidence", plan["stations"])):
        for item in items:
            origin = "observed" if category == "dated_engineering_measurement" else "public"
            if origin == "observed":
                raw = D.encoded(observe(item))
                uri, revision, license_id = f"vinc-local-observation:{item['key']}", D.digest(raw), "LicenseRef-Vinc-Owner-Authorized"
            else:
                uri, license_id = item["uri"], item["license_id"]
                raw = fetch(uri)
                if category == "general_factual_evidence":
                    station = json.loads(raw)
                    D.require(station["properties"]["stationIdentifier"] == item["station"], "station identity changed")
                revision = item.get("revision", "snapshot:" + D.digest(raw))
            path = f"sources/{item['key']}.txt"
            sources.append({"source_id": "source:laya-dev1-" + item["key"],
                            "document_id": "document:laya-dev1-" + item["key"],
                            "owner_scope": "vinc-project" if origin == "observed" else "public",
                            "origin_kind": origin, "origin_uri": uri, "revision": revision,
                            "license_id": license_id, "redaction_version": "none-no-private-input",
                            "captured_at": now(), "snapshot_path": path, "sha256": put(path, raw),
                            "category": category, "eligibility": {
                                "training": {"status": "pending", "decision_ref": plan["decision_ref"], "approved_by": "pending-data-review"},
                                "export": {"status": "approved", "decision_ref": plan["decision_ref"], "approved_by": "owner instruction; Codex public source inspection"}}})
    assigned = now()
    assignments = [{"source_id": s["source_id"], "role": "dev", "assigned_at": assigned,
                    "groups": [s["document_id"]]} for s in sources]
    allocation = [{"source_id": s["source_id"], "family_id": s["source_id"].replace("source:", "family:"),
                   "role": "dev", "language_cell": ("ko_ko", "en_en", "ko_en", "en_ko")[i % 4]}
                  for i, s in enumerate(sources)]
    result = {"schema": "vinc.laya.source-freeze.v1", "decision_ref": plan["decision_ref"],
              "created_at": now(), "sources": sources, "assignments": assignments,
              "allocation": allocation, "licenses": licenses, "source_plan_sha256": D.fingerprint(plan),
              "training_authorized": False, "final_measurement": False,
              "independent_statistical_draws_claimed": False}
    put("source-freeze.json", D.encoded(result))
    return {"sources": len(sources), "source_freeze_sha256": D.fingerprint(result)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = freeze(json.loads(args.plan.read_text(encoding="utf-8")), args.out)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
