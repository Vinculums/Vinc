#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""라이브 스토어 end-to-end 검증 — "데이터플레인이 실제로 동작하는가".

verify.py(stdlib·무 DB 게이트)와 달리 **실제 스토어**를 요구한다.

- `--backend neo4j`(기본): 서버 Neo4j 검증(마이그레이션·서버 경로). 스키마
  introspection(SHOW CONSTRAINTS/INDEXES)은 Neo4j 방언이라 이 경로 전용.
- **임베디드(LadybugDB) e2e 는 `tests/test_embedded_backend.py`** 가 담당한다 —
  인프로세스라 `verify.py` 안에서 그대로 실행된다(드라이버 설치 시). 임베디드는
  스키마 introspection 대신 provision→write→read 왕복으로 검증(설계 §5·§11 P1).

드라이버 취득은 `store.backend.open_store` 단일 지점(설계 §4-1).

    NEO4J_PASSWORD=... python e2e_live.py --owner user:doheon
exit 0 = PASS, 1 = FAIL, 2 = 연결 불가/설정 오류.
"""
from __future__ import annotations

import argparse
import json
import sys

from store.config import load_config
from store.backend import open_store
from store.schema import MIGRATIONS
from api import query as q
from api import activate as act

_OK, _FAIL = "  PASS", "  FAIL"


def check_schema(session) -> list[tuple[bool, str]]:
    out = []
    have_c = {r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name")}
    have_i = {r["name"] for r in session.run("SHOW INDEXES YIELD name")}
    have = have_c | have_i
    for m in MIGRATIONS:
        target = {"i_chunk_embedding": "chunk_embedding",
                  "i_chunk_text_ft": "chunk_text_ft"}.get(m.id, m.id)
        out.append((target in have, f"schema: {target}"))
    return out


def check_counts(session) -> tuple[list[tuple[bool, str]], dict]:
    counts = {}
    for label in ("Episode", "Concept", "Bridge", "LearnerPattern", "Chunk"):
        counts[label] = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
    checks = [(counts["Episode"] > 0, f"data: :Episode {counts['Episode']}개"),
              (counts["Concept"] > 0, f"data: :Concept {counts['Concept']}개")]
    cov = {}
    for label, prop in (("Episode", "owner_id"), ("Episode", "domain"),
                        ("Concept", "owner_id"), ("Concept", "domain")):
        total = counts[label] or 1
        n = session.run(
            f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL AND n.{prop} <> '' RETURN count(n) AS c"
        ).single()["c"]
        pct = round(100.0 * n / total, 1)
        cov[f"{label}.{prop}"] = pct
        checks.append((pct >= 90.0, f"coverage: {label}.{prop} {pct}% (≥90 기대)"))
    return checks, {"counts": counts, "coverage": cov}


def check_api(owner: str, domain: str | None) -> tuple[list[tuple[bool, str]], dict]:
    checks, sample = [], {}
    r = q.recall(owner=owner, domain=domain, rank="preference", limit=5)
    checks.append((r["executed"] and len(r["rows"]) > 0, f"api: recall rows={len(r['rows'])}"))
    sample["recall_top"] = [row.get("title") for row in r["rows"][:3]]
    d = q.domains(owner=owner)
    checks.append((d["executed"] and len(d["rows"]) > 0, f"api: domains {[x['domain'] for x in d['rows']]}"))
    g = q.graph(owner=owner, domain=domain)
    checks.append((g["executed"] and len(g["nodes"]) > 0, f"api: graph nodes={len(g['nodes'])} edges={len(g['edges'])}"))
    cross = sum(1 for e in g["edges"] if e.get("cross_domain"))
    sample["graph"] = {"nodes": len(g["nodes"]), "edges": len(g["edges"]), "cross_domain": cross}
    lm = q.learner_model(owner=owner)
    checks.append((lm["executed"], f"api: learner patterns={len(lm['rows'])}"))
    a = act.activate(owner=owner, domain=domain)
    checks.append((a["episode_count"] > 0, f"activate: episodes={a['episode_count']} learner={a['learner_pattern_count']}"))
    return checks, sample


def main() -> int:
    ap = argparse.ArgumentParser(description="live store end-to-end 검증")
    ap.add_argument("--owner", required=True, help="검증할 owner_id (예: user:doheon)")
    ap.add_argument("--domain", default=None, help="표본 도메인(자유 문자열, 생략=전체)")
    ap.add_argument("--backend", default=None, choices=["neo4j", "embedded"],
                    help="스토어 백엔드(생략 시 config). 스키마 introspection 은 neo4j 전용")
    args = ap.parse_args()

    overrides = {"backend": args.backend} if args.backend else None
    cfg = load_config(overrides)
    store = open_store(cfg)
    if store is None:
        print(f"[e2e] 스토어 연결 불가 (backend={cfg.backend}). 자격/설치 확인.", file=sys.stderr)
        return 2

    all_checks: list[tuple[bool, str]] = []
    info: dict = {}
    try:
        if cfg.backend == "neo4j":
            with store.session() as s:
                all_checks += check_schema(s)
                counts_checks, counts_info = check_counts(s)
                all_checks += counts_checks
                info.update(counts_info)
        else:
            # 임베디드: introspection 방언 부재 — 상세 왕복은 test_embedded_backend.py.
            print("[e2e] embedded 백엔드: 스키마 introspection 생략 "
                  "(provision→write→read 왕복은 tests/test_embedded_backend.py).")
    finally:
        store.close()

    api_checks, api_info = check_api(args.owner, args.domain)
    all_checks += api_checks
    info.update(api_info)

    print("vinc · live e2e\n" + "=" * 40)
    failed = 0
    for ok, msg in all_checks:
        print((_OK if ok else _FAIL) + " " + msg)
        failed += (not ok)
    print("=" * 40)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"\ne2e: {'PASS' if failed == 0 else f'FAIL ({failed})'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
