#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반입 금지 게이트 — WO-MEM-SA-001 §3 하드 룰 (F1–F8).

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

# 주석 제거기는 한국어 카피 재고 조사와 **같은 것**을 써야 한다 — 두 벌이 되면
# F9 가 위반이라 부르는 줄과 패리티 테스트가 위반이라 부르는 줄이 갈린다.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ko_copy_inventory import strip_comments  # noqa: E402

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
    # `.claude/worktrees/*` = 격리 에이전트 세션이 남긴 구 git worktree 체크아웃
    # (app/·tests/ 를 그대로 복제해 들고 있다). 같은 이유로 제품 표면이 아니라
    # 부산물이다 — F8 추가 후 실측 1655건이 전부 여기 한 곳이었다(2026-08-15).
    "worktrees",
    # `vinc.egg-info/` = setuptools 가 빌드 때 자동 생성하는 매니페스트(SOURCES.txt
    # 등). dist/build 와 같은 신분 — 소스 파일 이름이 바뀌면 다음 빌드에서 저절로
    # 갱신되므로 손으로 맞출 대상이 아니다.
    "vinc.egg-info",
}

# 자기 자신·정책/계획 문서는 금지어를 '정의·인용'하므로 제외(오탐 회피).
# ⚠ `vinc.a.terms.md` 는 **이행 전 파일명**이고 재작성본은 `vinc.canon.terminology.md`
# 다. 이름이 갈린 채로 두면 같은 문서가 새 이름으로 트리에 들어왔을 때 게이트가
# 터진다 — 실제로 2026-08-11 에 그렇게 터졌다. 둘 다 싣는다(구본은 아카이브에 남아 있다).
#
# F8 추가분 — 이 넷은 '정의·인용'이 아니라 다른 이유로 뺀다:
#   · CHANGELOG.md·README.md 는 그때 그 이름으로 실제로 출시된 이력이다(과거 커밋
#     메시지와 같은 신분 — F7-d Vinclums 예외와 동일 논리). 다시 쓰지 않는다.
#   · starter-packs 세 파일은 제품 표면이 아니라 예시 지식 콘텐츠이고, 거기 나오는
#     "atlas" 는 전부 무관한 일반명사(책 제목 "Atlas of AI", "a time atlas" 등 —
#     실측 2026-08-15, 셋 다 확인함). 서피스명과 우연히 겹치는 영어 단어일 뿐이다.
#: **루트에 고정된** 제외. `_EXCLUDE_DIRS` 와 나눈 이유가 실측이다: 그쪽은
#: `set(parts) & _EXCLUDE_DIRS` 라 **경로 어디에 있든** 그 이름을 뺀다. 거기에 `work`
#: 를 넣으면 `starter-packs/work/` 까지 빠지는데, 그것은 gitignore 된 부산물이 아니라
#: **추적되고 출하되는 시드 코퍼스 123파일**이고 이 게이트 스캔 대상 596 중 120, 20% 다.
#: 그 디렉터리가 관할이라는 증거는 아래 허용목록에 그 안의 파일 셋이 이름으로 올라
#: 있다는 것이다(미분기하의 "atlas of charts") — 이름 매칭으로 빼면 그 셋이 조용히
#: 죽은 코드가 되고, 새로 들어오는 스타터팩 문서의 금지 어휘는 아무도 안 본다.
#:
#: 여기 둘은 세션 부산물이다(.gitignore, 커밋 4698e6c). `models/` 와 같은 이유로 뺀다:
#: 다국어 토크나이저의 어휘가 금지 어휘를 정당하게 담는다(실측 2026-09-04:
#: `.cache/embed/baked-*/tokenizer.json` 에 `"▁Atlas"` · `"quvvatlash"` 6건). CI 는
#: 굽기를 안 해서 초록이고 **모델을 구운 로컬만 빨갛다** — 그 상태가 이어지면 사람이
#: 이 게이트를 잡음으로 배우고, 그러면 진짜 금지어가 지나간다.
#:
#: git 이 무시하는 것을 통째로 건너뛰지 않는 이유: 무시되지만 **배포에는 실리는**
#: 산출물이 있을 수 있고, 그런 파일의 금지 어휘는 여전히 사용자에게 닿는다.
_EXCLUDE_ROOTS = (".cache/", "work/")


_ALLOWLIST_SUFFIXES = (
    "scripts/gate_forbidden.py",
    "tests/test_gate_forbidden.py",  # 테스트가 금지어를 fixture 로 사용
    "vinc.a.terms.md",               # 금지 어휘 정본 (구 파일명)
    "vinc.canon.terminology.md",     # 금지 어휘 정본 (재작성본)
    "UI_INVENTORY_AUDIT.md",         # 흡수 매트릭스가 금지 카피를 예시 인용
    "PLAN.md",                       # 실행 계획이 F1/F7 토큰을 규칙으로 명명
    "CHANGELOG.md",
    "README.md",
    "CLAUDE.md",                     # F8 를 '정의·인용'한다 — 위 vinc.a.terms.md 와 동일 사유
    "starter-packs/work/art/04-digital-AI-aesthetics.md",
    "starter-packs/work/art/02-case-applications-2.md",
    "starter-packs/work/art/01-case-applications.md",
)


