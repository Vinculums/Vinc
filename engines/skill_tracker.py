#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스킬 실행 이력을 (:SkillExecution) 노드로 Neo4j 에 적재하고
스킬별 사용 통계를 분석해 출력한다.

사용:
    python skill_tracker.py --action log --skill think --workspace WORKSPACES/thinking/foo --mode COGNITIVE
    python skill_tracker.py --action stats
    python skill_tracker.py --action report
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# 스토어 추상화(설계 §4) — 드라이버 취득은 store.backend.open_store 단일 지점.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402


# ── dotenv 로드 ───────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    env_candidates = [
        Path(".env"),
        Path("../weg9000-hub/.env"),
        Path("../../weg9000-hub/.env"),
    ]
    for env_path in env_candidates:
        if env_path.exists():
            try:
                from dotenv import load_dotenv  # type: ignore
                load_dotenv(env_path, override=False)
            except ImportError:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        os.environ.setdefault(k, v)
            return


# ── 인수 파싱 ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="WEG-9000 스킬 실행 이력 추적기 - Neo4j :SkillExecution 노드 관리",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 스킬 실행 1건 기록
  python skill_tracker.py --action log \\
      --skill think --workspace WORKSPACES/thinking/foo \\
      --mode COGNITIVE --outcome success --duration-min 15

  # 스킬별 사용 통계 출력
  python skill_tracker.py --action stats

  # 전체 효과성 리포트 생성
  python skill_tracker.py --action report

  # dry-run (Neo4j 미적재)
  python skill_tracker.py --action log --skill think --workspace . --dry-run
        """,
    )
    p.add_argument("--action", required=True, choices=["log", "stats", "report"],
                   help="log: 실행 기록 적재 / stats: 통계 출력 / report: 리포트 생성")
    # log 전용
    p.add_argument("--skill", default=None,
                   help="(log 필수) 스킬명. 예: think, recall, decide")
    p.add_argument("--workspace", default=None,
                   help="(log 필수) 워크스페이스 경로")
    p.add_argument("--mode", default=None,
                   choices=["PM_PLANNING", "COGNITIVE", "LEARNING", "JOURNAL", "DECISION", "ROUTING"],
                   help="실행 모드")
    p.add_argument("--outcome", default="success",
                   choices=["success", "warn", "fail"],
                   help="실행 결과 (기본값: success)")
    p.add_argument("--duration-min", type=float, default=0,
                   help="소요 시간(분)")
    # 공통
    p.add_argument("--output", default=None,
                   help="(report 전용) 출력 파일 경로. 기본값: CONTEXT/memory/recall/skill-report-{date}.md")
    p.add_argument("--dry-run", action="store_true",
                   help="Neo4j 적재 없이 파싱 결과만 출력")
    return p


# ── Neo4j 연결 ───────────────────────────────────────────────────────────────

# ── 핵심 함수 ─────────────────────────────────────────────────────────────────

def log_execution(session, args: argparse.Namespace) -> str:
    """(:SkillExecution) 노드 CREATE (이력 누적 - MERGE 미사용)."""
    if not args.skill:
        print("[ERROR] --action log 시 --skill 이 필요합니다.", file=sys.stderr)
        sys.exit(1)
    if not args.workspace:
        print("[ERROR] --action log 시 --workspace 가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    exec_id = str(uuid.uuid4())[:8]
    now = datetime.now()

    session.run(
        """
        CREATE (n:SkillExecution {
          exec_id:      $exec_id,
          skill:        $skill,
          workspace:    $workspace,
          mode:         $mode,
          outcome:      $outcome,
          duration_min: $duration_min,
          date:         $date,
          timestamp:    $timestamp
        })
        """,
        exec_id=exec_id,
        skill=args.skill,
        workspace=args.workspace or "",
        mode=args.mode or "",
        outcome=args.outcome or "success",
        duration_min=args.duration_min or 0,
        date=now.strftime("%Y-%m-%d"),
        timestamp=now.isoformat(),
    )
    print(f"[완료] SkillExecution 기록: {args.skill} → exec_id={exec_id} | {args.outcome}")
    return exec_id


def print_stats(session) -> None:
    """스킬별 사용 통계를 표로 출력한다."""
    result = session.run(
        """
        MATCH (n:SkillExecution)
        RETURN n.skill                                                          AS skill,
               count(*)                                                         AS total,
               sum(CASE WHEN n.outcome = 'success' THEN 1 ELSE 0 END)          AS success,
               sum(CASE WHEN n.outcome = 'warn'    THEN 1 ELSE 0 END)          AS warn,
               sum(CASE WHEN n.outcome = 'fail'    THEN 1 ELSE 0 END)          AS fail,
               avg(n.duration_min)                                              AS avg_min,
               max(n.date)                                                      AS last_used
        ORDER BY total DESC
        """
    )
    rows = list(result)

    if not rows:
        print("[INFO] 기록된 SkillExecution 없음. --action log 로 먼저 기록하세요.")
        return

    header = f"{'스킬':<20} {'총':>5} {'성공':>5} {'경고':>5} {'실패':>5} {'평균(분)':>8} {'최근 사용':<12}"
    sep = "-" * len(header)
    print(header)
    print(sep)
    for row in rows:
        print(
            f"{row['skill']:<20} "
            f"{row['total']:>5} "
            f"{row['success']:>5} "
            f"{row['warn']:>5} "
            f"{row['fail']:>5} "
            f"{(row['avg_min'] or 0):>8.1f} "
            f"{row['last_used'] or '-':<12}"
        )
    print(sep)
    total_execs = sum(r['total'] for r in rows)
    print(f"총 실행 건수: {total_execs}건 | 스킬 종류: {len(rows)}개")


def generate_report(session, output_path: str) -> None:
    """전체 효과성 리포트를 Markdown 파일로 저장한다."""
    rows = list(session.run(
        """
        MATCH (n:SkillExecution)
        RETURN n.skill                                                                        AS skill,
               n.mode                                                                         AS mode,
               count(*)                                                                       AS total,
               avg(n.duration_min)                                                            AS avg_min,
               sum(CASE WHEN n.outcome = 'success' THEN 1 ELSE 0 END) * 100.0 / count(*)     AS success_rate,
               max(n.date)                                                                    AS last_used
        ORDER BY total DESC
        """
    ))

    mode_rows = list(session.run(
        """
        MATCH (n:SkillExecution)
        WHERE n.mode IS NOT NULL AND n.mode <> ''
        RETURN n.mode AS mode, count(*) AS total
        ORDER BY total DESC
        """
    ))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "---",
        f"generated: {datetime.now().isoformat()}",
        "type: skill-effectiveness-report",
        "---",
        "",
        "# Skill Effectiveness Report",
        f"생성 일시: {now_str}",
        "",
        "## 스킬별 실행 통계",
        "",
        "| 스킬 | 모드 | 총 실행 | 성공률 | 평균 소요(분) | 최근 사용 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['skill']} "
            f"| {row['mode'] or '-'} "
            f"| {row['total']} "
            f"| {(row['success_rate'] or 0):.1f}% "
            f"| {(row['avg_min'] or 0):.1f} "
            f"| {row['last_used'] or '-'} |"
        )

    lines += [
        "",
        "## 모드별 실행 분포",
        "",
        "| 모드 | 실행 건수 |",
        "|---|---|",
    ]
    for row in mode_rows:
        lines.append(f"| {row['mode']} | {row['total']} |")

    # 효과성 인사이트
    fail_skills = [r for r in rows if (r['success_rate'] or 100) < 70]
    lines += ["", "## 개선 필요 스킬 (성공률 < 70%)", ""]
    if fail_skills:
        for r in fail_skills:
            lines.append(f"- `/{r['skill']}` - 성공률 {(r['success_rate'] or 0):.1f}% (총 {r['total']}회)")
    else:
        lines.append("없음 - 모든 스킬 70% 이상 성공률 유지 중.")

    lines += ["", "## Workflow Connections", "- [[active-context]]", "- [[reflect]]", "- [[curriculum]]"]

    content = "\n".join(lines)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"[완료] 리포트 저장: {out}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv()
    args = build_parser().parse_args()

    # dry-run: log 액션만 미적재 허용
    if args.dry_run and args.action == "log":
        now = datetime.now()
        fake_id = str(uuid.uuid4())[:8]
        print("[DRY-RUN] SkillExecution 적재 대상:")
        print(f"  exec_id:      {fake_id}")
        print(f"  skill:        {args.skill}")
        print(f"  workspace:    {args.workspace}")
        print(f"  mode:         {args.mode or ''}")
        print(f"  outcome:      {args.outcome}")
        print(f"  duration_min: {args.duration_min}")
        print(f"  date:         {now.strftime('%Y-%m-%d')}")
        print("[DRY-RUN] 완료. --dry-run 없이 실행하면 Neo4j 에 적재됩니다.")
        return

    start = time.time()
    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)

    try:
        with store.session() as session:
            if args.action == "log":
                log_execution(session, args)

            elif args.action == "stats":
                print_stats(session)

            elif args.action == "report":
                date_str = datetime.now().strftime("%Y-%m-%d")
                out_path = (
                    args.output
                    or f"CONTEXT/memory/recall/skill-report-{date_str}.md"
                )
                generate_report(session, out_path)

    finally:
        store.close()

    if args.action in ("log",):
        elapsed = time.time() - start
        print(f"[완료] 소요 시간: {elapsed:.2f}초")


if __name__ == "__main__":
    main()
