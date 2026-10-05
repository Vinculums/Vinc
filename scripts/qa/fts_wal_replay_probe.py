"""새 합성 저장소에서 FTS의 급사/정상 종료 재생을 비교하고 실패 바이트를 보존한다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys


WRITER = r'''
import json,os,signal,sys,ladybug
db=ladybug.Database(sys.argv[1]);c=ladybug.Connection(db)
c.execute("LOAD EXTENSION '"+sys.argv[2].replace("'","''")+"'")
c.execute("CREATE NODE TABLE Chunk(id STRING PRIMARY KEY,text STRING)")
if sys.argv[3]=='seeded':c.execute("CREATE (:Chunk {id:'seed',text:'initial unrelated record'})")
c.execute("CALL CREATE_FTS_INDEX('Chunk','probe_ft',['text'])")
c.execute("CREATE (:Chunk {id:'sentinel',text:'frozen recovery sentinel'})")
r=c.execute("CALL QUERY_FTS_INDEX('Chunk','probe_ft','sentinel') RETURN node.id AS id,score")
rows=[]
while r.has_next():rows.append(r.get_next())
print(json.dumps({'before':rows,'version':ladybug.__version__}),flush=True)
if sys.argv[4]=='abrupt':os.kill(os.getpid(),signal.SIGKILL)
c.close();db.close()
'''

READER = r'''
import json,sys,ladybug
db=ladybug.Database(sys.argv[1]);c=ladybug.Connection(db)
c.execute("LOAD EXTENSION '"+sys.argv[2].replace("'","''")+"'")
queries={"chunks":"MATCH (c:Chunk) RETURN c.id AS id,c.text AS text", "after":"CALL QUERY_FTS_INDEX('Chunk','probe_ft','sentinel') RETURN node.id AS id,score"}
out={}
for key,q in queries.items():
 r=c.execute(q);rows=[]
 while r.has_next():rows.append(r.get_next())
 out[key]=rows
if sys.argv[3]=='rebuild':
 c.execute("CALL DROP_FTS_INDEX('Chunk','probe_ft')")
 c.execute("CALL CREATE_FTS_INDEX('Chunk','probe_ft',['text'])")
 r=c.execute(queries['after']);rows=[]
 while r.has_next():rows.append(r.get_next())
 out['after_local_rebuild']=rows
print(json.dumps(out));c.close();db.close()
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-local-rebuild", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("this probe requires Linux SIGKILL")
    extension = args.extension.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output.resolve()
    cases = []
    for seed in ("empty", "seeded"):
        for stop in ("abrupt", "graceful"):
            case = root / (seed + "-" + stop)
            case.mkdir()
            database = case / "db"
            record = {"seed": seed, "termination": stop, "passed": False}
            try:
                writer = subprocess.run([sys.executable, "-c", WRITER, str(database), str(extension), seed, stop],
                                        capture_output=True, text=True, timeout=30)
                (case / "writer.stdout").write_text(writer.stdout)
                (case / "writer.stderr").write_text(writer.stderr)
                record["writer_exit"] = writer.returncode
                record.update(json.loads(writer.stdout))
                expected = -signal.SIGKILL if stop == "abrupt" else 0
                if writer.returncode != expected:
                    raise RuntimeError("writer did not terminate as requested")
                preserved = case / "pre-replay"
                preserved.mkdir()
                record["pre_replay_files"] = []
                for source in case.glob("db*"):
                    if source.is_file():
                        copied = preserved / source.name
                        shutil.copy2(source, copied)
                        record["pre_replay_files"].append({"name": copied.name,
                            "bytes": copied.stat().st_size, "sha256": hashlib.sha256(copied.read_bytes()).hexdigest()})
                if stop == "abrupt" and not any(f["name"] == "db.wal" and f["bytes"] > 0
                                               for f in record["pre_replay_files"]):
                    raise RuntimeError("no nonempty WAL to replay")
                reader = subprocess.run([sys.executable, "-c", READER, str(database), str(extension),
                    "rebuild" if args.verify_local_rebuild else "read"], capture_output=True, text=True, timeout=30)
                (case / "reader.stdout").write_text(reader.stdout)
                (case / "reader.stderr").write_text(reader.stderr)
                record["reader_exit"] = reader.returncode
                record.update(json.loads(reader.stdout))
                expected_chunks = [["sentinel", "frozen recovery sentinel"]]
                if seed == "seeded":
                    expected_chunks.append(["seed", "initial unrelated record"])
                # 신규 행뿐 아니라 기존 seed도 그대로 남아야 데이터 보존이다.
                record["data_preserved"] = sorted(record.get("chunks", [])) == sorted(expected_chunks)
                record["search_preserved"] = any(r[0] == "sentinel" for r in record.get("after", []))
                record["passed"] = (reader.returncode == 0 and record["data_preserved"]
                    and record["search_preserved"] and any(r[0] == "sentinel" for r in record.get("before", [])))
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                # timeout도 실패다. 원래 stdout/stderr를 버리지 않는다.
                if isinstance(exc, subprocess.TimeoutExpired):
                    for name, data in (("timeout.stdout", exc.stdout), ("timeout.stderr", exc.stderr)):
                        (case / name).write_bytes(data if isinstance(data, bytes) else (data or "").encode())
            (case / "result.json").write_text(json.dumps(record, indent=2) + "\n")
            cases.append(record)
    receipt = {"schema": "vinc.fts-wal-replay-probe.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
               "extension_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
               "cases": cases, "passed": len(cases) == 4 and all(c["passed"] for c in cases),
               "limits": ["synthetic local engine experiment, not a frozen/native/SMB acceptance",
                          "operating engine pin and durable ACK unchanged",
                          "optional rebuild applies only to newly created synthetic stores"]}
    (root / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
