# -*- coding: utf-8 -*-
"""gate_runner_move.py — 직렬화 워크플로의 러너를 옮기는 PR 을 머지 전에 세운다.

## 무엇을 막나

`concurrency.cancel-in-progress: false` 인 워크플로는 **모든 런을 한 줄에 세운다.**
그것은 의도다 — `hub-deploy` 는 단일 writer 스토어를 건드리므로 배포 도중 취소가
위험하고, `release` 는 반쯤 발행된 릴리스를 남기면 안 된다.

대가는 **줄의 맨 앞이 안 끝나면 뒤가 전부 막힌다**는 것이고, 2026-08-30 에 실제로
그렇게 됐다(`Vinc#129`). 원인은 러너 이행이었다: `runs-on` 을 `ubuntu-latest` 로
옮기는 커밋이 머지되기 **전에** 만들어진 런이 옛 라벨(`vinc-local`)을 기다린 채
큐에 남았고, 노트북이 꺼져 있으니 영영 시작하지 못했다. 그 하나가 뒤의 배포 넷을
막았다.

즉 고장의 씨앗은 **워크플로 파일이 바뀌는 순간과 이미 큐에 있는 런 사이의 틈**이다.
그 틈은 `runs-on` 을 바꾸는 PR 마다 다시 열린다.

## 왜 산문이 아니라 검사인가

이 리포는 같은 교훈을 이미 비싸게 배웠다. 2026-08-23 에 같은 종류의 함정을
레코드에도, 계획서의 함정 목록에도, PR 본문에도 적어 두고 **십팔 분 뒤에 다시
밟았다**. 정본: [[record:writing-a-trap-down-does-not-stop-it-a-check-does]] —
"적어 두는 것은 막지 못한다. 막는 것은 행위 시점의 검사다."

그래서 이것은 런북의 한 문단이 아니라 PR 에 붙는 게이트다.

## 무엇을 안 막나

⚠ **이미 큐에 갇힌 런을 풀어 주지 않는다.** 이 게이트는 그런 런이 생기기 전에
사람에게 알릴 뿐이다. 갇힌 런은 사람이 취소해야 풀린다.

⚠ **`cancel-in-progress` 가 표현식인 워크플로는 대상이 아니다.** `ci.yml` 이
그렇다(`${{ github.event_name == 'pull_request' }}`). 그쪽은 push 일 때 그룹 키에
커밋 sha 가 들어가 런끼리 애초에 같은 줄에 서지 않으므로(#124) 이 고장의 모양이
아니다. 리터럴 `false` 만 본다 — 판정을 흐리게 하느니 좁게 두는 쪽이다.

## 쓰는 법

    python -X utf8 scripts/gate_runner_move.py <BASE_SHA> <HEAD_SHA>

발견이 있으면 종료코드 1. 판정은 하지 않는다 — "이 워크플로의 러너가 바뀌었고
그 워크플로는 직렬화한다"까지만 말하고, 큐를 비웠는지는 사람이 확인한다.
"""
from __future__ import annotations

import subprocess
import sys
import os
import re

try:
    import yaml
except ImportError:  # pragma: no cover - CI 는 pyyaml 을 깐다
    print("::error::pyyaml 이 필요하다", file=sys.stderr)
    raise SystemExit(2)

WORKFLOW_DIR = ".github/workflows/"


class Unreadable(Exception):
    """리비전 자체를 못 읽는다 — '검사했더니 없다' 와 **다른 사건**이다."""


def _git(*args: str) -> str | None:
    """실패를 None 으로 돌린다 — 파일이 그 리비전에 없는 경우가 정상이다.

    ⚠ 이 None 은 **'그 자리에 파일이 없다'만 뜻해야 한다.** 리비전을 통째로 못 읽는
    경우까지 같은 값으로 오면 호출부가 그것을 '변경 없음' 으로 읽고 게이트가 아무것도
    검사하지 않은 채 통과한다. 그래서 리비전 존재 여부는 `require_revisions` 가
    **먼저, 따로** 확인한다."""
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def require_revisions(*revs: str) -> None:
    """두 리비전이 이 체크아웃에 실재하는가. 없으면 **멈춘다.**

    이것이 없으면 게이트는 얕은 체크아웃에서 조용히 통과한다: `git show BASE:path` 가
    실패하고, 그 실패가 '그 리비전에 파일이 없다' 와 구분되지 않아 그냥 건너뛰어진다.
    검사한 워크플로가 0건인데 결과는 PASS 다.

    이 리포는 같은 모양을 이미 세 번 만났다 — 값이 빈 시크릿이 스킵 분기를 타고,
    테스트가 호환 분기를 타고, 이행 검증이 앞선 거절 때문에 툼스톤 표에 닿지 못한 채
    전부 초록이었다. 공통점은 **답할 수 없을 때 PASS 를 답한다**는 것이다. 여기서는
    답할 수 없으면 답하지 않는다."""
    for rev in revs:
        if _git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}") is None:
            raise Unreadable(rev)


