#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""user-model 의 학습 패턴표 → Neo4j (:LearnerPattern) 적재.

한 개인이 *어떻게* 학습하는가(U-패턴: 흐름구조 선호·연속흡수·회귀흡수·도메인쌍
결합 …)는 가장 개인적인 데이터 — 인지 지문이다. 이 엔진은 user-model 마크다운의
패턴표를 파싱해 개인 소유(:LearnerPattern {owner_id})로 적재한다. Activate 가 이걸
컨텍스트팩에 실어 "내 학습 + 내 학습법"을 함께 이식한다.

파서(parse_learner_model)는 순수·stdlib — DB 없이 테스트된다.

사용:
    python learner_to_neo4j.py --source CONTEXT/identity/user-model.md --owner user:doheon [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

# 스토어 추상화(설계 §4) — 드라이버 취득은 store.backend.open_store 단일 지점.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

# 패턴 코드: U-01 / MM-12 / R-09 처럼 대문자 1~3 + '-' + 숫자.
_CODE = re.compile(r"^([A-Z]{1,3}-\d+)\b\s*(.*)$")


def _clean(s: str) -> str:
    return s.replace("**", "").strip()


def parse_learner_model(text: str, *, owner: str) -> list[dict[str, Any]]:
    """user-model 마크다운의 패턴표 행을 LearnerPattern dict 목록으로 파싱한다.

    기대 행 형식: ``| **{code} {name}** — {desc} | {domain} | {obs} | {examples} |``
    (헤더·구분선·비패턴 행은 무시). ``domain`` 이 '전체…' 이면 도메인 무관으로 보고
    빈 문자열로 정규화 — LearnerPattern 의 domain 은 '이 패턴이 매인 특정 도메인'."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        first = cells[0]
        if not first.startswith("**"):  # skip header / separator / prose rows
            continue
        label = _clean(first)
        head, _, desc = label.partition("—")  # em-dash separates code+name from desc
        m = _CODE.match(_clean(head))
        if not m:
            continue
        code = m.group(1)
        if code in seen:  # a pattern can recur across tables; first (SSoT) wins
            continue
        seen.add(code)
        domain = _clean(cells[1])
        domain = "" if domain.startswith("전체") else domain
        obs = re.sub(r"[^\d]", "", cells[2]) if len(cells) > 2 else ""
        out.append({
            "pattern_id": f"{owner}:{code}",
            "owner_id": owner,
            "code": code,
            "name": _clean(m.group(2)),
            "description": _clean(desc),
            "domain": domain,
            "observations": int(obs) if obs else 0,
        })
    return out


def merge_pattern(session, p: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (n:LearnerPattern {pattern_id: $pattern_id})
        SET n.owner_id     = $owner_id,
            n.code         = $code,
            n.name         = $name,
            n.description  = $description,
            n.domain       = $domain,
            n.observations = $observations
        """,
        **p,
    )


def _load_dotenv() -> None:
    for env_path in (Path(".env"), Path("../weg9000-hub/.env"), Path("../../weg9000-hub/.env")):
        if env_path.exists():
            try:
                from dotenv import load_dotenv  # type: ignore
                load_dotenv(env_path, override=False)
            except ImportError:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="user-model 학습 패턴표 → Neo4j :LearnerPattern")
    p.add_argument("--source", default=os.environ.get("MEMORY_LEARNER_MODEL"),
                   help="user-model 마크다운 경로 (env MEMORY_LEARNER_MODEL)")
    p.add_argument("--owner", default=os.environ.get("MEMORY_DEFAULT_OWNER", "tenant:shared"),
                   help="학습자 owner_id (env MEMORY_DEFAULT_OWNER)")
    p.add_argument("--dry-run", action="store_true", help="파싱 결과만 출력, 적재 없음")
    return p


def main() -> None:
    _load_dotenv()
    args = build_parser().parse_args()
    if not args.source:
        print("[ERROR] --source (또는 MEMORY_LEARNER_MODEL) 가 필요합니다.", file=sys.stderr)
        sys.exit(1)
    text = Path(args.source).read_text(encoding="utf-8")
    patterns = parse_learner_model(text, owner=args.owner)
    print(f"[INFO] 파싱된 학습 패턴: {len(patterns)}개 (owner={args.owner})")

    if args.dry_run:
        for p in patterns[:15]:
            print(f"  {p['code']} {p['name']} | domain={p['domain'] or '전체'} | n={p['observations']}")
        if len(patterns) > 15:
            print(f"  ... 외 {len(patterns) - 15}개")
        return

    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)
    try:
        with store.session() as session:
            for p in patterns:
                merge_pattern(session, p)
    finally:
        store.close()
    print(f"[완료] :LearnerPattern 적재: {len(patterns)}개")


if __name__ == "__main__":
    main()
