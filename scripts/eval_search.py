#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""옛 진입점. 검색 평가는 `rag_eval/search.py` 로 옮겼다(VIN-245) — `python -m rag_eval.search`.

한 판 동안만 남기는 얇은 래퍼다. 인자는 그대로 넘긴다.
근거: [[decision:the-eval-harness-lives-in-top-level-rag-eval-not-inside-vinc-rag-2026-10-01]]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_eval.search import *  # noqa: E402,F401,F403  (옛 import 경로를 쓰던 호출부용)
from rag_eval.search import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
