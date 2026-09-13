# -*- coding: utf-8 -*-
"""worktree_hook.py — 워크스페이스 루트에서 연 세션에서 `isolation: worktree` 를 성립시킨다.

역할 껍데기는 워크스페이스 루트(`Vinc` 의 부모)에 설치되고, 그 루트는 git 리포가 아니다.
그래서 하네스는 격리 역할(dev-core·dev-app)을 "not in a git repository and no
WorktreeCreate hooks are configured" 로 거절한다. 이 파일이 그 빈자리의 두 훅이다:
`Vinc` 리포에서 `<루트>/_wt/<name>` 로 worktree 를 만들고, 끝나면 **일이 남지 않았을
때만** 지운다. 근거: [[record:worktree-isolation-makes-one-branch-per-package-physical]]

손으로 설치하지 않는다. `scripts/agents_sync.py --install` 이 이 파일을
`<루트>/.claude/hooks/` 로 복사하고 그 자리의 settings.json 에 두 훅을 건다. 복사하는
이유: 메인 체크아웃의 사본을 가리키면 훅이 그 체크아웃의 브랜치를 따라 바뀐다 — 이번에
걷어내는 두 번째 로스터와 같은 병이다.

훅 계약(하네스 문서 hooks · WorktreeCreate/WorktreeRemove):
  create  stdin 에 {"name": 슬러그}. stdout 의 마지막 줄이 만든 디렉터리 경로이고,
          0 이 아닌 종료는 생성 실패다. 그래서 git 의 stdout 은 여기서 삼킨다.
  remove  stdin 에 {"worktree_path": 경로}. 0 이 아닌 종료 뒤 디렉터리가 남아 있으면
          제거 실패로 치고 하네스는 그대로 둔다 — 이 파일은 그것을 **보존**으로 쓴다.

    python -I -X utf8 worktree_hook.py create --repo <Vinc> --into <루트>/_wt
    python -I -X utf8 worktree_hook.py remove --repo <Vinc> --into <루트>/_wt
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
BRANCH_PREFIX = "worktree-"  # 하네스 기본 worktree 와 같은 이름을 쓴다


def _git(where: Path, *args: str, check: bool = True, timeout: int = 300) -> str:
    """stdout 을 문자열로 돌려준다. stderr 는 물려받는다(하네스가 디버그 로그로 받는다)."""
    try:
        r = subprocess.run(["git", "-C", str(where), *args], stdout=subprocess.PIPE,
                           text=True, timeout=timeout)
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
    if not dest.exists():
        into.mkdir(parents=True, exist_ok=True)
        # 기준은 origin/main 이다(하네스 기본값 "fresh" 와 같다). 메인 체크아웃의 HEAD 는
        # 동시 세션이 옮기므로 기준이 못 된다. fetch 가 실패하면 캐시된 ref 로 간다.
        _git(repo, "fetch", "--quiet", "origin", "main", check=False, timeout=60)
        _git(repo, "worktree", "add", "-b", BRANCH_PREFIX + name, str(dest), "origin/main")
    return dest


def remove(path: str, repo: Path, into: Path) -> str:
    """지웠으면 사유 문자열, 보존이면 SystemExit(종료 1).

    보존 조건은 하네스의 정리 규칙과 같다: 커밋 안 된 변경·추적 안 된 파일·어느 원격에도
    없는 커밋. 푸시까지 끝난 깨끗한 트리만 지운다.

    `git worktree remove` 를 쓰지 않는다. 윈도에서 그것은 트리 안의 링크(정션)를 따라가
    대상을 비웠다([[record:worktree-removal-follows-links-and-deletes-ignored-files-2026-09-13]]).
    `shutil.rmtree` 는 중첩 정션의 링크만 지운다 — `tests/test_worktree_hook.py` 가 그
    성질을 고정한다."""
    wt = Path(path).resolve()
    if wt.parent != into.resolve():
        raise SystemExit(f"[worktree] {wt} 는 {into} 바로 아래가 아니다. 이 훅이 만든 것만 지운다")
    if wt.exists():
        if _git(wt, "status", "--porcelain").strip():
            raise SystemExit(f"[worktree] 보존: {wt} 에 커밋 안 된 변경이 있다")
        if _git(wt, "log", "--oneline", "HEAD", "--not", "--remotes").strip():
            raise SystemExit(f"[worktree] 보존: {wt} 에 푸시 안 된 커밋이 있다")
        shutil.rmtree(wt)
    _git(repo, "worktree", "prune")
    # -d 는 병합 안 된 브랜치를 거절한다. 역할이 브랜치를 새로 땄으면 이 이름은 빈 채로 남는다.
    _git(repo, "branch", "-d", BRANCH_PREFIX + wt.name, check=False)
    return f"지웠다: {wt}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WorktreeCreate / WorktreeRemove hook for Vinc")
    ap.add_argument("event", choices=["create", "remove"])
    ap.add_argument("--repo", required=True, type=Path, help="worktree 를 뽑을 리포(Vinc)")
    ap.add_argument("--into", required=True, type=Path, help="worktree 를 둘 디렉터리(<루트>/_wt)")
    args = ap.parse_args(argv)
    payload = json.loads(sys.stdin.read() or "{}")
    if args.event == "create":
        print(create(str(payload.get("name") or ""), args.repo, args.into))
    else:
        print(remove(str(payload.get("worktree_path") or ""), args.repo, args.into), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
