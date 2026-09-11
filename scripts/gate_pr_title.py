#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR 제목 계약 — 타입 접두 · 영어 · 대시 금지.

    PR_TITLE="fix(share): unchecked documents stay behind" python scripts/gate_pr_title.py
    python scripts/gate_pr_title.py "feat(search): say when the meaning arm is missing"

exit 0=통과 · 1=위반 · 2=제목을 못 받음.

**스쿼시 머지에서 PR 제목이 곧 main 의 커밋 제목**이다. 그래서 이 게이트가 지키는 것은
PR 화면이 아니라 `git log` 다 — 히스토리는 되돌릴 수 없고, 릴리스 노트를 쓸 때 그 목록을
사람이 훑는다.

실측 2026-09-11(최근 200 커밋): 접두가 붙은 것이 162건(fix 56 · feat 51 · ci 23 · chore 8 ·
design 6 · docs 5 · release 4 · test 3 · infra 3 · agents 2)이고 **접두가 아예 없는 것이
38건**이다. 즉 관례는 이미 있고 지켜지지 않았을 뿐이다. 지켜지지 않는 관례는 관례가 아니라
취향이므로, 여기서 계약으로 바꾼다.

영어를 요구하는 이유는 취향이 아니다. 이 리포의 제목은 한국어와 영어가 섞여 있어
`git log --oneline` 이 한 화면에서 두 언어로 읽히고, 릴리스 노트·CHANGELOG·PR 목록을
훑는 도구(그리고 사람)가 같은 변경을 다른 언어로 두 번 만난다. 본문과 코드 주석은
여전히 한국어다 — 이 게이트는 **제목 한 줄**만 본다.
"""
from __future__ import annotations

import os
import re
import sys

#: 허용 타입. 앞의 열둘은 이 리포가 실제로 써 온 것이고(위 실측), 뒤의 넷은 표준
#: Conventional Commits 에 있는데 아직 안 쓴 것이다. 소문자로 고정한다 — 커밋 수백 건이
#: 이미 소문자라 대문자로 바꾸면 히스토리가 두 표기로 갈리고 얻는 것이 없다.
TYPES = (
    "feat", "fix", "ci", "chore", "design", "docs", "release", "test", "infra",
    "agents", "refactor", "security",
    "perf", "build", "style", "revert",
)

#: `type(scope)!: subject`. scope 와 `!` 는 선택. 콜론 뒤 공백 하나는 필수다.
SHAPE = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")"
    r"(?:\((?P<scope>[a-z0-9._/-]+)\))?"
    r"(?P<breaking>!)?"
    r": (?P<subject>\S.*)$"
)

#: 화면 텍스트의 em-dash 금지를 제목까지 넓힌다. 제목은 좁은 목록에서 읽히고,
#: 대시는 폭이 넓어 `--oneline` 에서 줄을 밀어낸다. 대체는 콜론·괄호·새 문장.
DASHES = {"—": "em-dash", "–": "en-dash", "―": "horizontal bar"}

MAX_LEN = 100


def check(title: str) -> list[str]:
    """위반 목록. 빈 목록이면 통과."""
    bad: list[str] = []
    t = title.strip()
    if not t:
        return ["제목이 비었다"]

    for ch, name in DASHES.items():
        if ch in t:
            bad.append(f"{name}({ch}) 를 쓰지 않는다. 콜론·괄호·새 문장으로 바꿀 것")

    non_ascii = sorted({c for c in t if ord(c) > 127} - set(DASHES))
    if non_ascii:
        shown = " ".join(non_ascii[:8]) + ("…" if len(non_ascii) > 8 else "")
        bad.append(f"제목은 영어(ASCII)로 쓴다. 섞인 문자: {shown}")

    m = SHAPE.match(t)
    if not m:
        bad.append("`type(scope): subject` 모양이 아니다. "
                   f"type 은 {' · '.join(TYPES[:12])} 중 하나이고 콜론 뒤에 공백 하나다")
    else:
        subject = m.group("subject")
        if subject.endswith("."):
            bad.append("subject 는 마침표로 끝내지 않는다")
        if subject[:1].isupper() and not subject.split()[0].isupper():
            # 첫 낱말이 약어(MCP·CI)면 그대로 둔다. 문장처럼 대문자로 시작한 것만 잡는다.
            bad.append(f"subject 는 소문자로 시작한다: {subject.split()[0]!r}")

    if len(t) > MAX_LEN:
        bad.append(f"제목이 {len(t)}자다. {MAX_LEN}자 안으로 줄일 것 "
                   "(자세한 것은 본문에 쓴다 — 본문은 한국어여도 된다)")
    return bad


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    title = argv[1] if len(argv) > 1 else (os.environ.get("PR_TITLE") or "")
    if not title.strip():
        print("[gate_pr_title] 제목을 못 받았다 — 인자나 PR_TITLE 로 넘길 것",
              file=sys.stderr)
        return 2

    bad = check(title)
    if not bad:
        print(f"[gate_pr_title] PASS — {title}")
        return 0

    print(f"[gate_pr_title] FAIL — {title}", file=sys.stderr)
    for b in bad:
        print(f"  · {b}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  보기: fix(share): unchecked documents stay behind", file=sys.stderr)
    print("        feat(search): say when the meaning arm is missing", file=sys.stderr)
    print("        ci(release): pin the interpreter the container can run", file=sys.stderr)
    print("  제목만 고치면 된다. 이 게이트는 본문을 보지 않는다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
