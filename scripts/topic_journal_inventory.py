"""복원한 운영 사본 안의 토픽 이동 저널을 프로토콜 판과 상태별로 센다.

    python -X utf8 scripts/topic_journal_inventory.py <복원한 사본> [--out <보고 파일>]

한 줄 JSON 하나만 낸다: 판/상태별 개수, 전체 수, 1·2판 중 아직 안 끝난 수(`open_v1_or_v2`).
소유자·토픽·대상·실행 식별자는 읽어도 싣지 않는다. 판과 상태는 정해진 꼴일 때만 이름으로 싣고
아니면 `unknown` 으로 묶는다. 실패하면 사유 코드만 담은 한 줄을 내고 예외 원문은 내지 않는다.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

try:
    from scripts._root import ROOT  # noqa: F401  리포 루트를 sys.path 에 올린다
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401

from api import topic_conversion as conversion  # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

# 1·2판 저널은 이 두 상태에서만 이동 울타리를 풀었다(api.topic_conversion 의 _terminal_visible).
TERMINAL = ("active", "cancelled")
OLD_PROTOCOLS = (conversion.PROTOCOL_V1, conversion.PROTOCOL_V2)
KNOWN_PROTOCOLS = (*OLD_PROTOCOLS, conversion.PROTOCOL_V3)
# 상태 이름으로 싣는 꼴. 식별자(하이픈·숫자·콜론)가 섞인 값은 이름으로 싣지 않는다.
_STATUS = re.compile(r"[a-z][a-z_]{0,39}")


def _protocol(journal: dict) -> int | None:
    version = conversion.journal_protocol(journal)
    return version if type(version) is int and version in KNOWN_PROTOCOLS else None


def _status(journal: dict) -> str | None:
    status = journal.get("status")
    return status if isinstance(status, str) and _STATUS.fullmatch(status) else None


def summarize(rows: list[dict]) -> dict:
    """저널 목록을 식별자 없는 개수로 줄인다."""
    by_key: Counter = Counter()
    open_old = 0
    for journal in rows:
        protocol, status = _protocol(journal), _status(journal)
        name = "unknown" if protocol is None else f"v{protocol}"
        by_key[f"{name}/{status or 'unknown'}"] += 1
        # 종결 상태가 아니면 아직 열려 있다. 알 수 없는 상태도 닫혔다고 세지 않는다.
        if protocol in OLD_PROTOCOLS and status not in TERMINAL:
            open_old += 1
    return {"by_protocol_status": dict(sorted(by_key.items())),
            "open_v1_or_v2": open_old, "total": len(rows)}


def inventory(path: Path) -> dict:
    """사본 파일 하나를 열어 저널을 센다. 쓰지 않고, 스토어는 반드시 닫는다."""
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError("invalid copy")
    store = open_store(load_config({"data_dir": str(path.resolve())}))
    if store is None:
        raise ValueError("store unavailable")
    try:
        return summarize(conversion.journals(store))
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("copy", type=Path)
    parser.add_argument("--out", type=Path, help="보고 한 줄을 이 파일에 쓴다(없으면 표준 출력)")
    args = parser.parse_args(argv)
    try:
        report, code = inventory(args.copy), 0
    except Exception:
        # 예외 원문에는 경로나 행이 들어갈 수 있으므로 내지 않는다.
        report, code = {"error": "inventory_failed"}, 1
    line = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.out:
        args.out.write_text(line + "\n", encoding="utf-8")
    else:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
