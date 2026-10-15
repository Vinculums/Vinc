"""동결된 AI 후보 비교를 승인된 두 번의 시도 안에서 실행한다."""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from . import curation as C, development as D, intervention as I
from .data_contracts import require

SCHEMA = "vinc.laya.curated-execution.v1"
REG_SHA = "81537d2998d22805860f007be5aa4d20a3f9a142f4ac26a922795c221d94ea46"
DECISION = "decision:execute-registered-laya-curated-comparison-2026-10-15"
ARMS = ("positive_balanced_core", "curated_supports_replacement")
AUTH = C.DATA / "laya-curated-execution-v1/authorization.json"
BASE_METRICS = D.metrics


def checked(path, sha):
    raw = Path(path).read_bytes()
    require(C.D.digest(raw) == sha, "registered bytes changed: " + str(path))
    return raw


def validate_authority(auth):
    require(auth["schema"] == SCHEMA and auth["decision"] == DECISION
            and auth["owner_instruction"] == "그럼 다음 단계 작업 진행"
            and auth["training_execution_authorized"] is True, "execution authority missing")
    require(auth["registration_sha256"] == REG_SHA and auth["max_training_runs"] == 2
            and auth["max_total_seconds"] == 3600 and auth["arms"] == list(ARMS), "execution budget changed")
    require(auth["paid_resources"] is False and auth["production_authorized"] is False,
            "local development scope changed")


def validate_manifest(auth, manifest):
    validate_authority(auth)
    require(C.D.digest(C.D.encoded(manifest)) == auth["manifest_sha256"], "execution manifest changed")
    require(manifest["schema"] == SCHEMA and manifest["arms"] == list(ARMS), "arm inventory changed")
    for item in manifest["files"]:
        checked(item["path"], item["sha256"])
    require(C.D.digest(Path(__file__).read_bytes()) == manifest["execution_module_sha256"], "execution code changed")
    root = C.DATA / "laya-ai-curation-v1"
    reg = json.loads(checked(root / "comparison-registration.json", REG_SHA))
    for name, sha in reg["code_sha256"].items():
        checked(Path(__file__).with_name(name), sha)
    checked(root / reg["tokenizer_preflight"]["path"], reg["tokenizer_preflight"]["sha256"])
    checked(Path(manifest["snapshot"]) / "model.safetensors", D.BASE_SHA)
    cfg = C.read(Path(manifest["snapshot"]) / "rl_agent_config.json")
    require(cfg["max_len"] == 1024 and cfg["head_max_len"] == 256, "checkpoint layout changed")
    return reg


def prefix_diagnostics(rows, cohort):
    result = copy.deepcopy(rows)
    for row in result:
        row["questions"] = {cohort + "::" + k: v for k, v in row["questions"].items()}
        row["gold"] = {cohort + "::" + k: v for k, v in row["gold"].items()}
        row.setdefault("meta", {})["diagnostic_cohort"] = cohort
        if row["meta"].get("language"):
            row["meta"]["language_cell"] = row["meta"]["language"]
    return result


