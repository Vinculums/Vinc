# -*- coding: utf-8 -*-
"""커밋된 매니페스트와 코드 인용을 대조하는 CI 진입점."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api import refs  # noqa: E402

MANIFEST = refs.MANIFEST
normalize = refs.normalize
is_citation = refs.is_citation
scan_file = refs.scan_file
scan_repo = refs.scan_repo
load_manifest = refs.load_manifest


def main() -> int:
    # scan 을 주지 않는다 — 기본 경로(_walk)가 스캔과 함께 '스캔 안 된 확장자의 인용'을
    # 모아 NOTE 로 알린다(조용한 통과 방지). scan_repo 를 주면 그 보고가 꺼진다.
    return refs.offline_check(manifest=MANIFEST, roots=[ROOT], load=load_manifest)


if __name__ == "__main__":
    sys.exit(main())
