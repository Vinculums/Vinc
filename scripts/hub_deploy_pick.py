# -*- coding: utf-8 -*-
"""허브 배포가 **무엇을** 내보낼지 고른다. 안 내보낸다는 판정도 여기서 한다.

hub-deploy 는 main 의 CI(ci.yml)가 끝날 때마다 불린다(`workflow_run`). 그때 내보내는
것은 불러낸 커밋이 아니라 **main 에서 CI 가 초록인 가장 새 커밋**이다. 불러낸 커밋을
그대로 내보내면 두 가지가 깨진다:

- CI 는 커밋마다 따로 돌아서(ci.yml concurrency) **끝나는 순서가 머지 순서가 아니다.**
  러스트 잡이 붙은 옛 커밋이 새 커밋보다 늦게 끝나면 허브가 뒤로 간다.
- hub-deploy 는 직렬화 그룹이라 GitHub 이 **대기 중인 런을 하나만** 남긴다. 늦게 온
  런이 먼저 기다리던 런을 지우므로, 불러낸 커밋만 보면 지워진 쪽이 영영 안 나간다.

"가장 새 초록"을 고르면 둘 다 사라진다. 어느 런이 살아남든 같은 답을 낸다.

규칙(위에서부터):

1. `workflow_dispatch` 는 사람이 고른 커밋을 내보낸다. 단 그 커밋의 CI 가 초록이어야
   한다. 배포 안의 테스트 게이트를 걷었으므로 CI 가 유일한 게이트이고, 손으로 누른
   배포도 그것을 건너뛰지 않는다. 경로·순서 검사는 안 한다(같은 커밋 재배포가 이 길의
   용도다).
2. `workflow_run` 은 main 의 first-parent 를 새것부터 훑어 **지금 허브에 떠 있는 커밋
   앞에서** 멈추고, 그 사이에서 CI 가 초록인 가장 새 커밋을 고른다.
3. 고른 커밋이 이미 허브에 들어 있거나(조상), 떠 있는 커밋과 비교해 허브 경로가
   하나도 안 바뀌었으면 내보내지 않는다. 옛 `on.push.paths` 가 하던 일이다.
4. 불러낸 CI 가 **빨갛고** 그 커밋 자신이 허브 경로를 건드렸으면 `withheld` 로 표시한다.
   배포를 막은 것은 CI 인데 그것이 조용하면 허브가 옛 이미지에 남은 것을 아무도 모른다
   (2026-09-18 에 하루를 그렇게 잃었다). 누적 diff 가 아니라 **그 커밋만** 보는 이유는
   고칠 때까지 main push 마다 같은 이슈가 새로 열리지 않게 하려는 것이다.

실패 방향: API·az·git 이 답을 못 하면 **빨갛게 죽는다**(exit 1 → report 잡이 이슈를
연다). 못 읽은 것을 "바뀐 것 없음"으로 읽으면 배포가 조용히 사라진다.

로컬에서 판정만 보려면(쓰기 없음, `az` 로그인과 `GH_TOKEN` 필요):
    EVENT=workflow_run TRIGGER_SHA=<sha> TRIGGER_CONCLUSION=success \\
      python -X utf8 scripts/hub_deploy_pick.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

#: 허브 이미지에 들어가거나 배포 절차를 바꾸는 경로. 옛 `on.push.paths` 를 옮겼다.
#: `/` 로 끝나면 디렉터리 접두, 아니면 정확한 파일이다.
HUB_PATHS = (
    "api/",
    "store/",
    "engines/",
    "vinc_mcp/",
    "packaging/lbug_extensions/",
    "Dockerfile.hub",
    "packaging/docker-entrypoint.sh",
    "packaging/embed_worker.py",
    "packaging/bake_embed_model.py",
    "pyproject.toml",
    "scripts/check_hub_env.py",
    ".github/workflows/hub-deploy.yml",
)

#: first-parent 를 몇 개까지 훑나. 떠 있는 커밋이 이보다 멀면 창 전체를 본다.
WALK = 100

_SHA = re.compile(r"^[0-9a-f]{40}$")


class Unanswered(RuntimeError):
    """바깥(API·az·git)이 답을 못 했다. 판정하지 않고 죽는다."""


def touches_hub(paths: list[str]) -> bool:
    return any(p.startswith(h) if h.endswith("/") else p == h
               for p in paths for h in HUB_PATHS)


@dataclass
class Plan:
    deploy: bool = False
    sha: str = ""
    withheld: str = ""
    why: list[str] = field(default_factory=list)


def pick(*, event: str, trigger_sha: str, trigger_conclusion: str, dispatch_sha: str,
         live: str, main_commits: list[str],
         ci_status: Callable[[str], str],
         is_commit: Callable[[str], bool],
         is_ancestor: Callable[[str, str], bool],
         changed: Callable[[str, str], list[str]],
         parent_of: Callable[[str], str]) -> Plan:
    """판정. 바깥은 전부 인자로 받는다(테스트가 가짜를 꽂는다).

    `ci_status(sha)` 는 success · pending · failure · none 중 하나다.
    `changed(a, b)` 는 `git diff --name-only a b`, `parent_of(sha)` 는 first-parent.
    """
    plan = Plan()
    say = plan.why.append

    if event == "workflow_dispatch":
        st = ci_status(dispatch_sha)
        if st != "success":
            raise Unanswered(
                f"손으로 부른 배포지만 {dispatch_sha[:7]} 의 CI 가 초록이 아니다({st}). "
                "ci 를 그 커밋에서 다시 돌리고 초록이 된 뒤 부를 것")
        say(f"workflow_dispatch: {dispatch_sha[:7]} 를 그대로 내보낸다(CI 초록)")
        plan.deploy, plan.sha = True, dispatch_sha
        return plan

    if event != "workflow_run":
        raise Unanswered(f"모르는 이벤트: {event!r}")

    # 4. 불러낸 CI 가 빨갛고 그 커밋 자신이 허브를 건드렸으면 알린다(배포 여부와 무관).
    if trigger_conclusion != "success" and trigger_sha:
        parent = parent_of(trigger_sha)
        if parent and touches_hub(changed(parent, trigger_sha)):
            plan.withheld = trigger_sha
            say(f"불러낸 CI 가 {trigger_conclusion}: {trigger_sha[:7]} 의 허브 변경은 "
                "CI 가 초록이 될 때까지 안 나간다")

    live_known = bool(live) and is_commit(live)
    window = main_commits
    if live_known and live in main_commits:
        window = main_commits[:main_commits.index(live)]
    elif live_known:
        say(f"떠 있는 {live[:7]} 가 main 의 최근 {len(main_commits)}개 first-parent 에 없다 "
            "— 창 전체를 본다")
    else:
        say(f"떠 있는 이미지 태그를 커밋으로 못 읽었다({live!r}) — 초록이면 내보낸다")

    if not window:
        say(f"떠 있는 {live[:7]} 가 main 의 머리다 — 내보낼 것 없음")
        return plan

    target = ""
    for sha in window:
        st = ci_status(sha)
        if st == "success":
            target = sha
            break
        say(f"  {sha[:7]}: CI {st} — 건너뛴다")
    if not target:
        say("떠 있는 것보다 새 커밋 중 CI 가 초록인 것이 없다 — 내보낼 것 없음")
        return plan

    if live_known:
        if is_ancestor(target, live):
            say(f"{target[:7]} 는 이미 떠 있는 {live[:7]} 에 들어 있다 — 내보낼 것 없음")
            return plan
        hub = [p for p in changed(live, target) if touches_hub([p])]
        if not hub:
            say(f"{live[:7]}..{target[:7]} 에 허브 경로 변경이 없다 — 내보낼 것 없음")
            return plan
        say(f"{live[:7]}..{target[:7]} 허브 경로 변경 {len(hub)}건: "
            + ", ".join(hub[:8]) + (" …" if len(hub) > 8 else ""))

    say(f"내보낸다: {target[:7]} (CI 초록인 가장 새 main 커밋)")
    plan.deploy, plan.sha = True, target
    return plan


# ── 바깥 ────────────────────────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _git_out(*args: str) -> str:
    r = _git(*args)
    if r.returncode != 0:
        raise Unanswered(f"git {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout.strip()


def _live_tag() -> str:
    r = subprocess.run(
        ["az", "containerapp", "show", "--name", "vinc-hub", "--resource-group", "vincsRG",
         "--query", "properties.template.containers[0].image", "-o", "tsv"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise Unanswered(f"허브의 현재 이미지를 못 읽었다: {r.stderr.strip()}")
    tag = r.stdout.strip().rsplit(":", 1)[-1]
    return tag if _SHA.match(tag) else ""


def _ci_status_via_api(api: str, repo: str, token: str) -> Callable[[str], str]:
    memo: dict[str, str] = {}

    def status(sha: str) -> str:
        if sha in memo:
            return memo[sha]
        url = f"{api}/repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=20"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                runs = json.load(resp).get("workflow_runs") or []
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise Unanswered(f"{sha[:7]} 의 CI 상태를 못 읽었다: {e}") from e
        if any(r.get("conclusion") == "success" for r in runs):
            st = "success"
        elif any(r.get("status") != "completed" for r in runs):
            st = "pending"
        elif runs:
            st = "failure"
        else:
            st = "none"
        memo[sha] = st
        return st

    return status


def main() -> int:
    e = os.environ
    event = e.get("EVENT", "")
    try:
        if event == "workflow_run":
            _git_out("fetch", "--quiet", "origin", "main")
            commits = _git_out("rev-list", "--first-parent", f"--max-count={WALK}",
                               "origin/main").split()
            live = _live_tag()
        else:
            commits, live = [], ""
        plan = pick(
            event=event,
            trigger_sha=e.get("TRIGGER_SHA", ""),
            trigger_conclusion=e.get("TRIGGER_CONCLUSION", ""),
            dispatch_sha=e.get("DISPATCH_SHA", ""),
            live=live,
            main_commits=commits,
            ci_status=_ci_status_via_api(e.get("API", "https://api.github.com"),
                                         e.get("REPO", "Vinculums/Vinc"), e["GH_TOKEN"]),
            is_commit=lambda s: _git("cat-file", "-e", f"{s}^{{commit}}").returncode == 0,
            is_ancestor=lambda a, b: _git("merge-base", "--is-ancestor", a, b).returncode == 0,
            changed=lambda a, b: _git_out("diff", "--name-only", a, b).splitlines(),
            parent_of=lambda s: (_git("rev-parse", "--verify", "--quiet", f"{s}^").stdout
                                 .strip()),
        )
    except Unanswered as x:
        print(f"::error::배포 대상을 고르지 못했다: {x}", file=sys.stderr)
        # report 잡이 이슈 제목에 쓸 커밋. 고르지 못했으니 불러낸 것을 적는다.
        sha = e.get("TRIGGER_SHA") or e.get("DISPATCH_SHA") or ""
        _emit({"deploy": "false", "sha": sha, "withheld": ""})
        return 1

    for line in plan.why:
        print(line)
    if plan.withheld:
        print(f"::warning::{plan.withheld[:7]} 의 허브 변경이 CI 빨강으로 보류됐다 — 이슈로 남긴다")
    _emit({"deploy": "true" if plan.deploy else "false",
           "sha": plan.sha, "withheld": plan.withheld})
    return 0


def _emit(out: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in out.items())
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(lines)
    print("── 판정\n" + lines, end="")


if __name__ == "__main__":
    raise SystemExit(main())
