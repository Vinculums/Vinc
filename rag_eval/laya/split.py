# -*- coding: utf-8 -*-
"""층화 분할: (lang, source, 양성 패턴) 층마다 섞어 번갈아 calib/eval. 근거: 테스트 방식 §4-1"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from .pins import PREDICATES, SEED


def stratum(pair: dict) -> str:
    labels = pair.get("labels") or {}
    pos = "".join("1" if labels.get(p) == 1 else "0" if labels.get(p) == 0 else "?" for p in PREDICATES)
    return f"{pair.get('lang', '?')}|{pair.get('source', '?')}|{pos}"


def make_split(pairs: list[dict], seed: int = SEED) -> dict[str, str]:
    rng = random.Random(seed)
    groups: dict[str, list[str]] = defaultdict(list)
    for p in pairs:
        groups[stratum(p)].append(p["pair_id"])
    out: dict[str, str] = {}
    flip = 0
    for key in sorted(groups):
        ids = groups[key]
        rng.shuffle(ids)
        for i, pid in enumerate(ids):
            out[pid] = "calib" if (i + flip) % 2 == 0 else "eval"
        flip ^= len(ids) % 2  # 홀수 층의 치우침을 다음 층에서 되돌린다
    return out


def split_path(gold_path: Path) -> Path:
    return gold_path.with_name(gold_path.stem + ".split.json")


def load_or_make(gold_path: Path, pairs: list[dict]) -> dict[str, str]:
    sp = split_path(gold_path)
    if sp.exists():
        data = json.loads(sp.read_text(encoding="utf-8"))
        return data.get("split", data)
    split = make_split(pairs)
    sp.write_text(json.dumps({"seed": SEED, "split": split}, ensure_ascii=False, indent=1), encoding="utf-8")
    return split
