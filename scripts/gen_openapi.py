"""Regenerate/check the public v1 OpenAPI file from the route registry."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vinc_mcp.openapi_v1 import FILE, serialized  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = serialized()
    if args.check:
        if not FILE.exists() or FILE.read_text(encoding="utf-8") != expected:
            print("OpenAPI v1 drift: run python scripts/gen_openapi.py", file=sys.stderr)
            return 1
        return 0
    FILE.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
