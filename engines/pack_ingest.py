#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack.json 가져오기 — A+2 마이그레이션의 **2단계(preference 오버레이)**, 설계 §10.

`pinned`·`tags`·`access_count`·`last_access` 등 **preference 신호는 원문 코퍼스에
없는 런타임 축적 데이터**라 엔진 재수집(1단계)만으로는 복원되지 않는다 — 이를
유실 없이 옮기는 유일한 통로가 서버측 `activate` 가 산출한 pack.json 이다(§10·D4).

동작(§10 2단계): `episode_id` 로 :Episode 를 MERGE 하고 **preference 계열 속성만
덮어쓴다**. 구조 필드(title/date/domain/summary/…)는 `coalesce(기존, 팩)` 로 채워
1단계 재수집이 만든 노드를 **덮어쓰지 않는다**(재수집 우선). 재수집 없이 팩만 적재
하는 대안 경로(§10 "팩 단독")에서는 노드가 새로 생기며 구조가 팩 수록 범위로 채워
진다. `episode_id` 보존이라 몇 번을 돌려도 결과가 같다(멱등).

**범위 경계(learner 미포함):** §10 2단계 위임은 Episode preference 오버레이다.
LearnerPattern(학습법)은 1단계 재수집으로 복원되며, 현재 pack.json 의 learner_model
투영에 `pattern_id`(PK)가 없어 멱등 MERGE 키가 없다 — learner 오버레이는 팩이
pattern_id 를 싣도록 확장한 뒤(별도 작업)로 미룬다. 여기서 임의 키를 만들지 않는다.

**Vinc↔Neo4j 무연결(§10 ⓖ):** Vinc 는 서버에 접속하지 않는다. 서버가 산출한
pack.json 파일을 임베디드 스토어로 import 할 뿐이다(§1 오프라인 정합).

사용:
    python pack_ingest.py --pack ./pack/pack.json
    python pack_ingest.py --pack ./pack/pack.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# 스토어 추상화(설계 §4) — 드라이버 취득은 store.backend.open_store 단일 지점.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

# activate.ContextPack.to_json 의 kind — 다른 JSON 을 실수로 적재하지 않도록 확인.
PACK_KIND = "weg-9000-memory/context-pack"


# ── pack.json 파싱·검증 (순수) ────────────────────────────────────────────────

def load_pack(path: str | Path) -> dict:
    """pack.json 을 읽어 dict 로 반환. kind 불일치 시 명확히 실패(오적재 방지)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    kind = data.get("kind")
    if kind != PACK_KIND:
        raise ValueError(
            f"pack.json kind 불일치: {kind!r} (기대 {PACK_KIND!r}). "
            "activate 가 산출한 pack.json 인지 확인하세요(§10).")
    return data


def _episode_params(ep: dict) -> dict[str, Any]:
    """pack 에피소드 dict(투영 키) → 오버레이 Cypher 파라미터.
    투영 키(api.query.recall): id/title/date/owner/domain/tenant/workspace/
    summary/pinned/tags/access_count/last_access."""
    return {
        "episode_id": ep.get("id"),
        # preference 오버레이(팩 우선) — 런타임 축적분, 재수집 불가(§10).
        "pinned": ep.get("pinned"),
        "tags": ep.get("tags"),
        "access_count": ep.get("access_count"),
        "last_access": ep.get("last_access"),
        # 구조 필드(재수집 우선, 팩은 공백 채움) — 팩 단독 경로 대비.
        "title": ep.get("title"),
        "owner_id": ep.get("owner"),
        "tenant_id": ep.get("tenant"),
        "domain": ep.get("domain"),
        "date": ep.get("date"),
        "workspace": ep.get("workspace"),
        "summary": ep.get("summary"),
    }


# ── Episode preference 오버레이 (§10 2단계) ───────────────────────────────────
# ⚠ Kùzu 방언: 미사용 파라미터를 거부(§6-2)하므로 모든 파라미터를 Cypher 에서
# 참조한다. preference 는 coalesce(팩, 기존)=팩 우선, 구조는 coalesce(기존, 팩)=
# 재수집 우선 — 두 경로(재수집→오버레이 · 팩 단독)를 한 문장으로 처리.
_OVERLAY_CYPHER = """
MERGE (e:Episode {episode_id: $episode_id})
SET e.pinned       = coalesce($pinned, e.pinned),
    e.tags         = coalesce($tags, e.tags),
    e.access_count = coalesce($access_count, e.access_count),
    e.last_access  = coalesce($last_access, e.last_access),
    e.title        = coalesce(e.title, $title),
    e.owner_id     = coalesce(e.owner_id, $owner_id),
    e.tenant_id    = coalesce(e.tenant_id, $tenant_id),
    e.domain       = coalesce(e.domain, $domain),
    e.date         = coalesce(e.date, $date),
    e.workspace    = coalesce(e.workspace, $workspace),
    e.summary      = coalesce(e.summary, $summary)
"""


def overlay_episode(session, ep: dict) -> None:
    """단일 에피소드의 preference 오버레이(멱등 — episode_id 키)."""
    session.run(_OVERLAY_CYPHER, **_episode_params(ep))


def overlay_pack(session, pack: dict) -> int:
    """pack 의 모든 에피소드를 오버레이. episode_id 없는 행은 건너뛴다. 적재 수 반환."""
    count = 0
    for ep in pack.get("episodes", []):
        if not ep.get("id"):
            continue  # 식별자 없는 행은 멱등 키가 없어 스킵
        overlay_episode(session, ep)
        count += 1
    return count


# ── 인수 파싱 ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="pack.json preference 오버레이 → 임베디드 스토어 적재 (A+2 2단계, §10)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python pack_ingest.py --pack ./pack/pack.json
  python pack_ingest.py --pack ./pack/pack.json --dry-run
        """,
    )
    p.add_argument("--pack", required=True,
                   help="적재할 pack.json 경로(서버측 activate 산출물)")
    p.add_argument("--dry-run", action="store_true",
                   help="적재 없이 파싱·대상 에피소드 수만 출력")
    return p


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = build_parser().parse_args()
    start = time.time()

    pack = load_pack(args.pack)
    episodes = pack.get("episodes", [])
    ingestible = [e for e in episodes if e.get("id")]
    print(f"[INFO] pack 로드: owner={pack.get('owner')} · 에피소드 {len(episodes)}개 "
          f"(적재 대상 {len(ingestible)}개 / 식별자 없음 {len(episodes) - len(ingestible)}개)")

    if args.dry_run:
        print("\n[DRY-RUN] 오버레이 없이 종료합니다. 대상 에피소드:")
        for ep in ingestible[:10]:
            pin = " 📌" if ep.get("pinned") else ""
            tags = ", ".join(ep.get("tags") or [])
            print(f"  {ep.get('id')}{pin} | access={ep.get('access_count', 0)}"
                  f"{(' | tags: ' + tags) if tags else ''}")
        if len(ingestible) > 10:
            print(f"  ... 외 {len(ingestible) - 10}개")
        return

    store = open_store(load_config())
    if store is None:
        print("[ERROR] 스토어 연결 실패 (backend/자격 확인).", file=sys.stderr)
        sys.exit(1)
    loaded = 0
    try:
        with store.session() as session:
            print("[INFO] :Episode preference 오버레이 시작...")
            loaded = overlay_pack(session, pack)
    finally:
        store.close()

    elapsed = time.time() - start
    print(f"\n[완료] 소요 시간: {elapsed:.1f}초")
    print(f"[완료] preference 오버레이 적재: {loaded}개 (멱등 — episode_id 보존)")


if __name__ == "__main__":
    main()
