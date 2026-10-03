#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""소유권 게이트 — 추적되는 모든 파일에 주인 팀이 있는가.

    python -X utf8 scripts/gate_ownership.py                 # 리포 전체
    BASE=<sha> HEAD=<sha> python -X utf8 scripts/gate_ownership.py   # + 이 PR 이 닿은 팀

exit 0=통과 · 1=위반.

무엇을 보나(정직하게):

  ✅ `git ls-files` 의 모든 파일이 `.github/CODEOWNERS` 의 줄 하나 이상에 걸린다.
     주인 없는 파일이 하나라도 있으면 실패다. "누구 것인가"가 경로만 보고 정해져야
     리뷰 배정·CI 분리·지표가 전부 그 위에 선다 — 이 게이트가 지키는 것은 그 전제다.
  ✅ `agents/teams.json` 의 경로가 실재한다. `planned` 에 적은 미래 경로만 예외다.
  ✅ `teams.json` 이 이름 대는 역할이 `roles.json` 에 있고, `roles.json` 의 역할은
     어느 팀에든 속하거나 `read_only_roles` 에 있다. 두 파일이 서로를 잃으면
     CODEOWNERS 가 조용히 낡는다.
  ⚠ **로스터 드리프트는 경고다.** 역할 노드의 `paths` 가 없는 경로를 가리키거나
     (`book-scroll/`), 다른 팀이 소유한 경로를 가리키면 [DRIFT] 로 적는다. 정본은
     그래프이고 여기서 고칠 수 없으므로 실패로 만들면 이 게이트가 남의 고장을 보고
     하게 된다 — `agents_sync.py --check` 가 매니페스트 부재를 SKIP 하는 것과 같은
     분업이다. 갱신은 Linear VIN-241.
  ❌ **파이썬 테스트를 경로로 거르지 않는다.** 그 스위치가 441건을 끈 이력이
     `ci.yml` 의 `core-pkns` 주석에 있다. 이 게이트는 소유만 보고, 닿은 팀 목록은
     지표 원천으로만 출력한다.

CODEOWNERS 매칭 규칙은 GitHub 문서의 것을 따른다: 뒤에 오는 줄이 이긴다 · `/` 로
끝나면 디렉터리 전체 · 앞에 `/` 가 있으면 루트 기준 · `/` 가 없으면 어느 깊이의
그 이름이든 · `*` 는 `/` 를 넘지 않고 `**` 는 넘는다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEAMS = ROOT / "agents" / "teams.json"
ROLES = ROOT / "agents" / "roles.json"
CODEOWNERS = ROOT / ".github" / "CODEOWNERS"


# ── CODEOWNERS 매칭 ───────────────────────────────────────────────────────────