def prepare(workspace):
    workspace = Path(workspace).resolve()
    source = C.DATA / "laya-ai-curation-v1"
    reg = json.loads(checked(source/"comparison-registration.json", REG_SHA))
    prepared, freeze = workspace/"_scratch/laya/vin476/prepared-final-v2", workspace/"_scratch/laya/vin483/freeze-v2"
    receipt = C.build(prepared,freeze)
    require(C.D.encoded(receipt) == (source/"curation-receipt.json").read_bytes(), "curation replay differs")
    draft_path = workspace/"_scratch/laya/vin511/draft-inputs.json"
    draft = json.loads(checked(draft_path,reg["inputs"]["draft_inputs_sha256"]))
    rows = [r for cohort in C.A.G.COHORTS for r in C.A.read_lines(freeze/(cohort+".jsonl"))]
    train, dev, _, _ = C.select(rows,C.read(source/"source-dispositions.json"))
    core = C.A.read_lines(C.DATA/"laya-predicates-community-supports-v2/training.jsonl")
    require(draft == C.draft_inputs(core,train,dev), "draft input replay differs")
    diagnostic_paths = {"exposed_core_dev":workspace/"_scratch/laya/vin471/core-development.jsonl",
                        "native_dev":workspace/"_scratch/laya/vin471/auxiliary/development.jsonl"}
    development = copy.deepcopy(draft["development"])
    for cohort,path in diagnostic_paths.items():
        checked(path,reg["historical_crossings"][cohort]["sha256"])
        development.extend(prefix_diagnostics(D.read_rows(path),cohort))
    out = workspace/"_scratch/laya/vin514/inputs"
    require(not AUTH.exists(), "execution authorization already frozen")
    out.mkdir(parents=True,exist_ok=False)
    files = []
    def pin(path):
        item={"path":str(path.resolve()),"sha256":C.D.digest(path.read_bytes())}
        files.append(item)
        return item
    for arm,key in zip(ARMS,("control","intervention")):
        (out/(arm+".jsonl")).write_bytes(I.jsonl_bytes(draft[key]))
    (out/"development.jsonl").write_bytes(I.jsonl_bytes(development))
    dev_record=pin(out/"development.jsonl")
    snapshot=workspace/"_scratch/hf-home/hub/models--convaiinnovations--laya-multilingual/snapshots"/reg["recipe"]["base_revision"]
    for path in sorted(snapshot.rglob("*")):
        if path.is_file():pin(path)
    for path in sorted(source.glob("*.json")):pin(path)
    pin(draft_path)
    for path in diagnostic_paths.values():pin(path)
    training_sha=C.read(source/"tokenizer-preflight.json")["laya_train_sha256"]
    registrations={arm:{"schema":SCHEMA,"arm":arm,"base_sha256":D.BASE_SHA,"loss":"soft-ce",
        "sealed_test":False,"epochs":2,"item_cap":3012,"scheduled_updates":96,"seed":20261007,
        "selection":I.SELECTION,"training":[pin(out/(arm+".jsonl"))],"development":[dev_record],
        "development_module_sha256":reg["code_sha256"]["development.py"],"laya_train_sha256":training_sha} for arm in ARMS}
    manifest={"schema":SCHEMA,"arms":list(ARMS),"snapshot":str(snapshot),"hf_home":str(workspace/"_scratch/hf-home"),
        "attempt_root":str(workspace/"_scratch/laya/vin514/attempt-1"),"registrations":registrations,"files":files,
        "execution_module_sha256":C.D.digest(Path(__file__).read_bytes())}
    manifest_path=out/"manifest.json"
    manifest_path.write_bytes(C.D.encoded(manifest))
    auth={"schema":SCHEMA,"decision":DECISION,"owner_instruction":"그럼 다음 단계 작업 진행",
        "training_execution_authorized":True,"registration_sha256":REG_SHA,"max_training_runs":2,
        "max_total_seconds":3600,"arms":list(ARMS),"paid_resources":False,"production_authorized":False,
        "manifest_path":str(manifest_path),"manifest_sha256":C.D.digest(manifest_path.read_bytes())}
    AUTH.parent.mkdir(parents=True,exist_ok=True)
    with AUTH.open("xb") as handle:handle.write(C.D.encoded(auth))
    return auth,manifest


def enhanced_metrics(records, metadata):
    require(len(records) == len(metadata), "evaluation cardinality mismatch")
    result = BASE_METRICS(records, metadata)
    for task, values in result.items():
        pairs = [(D.softmax(list(map(float, r[1])))[1], int(r[2][1]))
                 for r, m in zip(records, metadata) if m["task"] == task]
        tp = sum(p >= .5 and y == 1 for p, y in pairs)
        fp = sum(p >= .5 and y == 0 for p, y in pairs)
        fn = sum(p < .5 and y == 1 for p, y in pairs)
        tn = sum(p < .5 and y == 0 for p, y in pairs)
        values.update(tp=tp, fp=fp, fn=fn, tn=tn, f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.)
    languages = sorted({m["language_cell"] for m in metadata if m.get("language_cell")})
    result["by_language"] = {}
    for lang in languages:
        subset = [(r, m) for r, m in zip(records, metadata) if m.get("language_cell") == lang]
        # 하위 호출에는 언어 키를 빼서 재귀를 끝낸다.
        result["by_language"][lang] = enhanced_metrics([r for r, _ in subset],
            [{k:v for k,v in m.items() if k != "language_cell"} for _,m in subset])
        result["by_language"][lang].pop("by_language")
    return result


