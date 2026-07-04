#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반입 금지 게이트 — WO-MEM-SA-001 §3 하드 룰 (F1–F7).

Vinc 는 memory 데이터플레인을 흡수하되 **과금·학습 프레이밍·구 명칭**을 반입하지
않는다. 본 게이트가 그 하드 룰의 실행체(executable SSoT)다.

- 인간 가독 명세(정본): `Vinc-docs vinc/A/vinc.a.terms.md` §4–6.
- 본 스크립트의 패턴 표는 그 명세의 코드 미러다 — **둘을 동기화**한다
  (vinc.a.terms 변경 시 본 표도 갱신, revamp catalog↔RULES 동기 모델과 동일).

stdlib 전용. exit 0=PASS(0건) · 1=위반 발견 · 3=내부 오류.

    python scripts/gate_forbidden.py [--root .] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── 검사 대상 파일 확장자(텍스트) ─────────────────────────────────────────
_TEXT_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".toml", ".json", ".yml",
    ".yaml", ".md", ".txt", ".html", ".css", ".cfg", ".ini", ".sh",
}

# ── 검사 제외 경로(디렉토리 조각) ─────────────────────────────────────────
_EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "__pycache__",
    ".venv", "venv", "models",  # models/ = 임베딩 캐시(바이너리)
}

# 자기 자신·정책/계획 문서는 금지어를 '정의·인용'하므로 제외(오탐 회피).
# 이들은 Vinc-docs(별도 레포)에 있어 기본 트리에 없지만, 레포 동거 시 대비.
_ALLOWLIST_SUFFIXES = (
    "scripts/gate_forbidden.py",
    "tests/test_gate_forbidden.py",  # 테스트가 금지어를 fixture 로 사용
    "vinc.a.terms.md",               # 금지 어휘 정본
    "UI_INVENTORY_AUDIT.md",         # 흡수 매트릭스가 금지 카피를 예시 인용
    "PLAN.md",                       # 실행 계획이 F1/F7 토큰을 규칙으로 명명
)


@dataclass
class Rule:
    gate: str            # F1 / F2 / F7
    label: str
    pattern: re.Pattern
    note: str = ""
    # code-provenance 예외: 주석/문서 계보 표기에서만 허용되는 상위 토큰이 있으면
    # 그 상위 매치는 위반에서 제외한다(F7 의 weg-9000-memory 등).
    allow_within: re.Pattern | None = None


# ── F1 과금·계측 (vinc.a.terms §4) — 코드·개념·UI 불문, 전체 0건 ──────────
_F1 = [
    Rule("F1", "usage_record", re.compile(r"usage_record")),
    Rule("F1", "token_margin", re.compile(r"token_margin")),
    Rule("F1", "billing", re.compile(r"(?i)\bbilling\b")),
    Rule("F1", "quota", re.compile(r"(?i)\bquota\b")),
    Rule("F1", "plan.kind", re.compile(r"plan\.kind")),
]

# ── F2 학습 프레이밍 카피 (vinc.a.terms §5) — UI 문자열 0건 ────────────────
# 코드 식별자 learner_* 는 D6 처리 전까지 허용(패턴에 포함하지 않음).
_F2 = [
    Rule("F2", "나의 기억", re.compile(r"나의\s*기억")),
    Rule("F2", "학습을(자산…)", re.compile(r"학습을")),
    Rule("F2", "배움", re.compile(r"배움")),
]

# ── F7 구 명칭 (vinc.a.terms §6 · 설계 §0) — 사용자 가시 식별자 0건 ────────
# weg-memory 는 금지. weg-9000-memory 는 코드 출처 표기에서만 허용.
# weg-memory 는 weg-9000-memory 의 부분문자열이 아니므로 단순 검색으로 구분됨.
_F7 = [
    Rule("F7", "weg-memory", re.compile(r"weg-memory"),
         note="구 데이터 디렉토리·패키지 명칭 → vinc 로 대체"),
]


ALL_RULES = _F1 + _F2 + _F7


@dataclass
class Violation:
    gate: str
    label: str
    file: str
    line: int
    text: str


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = set(p.relative_to(root).parts)
        if parts & _EXCLUDE_DIRS:
            continue
        if p.suffix.lower() not in _TEXT_EXT:
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel.endswith(s) for s in _ALLOWLIST_SUFFIXES):
            continue
        yield p, rel


def scan(root: Path) -> list[Violation]:
    out: list[Violation] = []
    for path, rel in _iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            for rule in ALL_RULES:
                for m in rule.pattern.finditer(line):
                    if rule.allow_within and rule.allow_within.search(line):
                        # 상위 허용 토큰 내부 매치인지 확인
                        if any(a.start() <= m.start() and m.end() <= a.end()
                               for a in rule.allow_within.finditer(line)):
                            continue
                    out.append(Violation(rule.gate, rule.label, rel, i,
                                         line.strip()[:120]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="반입 금지 게이트 (WO §3 F1–F7)")
    ap.add_argument("--root", default=".", help="검사 루트(기본 cwd)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"[gate_forbidden] 경로 없음: {root}", file=sys.stderr)
        return 3

    violations = scan(root)

    if args.json:
        print(json.dumps({
            "root": str(root),
            "status": "PASS" if not violations else "FAIL",
            "count": len(violations),
            "violations": [v.__dict__ for v in violations],
        }, ensure_ascii=False, indent=2))
    else:
        if not violations:
            print(f"[gate_forbidden] PASS — 금지어 0건 (검사 룰 {len(ALL_RULES)}종, root={root})")
        else:
            print(f"[gate_forbidden] FAIL — {len(violations)}건 위반:")
            for v in violations:
                print(f"  [{v.gate}] {v.label}  {v.file}:{v.line}  {v.text}")
            print("\n정본: Vinc-docs vinc/A/vinc.a.terms.md §4–6")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
