# -*- coding: utf-8 -*-
"""원격 그래프에서 인용 매니페스트를 갱신하는 기존 스크립트 진입점."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api import refs  # noqa: E402

MANIFEST = refs.MANIFEST
remote = refs.remote
scan_repo = refs.scan_repo
fetch_docs = refs.fetch_docs
verify_nodes = refs.verify_nodes
_citable = refs._citable
_TRIES = refs.TRIES
_MISS_CEILING = refs.MISS_CEILING


def check() -> int:
    return refs.live_check(scan=scan_repo, fetch=fetch_docs, verify=verify_nodes)


def main() -> int:
    if "--check" in sys.argv[1:]:
        return check()
    return refs.refresh(manifest=MANIFEST, root=ROOT,
                        scan=scan_repo, fetch=fetch_docs, verify=verify_nodes)


if __name__ == "__main__":
    sys.exit(main())
