#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""라이브 임베디드 스토어 end-to-end 검증 — "데이터플레인이 실제로 동작하는가".

verify.py(stdlib·무 DB 게이트)와 달리 **실제 임베디드 스토어**(LadybugDB/Kùzu)를
요구한다. 인프로세스라 서버 없이 이 환경에서 그대로 돈다(설계 §11 P1 게이트).

  ① 스토어/스키마  — open + provision(멱등, §5)
  ② 데이터 상태    — 라벨별 카운트, owner_id/domain 커버리지(%)
  ③ read API       — recall/domains/graph/learner executed=True + 실 rows
  ④ Activate       — 컨텍스트팩 생성(에피소드·학습법)

드라이버 취득은 `store.backend.open_store` 단일 지점(§4-1). 사전에 엔진으로 데이터를
적재한 스토어를 대상으로 실행한다(빈 스토어면 카운트 0 → FAIL 로 상태를 보고).

    MEMORY_DATA_DIR=~/.local/share/vinc/store python e2e_live.py --owner user:doheon
exit 0 = PASS, 1 = FAIL, 2 = 스토어 불가.
"""
from __future__ import annotations

import argparse
import json
import sys

from store.config import load_config
from store.backend import open_store
from api import query as q
from api import activate as act

_OK, _FAIL = "  PASS", "  FAIL"

_LABELS = ("Episode", "Concept", "Bridge", "LearnerPattern", "Chunk")


def check_counts(store) -> tuple[list[tuple[bool, str]], dict]:
    counts = {}
    for label in _LABELS:
        rows = store.run(f"MATCH (n:{label}) RETURN count(n) AS c")
        counts[label] = (rows[0]["c"] if rows else 0)
    checks = [(counts["Episode"] > 0, f"data: :Episode {counts['Episode']}개"),
              (counts["Concept"] > 0, f"data: :Concept {counts['Concept']}개")]
    cov = {}
    for label, prop in (("Episode", "owner_id"), ("Episode", "domain"),
                        ("Concept", "owner_id"), ("Concept", "domain")):
        total = counts[label] or 1
        rows = store.run(
            f"MATCH (n:{label}) WHERE n.{prop} IS NOT NULL AND n.{prop} <> '' "
            "RETURN count(n) AS c")
        n = (rows[0]["c"] if rows else 0)
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
    ap = argparse.ArgumentParser(description="live embedded store end-to-end 검증")
    ap.add_argument("--owner", required=True, help="검증할 owner_id (예: user:doheon)")
    ap.add_argument("--domain", default=None, help="표본 도메인(자유 문자열, 생략=전체)")
    args = ap.parse_args()

    cfg = load_config()
    store = open_store(cfg)
    if store is None:
        print("[e2e] 임베디드 스토어 불가 — 드라이버(ladybug/kuzu) 설치 확인.", file=sys.stderr)
        return 2

    all_checks: list[tuple[bool, str]] = []
    info: dict = {}
    try:
        store.provision(dim=cfg.embedding_dim)  # 멱등(§5)
        counts_checks, counts_info = check_counts(store)
        all_checks += counts_checks
        info.update(counts_info)
    finally:
        store.close()

    api_checks, api_info = check_api(args.owner, args.domain)
    all_checks += api_checks
    info.update(api_info)

    print("vinc · live embedded e2e\n" + "=" * 40)
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
