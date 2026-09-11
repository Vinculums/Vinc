#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""버전 동기화·검증 게이트 — 릴리스 버전이 박히는 자리를 한 번에 다룬다.

배경: 버전 범프는 여태 **손으로 6곳**을 고치는 일이었고, 어디를 고쳐야 하는지
열거한 표는 DEPLOYMENT.md 개정 과정에서 한때 소실되기도 했다. 그 결과 실제로
어긋났다 — `app/package-lock.json` 이 7릴리스(0.6.0→0.6.7) 동안 방치됐다.
`Cargo.lock` 은 체크리스트에 있는데 `package-lock.json` 은 빠진 **비대칭**이
원인이고, `npm ci` 는 의존성 트리만 대조하므로 **조용히 통과**한다.

이 스크립트가 그 체크리스트의 실행체(executable SSoT)다.

    python scripts/version_sync.py --check          # 검증만(CI 게이트)
    python scripts/version_sync.py --check --expect 0.6.8
    python scripts/version_sync.py --set 0.6.8      # 모든 자리 일괄 갱신

exit 0=일치 · 1=불일치/실패 · 3=내부 오류.

⚠ `version_info.txt` 는 단일 파일 안에 표기가 **3가지**다(`(0, 6, 7, 0)` 4튜플 ·
`'0.6.7.0'` 점 4개 · 주석의 `(현재 0.6.7)`). 정규식을 하나로 뭉뚱그리면 조용히
일부만 바뀌므로 형식별로 따로 다룬다.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class Site:
    """버전이 박히는 한 자리 — 읽기(패턴)와 쓰기(치환)를 같은 규칙으로 묶는다."""
    path: Path
    pattern: re.Pattern[str]
    label: str
    # 치환 시 버전을 어떤 표기로 넣을지. None 이면 semver 그대로.
    render: str = "semver"

    def read(self, text: str) -> str | None:
        m = self.pattern.search(text)
        return m.group("v") if m else None

    def write(self, text: str, version: str) -> str:
        want = _render(version, self.render)
        return self.pattern.sub(
            lambda m: m.group(0).replace(m.group("v"), want, 1), text, count=1)


def _render(version: str, kind: str) -> str:
    major, minor, patch = version.split(".")
    if kind == "semver":
        return version
    if kind == "quad":                      # 0.6.7 → 0.6.7.0
        return f"{version}.0"
    if kind == "tuple":                     # 0.6.7 → 0, 6, 7, 0
        return f"{major}, {minor}, {patch}, 0"
    raise ValueError(f"unknown render kind: {kind}")