def changed_workflows(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", base, head, "--", WORKFLOW_DIR)
    if out is None:
        # 리비전은 `require_revisions` 가 이미 봤으므로 여기 실패는 git 자체의 문제다.
        # 빈 목록으로 강등하면 그것도 무음 통과가 된다.
        raise Unreadable(f"diff {base}..{head}")
    return [p for p in out.splitlines() if p.strip().endswith((".yml", ".yaml"))]


def _load(rev: str, path: str) -> dict | None:
    raw = _git("show", f"{rev}:{path}")
    if raw is None:
        return None
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    return doc if isinstance(doc, dict) else None


def serializes(doc: dict) -> bool:
    """리터럴 `cancel-in-progress: false` 만 참으로 본다(위 주석의 이유)."""
    c = doc.get("concurrency")
    return isinstance(c, dict) and c.get("cancel-in-progress") is False


def runners(doc: dict) -> dict[str, object]:
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {
        name: job.get("runs-on")
        for name, job in jobs.items()
        if isinstance(job, dict)
    }


def active_runs(repository: str, workflow: str) -> list[int]:
    """러너를 옮길 워크플로의 큐와 실행을 읽는다. 조회 실패는 통과가 아니다."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise Unreadable("GITHUB_REPOSITORY")
    found: set[int] = set()
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        endpoint = (f"repos/{repository}/actions/workflows/{workflow}/runs"
                    f"?status={status}&per_page=100")
        try:
            result = subprocess.run(
                ["gh", "api", "--paginate", endpoint, "--jq", ".workflow_runs[].id"],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Unreadable(f"Actions queue: {workflow}") from exc
        if result.returncode:
            raise Unreadable(f"Actions queue: {workflow} ({status})")
        try:
            found.update(int(line) for line in result.stdout.splitlines() if line.strip())
        except ValueError as exc:
            raise Unreadable(f"Actions queue response: {workflow}") from exc
    return sorted(found)


def main(argv: list[str]) -> int:
    check_queues = len(argv) == 3 and argv[2] == "--check-queues"
    if len(argv) != 2 and not check_queues:
        print("usage: gate_runner_move.py <BASE_SHA> <HEAD_SHA> [--check-queues]", file=sys.stderr)
        return 2
    base, head = argv[:2]

    try:
        require_revisions(base, head)
        paths = changed_workflows(base, head)
    except Unreadable as e:
        print(f"::error::게이트가 리비전을 못 읽었다: {e} — 검사하지 못했으므로 통과시키지 않는다",
              file=sys.stderr)
        print(
            "\n"
            "가장 흔한 원인은 **얕은 체크아웃**이다. 이 게이트는 두 리비전의 워크플로\n"
            "파일을 `git show` 로 읽어 비교하므로 base 커밋이 로컬에 있어야 한다.\n"
            "  actions/checkout 에 `fetch-depth: 0` 을 준다(ci.yml 이 그렇게 한다).\n"
            "\n"
            "왜 통과시키지 않는가: 못 읽은 것을 '바뀐 것이 없다' 로 강등하면 게이트가\n"
            "0건을 검사하고 PASS 를 답한다. 그러면 이 게이트가 막으려던 러너 이동이\n"
            "그대로 지나가고, 초록이라 아무도 다시 보지 않는다.",
            file=sys.stderr,
        )
        return 2

    findings: list[str] = []
    moved_workflows: set[str] = set()
    for path in paths:
        after = _load(head, path)
        before = _load(base, path)
        # 새로 생겼거나 지워진 파일은 '옮긴' 것이 아니다.
        if after is None or before is None:
            continue
        if not serializes(after):
            continue
        a, b = runners(after), runners(before)
        for job in sorted(set(a) & set(b)):
            if a[job] != b[job]:
                moved_workflows.add(path.removeprefix(WORKFLOW_DIR))
                findings.append(
                    f"{path} 의 잡 `{job}` 러너가 바뀐다: {b[job]!r} → {a[job]!r}"
                )

    if not findings:
        return 0

    if check_queues:
        try:
            queues = {workflow: active_runs(os.environ.get("GITHUB_REPOSITORY", ""), workflow)
                      for workflow in sorted(moved_workflows)}
        except Unreadable as exc:
            print(f"::error::러너 이동 전 Actions 큐를 읽지 못했다: {exc}", file=sys.stderr)
            return 2
        if not any(queues.values()):
            for workflow in queues:
                print(f"Runner move: {workflow}: no queued or active runs")
            return 0
        for workflow, run_ids in queues.items():
            if run_ids:
                print(f"::error::{workflow}: queued or active runs {run_ids}", file=sys.stderr)

    print("::error::직렬화 워크플로의 러너가 바뀐다 — 머지 전에 그 워크플로의 큐를 비울 것", file=sys.stderr)
    for f in findings:
        print(f"  · {f}", file=sys.stderr)
    print(
        "\n"
        "이 워크플로는 cancel-in-progress: false 라 모든 런이 한 줄에 선다.\n"
        "이 PR 이 머지되기 전에 만들어진 런은 옛 러너 라벨을 기다리므로, 그 라벨의\n"
        "러너가 없으면 영영 시작하지 못하고 뒤의 런을 전부 막는다(Vinc#129 에서\n"
        "배포 넷이 그렇게 밀렸다).\n"
        "\n"
        "머지 전에 할 것: 그 워크플로에 queued 런이 있는지 보고, 있으면 취소한다.\n"
        "  gh run list --repo <org>/<repo> --workflow <파일> --status queued\n"
        "  gh(없으면) GitHub MCP 의 list_workflow_runs 로 같은 것을 읽는다.\n"
        "확인했으면 PR 본문에 그 사실을 적는다 — 다음 사람이 재확인할 수 있게.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
