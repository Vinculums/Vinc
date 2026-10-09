"""가중치·추론 없이 채움 산출물의 해시와 모든 판단·집계를 다시 계산한다."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from .fill_curve import completed, eligible, sha, summarize
from .run_gold import load_calib, read_jsonl


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError(f"artifact hash mismatch: {name}")
    frozen = json.loads((root / "measurement/frozen.json").read_text(encoding="utf-8"))
    candidates = root / "candidates.jsonl"
    if sha(candidates) != frozen["candidates_sha256"]:
        raise ValueError("candidate file differs from the inference pin")
    for name, expected in frozen["calibrations"].items():
        if sha(root / "calib" / name) != expected:
            raise ValueError("calibration differs from the inference pin")
    pairs = read_jsonl(candidates)
    rows, _ = completed(root / "measurement/raw.jsonl", {p["pair_id"] for p in pairs if eligible(p)})
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        summary = summarize(pairs, rows, load_calib(root / "calib"), out,
                            run_id=frozen["run_id"], model_sha=frozen["model_sha256"], day=frozen["day"])
        for name in ("summary.json", "outcomes.jsonl", "beliefs.jsonl", "abstained.jsonl"):
            if (out / name).read_bytes() != (root / "measurement" / name).read_bytes():
                raise ValueError(f"recomputed output differs: {name}")
    return {"verified_files": len(manifest["files"]), "candidate_pairs": len(pairs),
            "inference_directions": len(rows), "table_rows": len(summary["table"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()
    print(json.dumps(verify(args.run_dir)))


if __name__ == "__main__":
    main()