def _sites() -> list[Site]:
    """버전이 박히는 자리 전부. 순서 = 사람이 확인하기 좋은 순서
    (앱 → 셸 → 패키징 → 기록 → MCP).

    개수를 문장으로 적지 않는다. 여기 적혀 있던 "7곳"은 자리가 늘어나는 동안
    갱신되지 않아 거짓이 됐고, 그 숫자를 믿고 목록을 안 읽으면 빠진 자리를 못 본다
    (실제로 `vinc_mcp/__init__.py` 가 그렇게 빠져 있었다). 세는 것은 `cmd_check` 의
    출력이 한다."""
    return [
        Site(ROOT / "app/package.json",
             re.compile(r'"version"\s*:\s*"(?P<v>\d+\.\d+\.\d+)"'),
             "app/package.json"),
        # package-lock 은 최상위 + packages."" 두 곳에 같은 버전이 박힌다.
        # (여태 체크리스트에서 빠져 7릴리스 방치된 자리 — count=2 로 둘 다 잡는다.)
        Site(ROOT / "app/package-lock.json",
             re.compile(r'"version"\s*:\s*"(?P<v>\d+\.\d+\.\d+)"'),
             "app/package-lock.json"),
        Site(ROOT / "app/src-tauri/tauri.conf.json",
             re.compile(r'"version"\s*:\s*"(?P<v>\d+\.\d+\.\d+)"'),
             "app/src-tauri/tauri.conf.json"),
        Site(ROOT / "app/src-tauri/Cargo.toml",
             re.compile(r'^version\s*=\s*"(?P<v>\d+\.\d+\.\d+)"', re.M),
             "app/src-tauri/Cargo.toml"),
        # Cargo.lock 은 name = "vinc" 바로 뒤의 version 만 대상(다른 crate 무관).
        Site(ROOT / "app/src-tauri/Cargo.lock",
             re.compile(r'name\s*=\s*"vinc"\nversion\s*=\s*"(?P<v>\d+\.\d+\.\d+)"'),
             "app/src-tauri/Cargo.lock"),
        Site(ROOT / "packaging/version_info.txt",
             re.compile(r"filevers=\((?P<v>\d+,\s*\d+,\s*\d+,\s*\d+)\)"),
             "version_info.txt filevers", render="tuple"),
        Site(ROOT / "packaging/version_info.txt",
             re.compile(r"prodvers=\((?P<v>\d+,\s*\d+,\s*\d+,\s*\d+)\)"),
             "version_info.txt prodvers", render="tuple"),
        Site(ROOT / "packaging/version_info.txt",
             re.compile(r"StringStruct\('FileVersion',\s*'(?P<v>[\d.]+)'\)"),
             "version_info.txt FileVersion", render="quad"),
        Site(ROOT / "packaging/version_info.txt",
             re.compile(r"StringStruct\('ProductVersion',\s*'(?P<v>[\d.]+)'\)"),
             "version_info.txt ProductVersion", render="quad"),
        Site(ROOT / "packaging/version_info.txt",
             re.compile(r"현재 (?P<v>\d+\.\d+\.\d+)\)"),
             "version_info.txt 주석"),
        # MCP 핸드셰이크가 알리는 버전. 이 자리가 목록에 없어서 **v0.3.0 에 멈춘 채
        # 제품이 0.9.1 이 될 때까지 아무도 몰랐다**(2026-09-11 측정). 어시스턴트가
        # 화면에 그리는 유일한 Vinc 버전이라 사람에게 보이는 자리이면서, 어긋나도
        # 아무 기능이 깨지지 않아 증상이 없는 종류다.
        Site(ROOT / "vinc_mcp/__init__.py",
             re.compile(r'^__version__\s*=\s*"(?P<v>\d+\.\d+\.\d+)"', re.M),
             "vinc_mcp/__init__.py serverInfo"),
    ]


def _normalize(raw: str) -> str:
    """어떤 표기로 읽혔든 semver 로 되돌린다(비교는 항상 semver 기준)."""
    if "," in raw:                                   # "0, 6, 7, 0"
        parts = [p.strip() for p in raw.split(",")]
        return ".".join(parts[:3])
    parts = raw.split(".")
    return ".".join(parts[:3])                       # "0.6.7.0" → "0.6.7"


def collect() -> tuple[dict[str, str], list[str]]:
    """각 자리의 현재 버전(semver 정규화)과 읽기 실패 목록."""
    found: dict[str, str] = {}
    missing: list[str] = []
    for site in _sites():
        if not site.path.exists():
            missing.append(f"{site.label} (파일 없음: {site.path})")
            continue
        raw = site.read(site.path.read_text(encoding="utf-8"))
        if raw is None:
            missing.append(f"{site.label} (패턴 불일치 — 형식이 바뀌었는지 확인)")
            continue
        found[site.label] = _normalize(raw)
    return found, missing


def cmd_check(expect: str | None) -> int:
    found, missing = collect()
    if missing:
        print("[version_sync] FAIL — 읽지 못한 자리:")
        for m in missing:
            print(f"  {m}")
        return 1

    versions = set(found.values())
    if len(versions) != 1:
        print(f"[version_sync] FAIL — 버전 불일치 {sorted(versions)}:")
        for label, v in found.items():
            mark = " " if len(versions) == 1 else ("!" if v != max(versions, key=list(found.values()).count) else " ")
            print(f" {mark} {v:<10} {label}")
        return 1

    current = versions.pop()
    if expect is not None and current != expect:
        print(f"[version_sync] FAIL — 기대 {expect} · 실제 {current}")
        print("  태그와 파일이 어긋난 채 빌드되면 업데이트 토스트가 무한 반복된다")
        print("  (latest.json 은 git 태그에서, 클라이언트 자기 버전은 tauri.conf.json 에서 온다).")
        return 1

    print(f"[version_sync] PASS — {len(found)}곳 전부 {current}")
    return 0


