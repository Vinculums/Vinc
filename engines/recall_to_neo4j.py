#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CONTEXT/memory/recall/ 세션 로그를 Neo4j (:Episode) 노드로 적재한다.
30일 경과 파일을 CONTEXT/memory/archival/episode/ 로 이동하는 migrate 기능 포함.

사용:
    python recall_to_neo4j.py [--dry-run]
    python recall_to_neo4j.py --migrate [--dry-run]
    python recall_to_neo4j.py --neo4j-uri neo4j://127.0.0.1:7687 --neo4j-user neo4j
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# 스토어 추상화(설계 §4) — 드라이버 취득은 store.backend.open_store 단일 지점.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402


# ── 상수 ─────────────────────────────────────────────────────────────────────

RECALL_DIR = Path("CONTEXT/memory/recall")
ARCHIVAL_DIR = Path("CONTEXT/memory/archival/episode")
ARCHIVE_DAYS = 30  # 경과 일수 기준

# 하이브리드 소유 모델: Episode 는 (테넌트 거버넌스 경계 안의) 개인이 소유한다.
# frontmatter 의 owner/tenant 가 우선, 없으면 환경변수 → 테넌트 공용 기본값.
DEFAULT_OWNER = "tenant:shared"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── dotenv 로드 ───────────────────────────────────────────────────────────────

def _load_dotenv() -> None:
    """weg9000-hub/.env 를 환경변수로 로드한다."""
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
                print(f"[INFO] .env 로드: {env_path.resolve()}")
            except ImportError:
                # python-dotenv 미설치 시 수동 파싱
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        os.environ.setdefault(key, val)
            return


# ── 인수 파싱 ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="recall 세션 로그 → Neo4j :Episode 적재 + Archival 이관",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 전체 recall 세션을 Neo4j에 적재 (dry-run)
  python recall_to_neo4j.py --dry-run

  # 실제 적재
  python recall_to_neo4j.py

  # 30일 경과 파일 archival/ 로 이관 (dry-run)
  python recall_to_neo4j.py --migrate --dry-run

  # 이관 + 적재 동시 실행
  python recall_to_neo4j.py --migrate
        """,
    )
    p.add_argument("--neo4j-uri", default=None,
                   help="Neo4j URI (기본값: NEO4J_URI 환경변수 → neo4j://127.0.0.1:7687)")
    p.add_argument("--neo4j-user", default=None,
                   help="Neo4j 사용자명 (기본값: NEO4J_USER 환경변수 → neo4j)")
    p.add_argument("--neo4j-password", default=None,
                   help="Neo4j 비밀번호 (기본값: NEO4J_PASSWORD 환경변수)")
    p.add_argument("--migrate", action="store_true",
                   help=f"{ARCHIVE_DAYS}일 경과 파일을 archival/episode/ 로 이동")
    p.add_argument("--dry-run", action="store_true",
                   help="실제 변경 없이 파싱·이동 대상만 출력")
    return p


# ── frontmatter 파싱 ─────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _parse_body_sections(text: str) -> dict[str, str]:
    """마크다운 body 에서 ## 섹션별 텍스트를 추출한다."""
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    body = _FRONTMATTER_RE.sub("", text, count=1)
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines = []
        else:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


# ── 세션 파일 수집 ────────────────────────────────────────────────────────────

def collect_session_files() -> list[tuple[Path, date]]:
    """recall/ 하위 날짜 디렉토리에서 session-*.md 파일을 수집한다."""
    if not RECALL_DIR.exists():
        print(f"[WARN] recall 디렉토리 없음: {RECALL_DIR}", file=sys.stderr)
        return []

    result: list[tuple[Path, date]] = []
    for dir_path in sorted(RECALL_DIR.iterdir()):
        if not dir_path.is_dir():
            continue
        if not _DATE_DIR_RE.match(dir_path.name):
            continue
        try:
            dir_date = date.fromisoformat(dir_path.name)
        except ValueError:
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            result.append((md_file, dir_date))

    return result


# ── Episode 노드 생성 ─────────────────────────────────────────────────────────

_LEARNING_SEG = re.compile(r"learning/([^/\s,+)]+)")


