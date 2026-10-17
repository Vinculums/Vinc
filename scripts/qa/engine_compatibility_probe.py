"""구판 저장소의 후보 개방과 원본 백업을 이용한 구판 복귀를 별도 사본에서 검증한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT
OWNER = "user:compatibility-probe"
TEXT = "compatibilitysentinel original checkpoint document"
# 꼬리(마지막 길이 % 8 바이트)에 한글이 걸리는 물리 키. 0.20.4 이하가 지은 PK 색인을 0.21 이
# 열면 등호 조회가 이 키를 놓친다. 후보가 구판 스토어를 그대로 쓸 수 있는지는 이것으로 갈린다.
HANGUL_KEY = OWNER + "\x1f" + "concept:재현-가능한-측정값"
_RAW = HANGUL_KEY.encode()
assert len(_RAW) % 8 and max(_RAW[-(len(_RAW) % 8):]) >= 0x80, "key must end in a non-ASCII tail"


def exercise(path: Path, mode: str) -> dict:
    from api.docstore import get_doc, put_doc
    from store.backend import open_store
    from store.config import load_config
    import ladybug
    store = open_store(load_config({"data_dir": str(path), "buffer_pool_mb": 128,
                                   "max_db_size": 1024**3}))
    if store is None:
        raise RuntimeError("store did not open")
    try:
        store.provision(dim=4)
        store.heal_fts_index(dim=4)
        if mode == "create":
            receipt = put_doc(store, owner=OWNER, doc_id="compatibility-sentinel", text=TEXT, link=False)
            assert receipt.get("executed") and not receipt.get("warnings"), receipt
            store.run("MERGE (c:Concept {concept_id:$k}) SET c.owner_id=$o, c.title='hangul tail'",
                      {"k": HANGUL_KEY, "o": OWNER}, raw=True)
        non_ascii_pk = {
            "pk": store.run("MATCH (c:Concept) WHERE c.concept_id = $k RETURN count(c) AS n",
                            {"k": HANGUL_KEY}, raw=True)[0]["n"],
            "scan": store.run("MATCH (c:Concept) WHERE c.owner_id = $o AND c.title = 'hangul tail' "
                              "RETURN count(c) AS n", {"o": OWNER}, raw=True)[0]["n"]}
        doc = get_doc(store, "compatibility-sentinel", owner=OWNER)
        assert doc.get("verified") and doc.get("text") == TEXT, doc
        assert store.fulltext_search("compatibilitysentinel", 10, {"owner_id": OWNER})
        if mode == "mutate":
            receipt = put_doc(store, owner=OWNER, doc_id="candidate-write",
                              text="candidatewrite succeeds after opening the previous engine store", link=False)
            assert receipt.get("executed") and not receipt.get("warnings"), receipt
            assert store.fulltext_search("candidatewrite", 10, {"owner_id": OWNER})
        elif mode == "rollback":
            assert not get_doc(store, "candidate-write", owner=OWNER).get("verified")
        store.run("CHECKPOINT")
        return {"engine": ladybug.__version__, "mode": mode, "canonical": True,
                "fts": True, "non_ascii_pk": non_ascii_pk, "passed": non_ascii_pk["scan"] == 1}
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-python", type=Path)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", choices=("create", "mutate", "rollback"))
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(exercise(args.database, args.child)))
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve()
    original = root / "original.db"
    candidate = root / "candidate.db"
    rollback = root / "rollback.db"
    records = []

    def child_env(extension_root):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VINC_", "MEMORY_", "PYTHON"))}
        env.update(HOME=str(extension_root / "build-home"), USERPROFILE=str(extension_root / "build-home"),
                   VINC_LBUG_EXT_DIR=str(extension_root / "extensions"), VINC_PK_NAMESPACED="on")
        return env

    def run(python, mode, database, extension_root):
        env = child_env(extension_root)
        try:
            completed = subprocess.run([str(python), str(Path(__file__).resolve()), "--child", mode,
                                       "--database", str(database), "--output", str(root)],
                                      cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        except subprocess.TimeoutExpired as error:
            for channel in ("stdout", "stderr"):
                data = getattr(error, channel) or b""
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                (root / f"{mode}.{channel}").write_text(data, encoding="utf-8")
            (root / "result.json").write_text(json.dumps({"passed": False, "failed_step": mode,
                                                         "error": "timeout", "steps": records}) + "\n", encoding="utf-8")
            raise RuntimeError(f"{mode} timed out; see preserved logs") from error
        (root / f"{mode}.stdout").write_text(completed.stdout, encoding="utf-8")
        (root / f"{mode}.stderr").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"{mode} failed with {completed.returncode}; see preserved logs")
        record = json.loads(completed.stdout)
        records.append(record)
        return record

    # venv 인터프리터의 심볼릭 링크를 풀면 시스템의 후보 인터프리터로 바뀐다.
    baseline_python = args.baseline_python.absolute()
    run(baseline_python, "create", original, args.baseline_root.resolve())
    if Path(str(original) + ".wal").exists() and Path(str(original) + ".wal").stat().st_size:
        raise RuntimeError("old store is not checkpointed")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    shutil.copy2(original, candidate)
    run(Path(sys.executable), "mutate", candidate, args.candidate_root.resolve())
    # 후보가 쓴 파일을 구판으로 열지 않는다. 보존한 원본의 별도 사본으로 복귀한다.
    shutil.copy2(original, rollback)
    run(baseline_python, "rollback", rollback, args.baseline_root.resolve())
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    # 후보가 구판 PK 색인에서 비ASCII 키를 놓치면 그 스토어는 재구축해야 한다. 후보로 재구축한
    # 사본이 그 키를 찾는 것까지가 합격이다.
    shutil.copy2(original, root / "rebuild-source.db")
    completed = subprocess.run([sys.executable, str(Path(ROOT) / "scripts" / "rebuild_store_native.py"),
                                "--data", str(root / "rebuild-source.db"), "--out", str(root / "rebuilt.db"),
                                "--json"], cwd=ROOT, env=child_env(args.candidate_root.resolve()),
                               capture_output=True, text=True, encoding="utf-8", timeout=300)
    (root / "rebuild.stdout").write_text(completed.stdout, encoding="utf-8")
    (root / "rebuild.stderr").write_text(completed.stderr, encoding="utf-8")
    rebuilt = json.loads(completed.stdout) if completed.stdout.strip() else {"ok": False}
    rebuild = {"ok": bool(rebuilt.get("ok")), "src_pk_misses": rebuilt.get("src_pk_misses"),
               "dst_pk_misses": rebuilt.get("dst_pk_misses")}
    receipt = {"source_sha": os.environ.get("GITHUB_SHA"), "original_sha256": digest,
               "steps": records,
               "requires_rebuild": records[1]["non_ascii_pk"]["pk"] != 1,
               "rebuild": rebuild,
               "passed": (len(records) == 3 and all(r["passed"] for r in records) and rebuild["ok"]
                          and rebuild["dst_pk_misses"] == {"Concept": {"keys": 1, "missed": 0}}),
               "rollback": "restore the unchanged pre-upgrade copy; no reverse-format compatibility claim",
               "limits": ["Synthetic checkpointed local store; not live migration or persistent-share acceptance."]}
    (root / "result.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
