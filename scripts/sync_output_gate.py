"""허브의 역할 출력 게이트를 SDK 원본에서 생성하거나(기본) 신선한지 검사한다(--check).

원본은 integrations/client/python/src/vinc_client/output_gate.py 하나다.
vinc_mcp/role_output_gate.py 는 그 바이트 앞에 생성 머리말만 붙인 사본이고, 허브 이미지와
데스크톱 사이드카는 SDK 를 설치하지 않으므로 사본은 리포에 커밋된 채로 남는다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "integrations" / "client" / "python" / "src" / "vinc_client" / "output_gate.py"
TARGET = ROOT / "vinc_mcp" / "role_output_gate.py"
HEADER = (
    "# 생성 파일이다. 손으로 고치지 말 것.\n"
    "# source: integrations/client/python/src/vinc_client/output_gate.py\n"
    "# regenerate: python -X utf8 scripts/sync_output_gate.py\n"
).encode("utf-8")


def expected() -> bytes:
    return HEADER + SOURCE.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    want = expected()
    if args.check:
        if not TARGET.exists() or TARGET.read_bytes() != want:
            print("vinc_mcp/role_output_gate.py is stale: run "
                  "python -X utf8 scripts/sync_output_gate.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_bytes(want)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
