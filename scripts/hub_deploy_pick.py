# -*- coding: utf-8 -*-
"""허브 배포가 **무엇을** 내보낼지 고른다. 안 내보낸다는 판정도 여기서 한다.

hub-deploy 는 main 의 CI(ci.yml)가 끝날 때마다 불린다(`workflow_run`). 그때 내보내는
것은 불러낸 커밋이 아니라 **main 에서 CI 의 core 잡(파이썬 게이트)이 초록인 가장 새 커밋**이다. 불러낸 커밋을
그대로 내보내면 두 가지가 깨진다:

- CI 는 커밋마다 따로 돌아서(ci.yml concurrency) **끝나는 순서가 머지 순서가 아니다.**
  러스트 잡이 붙은 옛 커밋이 새 커밋보다 늦게 끝나면 허브가 뒤로 간다.
- hub-deploy 는 직렬화 그룹이라 GitHub 이 **대기 중인 런을 하나만** 남긴다. 늦게 온
  런이 먼저 기다리던 런을 지우므로, 불러낸 커밋만 보면 지워진 쪽이 영영 안 나간다.

"가장 새 초록"을 고르면 둘 다 사라진다. 어느 런이 살아남든 같은 답을 낸다.

규칙(위에서부터):

1. `workflow_dispatch` 는 사람이 고른 커밋을 내보낸다. 단 그 커밋의 core 잡이 초록이어야
   한다. 배포 안의 테스트 게이트를 걷었으므로 CI 가 유일한 게이트이고, 손으로 누른
   배포도 그것을 건너뛰지 않는다. 경로·순서 검사는 안 한다(같은 커밋 재배포가 이 길의
   용도다).
2. `workflow_run` 은 main 의 first-parent 를 새것부터 훑어 **지금 허브에 떠 있는 커밋
   앞에서** 멈추고, 그 사이에서 core 잡이 초록인 가장 새 커밋을 고른다(`classify`).
3. 고른 커밋이 이미 허브에 들어 있거나(조상), 떠 있는 커밋과 비교해 허브 경로가
   하나도 안 바뀌었으면 내보내지 않는다. 옛 `on.push.paths` 가 하던 일이다.
4. 불러낸 CI 가 **빨갛고** 그 커밋 자신이 허브 경로를 건드렸으면 `withheld` 로 표시한다.
   배포를 막은 것은 CI 인데 그것이 조용하면 허브가 옛 이미지에 남은 것을 아무도 모른다
   (2026-09-18 에 하루를 그렇게 잃었다). 누적 diff 가 아니라 **그 커밋만** 보는 이유는
   고칠 때까지 main push 마다 같은 이슈가 새로 열리지 않게 하려는 것이다.

실패 방향: API·az·git 이 답을 못 하면 **빨갛게 죽는다**(exit 1 → report 잡이 이슈를
연다). 못 읽은 것을 "바뀐 것 없음"으로 읽으면 배포가 조용히 사라진다.
같은 이유로 **불러낸 커밋의 CI 는 "없음"일 수 없다**(그 런이 끝나서 이 런이 불렸다).
런 목록 API 는 끝난 런을 늦게 싣는다 — 2026-10-02 5cb87b3 은 CI 가 끝나고 22초 뒤
목록이 0건이었고 "CI 없음"으로 건너뛰어져 허브가 조용히 뒤에 남았다. 그래서 불러낸
런은 id(`TRIGGER_RUN_ID`)로 바로 읽고, 그래도 "없음"이면 답을 못 한 것으로 죽는다.

로컬에서 판정만 보려면(쓰기 없음, `az` 로그인과 `GH_TOKEN` 필요):
    EVENT=workflow_run TRIGGER_SHA=<sha> TRIGGER_CONCLUSION=success \\
      python -X utf8 scripts/hub_deploy_pick.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.error
from dataclasses import dataclass, field
from typing import Callable

try:
    from scripts._gitutil import git, git_ok
    from scripts._root import ROOT  # noqa: F401  리포 루트를 sys.path 에 올린다
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _gitutil import git, git_ok
    from _root import ROOT  # noqa: F401
from vinc_mcp import hubhttp  # noqa: E402

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
    "vinc_rag/",
    "packaging/bake_embed_model.py",
    "pyproject.toml",
    "scripts/check_hub_env.py",
    "scripts/verify_layout_remote.py",
    "scripts/verify_search_temporal_remote.py",
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
                f"손으로 부른 배포지만 {dispatch_sha[:7]} 의 core 잡이 초록이 아니다({st}). "
                "ci 를 그 커밋에서 다시 돌리고 초록이 된 뒤 부를 것")
        say(f"workflow_dispatch: {dispatch_sha[:7]} 를 그대로 내보낸다(core 잡 초록)")
        plan.deploy, plan.sha = True, dispatch_sha
        return plan

    if event != "workflow_run":
        raise Unanswered(f"모르는 이벤트: {event!r}")

    def gate_of(sha: str) -> str:
        # 불러낸 커밋의 CI 는 끝났다. "없음"은 API 가 아직 못 실은 것이다(머리말 실패 방향).
        st = ci_status(sha)
        if st == "none" and sha == trigger_sha:
            raise Unanswered(
                f"불러낸 {sha[:7]} 의 CI 는 {trigger_conclusion or '끝났'}인데 런을 못 찾았다 "
                "— 건너뛰면 그 커밋이 조용히 안 나간다")
        return st

    # 4. 불러낸 CI 가 빨갛고 그 커밋 자신이 허브를 건드렸으면 알린다(배포 여부와 무관).
    #    런 전체가 빨개도 core 잡이 초록이면(러스트·앱만 빨강) 허브는 막히지 않았다.
    if trigger_conclusion != "success" and trigger_sha:
        gate = gate_of(trigger_sha)
        parent = parent_of(trigger_sha)
        if gate == "failure" and parent and touches_hub(changed(parent, trigger_sha)):
            plan.withheld = trigger_sha
            say(f"불러낸 CI 가 {trigger_conclusion}(core 잡 {gate}): {trigger_sha[:7]} 의 "
                "허브 변경은 core 잡이 초록이 될 때까지 안 나간다")
        elif gate == "success":
            say(f"불러낸 CI 는 {trigger_conclusion} 이지만 core 잡은 초록이다 — 허브는 막히지 않았다")

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
        st = gate_of(sha)
        if st == "success":
            target = sha
            break
        say(f"  {sha[:7]}: CI {st} — 건너뛴다")
    if not target:
        say("떠 있는 것보다 새 커밋 중 core 잡이 초록인 것이 없다 — 내보낼 것 없음")
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

    say(f"내보낸다: {target[:7]} (core 잡이 초록인 가장 새 main 커밋)")
    plan.deploy, plan.sha = True, target
    return plan


# ── 바깥 ────────────────────────────────────────────────────────────────────

def _git_out(*args: str) -> str:
    try:
        return git(args).strip()
    except subprocess.CalledProcessError as e:
        raise Unanswered(f"git {' '.join(args)} 실패: {e.stderr.strip()}") from None


def _live_tag() -> str:
    r = subprocess.run(
        ["az", "containerapp", "show", "--name", "vinc-hub", "--resource-group", "vincsRG",
         "--query", "properties.template.containers[0].image", "-o", "tsv"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise Unanswered(f"허브의 현재 이미지를 못 읽었다: {r.stderr.strip()}")
    tag = r.stdout.strip().rsplit(":", 1)[-1]
    return tag if _SHA.match(tag) else ""


#: 허브를 막는 잡. ci.yml 런 **전체**의 결론이 아니라 이 잡들만 본다 — 옛 `gate` 가
#: 지키던 범위(파이썬 스위트)와 같다. 런 전체를 보면 허브와 무관한 데스크탑 러스트·앱
#: 잡이 허브를 막는다: 2026-09-25 e9be2fa 의 main CI 가 윈도우 `cargo test` 의 메모리
#: 부족(호스트가 core 둘과 겹쳐 돌았다) 하나로 빨개졌고, 그대로면 허브 변경이 보류됐다.
#: core 잡 이름이 바뀌면 여기를 같이 고칠 것 — 하나도 못 찾으면 판정하지 않고 죽는다.
GATE_JOB_PREFIX = "core ("


def classify(runs: list[dict]) -> str:
    """한 커밋의 ci.yml 런들(각각 `jobs` 를 실은 것)을 success·pending·failure·none 으로.

    런 하나라도 core 잡이 **전부** 성공이면 success 다(재실행은 같은 런의 다음 시도라
    jobs 가 최신 시도를 준다). 아직 안 끝난 core 잡이 있으면 pending.
    """
    if not runs:
        return "none"
    pending = False
    for r in runs:
        jobs = r.get("jobs") or []
        gate = [j for j in jobs if str(j.get("name", "")).startswith(GATE_JOB_PREFIX)]
        if not gate:
            if r.get("status") != "completed":
                pending = True          # 잡이 아직 안 만들어졌다
            elif jobs:
                raise Unanswered(
                    f"ci.yml 런 {r.get('id')} 에 '{GATE_JOB_PREFIX}' 로 시작하는 잡이 없다 — "
                    "잡 이름이 바뀌었으면 GATE_JOB_PREFIX 를 고칠 것")
            continue                    # 잡 0개 = 워크플로 검증 실패. 실패로 센다
        if all(j.get("conclusion") == "success" for j in gate):
            return "success"
        if any(j.get("status") != "completed" for j in gate):
            pending = True
    return "pending" if pending else "failure"


def _ci_status_via_api(api: str, repo: str, token: str,
                       trigger_sha: str = "", trigger_run: str = "") -> Callable[[str], str]:
    """`trigger_run` 은 불러낸 ci.yml 런의 id. 목록에 아직 없으면 id 로 읽어 보탠다
    (목록은 끝난 런을 늦게 싣는다 — 머리말 실패 방향)."""
    memo: dict[str, str] = {}

    def get(path: str) -> dict:
        return hubhttp.get_json(f"{api}/repos/{repo}{path}", token=token,
                                accept="application/vnd.github+json", timeout=30)

    def status(sha: str) -> str:
        if sha in memo:
            return memo[sha]
        try:
            runs = get(f"/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=20"
                       ).get("workflow_runs") or []
            if (sha == trigger_sha and trigger_run
                    and all(str(r.get("id")) != trigger_run for r in runs)):
                runs.append(get(f"/actions/runs/{trigger_run}"))
            for r in runs:
                r["jobs"] = get(f"/actions/runs/{r['id']}/jobs?per_page=100").get("jobs") or []
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            raise Unanswered(f"{sha[:7]} 의 CI 상태를 못 읽었다: {e}") from e
        memo[sha] = classify(runs)
        return memo[sha]

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
                                         e.get("REPO", "Vinculums/Vinc"), e["GH_TOKEN"],
                                         e.get("TRIGGER_SHA", ""), e.get("TRIGGER_RUN_ID", "")),
            is_commit=lambda s: git_ok(["cat-file", "-e", f"{s}^{{commit}}"]),
            is_ancestor=lambda a, b: git_ok(["merge-base", "--is-ancestor", a, b]),
            changed=lambda a, b: _git_out("diff", "--name-only", a, b).splitlines(),
            parent_of=lambda s: git(["rev-parse", "--verify", "--quiet", f"{s}^"],
                                    check=False).strip(),
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