def validate_arm(reg, manifest, arm):
    require(arm in ARMS and reg == manifest["registrations"][arm], "arm registration changed")
    require(reg["schema"] == SCHEMA and reg["selection"] == I.SELECTION
            and reg["epochs"] == 2 and reg["item_cap"] == 3012 and reg["scheduled_updates"] == 96
            and reg["seed"] == 20261007 and reg["loss"] == "soft-ce" and reg["sealed_test"] is False
            and reg["base_sha256"] == D.BASE_SHA, "registered recipe changed")


def load_execution():
    auth = C.read(AUTH)
    manifest = json.loads(checked(auth["manifest_path"], auth["manifest_sha256"]))
    validate_manifest(auth, manifest)
    return auth, manifest


def run_arm(arm):
    auth, manifest = load_execution()
    root = Path(manifest["attempt_root"])
    ledger = C.read(root / "attempt.json")
    require(ledger["manifest_sha256"] == auth["manifest_sha256"]
            and ledger["deadline"] > time.time(), "supervised attempt absent or expired")
    reg = manifest["registrations"][arm]
    validate_arm(reg, manifest, arm)
    reg_path = root / (arm + "-registration.json")
    # 같은 팔을 다른 경로로 재시도할 수 없도록 시작 표식을 독점 생성한다.
    with reg_path.open("xb") as handle:
        handle.write(C.D.encoded(reg))
    with patch.object(D, "validate_registration", lambda value: validate_arm(value, manifest, arm)), \
         patch.object(D, "metrics", enhanced_metrics):
        D.main(["--registration", str(reg_path), "--snapshot", manifest["snapshot"],
                "--hf-home", manifest["hf_home"], "--out", str(root / arm)])


def paired_uncertainty(control, intervention, replicates=1000):
    def rows(outputs):
        return {r["meta"]["pair_id"]: r for r in outputs if r["meta"]["task"] == "supports"}
    left, right = rows(control), rows(intervention)
    require(left.keys() == right.keys() and left, "paired development identities differ")
    groups = defaultdict(list)
    for pid, r in left.items():
        require(r["target"] == right[pid]["target"] and r["meta"]["family_id"] == right[pid]["meta"]["family_id"],
                "paired target or family differs")
        groups[r["meta"]["family_id"]].append(pid)
    def score(outputs, ids):
        pairs = [(D.softmax(outputs[i]["logits"])[1], int(outputs[i]["target"][1])) for i in ids]
        tp = sum(p >= .5 and y for p,y in pairs)
        fp = sum(p >= .5 and not y for p,y in pairs)
        fn = sum(p < .5 and y for p,y in pairs)
        return 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.
    rng, families, delta = random.Random(20261007), sorted(groups), []
    for _ in range(replicates):
        ids = [pid for f in rng.choices(families, k=len(families)) for pid in groups[f]]
        delta.append(score(right, ids)-score(left, ids))
    delta.sort()
    return {"families":len(groups), "pairs":len(left), "replicates":replicates,
            "seed":20261007, "f1_delta":score(right,list(left))-score(left,list(left)),
            "percentile_95":[delta[int(.025*replicates)],delta[min(replicates-1,int(.975*replicates))]],
            "interpretation":"descriptive paired family bootstrap on selected AI development; not independent final-test evidence"}


