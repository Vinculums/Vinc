# -*- coding: utf-8 -*-
"""worktree_hook.py — 워크스페이스 루트에서 연 세션에서 `isolation: worktree` 를 성립시킨다.

역할 껍데기는 워크스페이스 루트(`Vinc` 의 부모)에 설치되고, 그 루트는 git 리포가 아니다.
그래서 하네스는 격리 역할(dev-core·dev-app)을 "not in a git repository and no
WorktreeCreate hooks are configured" 로 거절한다. 이 파일이 그 빈자리의 훅이다:
`Vinc` 리포에서 `<루트>/_wt/<name>` 로 worktree 를 만들고, **일이 남지 않았을 때만**
지운다. 근거: [[record:worktree-isolation-makes-one-branch-per-package-physical]]

지우는 자리는 셋이다(remove · subagent-stop · create 앞의 청소). 하네스는 훅이 만든
서브에이전트 트리에 WorktreeRemove 를 부르지 않고 넘어갈 때가 있고, 자기 주기 청소에서도
그 트리를 뺀다. remove 만 믿으면 `_wt/agent-*` 가 쌓인다.
근거: [[record:hook-created-worktrees-are-not-removed-by-the-harness-2026-09-13]]

손으로 설치하지 않는다. `scripts/agents_sync.py --install` 이 이 파일을
`<루트>/.claude/hooks/` 로 복사하고 그 자리의 settings.json 에 세 훅을 건다. 복사하는
이유: 메인 체크아웃의 사본을 가리키면 훅이 그 체크아웃의 브랜치를 따라 바뀐다 — 걷어낸
두 번째 로스터와 같은 병이다.

훅 계약(하네스 문서 hooks · WorktreeCreate/WorktreeRemove/SubagentStop):
  create         stdin 에 {"name": 슬러그}. stdout 의 마지막 줄이 만든 디렉터리 경로이고,
                 0 이 아닌 종료는 생성 실패다. 그래서 git 의 stdout 은 여기서 삼킨다.
                 만들기 전에 하루 넘게 쉰 `agent-*` 트리를 쓸어 낸다(`sweep`).
  remove         stdin 에 {"worktree_path": 경로}. 0 이 아닌 종료 뒤 디렉터리가 남아 있으면
                 제거 실패로 치고 하네스는 그대로 둔다 — 이 파일은 그것을 **보존**으로 쓴다.
  subagent-stop  stdin 에 {"agent_id", "cwd", ...}. 종료 2 는 서브에이전트를 붙잡고
                 stderr 를 다음 지시로 먹이므로 **언제나 0 으로 끝난다**.

    python -I -X utf8 worktree_hook.py create --repo <Vinc> --into <루트>/_wt
    python -I -X utf8 worktree_hook.py remove --repo <Vinc> --into <루트>/_wt
    python -I -X utf8 worktree_hook.py subagent-stop --repo <Vinc> --into <루트>/_wt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
BRANCH_PREFIX = "worktree-"  # 하네스 기본 worktree 와 같은 이름을 쓴다
AGENT_PREFIX = "agent-"  # 하네스가 서브에이전트 트리에 붙이는 이름. subagent-stop·청소는 이것만 본다
#: 무시 파일 중 지워도 되는 것: 경로의 한 마디라도 이것이면 버려도 되는 것으로 친다. 그 밖의
#: 무시 파일이 하나라도 있으면 지우지 않는다 — git 이 안 보는 파일이라 지우면 되돌릴 곳이 없다.
#: 캐시 넷에 더해 `vinc_store`(테스트 스위트가 cwd 에 만드는 임베디드 스토어)와 `.claude`
#: (에이전트가 편집할 때마다 PostToolUse 훅이 ui-events.jsonl 을 쓴다)가 있다. 둘이 빠지면
#: 테스트를 돌렸거나 파일을 고친 트리는 영영 안 지워진다. 실측은 위 hook-created 레코드에.
DISPOSABLE = frozenset({"__pycache__", ".pytest_cache", "node_modules", ".venv",
                        "vinc_store", ".claude"})
#: 청소가 트리를 쉰 것으로 치는 시간. 신호는 그 worktree 의 git 관리 디렉터리에 있는
#: `index`·`HEAD` 중 늦은 mtime 이다(add·commit·checkout·switch 가 갱신한다).
IDLE_SECONDS = 24 * 3600


def _git(where: Path, *args: str, check: bool = True, timeout: int = 300) -> str:
    """stdout 을 문자열로 돌려준다. stderr 는 물려받는다(하네스가 디버그 로그로 받는다)."""
    try:
        r = subprocess.run(["git", "-C", str(where), *args], stdout=subprocess.PIPE,
                           text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        if check:
            raise SystemExit(f"[worktree] git {' '.join(args)} 시간 초과")
        return ""
    if check and r.returncode:
        raise SystemExit(f"[worktree] git {' '.join(args)} 실패({r.returncode})")
    return r.stdout


def create(name: str, repo: Path, into: Path) -> Path:
    if not _NAME.match(name) or ".." in name:
        raise SystemExit(f"[worktree] 이름이 슬러그가 아니다: {name!r}")
    dest = into.resolve() / name
    sweep(repo, into, skip=name)
    if not dest.exists():
        into.mkdir(parents=True, exist_ok=True)
        # 기준은 origin/main 이다(하네스 기본값 "fresh" 와 같다). 메인 체크아웃의 HEAD 는
        # 동시 세션이 옮기므로 기준이 못 된다. fetch 가 실패하면 캐시된 ref 로 간다.
        _git(repo, "fetch", "--quiet", "origin", "main", check=False, timeout=60)
        _git(repo, "worktree", "add", "-b", BRANCH_PREFIX + name, str(dest), "origin/main")
    return dest


def _why_keep(wt: Path, repo: Path) -> str | None:
    """지우면 안 되는 이유, 없으면 None. 하네스의 정리 규칙(커밋 안 된 변경·추적 안 된
    파일·어느 원격에도 없는 커밋)에 버려도 되는 것이 아닌 무시 파일과, 트리 안에 등록된
    다른 worktree(무시된 `.claude/worktrees/*` 가 그 자리다)를 더한다."""
    entries = [e for e in _git(wt, "status", "--porcelain", "--ignored", "-z").split("\0") if e]
    if any(not e.startswith("!! ") for e in entries):
        return "커밋 안 된 변경이 있다"
    if _git(wt, "log", "--oneline", "HEAD", "--not", "--remotes").strip():
        return "푸시 안 된 커밋이 있다"
    odd = [e[3:] for e in entries if not DISPOSABLE & set(e[3:].rstrip("/").split("/"))]
    if odd:
        return f"캐시가 아닌 무시 파일이 있다: {', '.join(odd[:5])}"
    listed = _git(repo, "worktree", "list", "--porcelain").splitlines()
    nested = [ln[9:] for ln in listed if ln.startswith("worktree ") and wt in Path(ln[9:]).resolve().parents]
    if nested:
        return f"안에 다른 worktree 가 있다: {nested[0]}"
    return None


def remove(path: str, repo: Path, into: Path) -> str:
    """지웠으면 사유 문자열, 보존이면 SystemExit(종료 1).

    `git worktree remove` 를 쓰지 않는다. 윈도에서 그것은 트리 안의 링크(정션)를 따라가
    대상을 비웠다([[record:worktree-removal-follows-links-and-deletes-ignored-files-2026-09-13]]).
    `shutil.rmtree` 는 중첩 정션의 링크만 지운다 — `tests/test_worktree_hook.py` 가 그
    성질을 고정한다.

    지우기 전에 이름부터 바꾼다. 윈도는 트리 안에 cwd 나 열린 파일을 가진 프로세스가
    있으면 디렉터리 이름 바꾸기를 거절한다(WinError 5) — 쓰는 중인 트리를 반쯤 지우는
    대신 통째로 보존한다."""
    wt = Path(path).resolve()
    if wt.parent != into.resolve():
        raise SystemExit(f"[worktree] {wt} 는 {into} 바로 아래가 아니다. 이 훅이 만든 것만 지운다")
    if wt.exists():
        why = _why_keep(wt, repo)
        if why:
            raise SystemExit(f"[worktree] 보존: {wt}: {why}")
        tomb = wt.with_name(f".removing-{wt.name}")
        try:
            wt.rename(tomb)
        except OSError as e:
            raise SystemExit(f"[worktree] 보존: {wt} 이름을 못 바꿨다(쓰는 중일 수 있다): {e}")
        shutil.rmtree(tomb)
    _git(repo, "worktree", "prune")
    # -d 는 병합 안 된 브랜치를 거절한다. 역할이 브랜치를 새로 땄으면 이 이름은 빈 채로 남는다.
    _git(repo, "branch", "-d", BRANCH_PREFIX + wt.name, check=False)
    return f"지웠다: {wt}"


def subagent_stop(payload: dict, repo: Path, into: Path) -> str:
    """멈춘 서브에이전트의 `_wt/agent-<id>` 가 **손대지 않은** 트리면 지운다.

    배경 서브에이전트는 끝나기 전에도 여러 번 멈춘다(푸시하고 CI 를 기다리는 동안). 그래서
    여기서는 푸시가 끝났어도 제 커밋이 있는 트리는 두고, HEAD 가 origin/main 의 조상인
    트리만 지운다. 제 일이 있던 트리는 `sweep` 이 하루 쉰 뒤에 가져간다.

    cwd 로 트리를 찾는 것은 agent_id 가 **없을 때만**이다. 격리 역할이 띄운 비격리 자식
    에이전트는 부모의 트리를 cwd 로 물려받으므로, 자식이 멈출 때 cwd 를 따르면 아직 일하는
    부모의 트리를 지운다."""
    into_r = into.resolve()
    aid = str(payload.get("agent_id") or "")
    name = aid if aid.startswith(AGENT_PREFIX) else AGENT_PREFIX + aid
    wt = into_r / name if aid and _NAME.match(name) else None
    if not aid and payload.get("cwd"):
        cwd = Path(str(payload["cwd"])).resolve()
        wt = next((p for p in (cwd, *cwd.parents) if p.parent == into_r), None)
    if wt is None or not wt.name.startswith(AGENT_PREFIX) or not wt.is_dir():
        return "[worktree] 이 훅이 만든 서브에이전트 트리가 아니다"
    own = subprocess.run(["git", "-C", str(wt), "merge-base", "--is-ancestor", "HEAD",
                          "refs/remotes/origin/main"], stdout=subprocess.DEVNULL).returncode
    if own:
        return f"[worktree] 보존: {wt}: HEAD 가 origin/main 에 없는 커밋이다(제 일이 있다)"
    return remove(str(wt), repo, into)


def sweep(repo: Path, into: Path, skip: str = "", now: float | None = None) -> None:
    """`into` 바로 아래 `agent-*` 중 하루 넘게 쉬었고 `remove` 가 받아 주는 트리를 지운다.
    하나가 실패해도 생성은 계속한다. 사유는 stderr 로만 낸다(stdout 은 경로 자리다)."""
    if not into.is_dir():
        return
    now = time.time() if now is None else now
    for wt in sorted(into.iterdir()):
        if not wt.name.startswith(AGENT_PREFIX) or wt.name == skip or not (wt / ".git").is_file():
            continue
        try:
            # 쉰 시간을 git status 보다 먼저 잰다. status 는 index 를 새로 쓸 수 있다.
            gitdir = Path(_git(wt, "rev-parse", "--absolute-git-dir").strip())
            touched = max(f.stat().st_mtime for f in (gitdir / "index", gitdir / "HEAD") if f.exists())
            if now - touched < IDLE_SECONDS:
                continue
            print(remove(str(wt), repo, into), file=sys.stderr)
        except (SystemExit, OSError, ValueError) as e:
            print(f"[worktree] 청소 건너뜀: {wt.name}: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WorktreeCreate / WorktreeRemove / SubagentStop hook for Vinc")
    ap.add_argument("event", choices=["create", "remove", "subagent-stop"])
    ap.add_argument("--repo", required=True, type=Path, help="worktree 를 뽑을 리포(Vinc)")
    ap.add_argument("--into", required=True, type=Path, help="worktree 를 둘 디렉터리(<루트>/_wt)")
    args = ap.parse_args(argv)
    payload = json.loads(sys.stdin.read() or "{}")
    # 하네스는 훅을 에이전트의 cwd 에서 띄울 수 있다. 그 트리 안에 머물면 이 프로세스가
    # 이름 바꾸기를 막는다.
    os.chdir(args.repo)
    if args.event == "create":
        print(create(str(payload.get("name") or ""), args.repo, args.into))
    elif args.event == "remove":
        print(remove(str(payload.get("worktree_path") or ""), args.repo, args.into), file=sys.stderr)
    else:
        try:
            msg = subagent_stop(payload, args.repo, args.into)
        except (SystemExit, OSError) as e:
            msg = str(e)
        print(msg, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
