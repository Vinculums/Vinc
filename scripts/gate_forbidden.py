#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반입 금지 게이트 — WO-MEM-SA-001 §3 하드 룰 (F1–F7).

Vinc 는 memory 데이터플레인을 흡수하되 **CP 계측 코드·학습 프레이밍·구 명칭**을
반입하지 않는다. 본 게이트가 그 하드 룰의 실행체(executable SSoT)다.

- 인간 가독 명세(정본): 그래프 문서 `vinc.canon.terminology` (`vinc_doc_get`).
  ⚠ 파일 사본을 정본으로 읽지 말 것. `_migration/` 안의 `vinc.canon.terminology.md`
  와 `export/vinc.a.terms.md` 는 이행 시점의 스냅샷이고, 후자는 v0.1 에서 동결돼
  F1 v0.2 개정(결제 카피 4종 해제)조차 없다.
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
    # `_migration/` = Vinc-docs 폐기(2026-08-11) 때 들여온 이행 원장·재작성본·잔여
    # 자산이다. **제품 표면이 아니라 이력**이라 이 게이트의 관할 밖이다. 그리고
    # 여기엔 금지 어휘 정본 자체가 들어 있어서 — 금지하려면 그 단어를 적어야 하므로 —
    # 검사하면 반드시 실패한다(실측: 16건, 그중 12건이 terminology 문서 한 벌).
    # 파일별 allowlist 로 막지 않는 이유: 이 디렉터리는 불변 아카이브이고, 목록을
    # 유지하는 순간 아래 `vinc.a.terms.md` 가 그랬듯 파일명이 바뀌면 조용히 샌다.
    "_migration",
}

# 자기 자신·정책/계획 문서는 금지어를 '정의·인용'하므로 제외(오탐 회피).
# ⚠ `vinc.a.terms.md` 는 **이행 전 파일명**이고 재작성본은 `vinc.canon.terminology.md`
# 다. 이름이 갈린 채로 두면 같은 문서가 새 이름으로 트리에 들어왔을 때 게이트가
# 터진다 — 실제로 2026-08-11 에 그렇게 터졌다. 둘 다 싣는다(구본은 아카이브에 남아 있다).
_ALLOWLIST_SUFFIXES = (
    "scripts/gate_forbidden.py",
    "tests/test_gate_forbidden.py",  # 테스트가 금지어를 fixture 로 사용
    "vinc.a.terms.md",               # 금지 어휘 정본 (구 파일명)
    "vinc.canon.terminology.md",     # 금지 어휘 정본 (재작성본)
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


# ── F1 계측 코드 반입 (vinc.a.terms §4) — CP 계측 코드 식별자 0건 ──
# v0.4 정정: 영어 단어 billing/quota 자체는 금지 아님(도메인 자유 문자열 오탐 회피),
# plan.kind 는 정본 QueryPlan.kind 와 충돌하므로 제외. F1 은 CP 계측 '코드 식별자'를
# 겨냥한다.
# vinc.a.terms v0.2(2026-08-02 · DEC AP-1): 결제 UI 카피 4종(과금·결제·구독·요금제)은
# **정식으로 해제**됐다. 여기서 이미 빠져 있던 상태를 명세가 따라온 것이다 — 유료 플랜과
# 결제 표면이 생겨 '자체 과금 표면 부재'라는 금지 근거가 소멸했다. 아래 세 식별자만이
# F1 의 본체이고 영구 금지다.
_F1 = [
    Rule("F1", "usage_record", re.compile(r"usage_record"),
         note="CP 사용량 계측 레코드"),
    Rule("F1", "token_margin", re.compile(r"token_margin"),
         note="CP 토큰 마진 계측"),
    Rule("F1", "UsageRecord", re.compile(r"\bUsageRecord\b"),
         note="CP 계측 클래스"),
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
    # 한국어·em-dash 출력이 Windows 기본 콘솔(cp949)에서 UnicodeEncodeError 로
    # 죽는다 — **위반 0건이어도** 결과를 찍다 크래시해 게이트를 로컬에서 못 쓴다
    # (실측). CI(Linux, UTF-8)는 무관하지만 개발자 로컬이 막히면 게이트를 안 돌리게
    # 되므로 출력 스트림을 UTF-8 로 고정한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

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
            print("\n정본: 그래프 문서 vinc.a.terms §4–6 (vinc_doc_get)")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