def _primary_domain(raw: str) -> str:
    """frontmatter domain/workspace 값 → 주 도메인 정규화 (자유 문자열, 목록 검증 없음).

    - `[math, art]` 리스트형 → 첫 항목(주 도메인).
    - 경로 서술형 workspace(예: `WORKSPACES/learning/physics/modern + …`, 구세대
      physics 세션) → `learning/` 다음 세그먼트 추출 — 어떤 도메인 이름이든 동작.
    - 공백/구분자가 섞인 서술형인데 learning 세그먼트도 없으면 도메인 아님 → ''."""
    s = (raw or "").strip()
    if s.startswith("[") and s.endswith("]"):
        return s[1:-1].split(",")[0].strip().strip('"').strip("'")
    m = _LEARNING_SEG.search(s)
    if m:
        return m.group(1)
    if any(ch in s for ch in " /+("):
        return ""
    return s


def build_episode(file_path: Path, dir_date: date) -> dict[str, Any]:
    """파일을 파싱해 Episode 노드 딕셔너리를 반환한다."""
    text = file_path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    sections = _parse_body_sections(text)

    # 세션 ID: {디렉토리 날짜}-{frontmatter id 또는 파일명}
    # 날짜 prefix 로 교차 일자 동일 파일명(session-HHMMSS.md) 충돌을 방지한다.
    base_id = (
        fm.get("session_id")
        or fm.get("id")
        or re.sub(r"[^a-zA-Z0-9_-]", "_", file_path.stem)
    )
    session_id = f"{dir_date}-{base_id}"

    # 날짜: frontmatter > 디렉토리 날짜
    raw_date = fm.get("date") or fm.get("created") or str(dir_date)

    # 요약: 세션 요약 섹션 > 제목 > 파일명
    summary = (
        sections.get("세션 요약")
        or sections.get("요약")
        or sections.get("Summary")
        or fm.get("summary", "")
    )[:500]  # 최대 500자

    # 완료 항목: 해당 섹션 텍스트
    completed = sections.get("완료 항목") or sections.get("완료한 것") or ""

    # 발견 패턴
    patterns = sections.get("발견된 패턴") or sections.get("패턴") or ""

    return {
        "episode_id": session_id,
        "title": fm.get("title") or file_path.stem,
        "date": raw_date,
        "owner_id": fm.get("owner") or os.environ.get("MEMORY_DEFAULT_OWNER", DEFAULT_OWNER),
        "tenant_id": fm.get("tenant") or os.environ.get("MEMORY_TENANT_ID", ""),
        # domain = the learning domain, a free user-defined string. Never enforced
        # against a list; frontmatter `domain:` wins, else `workspace:`. Real hub
        # sessions sometimes carry a list form `domain: [math, art]` — the first
        # entry is the primary domain (multi-domain 표기는 MIGRATION.md 참조).
        "domain": _primary_domain(fm.get("domain") or fm.get("workspace", "")),
        "workspace": fm.get("workspace", ""),
        "mode": fm.get("mode", ""),
        "summary": summary,
        "completed": completed[:300],
        "patterns": patterns[:300],
        "source_file": str(file_path),
    }


# ── Episode MERGE ─────────────────────────────────────────────────────────────