def summarize(root):
    arms, outputs, original = {}, {}, {}
    for arm in ARMS:
        folder = root / arm
        summary, history = C.read(folder/"summary.json"), C.read(folder/"history.json")
        chosen = I.select_checkpoint(history, I.SELECTION)
        require(chosen["epoch"] == summary["selected_epoch"] and chosen["development"] == summary["selected"],
                "checkpoint selector replay differs")
        require(summary["scheduled_updates"] == 96 and summary["executed_updates"] + summary["AMP_skips"] == 96,
                "optimizer accounting differs")
        checkpoint = folder / f'epoch-{chosen["epoch"]}'
        checked(checkpoint/"model.safetensors", summary["model_sha256"])
        outputs[arm] = C.read(checkpoint/"development.json")["outputs"]
        original[arm] = C.read(folder/"original-development.json")["outputs"]
        for name, records in [("baseline", original[arm]), ("selected", outputs[arm])]:
            replay = enhanced_metrics([(i,r["logits"],r["target"],2) for i,r in enumerate(records)], [r["meta"] for r in records])
            require(replay == summary[name], "saved logits/metric replay differs")
        arms[arm] = summary
    require(original[ARMS[0]] == original[ARMS[1]], "original-weight baseline differs between arms")
    return {"schema":SCHEMA, "arms":arms, "paired_supports":paired_uncertainty(outputs[ARMS[0]],outputs[ARMS[1]]),
            "original_outputs_equal":True, "production_authorized":False, "sealed_test_opened":False}


def supervise(manifest, auth):
    root = Path(manifest["attempt_root"])
    root.mkdir(parents=True, exist_ok=False)
    started = time.time()
    deadline = started + auth["max_total_seconds"]
    (root/"attempt.json").write_bytes(C.D.encoded({"started_at":started,"deadline":deadline,
        "manifest_sha256":auth["manifest_sha256"],"max_training_runs":2,"no_retry":True}))
    results = []
    for arm in ARMS:
        remaining = deadline-time.time()
        if remaining <= 0:
            results.append({"arm":arm,"status":"not_started_budget_exhausted"})
            break
        with (root/(arm+".log")).open("wb") as log:
            try:
                child = subprocess.run([sys.executable,"-u","-m","rag_eval.laya.curated_execution","arm","--arm",arm],
                                       stdout=log,stderr=subprocess.STDOUT,timeout=remaining,check=False)
                result = {"arm":arm,"status":"complete" if child.returncode == 0 else "failed","exit_code":child.returncode}
            except subprocess.TimeoutExpired:
                result = {"arm":arm,"status":"timeout"}
        result["elapsed_total_seconds"] = time.time()-started
        results.append(result)
        (root/"attempt-results.json").write_bytes(C.D.encoded(results))
        print(json.dumps(result), flush=True)
        if result["status"] != "complete":
            break
    (root/"attempt-results.json").write_bytes(C.D.encoded(results))
    require(len(results) == 2 and all(r["status"] == "complete" for r in results), "bounded comparison incomplete; no retry authorized")
    result = summarize(root)
    result["elapsed_total_seconds"] = time.time()-started
    (root/"comparison.json").write_bytes(C.D.encoded(result))
    print(json.dumps({"verified":True,"paired_supports":result["paired_supports"]}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare","verify","run","arm","summarize"))
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--workspace",type=Path)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        require(args.workspace is not None,"workspace required")
        auth,manifest=prepare(args.workspace)
        print(json.dumps({"prepared":True,"manifest_sha256":auth["manifest_sha256"]}))
        return
    auth, manifest = load_execution()
    if args.action == "verify":
        for arm in ARMS:
            validate_arm(manifest["registrations"][arm],manifest,arm)
        print(json.dumps({"verified":True,"manifest_sha256":auth["manifest_sha256"]}))
    elif args.action == "run":
        supervise(manifest,auth)
    elif args.action == "arm":
        require(args.arm in ARMS,"arm required")
        run_arm(args.arm)
    else:
        print(json.dumps(summarize(Path(manifest["attempt_root"]))["paired_supports"]))


if __name__ == "__main__":
    main()