def _glob_to_regex(pattern: str) -> re.Pattern:
    """CODEOWNERS 패턴 하나를 정규식으로. gitignore 와 같은 문법의 부분집합이다."""
    anchored = pattern.startswith("/")
    p = pattern.lstrip("/")
    dir_only = p.endswith("/")
    p = p.rstrip("/")
    has_slash = "/" in p

    out = []
    i = 0
    while i < len(p):
        c = p[i]
        if c == "*":
            if p[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    body = "".join(out)

    # 어느 깊이든: `/` 없는 패턴은 basename 이거나 어느 디렉터리 이름이다.
    prefix = "^" if (anchored or has_slash) else "^(?:.*/)?"
    if dir_only:
        tail = "/.*$"
    else:
        # 파일이든, 그 이름의 디렉터리 아래 전부든.
        tail = "(?:/.*)?$"
    return re.compile(prefix + body + tail)


def parse_codeowners(text: str) -> list[tuple[str, tuple[str, ...], re.Pattern]]:
    """(패턴, 소유자들, 정규식) 목록. 주석과 빈 줄은 뺀다."""
    rules = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        pattern, owners = parts[0], tuple(parts[1:])
        rules.append((pattern, owners, _glob_to_regex(pattern)))
    return rules


def owners_for(path: str, rules) -> tuple[str, ...]:
    """마지막에 걸린 줄의 소유자. 아무 줄에도 안 걸리면 빈 튜플."""
    found: tuple[str, ...] = ()
    for _pattern, owners, rx in rules:
        if rx.match(path):
            found = owners
    return found


def unowned(files: list[str], rules) -> list[str]:
    return [f for f in files if not owners_for(f, rules)]


# ── teams.json ↔ roles.json ──────────────────────────────────────────────────

def _looks_like_path(s: str) -> bool:
    """역할 `paths` 에는 산문도 섞여 있다("read only", "the Linear backlog"). 경로처럼
    생긴 것만 검사한다 — 공백 없고, `/` 가 있거나 확장자가 있는 것."""
    if " " in s:
        return False
    return "/" in s or bool(re.search(r"\.(py|md|json|ts|yml|yaml|txt|css)$", s))


def _strip_note(s: str) -> str:
    """`app/src (consuming copy)` → `app/src`."""
    return re.sub(r"\s*\(.*\)\s*$", "", s).strip()


def ghost_team_paths(teams: dict, exists) -> list[tuple[str, str]]:
    """teams.json 이 대는 경로 중 리포에 없는 (팀, 경로). `planned` 는 제외."""
    bad = []
    for name, t in teams["teams"].items():
        for p in t.get("paths", []):
            if not exists(p):
                bad.append((name, p))
    for p in teams.get("shared", {}).get("paths", {}):
        if not exists(p):
            bad.append(("shared", p))
    return bad


def role_team_index(teams: dict) -> dict[str, str]:
    idx: dict[str, str] = {}
    for name, t in teams["teams"].items():
        for r in t.get("roles", []):
            idx[r] = name
    return idx


def roster_mismatch(teams: dict, roles: dict) -> list[str]:
    """두 파일이 서로를 잃은 곳. 실패 사유다."""
    known = {r["name"] for r in roles["roles"]}
    by_role = role_team_index(teams)
    read_only = set(teams.get("read_only_roles", []))
    bad = []
    for r, team in by_role.items():
        if r not in known:
            bad.append(f"teams.json 의 {team} 이 없는 역할을 댄다: {r}")
    for r in read_only:
        if r not in known:
            bad.append(f"teams.json 의 read_only_roles 에 없는 역할: {r}")
        if r in by_role:
            bad.append(f"{r} 가 read_only 이면서 {by_role[r]} 팀에도 있다")
    for r in sorted(known - set(by_role) - read_only):
        bad.append(f"roles.json 의 {r} 가 어느 팀에도 없다 (teams.json 에 넣거나 "
                   f"read_only_roles 에 적을 것)")
    return bad


def roster_drift(teams: dict, roles: dict, rules, exists) -> list[str]:
    """역할 `paths` 와 CODEOWNERS 가 어긋난 곳. 경고 사유다 — 정본이 그래프라서."""
    by_role = role_team_index(teams)
    handle = {name: t["github"] for name, t in teams["teams"].items()}
    out = []
    for role in roles["roles"]:
        name = role["name"]
        team = by_role.get(name)
        for raw in role.get("props", {}).get("paths", []) or []:
            p = _strip_note(raw)
            if not _looks_like_path(p):
                continue
            if not exists(p):
                # 다른 리포의 경로(`account/`·`site/`)는 여기서 판정할 수 없다.
                repos = role.get("props", {}).get("repos") or []
                if "Vinc" in repos and len(repos) == 1:
                    out.append(f"{name} 의 paths 가 없는 경로를 가리킨다: {raw}")
                continue
            if team is None:
                continue
            probe = p.rstrip("/") + "/_" if os.path.isdir(ROOT / p) else p
            got = owners_for(probe, rules)
            if got and handle[team] not in got:
                out.append(f"{name}({team}) 의 {raw} 는 {' '.join(got)} 소유다")
    return out


# ── 층 방향 ──────────────────────────────────────────────────────────────────

#: 아래 층은 위 층을 import 하지 않는다. `store`·`vinc_mcp` 가 `api` 를 부르던 두 곳을
#: 2026-09-30 에 걷었고(VIN-242: `procutil` 은 store 로 내리고, 재구축의 재큐는 인자로
#: 주입), 그 뒤로 다시 생기지 않게 여기서 잠근다. 함수 안 지연 import 도 잡는다 —
#: 순환을 피하려고 지연시킨 import 가 정확히 그 방향을 숨기던 것이었다.
#: `engines` 는 아직 어느 쪽에도 없다: `embed_pipeline`·`recall_ingest` 가 `api` 를 써서
#: `vinc_rag` 로 옮길 수 없고(VIN-244 는 워커·인코더·리랭커만 옮겼다), 반대로
#: `store/embedded_backend.py` 가 `engines.domain_hierarchy` 를 쓴다. 그것을 정리한 뒤
#: `store/` 의 금지에 `engines` 를 더한다.
#: `vinc_rag` 는 검색 코어(VIN-243)다. `api` 가 그것을 부르고, 그것은 `store`·`engines`
#: 만 부른다 — 공용 바닥 함수를 `store/querybase.py` 로 내린 이유가 이 한 줄이다.
LAYER_BANS: dict[str, tuple[str, ...]] = {
    "store/": ("api", "vinc_mcp", "vinc_rag"),
    "vinc_mcp/": ("api",),
    "vinc_rag/": ("api", "vinc_mcp"),
}

_IMPORT_RE = re.compile(r"^\s*(?:from\s+(\w+)(?:\.\w+)*\s+import\b|import\s+(\w+)(?:\.\w+)*)",
                        re.M)


def layer_violations(files: list[str], read) -> list[tuple[str, str]]:
    """(파일, 금지된 최상위 모듈). 문자열 검사라 정확하지는 않지만 그래서 오프라인이다."""
    bad = []
    for f in files:
        if not f.endswith(".py"):
            continue
        for prefix, banned in LAYER_BANS.items():
            if not f.startswith(prefix):
                continue
            for m in _IMPORT_RE.finditer(read(f)):
                top = m.group(1) or m.group(2)
                if top in banned:
                    bad.append((f, top))
    return bad


# ── 닿은 팀 (지표 원천) ──────────────────────────────────────────────────────

def teams_touched(changed: list[str], rules, teams: dict) -> list[str]:
    by_handle = {t["github"]: name for name, t in teams["teams"].items()}
    hit = set()
    for f in changed:
        for h in owners_for(f, rules):
            hit.add(by_handle.get(h, h))
    return sorted(hit)


# ── 실행 ─────────────────────────────────────────────────────────────────────

def _git(*args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def tracked_files() -> list[str]:
    return [f for f in _git("ls-files", "-z").split("\0") if f]


def main(argv: list[str] | None = None) -> int:
    if not CODEOWNERS.exists():
        print(f"[FAIL] {CODEOWNERS.relative_to(ROOT)} 가 없다 — "
              f"scripts/agents_sync.py --gen 을 돌린다")
        return 1
    teams = json.loads(TEAMS.read_text(encoding="utf-8"))
    roles = json.loads(ROLES.read_text(encoding="utf-8"))
    rules = parse_codeowners(CODEOWNERS.read_text(encoding="utf-8"))
    files = tracked_files()
    exists = lambda p: (ROOT / p).exists()  # noqa: E731

    bad = 0

    for team, p in ghost_team_paths(teams, exists):
        print(f"[GHOST] teams.json {team} 의 경로가 리포에 없다: {p}")
        bad += 1

    for msg in roster_mismatch(teams, roles):
        print(f"[MISMATCH] {msg}")
        bad += 1

    orphans = unowned(files, rules)
    for f in orphans:
        print(f"[UNOWNED] {f}")
    if orphans:
        print(f"[FAIL] 주인 없는 파일 {len(orphans)}건 — agents/teams.json 에 경로를 "
              f"더하고 agents_sync.py --gen")
        bad += len(orphans)

    read = lambda f: (ROOT / f).read_text(encoding="utf-8", errors="replace")  # noqa: E731
    for f, top in layer_violations(files, read):
        print(f"[LAYER] {f} 가 위 층 `{top}` 를 import 한다 — 아래 층이 위 층을 모르게 "
              f"인자로 주입하거나 그 코드를 아래로 내릴 것 (VIN-242)")
        bad += 1

    for msg in roster_drift(teams, roles, rules, exists):
        print(f"[DRIFT] {msg} — 그래프의 역할 노드를 고치고 --pull (VIN-241)")

    base, head = os.environ.get("BASE"), os.environ.get("HEAD")
    if base and head:
        try:
            changed = [f for f in _git("diff", "--name-only", base, head).splitlines() if f]
        except subprocess.CalledProcessError:
            changed = []
            print(f"::warning::변경 목록을 못 구했다({base}..{head}) — 닿은 팀을 비운다")
        hit = teams_touched(changed, rules, teams)
        print(f"── 닿은 팀({len(changed)}파일): {' '.join(hit) or '(없음)'}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(f"teams={','.join(hit)}\n")
                fh.write(f"teams_count={len(hit)}\n")

    print(f"\n{'소유권 이상 없음' if not bad else f'문제 {bad}건'} "
          f"(파일 {len(files)}, 팀 {len(teams['teams'])})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
