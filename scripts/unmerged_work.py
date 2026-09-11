#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""태그 밖에 무엇이 있는가 — `main` 에 없는 브랜치의 일을 센다.

    python scripts/unmerged_work.py            # 목록을 낸다(언제나 exit 0)
    python scripts/unmerged_work.py --markdown # GITHUB_STEP_SUMMARY 용

🔴 **이 파일이 존재하는 이유는 2026-09-11 이다.** v0.9.1 을 `491d92e`(09-07)에 찍었는데,
셸 크롬 개편과 두 층 LNB 가 `design/shell-depth-and-layer-lnb` 에 **30커밋**으로 있었고
머지도 푸시도 되지 않았다(작업 시작 09-08). 배포 가능 판정이 `main` 에 있는 것만 셌으므로
그 30커밋은 **원리적으로 보이지 않았다.** 사용자는 그것이 릴리스에 들어간 줄 알았고,
설치본에는 한 글자도 들어가지 않았다.

**막는 검사가 아니다.** 실험 브랜치가 태그를 볼모로 잡으면 안 되므로 언제나 exit 0 이고,
하는 일은 **사람 앞에 목록을 놓는 것**뿐이다. 판단은 사람이 한다.

## 스쿼시 머지의 잔해를 걸러야 쓸모가 있다

이 리포는 스쿼시 머지를 쓴다. 그러면 머지된 브랜치의 커밋들은 `main` 에 **같은 sha 로도
같은 patch-id 로도** 남지 않으므로 `git branch --merged` 도 `git cherry` 도 그것을
"안 들어갔다"고 답한다. 실측 2026-09-11: 그 필터가 없으면 목록이 **43줄**이고 그중 진짜는
일곱이다. 40줄짜리 경고는 아무도 안 읽는다 — 그래서 안 거르는 것은 거짓 목록보다 나쁘다.

거르는 방법은 **제목 대조**다. 스쿼시 머지는 PR 제목을 커밋 제목으로 남기고 그 제목은
브랜치의 어느 커밋 제목과 같기 마련이다(관례상 마지막 것). 끝의 `(#123)` 을 떼고 맞춘다.
완벽하지 않다 — 제목을 갈아엎고 머지하면 못 거른다. 그때는 목록에 한 줄 더 나올 뿐이고,
**놓치는 쪽이 아니라 더 세는 쪽으로 틀린다.** 이 도구에서는 그 방향이 맞다.

## 얕은 체크아웃에서는 아무것도 못 본다

CI 의 기본 체크아웃은 `fetch-depth: 1` 이고 원격 브랜치 ref 가 없다. 그 상태에서 이
스크립트는 "없다"가 아니라 **"못 봤다"**고 말해야 한다. 조용한 빈 목록이 곧 2026-09-11 의
고장이기 때문이다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

#: `main` 의 어디까지 훑어 제목을 모을 것인가. 400커밋이면 이 리포의 두 달이다.
#: 그보다 오래된 브랜치는 어차피 릴리스 판정 대상이 아니라 목록에 나오는 편이 낫다.
MAIN_DEPTH = 400

#: 끝의 PR 번호. 스쿼시 머지가 붙이고 브랜치 쪽 원본에는 없다.
_PR_SUFFIX = re.compile(r"\s*\(#\d+\)\s*$")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def norm(subject: str) -> str:
    return _PR_SUFFIX.sub("", subject.strip())


def base_ref() -> str:
    """`origin/main` 이 있으면 그것, 없으면 `main`. 워크트리와 CI 양쪽에서 돈다."""
    return "origin/main" if _git("rev-parse", "--verify", "-q", "origin/main").strip() else "main"


#: 목록에 찍는 최대 줄 수. 넘으면 "그 밖 N개"로 접는다. 40줄짜리 경고는 아무도 안 읽고,
#: 판정에 쓰이는 것은 위쪽 몇 줄이다(실측 2026-09-11: 진짜는 일곱, 목록은 43줄이었다).
TOP = 12


def _refs(pattern: str) -> dict[str, str]:
    out = {}
    for line in _git("for-each-ref", "--format=%(refname:short)%09%(objectname)", pattern).split("\n"):
        if "\t" in line:
            name, sha = (x.strip() for x in line.split("\t", 1))
            if name:
                out[name] = sha
    return out