def cmd_set(version: str) -> int:
    if not SEMVER.match(version):
        print(f"[version_sync] 잘못된 버전 형식: {version} (기대: X.Y.Z)")
        return 3

    # 같은 파일에 여러 자리가 있으므로 파일 단위로 모아 한 번에 쓴다.
    by_file: dict[Path, list[Site]] = {}
    for site in _sites():
        by_file.setdefault(site.path, []).append(site)

    changed: list[str] = []
    for path, sites in by_file.items():
        if not path.exists():
            print(f"[version_sync] 파일 없음: {path}")
            return 3
        text = original = path.read_text(encoding="utf-8")
        for site in sites:
            # package-lock 은 같은 패턴이 2곳(최상위 + packages."") — 둘 다 바꾼다.
            count = 2 if path.name == "package-lock.json" else 1
            want = _render(version, site.render)
            text = site.pattern.sub(
                lambda m: m.group(0).replace(m.group("v"), want, 1), text, count=count)
        if text != original:
            path.write_text(text, encoding="utf-8", newline="")
            changed.append(str(path.relative_to(ROOT)))

    print(f"[version_sync] {version} 로 갱신 — 파일 {len(changed)}개")
    for c in changed:
        print(f"  {c}")
    print()

    # 🔴 **태그 밖에 무엇이 있는지 여기서 보여 준다.** 2026-09-11: v0.9.1 을 찍을 때
    # 셸 크롬 개편 30커밋이 머지도 푸시도 안 된 브랜치에 있었고, `main` 만 세는 판정에는
    # 보이지 않았다. 릴리스에 들어간 줄 알았던 그 작업은 설치본에 한 글자도 없었다.
    #
    # 자리가 여기인 이유: **범프는 릴리스 내용을 정하는 그 순간**이고, 이 명령을 거치지
    # 않고 태그를 찍는 길이 없다(릴리스 게이트가 자리 열한 곳의 일치를 요구한다).
    # 나중에 CI 어디에 붙이면 로그에 묻히고, 태그 런에 붙이면 이미 늦다.
    #
    # 막지 않는다 — 실험 브랜치가 태그를 볼모로 잡으면 안 되고, 판단은 사람이 한다.
    # 실패해도 범프를 되돌리지 않는다(목록을 못 냈다고 버전 갱신이 무효가 되면 그것이
    # 더 나쁘다). 자세한 것은 `scripts/unmerged_work.py`.
    try:
        # 이 파일은 스크립트로 불린다(`python scripts/version_sync.py`). 그러면 sys.path
        # 의 첫 항목이 `scripts/` 라 `scripts` 패키지를 못 찾는다 — 리포 루트를 넣어야
        # 한다. 실측으로 물렸다: 처음 판이 ModuleNotFoundError 로 떨어졌고, 아래 except
        # 가 그것을 경고로 바꿔 범프는 살렸지만 목록은 못 냈다.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts import unmerged_work
        rows, saw = unmerged_work.survey()
        print(unmerged_work.render(rows, saw, markdown=False), end="")
        print()
    except Exception as exc:                                  # noqa: BLE001
        print(f"[version_sync] ⚠ 태그 밖 목록을 못 냈다 — {type(exc).__name__}: {exc}")
        print("  범프 자체는 끝났다. `python scripts/unmerged_work.py` 를 손으로 돌려 볼 것.")

    return cmd_check(version)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="릴리스 버전 자리 동기화·검증")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="검증만(CI 게이트)")
    g.add_argument("--set", metavar="X.Y.Z", help="모든 자리를 이 버전으로 갱신")
    ap.add_argument("--expect", metavar="X.Y.Z",
                    help="--check 와 함께: 이 버전과 같아야 통과(태그 대조용)")
    args = ap.parse_args(argv)

    try:
        if args.set:
            return cmd_set(args.set)
        return cmd_check(args.expect)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[version_sync] 내부 오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