@dataclass
class Rule:
    gate: str            # F1 / F2 / F7 / F8 / F9
    label: str
    pattern: re.Pattern
    note: str = ""
    # code-provenance 예외: 주석/문서 계보 표기에서만 허용되는 상위 토큰이 있으면
    # 그 상위 매치는 위반에서 제외한다(F7 의 weg-9000-memory 등).
    allow_within: re.Pattern | None = None
    # 경로 한정: 이 접두사로 시작하는 파일에만 건다(빈 값이면 리포 전체).
    scope: tuple[str, ...] = ()
    # 주석을 걷어 낸 코드만 본다. 이 리포의 주석 언어는 한국어라(CLAUDE.md),
    # 사용자 가시 문자열만 겨냥하는 규칙은 반드시 이 모드여야 한다.
    code_only: bool = False


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

# ── F8 구 서피스명 atlas (vinc.canon.terminology §7) — 현재 코드·UI·라우트·문서 0건 ──
# 지도 서피스(구명 Atlas)를 View 로 개명(발주자 지시 2026-08-15). 대소문자 무관
# 부분일치로 잡는다(단어경계를 걸면 AtlasView·atlasScene·atlas3d 같은 파생
# 식별자를 못 잡는다 — 실제 위반의 대부분이 이 형태다). 한글 음역은 별도 규칙.
# CHANGELOG.md·README.md 의 과거 릴리스 기록, starter-packs 의 무관한 일반명사
# 용례는 파일 단위로 허용목록에서 뺀다(위 _ALLOWLIST_SUFFIXES 참조).
# main.ts 의 ALIAS 항목(`atlas: "view"`)은 예외다 — weg-9000-memory 와 같은 신분:
# 구 해시 북마크를 새 표면으로 되돌리는 하위호환 표가 그 옛 이름을 실제로 담고
# 있어야 작동한다(surface-tree §3, "이 항목들은 무기한 유지된다"). 백틱으로 감싼
# 인라인 코드 인용(`atlas`·`#atlas/...`)과 그 표 항목 자체만 허용한다.
_ATLAS_ALIAS_OK = re.compile(r"`#?atlas[^`]*`|\batlas:\s*\"view\"")
_F8 = [
    Rule("F8", "atlas", re.compile(r"atlas", re.IGNORECASE),
         note="구 지도 서피스명 → view", allow_within=_ATLAS_ALIAS_OK),
    Rule("F8", "아틀라스", re.compile(r"아틀라스"),
         note="구 지도 서피스명(한글 음역) → 뷰"),
]


# ── F9 한국어 사용자 가시 어휘 (vinc.spec.ui-copy-ko §4) — app/src 문자열 0건 ──
# 한국어 덱은 번역이 아니라 저작이다(adr-design-042). 여기 여덟 중 앞 넷은 2026-07-12
# 재작성이 이미 걷어 낸 개발자 직역어이고(말뭉치→데이터 · 채굴→분석 · 표면→화면 ·
# 레일→사이드바, vinc.spec.ui-copy §4 표), 나머지는 호칭·구명이다.
#
# ⚠ 이 넷(표면·레일·관문 등)은 **주석과 문서에서는 정상 어휘**다 — "제품 표면",
# "레일은 좁다" 는 이 리포가 자기 코드를 설명하는 말이고 화면에 나가지 않는다.
# 그래서 F9 만 `code_only` 로 주석을 걷어 내고 `scope` 로 app/src 에 가둔다.
# 리포 전체에 거는 순간 주석 수백 건이 위반으로 뜨고, 그러면 게이트를 끄게 된다.
#
# `아틀라스` 는 여기 다시 적지 않는다 — F8-b 가 이미 리포 전체에서 금지하므로
# (주석·문서 포함) 더 강하다. 같은 줄을 두 번 세지 않으려고 뺀다.
# `온톨로지` 는 **보류**다(금지 아님). 영어 쪽 제품 명사 결정이 아직 안 났고
# (vinc.spec.ui-copy-ko §9.2), 한국어는 그 결정을 따라간다. W8 항목 11 이후 재검토.
_KO_SCOPE = ("app/src/",)
_F9 = [
    Rule("F9", w, re.compile(w), note=note, scope=_KO_SCOPE, code_only=True)
    for w, note in (
        ("말뭉치", "corpus 직역 → 데이터"),
        ("채굴", "mine 직역 → 분석·추가"),
        ("표면", "surface 직역 → 화면"),
        ("레일", "rail 직역 → 사이드바"),
        ("관문", "gateway 음역 → 게이트웨이"),
        ("당신", "2인칭 금지(§3) → 내·생략"),
        ("귀하", "2인칭 금지(§3) → 내·생략"),
    )
]


ALL_RULES = _F1 + _F2 + _F7 + _F8 + _F9


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
        if rel.startswith(_EXCLUDE_ROOTS):
            continue
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
        rules = [r for r in ALL_RULES
                 if not r.scope or any(rel.startswith(s) for s in r.scope)]
        if not rules:
            continue
        lines = text.splitlines()
        # 주석을 걷어 낸 사본은 `code_only` 규칙이 하나라도 살아 있을 때만 만든다.
        code = ({i: c for i, c in strip_comments(text)}
                if any(r.code_only for r in rules) else {})
        for i, line in enumerate(lines, 1):
            for rule in rules:
                subject = code.get(i, "") if rule.code_only else line
                for m in rule.pattern.finditer(subject):
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
            print("\n정본: 그래프 문서 vinc.canon.terminology §4–7 (vinc_doc_get)")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