def branches() -> list[tuple[str, str]]:
    """볼 것들 — `(git 에 물어볼 ref, 사람에게 보일 이름)`.

    로컬과 `origin/<같은 이름>` 이 **같은 커밋이면 한 줄로 접는다.** 접지 않으면 목록이
    두 배가 되고(실측 2026-09-11: 43 → 78) 그 길이 자체가 이 도구를 못 읽게 만든다.

    다르면 둘 다 낸다. 로컬이 앞서 있다는 것 자체가 정보이기 때문이다 — 그날 문제의
    브랜치는 **푸시조차 안 돼 있었고**, 그것이 릴리스에서 빠진 이유의 절반이었다."""
    local = {n: s for n, s in _refs("refs/heads/").items() if n != "main"}
    remote = {n: s for n, s in _refs("refs/remotes/origin/").items()
              if n not in ("origin/main", "origin/HEAD")}
    out: list[tuple[str, str]] = []
    paired = set()
    for rname, rsha in remote.items():
        short = rname[len("origin/"):]
        if local.get(short) == rsha:
            out.append((short, short))
            paired.add(short)
        else:
            out.append((rname, rname))
    for lname in local:
        if lname in paired:
            continue
        out.append((lname, lname + ("" if f"origin/{lname}" in remote else "  ← 푸시 안 됨")))
    return out


def survey() -> tuple[list[dict], bool]:
    """`(행 목록, 볼 수 있었나)`. 행은 미반영 커밋이 하나 이상인 브랜치만."""
    base = base_ref()
    known = {norm(s) for s in _git("log", "--format=%s", f"-{MAIN_DEPTH}", base).split("\n") if s.strip()}
    # 원격 ref 가 하나도 없으면 얕은 체크아웃이다 — "없다"고 답하면 안 되는 상태.
    saw_remote = bool(_refs("refs/remotes/origin/"))
    rows = []
    for ref, label in branches():
        subs = [norm(s) for s in _git("log", "--format=%s", f"{base}..{ref}").split("\n") if s.strip()]
        if not subs:
            continue
        new = [s for s in subs if s not in known]
        if not new:
            continue
        rows.append({
            "branch": label,
            "new": len(new),
            "total": len(subs),
            "last": _git("log", "-1", "--format=%cs", ref).strip(),
            "head": new[0],
        })
    rows.sort(key=lambda r: (-r["new"], r["branch"]))
    return rows, saw_remote


def render(rows: list[dict], saw_remote: bool, markdown: bool) -> str:
    if not saw_remote:
        warn = ("원격 브랜치 ref 가 하나도 없다 — 얕은 체크아웃이면 이 목록은 **비어 있는 것이 "
                "아니라 못 본 것**이다. `fetch-depth: 0` 으로 받을 것.")
        return (f"> ⚠ {warn}\n" if markdown else f"[unmerged_work] ⚠ {warn}\n")
    if not rows:
        return ("태그 밖에 남은 일이 없다.\n" if markdown
                else "[unmerged_work] 태그 밖에 남은 일이 없다.\n")
    # 위에서부터 TOP 줄만 낸다. 미반영 커밋이 많은 순이라 판정에 쓰이는 것이 위에 온다.
    shown, rest = rows[:TOP], rows[TOP:]
    tail = (f"그 밖 {len(rest)}개는 미반영 커밋이 {rest[0]['new']}건 이하다"
            if rest else "")
    if markdown:
        out = [f"### 태그 밖에 있는 일 ({len(rows)}개)", "",
               "| 브랜치 | 미반영 | 전체 | 마지막 | 첫 미반영 커밋 |", "|---|---:|---:|---|---|"]
        out += [f"| `{r['branch']}` | **{r['new']}** | {r['total']} | {r['last']} | {r['head'][:70]} |"
                for r in shown]
        out += ["", tail] if tail else []
        out += ["", "스쿼시 머지의 잔해는 제목 대조로 걸렀다. 판단은 사람이 한다."]
        return "\n".join(out) + "\n"
    out = [f"[unmerged_work] `main` 에 없는 일을 가진 브랜치 {len(rows)}개",
           f"{'미반영':>6} {'전체':>5}  브랜치 (마지막)"]
    for r in shown:
        out.append(f"{r['new']:>6} {r['total']:>5}  {r['branch']}  ({r['last']})")
        out.append(f"{'':>13}↳ {r['head'][:74]}")
    if tail:
        out.append(f"  … {tail}")
    out.append("  스쿼시 머지의 잔해는 제목 대조로 걸렀다. 이 목록은 막지 않는다 — 판단은 사람이 한다.")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    rows, saw_remote = survey()
    text = render(rows, saw_remote, "--markdown" in argv)
    print(text, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and "--markdown" in argv:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0            # 언제나 0. 이 도구는 보여 줄 뿐 막지 않는다.


if __name__ == "__main__":
    sys.exit(main(sys.argv))
