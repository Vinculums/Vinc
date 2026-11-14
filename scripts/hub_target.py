"""허브 앱과 계정 앱 이름을 대상 설정 한 줄(.github/container-apps-target.env 의 CAE)에서 푼다.

환경에서 앱으로 가는 표는 hub-deploy 의 plan 잡 "배포 대상" 스텝(id: target) 하나에만 있다. 여기서는 그
스텝의 case 줄(`<환경>) app=<허브 앱> account=<계정 앱> ;;`)을 읽기만 한다. 둘째 표를 두지 않는다.
표준 라이브러리만 쓴다: 운영 워크플로가 setup-python 없이 러너의 python3 로 부른다.

    python3 scripts/hub_target.py          # HUB_APP=<허브 앱> 한 줄. 워크플로는 이 줄을 "$GITHUB_ENV" 에 붙인다.
    python3 scripts/hub_target.py account  # ACCOUNT_APP=<계정 앱> 한 줄.

출력은 언제나 한 줄이다. 워크플로가 `${line#HUB_APP=}` 로 잘라 쓰므로 두 앱을 한 번에 내지 않는다.
CAE 줄이 하나가 아니거나 표에 없는 값이면 멈춘다: 다른 앱을 바꾸거나 읽으면 안 된다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTING = ".github/container-apps-target.env"
DEPLOY = ".github/workflows/hub-deploy.yml"
# 역할 → 워크플로 환경변수 이름. 표의 열 순서(app=, account=)와 같다.
ROLES = {"hub": "HUB_APP", "account": "ACCOUNT_APP"}
_LABELS = {"hub": "허브 앱", "account": "계정 앱"}
_ROW = re.compile(r"\s*([a-z0-9-]+)\) app=([a-z0-9-]+) account=([a-z0-9-]+) ;;")
# 표의 줄처럼 생긴 줄(`<환경>) app=`)은 모두 위 꼴이어야 한다. 계정 앱이 빠진 줄을 조용히 건너뛰지 않는다.
_ROW_LIKE = re.compile(r"\s*[a-z0-9-]+\) app=")


def target_table(root: Path = ROOT) -> dict[str, dict[str, str]]:
    """hub-deploy 의 `id: target` 스텝 본문에서 환경→{hub, account} 줄을 모은다. 스텝은 정확히 하나여야 한다."""
    lines = (root / DEPLOY).read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if re.fullmatch(r"\s*- id: target\s*", line)]
    if len(starts) != 1:
        raise ValueError("hub-deploy must hold exactly one target step")
    indent = len(lines[starts[0]]) - len(lines[starts[0]].lstrip())
    table: dict[str, dict[str, str]] = {}
    for line in lines[starts[0] + 1:]:
        body = line.lstrip()
        # 같은 들여쓰기나 그보다 얕은 줄(다음 스텝, 그 주석, 다음 잡)에서 스텝이 끝난다.
        if body and len(line) - len(body) <= indent:
            break
        row = _ROW.fullmatch(line)
        if row:
            table[row.group(1)] = {"hub": row.group(2), "account": row.group(3)}
        elif _ROW_LIKE.match(line):
            raise ValueError("every target row must name the hub app and the account app")
    if not table:
        raise ValueError("the hub-deploy target step holds no environment table")
    return table


def hub_table(root: Path = ROOT) -> dict[str, str]:
    """환경→허브 앱. target_table 의 허브 열이다."""
    return {cae: apps["hub"] for cae, apps in target_table(root).items()}


def target_app(role: str, root: Path = ROOT) -> str:
    """대상 설정의 CAE 를 hub-deploy 의 표로 풀어 그 역할(hub, account)의 앱 이름을 돌려준다."""
    if role not in ROLES:
        raise ValueError("unknown role")
    values = re.findall(r"(?m)^CAE=(\S+)$", (root / SETTING).read_text(encoding="utf-8"))
    if len(values) != 1:
        raise ValueError("the target setting must hold exactly one CAE line")
    table = target_table(root)
    if values[0] not in table:
        raise ValueError("unknown target environment")
    return table[values[0]][role]


def target_hub_app(root: Path = ROOT) -> str:
    return target_app("hub", root)


def target_account_app(root: Path = ROOT) -> str:
    return target_app("account", root)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    role = args[0] if args else "hub"
    if len(args) > 1 or role not in ROLES:
        print("usage: python3 scripts/hub_target.py [hub|account]", file=sys.stderr)
        return 2
    try:
        app = target_app(role)
    except (OSError, ValueError) as exc:
        print(f"::error::{_LABELS[role]}을 대상 설정에서 풀지 못했다: {exc}", file=sys.stderr)
        return 1
    print(f"{ROLES[role]}={app}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
