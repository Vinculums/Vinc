# -*- coding: utf-8 -*-
"""합성 쌍으로 세 스크립트를 끝까지 돌린다(VIN-405 완료 조건). 골드는 쓰지 않는다. 모델은 실제로 돈다."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .pins import detect_lang
from .split import split_path

ROOT = Path(os.environ.get("LAYA_SELFTEST_OUT", "out/laya-selftest")).resolve()

PAIRS = [
    ("The reranker must wait for a second container after the rebuild.",
     "The reranker can run in the same container as the encoder right after the rebuild.", "contradict", "comention"),
    ("A warm hub answers a search in about 200 ms with the vector arm attached.",
     "After idle the encoder scales to zero and the first query takes almost five seconds.", "support", "ties"),
    ("The desktop sign-in failed because the sidecar had crashed on a bad WAL.",
     "The Korean tagline must not use the banned stem.", "unrelated", "random"),
    ("Decisions are retired with valid_until and never deleted.",
     "A decision is never deleted; it is closed by setting valid_until.", "same", "merge"),
    ("리랭커는 재구축 뒤 두 번째 컨테이너를 기다려야 한다.",
     "리랭커는 재구축 직후 인코더와 같은 컨테이너에서 돌릴 수 있다.", "contradict", "comention"),
    ("따뜻한 허브는 벡터 팔을 붙인 채 약 200ms에 검색에 답한다.",
     "유휴 뒤 인코더가 0으로 줄어 첫 질의가 거의 5초 걸린다.", "support", "ties"),
    ("데스크톱 로그인은 사이드카가 깨진 WAL로 죽어서 실패했다.",
     "한국어 태그라인에는 금지 어간을 쓰면 안 된다.", "unrelated", "random"),
    ("결정은 valid_until로 은퇴시키고 절대 지우지 않는다.",
     "결정은 절대 지우지 않고 valid_until을 찍어 닫는다.", "same", "merge"),
]
LABELS = {"contradict": {"supports": 0, "contradicts": 1, "same_as": 0},
          "support": {"supports": 1, "contradicts": 0, "same_as": 0},
          "unrelated": {"supports": 0, "contradicts": 0, "same_as": 0},
          "same": {"supports": 0, "contradicts": 0, "same_as": 1}}


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    gold = ROOT / "gold.jsonl"
    n = 0
    with gold.open("w", encoding="utf-8") as fh:
        for _rep in range(4):  # 32쌍: 층마다 둘 이상 들어가도록 반복
            for a, b, kind, src in PAIRS:
                n += 1
                fh.write(json.dumps({"pair_id": f"s{n:04d}", "a": {"id": f"synthetic:a{n}", "rev": 1, "text": a},
                                     "b": {"id": f"synthetic:b{n}", "rev": 1, "text": b},
                                     "context": None, "lang": detect_lang(a, b), "source": src,
                                     "labels": LABELS[kind], "hard_kind": None, "author": "synthetic",
                                     "labeled_at": "2026-10-07", "note": "selftest"}, ensure_ascii=False) + "\n")
    if split_path(gold).exists():
        split_path(gold).unlink()
    from . import fit_calib, report, run_gold
    assert run_gold.main(["--gold", str(gold), "--split", "calib", "--out", str(ROOT / "calib.jsonl")]) == 0
    assert fit_calib.main(["--in", str(ROOT / "calib.jsonl"), "--out", str(ROOT / "calib"), "--min-bucket-n", "4"]) == 0
    assert run_gold.main(["--gold", str(gold), "--split", "eval", "--calib", str(ROOT / "calib"),
                          "--out", str(ROOT / "eval.jsonl")]) == 0
    assert report.main(["--in", str(ROOT / "eval.jsonl"), "--gold", str(gold), "--out", str(ROOT / "report.md")]) == 0
    head = (ROOT / "eval.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("# ") and "revision" in head, "핀 머리가 없다"
    print("SELFTEST OK", ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