def merge_episode(session, ep: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (n:Episode {episode_id: $episode_id})
        SET n.title       = $title,
            n.date        = $date,
            n.owner_id    = $owner_id,
            n.tenant_id   = $tenant_id,
            n.domain      = $domain,
            n.workspace   = $workspace,
            n.mode        = $mode,
            n.summary     = $summary,
            n.completed   = $completed,
            n.patterns    = $patterns,
            n.source_file = $source_file
        """,
        episode_id=ep["episode_id"],
        title=ep["title"],
        date=ep["date"],
        owner_id=ep["owner_id"],
        tenant_id=ep["tenant_id"],
        domain=ep["domain"],
        workspace=ep["workspace"],
        mode=ep["mode"],
        summary=ep["summary"],
        completed=ep["completed"],
        patterns=ep["patterns"],
        source_file=ep["source_file"],
    )


# ── Archival 이관 ─────────────────────────────────────────────────────────────

def migrate_old_files(
    session_files: list[tuple[Path, date]],
    dry_run: bool,
) -> tuple[int, int]:
    """ARCHIVE_DAYS 경과 파일을 archival/episode/ 로 이동한다.
    Returns (moved_count, skipped_count).
    """
    cutoff = date.today() - timedelta(days=ARCHIVE_DAYS)
    moved = 0
    skipped = 0

    for file_path, dir_date in session_files:
        if dir_date > cutoff:
            skipped += 1
            continue

        dest_dir = ARCHIVAL_DIR / str(dir_date.year)
        dest_path = dest_dir / file_path.name

        print(f"  [이관] {file_path} → {dest_path}")
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(file_path), str(dest_path))

            # 원본 위치에 포인터 파일 생성
            pointer_path = file_path.with_suffix(".archived.md")
            pointer_path.write_text(
                f"---\narchived_to: {dest_path}\narchived_at: {datetime.now().isoformat()}\n---\n"
                f"# Archived\n이 파일은 archival/episode/ 로 이동됐습니다.\n"
                f"경로: `{dest_path}`\n",
                encoding="utf-8",
            )
        moved += 1

    return moved, skipped


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv()

    args = build_parser().parse_args()

    # 연결 정보: 인수 > 환경변수 > 기본값
    uri = args.neo4j_uri or os.environ.get("NEO4J_URI", "neo4j://127.0.0.1:7687")
    user = args.neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
    password = args.neo4j_password or os.environ.get("NEO4J_PASSWORD", "")

    if not password and not args.dry_run:
        print("[ERROR] Neo4j 비밀번호가 없습니다.", file=sys.stderr)
        print("        NEO4J_PASSWORD 환경변수 또는 --neo4j-password 인수를 사용하세요.",
              file=sys.stderr)
        sys.exit(1)

    start = time.time()

    # 1. 세션 파일 수집
    print(f"[INFO] recall 디렉토리 탐색: {RECALL_DIR}")
    session_files = collect_session_files()
    print(f"[INFO] 발견된 세션 파일: {len(session_files)}개")

    if not session_files:
        print("[INFO] 처리할 세션 파일이 없습니다.")
        return

    # 2. Episode 파싱
    episodes: list[dict[str, Any]] = []
    skip_count = 0
    for file_path, dir_date in session_files:
        try:
            ep = build_episode(file_path, dir_date)
            episodes.append(ep)
        except Exception as e:
            print(f"  [WARN] 파싱 실패 {file_path.name}: {e}", file=sys.stderr)
            skip_count += 1

    print(f"[INFO] 파싱 완료: {len(episodes)}개 (스킵: {skip_count}개)")

    if args.dry_run:
        print("\n[DRY-RUN] 파싱 결과 (실제 적재 없음):")
        for ep in episodes[:10]:
            print(f"  Episode: {ep['episode_id']} | {ep['date']} | {ep['workspace'] or '워크스페이스 미지정'}")
        if len(episodes) > 10:
            print(f"  ... 외 {len(episodes) - 10}개")
        if args.migrate:
            print(f"\n[DRY-RUN] 이관 대상 (>{ARCHIVE_DAYS}일):")
            m, s = migrate_old_files(session_files, dry_run=True)
            print(f"  이관 예정: {m}개 / 유지: {s}개")
        print("\n[DRY-RUN] 완료. --dry-run 없이 실행하면 적재됩니다.")
        return

    # 3. 스토어 적재 (백엔드 중립 — 설계 §4-3)
    store = open_store(load_config({"uri": uri, "user": user, "password": password}))
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)
    loaded = 0
    try:
        with store.session() as session:
            print("[INFO] :Episode 노드 MERGE 시작...")
            for ep in episodes:
                merge_episode(session, ep)
                loaded += 1
    finally:
        store.close()

    # 4. Archival 이관 (--migrate)
    moved = skipped_migrate = 0
    if args.migrate:
        print(f"\n[INFO] {ARCHIVE_DAYS}일 경과 파일 archival 이관 시작...")
        moved, skipped_migrate = migrate_old_files(session_files, dry_run=False)

    elapsed = time.time() - start
    print(f"\n[완료] 소요 시간: {elapsed:.1f}초")
    print(f"[완료] :Episode 적재: {loaded}개 / 파싱 스킵: {skip_count}개")
    if args.migrate:
        print(f"[완료] archival 이관: {moved}개 / 유지({ARCHIVE_DAYS}일 미경과): {skipped_migrate}개")


if __name__ == "__main__":
    main()
