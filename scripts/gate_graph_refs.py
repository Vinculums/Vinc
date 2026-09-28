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
    return refs.offline_check(manifest=MANIFEST, roots=[ROOT],
                              scan=scan_repo, load=load_manifest)


if __name__ == "__main__":
    sys.exit(main())
