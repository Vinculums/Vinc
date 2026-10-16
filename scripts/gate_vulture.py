#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""죽은 파이썬 게이트 — vulture 를 신뢰도 80 이상으로 돌린다.

    python -X utf8 scripts/gate_vulture.py
    VINC_VULTURE_PATH=/some/dir python -X utf8 scripts/gate_vulture.py   # vulture 가 venv 밖에 있을 때

exit 0=통과 · 1=허용 목록 밖 발견 · 2=vulture 를 못 돌림.

검사 대상은 테스트가 아닌 패키지 전부(`TARGETS`)다. 80 은 vulture 가 "확실하다"고
말하는 구간이다(미사용 import·지역 변수·도달 불가 코드). 함수·클래스는 60 이라 여기
안 걸린다 — 그쪽은 동적 디스패치(`do_PUT`, pydantic 검증자, LangGraph 훅)와 갈리지
않아서 게이트로 세우면 허용 목록이 곧 소음이 된다.

허용 목록은 `scripts/vulture-allow.txt` 다. 한 줄에 `<경로 글롭> <이름>  # 이유`.
이유 없는 줄은 받지 않는다(왜 남겼는지 모르는 예외는 다음 사람이 못 지운다).

⚠ vulture 는 `# type:` 로 시작하는 주석을 타입 주석으로 읽어 그 파일을 통째로
못 읽는다(`invalid syntax`). 그런 파일은 **검사되지 않은 것**이므로 경고로 이름을
남긴다 — 실패로 세면 주석 하나 때문에 게이트가 꺼진다.
근거: [[vinc.plan.code-cleanup-2026-10-16]]
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW = ROOT / "scripts" / "vulture-allow.txt"
MIN_CONFIDENCE = 80

#: 테스트가 아닌 파이썬 전부. `integrations/*` 는 패키지마다 `python/src` 아래에 산다.
TARGETS = ("api", "store", "engines", "vinc_mcp", "vinc_rag", "rag_eval", "packaging",
           "integrations/*/python/src", "scripts")

#: `path:line: message (NN% confidence)` — vulture 의 발견 한 줄.
FINDING = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<msg>.+?) \((?P<conf>\d+)% confidence")
#: 발견 문장 안의 이름(`unused variable 'newurl'`). 도달 불가 코드처럼 이름이 없는 것도 있다.
NAME = re.compile(r"'([^']+)'")


def load_allow(path: Path = ALLOW) -> list[tuple[str, str]]:
    """허용 목록을 (경로 글롭, 이름) 으로. 이유(`#` 뒤)가 없는 줄은 ValueError."""
    out: list[tuple[str, str]] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, sep, why = line.partition("#")
        fields = body.split()
        if len(fields) != 2 or not sep or not why.strip():
            raise ValueError(f"{path.name}:{n}: '<경로 글롭> <이름>  # 이유' 꼴이 아니다: {raw!r}")
        out.append((fields[0], fields[1]))
    return out


def allowed(path: str, name: str | None, allow: list[tuple[str, str]]) -> bool:
    p = path.replace("\\", "/")
    return name is not None and any(
        fnmatch.fnmatch(p, g) and name == want for g, want in allow)


def targets(root: Path = ROOT) -> list[str]:
    out: list[str] = []
    for t in TARGETS:
        out += sorted(Path(m).relative_to(root).as_posix()
                      for m in glob.glob(str(root / t)) if Path(m).is_dir())
    return out


def run_vulture(root: Path = ROOT) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    extra = env.get("VINC_VULTURE_PATH")
    if extra:   # venv 에 pip 가 없는 기계: 다른 자리에 깔린 vulture 를 쓴다
        env["PYTHONPATH"] = extra + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUTF8"] = "1"   # 윈도우 콘솔 코드페이지로 쓰면 한국어 경로·메시지가 깨진다
    cmd = [sys.executable, "-m", "vulture", "--min-confidence", str(MIN_CONFIDENCE), *targets(root)]
    return subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def judge(stdout: str, allow: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str]]:
    """vulture 출력을 (실패, 허용됨, 못 읽은 파일) 로 가른다."""
    bad, ok, skipped = [], [], []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = FINDING.match(line)
        if not m:
            skipped.append(line)
            continue
        n = NAME.search(m["msg"])
        (ok if allowed(m["path"], n.group(1) if n else None, allow) else bad).append(line)
    return bad, ok, skipped


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        allow = load_allow()
    except ValueError as e:
        print(f"[gate_vulture] 허용 목록 오류: {e}", file=sys.stderr)
        return 2
    r = run_vulture()
    if "No module named vulture" in (r.stderr or ""):
        print("[gate_vulture] vulture 가 없다 — `pip install vulture` 또는 "
              "VINC_VULTURE_PATH 로 설치 자리를 준다", file=sys.stderr)
        return 2
    if r.returncode not in (0, 1, 3):   # 1=못 읽은 파일 있음, 3=발견 있음
        print(f"[gate_vulture] vulture 실패 rc={r.returncode}\n{r.stderr}", file=sys.stderr)
        return 2
    # 못 읽은 파일(`invalid syntax`)은 판에 따라 stdout 이나 stderr 로 나온다. 둘 다 본다.
    bad, ok, skipped = judge(r.stdout + "\n" + r.stderr, allow)
    for line in skipped:
        print(f"[gate_vulture] 경고 — 검사되지 않음: {line}")
    if bad:
        print(f"[gate_vulture] FAIL — 신뢰도 {MIN_CONFIDENCE}% 이상 미사용 {len(bad)}건 "
              f"(지우거나, 동적 디스패치면 scripts/vulture-allow.txt 에 이유와 함께 올린다)")
        for line in bad:
            print(f"  {line}")
        return 1
    print(f"[gate_vulture] PASS — 허용 목록 밖 0건 (허용 {len(ok)}건, 못 읽은 파일 {len(skipped)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
