# -*- coding: utf-8 -*-
"""age_status_lines.py — 계획 문서의 상태 줄을 **리포에 대고** 늙힌다(R6).

정본과 이유: [[d4ab94561078498e3]] §7.3 R6.

`vinc.plan.sync-node-time-fields` 는 머지되고 엿새 뒤에도 "not yet merged to main"
이라고 적혀 있었다. 그 문장은 이 리포의 인용 게이트가 잡는 것과 정반대 방향이다 —
게이트는 코드가 **없는 문서**를 가리키는 것을 잡고, 이쪽은 문서가 **지나간 사실**을
현재로 말하는 것을 잡는다. 그 방향에는 여태 아무것도 없었다.

**첫 초안은 매니페스트를 재사용하자고 했고 그건 틀렸다.** `scripts/graph-refs.json`
에는 문서 id 와 노드 id 뿐이고 커밋 해시도 날짜도 없다(2026-08-24 대조). 그래서 실제로
필요한 입력 둘은 다른 짝이고, 둘 다 로컬이라 오프라인은 그대로 성립한다:

  ① **문서 본문** — 커밋을 인용하는 계획은 이미 자기 본문에 해시를 적는다
  ② **git** — 그 해시가 언제 주 가지에 닿았나

그래서 이것은 CI 게이트가 아니다. CI 에는 문서가 없다(그래프 키를 안 넣는 것과 같은
사유). 스튜어드가 세션 닫을 때 돌리는 검사고, 그때는 두 입력이 다 손에 있다.

    vinc_doc_get 으로 계획 문서를 받아 파일로 떨군 뒤:
    python -X utf8 scripts/age_status_lines.py plans/*.md
    cat doc.md | python -X utf8 scripts/age_status_lines.py -

발견이 있으면 종료코드 1. 판정은 하지 않는다 — "이 상태 줄은 인용한 커밋보다 오래됐다"
까지만 말하고, 고칠지는 문서를 여는 사람이 정한다.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 헤더의 as-of. `status_as_of` 가 정본이고 `updated` 는 그것이 없을 때의 대타다 —
#: 기존 문서 전부가 `updated` 만 갖고 있어서, 대타가 없으면 이 검사는 새 문서에만
#: 걸리고 정작 낡은 것들을 못 본다.
_AS_OF = re.compile(r"^(status_as_of|updated)\s*:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
#: 커밋처럼 생긴 토큰. **최소 한 자는 a-f 여야 한다** — 그 조건이 없으면 `20260817`
#: 같은 날짜가 8자리 16진수로 잡힌다.
_SHA = re.compile(r"\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
_DOC_ID = re.compile(r"^doc_id\s*:\s*(\S+)\s*$", re.M)
_LAYER = re.compile(r"^layer\s*:\s*(\S+)\s*$", re.M)


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def landed_at(sha: str, branch: str = "main") -> str:
    """이 커밋이 ``branch`` 에 닿은 날(YYYY-MM-DD). 아직 안 닿았거나 모르는 해시면 ''.

    닿은 날은 커밋 자신의 날짜가 **아니다**: 브랜치에서 만든 커밋은 며칠 뒤 병합으로
    들어온다. 그래서 조상 경로 위의 마지막 병합 커밋 날짜를 쓰고, 병합이 없으면
    (주 가지에 직접 얹힌 커밋) 자기 날짜를 쓴다."""
    if not sha or _git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}") == "":
        return ""
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, branch],
                      cwd=ROOT, capture_output=True).returncode != 0:
        return ""
    merges = _git("rev-list", "--merges", "--ancestry-path", f"{sha}..{branch}").split()
    return _git("log", "-1", "--format=%cs", merges[-1] if merges else sha)


def audit(text: str, *, landed=landed_at) -> dict | None:
    """문서 하나 → 발견 또는 None. ``landed`` 주입은 테스트가 git 없이 부르려는 것이다.

    발견의 조건은 하나다: 본문이 인용한 커밋 중 **상태 줄보다 늦게 주 가지에 닿은
    것**이 있다. 그런 커밋이 있으면 그 상태 줄은 그 사실을 모른 채 쓰인 것이다."""
    m = _AS_OF.search(text)
    if not m:
        return None
    as_of = m.group(2)
    shas = sorted(set(_SHA.findall(text)))
    late = [(s, d) for s in shas for d in (landed(s),) if d and d > as_of]
    if not late:
        return None
    did, lay = _DOC_ID.search(text), _LAYER.search(text)
    return {"doc_id": did.group(1) if did else "(no doc_id)",
            "as_of_field": m.group(1), "as_of": as_of,
            "layer": lay.group(1) if lay else "",
            "late": sorted(late, key=lambda p: p[1], reverse=True)}


def _report(f: dict, source: str) -> str:
    newest = f["late"][0]
    rest = f" (+{len(f['late']) - 1} more)" if len(f["late"]) > 1 else ""
    return (f"  {source}: {f['doc_id']} says {f['as_of_field']}: {f['as_of']}, "
            f"but {newest[0]} landed on main {newest[1]}{rest}")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__.strip().splitlines()[0])
        print("usage: age_status_lines.py <doc.md> [...] | -")
        return 2
    findings: list[str] = []
    scanned = 0
    for a in args:
        if a == "-":
            texts = [("(stdin)", sys.stdin.read())]
        else:
            p = Path(a)
            if not p.is_file():
                print(f"[age] 없는 파일: {a}")
                return 2
            texts = [(a, p.read_text(encoding="utf-8"))]
        for source, text in texts:
            scanned += 1
            f = audit(text)
            if f:
                findings.append(_report(f, source))
    if findings:
        print(f"[age] 상태 줄이 인용한 커밋보다 오래된 문서 {len(findings)}건 "
              f"(검사 {scanned}건)")
        print("\n".join(findings))
        print("  고치는 법: 문서를 열어 상태 줄을 다시 쓰고 status_as_of 를 오늘로.")
        print("  이 도구는 어느 쪽이 옳은지 판정하지 않는다 — 문서를 여는 것은 사람이다.")
        return 1
    print(f"[age] PASS — 상태 줄이 앞선 문서 없음 (검사 {scanned}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
