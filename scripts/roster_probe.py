# -*- coding: utf-8 -*-
"""roster_probe.py — 이 세션에서 역할 로스터가 **보이는가**를 답한다(R9 의 절반).

정본과 이유: [[d4ab94561078498e3]] §7.4 R9, 그리고
[[record:agent-files-are-found-by-walking-up-from-cwd]].

하위 에이전트 정의는 **cwd 에서 위로 걸어 올라가며** 찾는다. 로스터는
`Vinc/.claude/agents` 에 있고 이 워크스페이스는 그 부모에서 열린다. 그래서 루트에서
연 세션에는 열여섯 역할이 통째로 없는데, 그 사실을 알려 주는 것이 아무 데도 없었다.

한 번 더 나쁘게 굴러간 방식이 있다: 잘못된 디렉터리에서 `ls` 한 결과를 보고 "로스터가
아예 없다"고 단정했다(§6.4 항목 3). 그래서 이 도구는 **개수만 세지 않는다** — 어디서
시작해 어디까지 걸었고 어디서 찾았는지를 같이 말한다. 빈손일 때 "없다"와 "여기서는
안 보인다"를 가르는 것이 요점이다.

    python -X utf8 scripts/roster_probe.py          # cwd 기준
    python -X utf8 scripts/roster_probe.py <경로>   # 그 디렉터리에서 연 것처럼

보이면 종료코드 0, 안 보이면 1. 하네스가 세션 첫 줄에 이걸 스스로 찍는 것이 R9 의
나머지 절반이고, 그건 이 리포가 못 낸다.
"""
from __future__ import annotations

import sys
from pathlib import Path


def walk_up(start: Path) -> tuple[Path | None, list[Path]]:
    """``start`` 에서 위로 걸으며 첫 `.claude/agents` 를 찾는다 → (찾은 곳, 걸은 경로).

    걸은 경로를 함께 돌려주는 이유가 이 함수의 존재 이유다: 못 찾았을 때 "어디를
    보고 못 찾았나"가 있어야 결론이 "없다"로 굳지 않는다."""
    walked: list[Path] = []
    cur = start.resolve()
    for d in [cur, *cur.parents]:
        walked.append(d)
        cand = d / ".claude" / "agents"
        if cand.is_dir():
            return cand, walked
    return None, walked


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    start = Path(args[0]) if args else Path.cwd()
    if not start.is_dir():
        print(f"[roster] 디렉터리가 아니다: {start}")
        return 2
    found, walked = walk_up(start)
    print(f"[roster] cwd: {start.resolve()}")
    if found is None:
        print(f"[roster] 못 찾았다 — {len(walked)}개 디렉터리를 걸었고 "
              f"{walked[-1]} 에서 멈췄다")
        print("[roster] 이것은 '로스터가 없다'가 아니라 '여기서는 안 보인다'다. "
              "Vinc 안에서 세션을 열면 보인다.")
        return 1
    shells = sorted(p.name for p in found.glob("*.md"))
    print(f"[roster] 찾았다: {found} — 껍데기 {len(shells)}개")
    print("[roster] " + ", ".join(s[:-3] for s in shells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
