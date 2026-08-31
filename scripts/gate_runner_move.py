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

try:
    import yaml
except ImportError:  # pragma: no cover - CI 는 pyyaml 을 깐다
    print("::error::pyyaml 이 필요하다", file=sys.stderr)
    raise SystemExit(2)

WORKFLOW_DIR = ".github/workflows/"


def _git(*args: str) -> str | None:
    """실패를 None 으로 돌린다 — 파일이 그 리비전에 없는 경우가 정상이다."""
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def changed_workflows(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", base, head, "--", WORKFLOW_DIR)
    if out is None:
        return []
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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gate_runner_move.py <BASE_SHA> <HEAD_SHA>", file=sys.stderr)
        return 2
    base, head = argv

    findings: list[str] = []
    for path in changed_workflows(base, head):
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
                findings.append(
                    f"{path} 의 잡 `{job}` 러너가 바뀐다: {b[job]!r} → {a[job]!r}"
                )

    if not findings:
        return 0

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
