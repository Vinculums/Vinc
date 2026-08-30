#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PK 네임스페이싱을 켜기 전에 스토어가 준비됐는지 **읽기만 해서** 재는 도구.

## 왜 필요한가

`VINC_PK_NAMESPACED` 를 허브에서 켜는 것은 되돌리기 어려운 데이터 이관을 동반한다
(노드 PK 가 `slug` → `owner\\x1fslug` 로 바뀐다). 그 결정에 필요한 사실은 코드가
아니라 **그 스토어의 행들**에 있고, 지금까지 그것은 손으로 스냅샷을 뜨고 눈으로 세는
일이었다. 그렇게 잰 수는 세션이 끝나면 사라지고, 다음 사람은 인용만 물려받는다 —
이 리포가 이미 한 번 배운 실패다("원장을 유실 검사로 쓰면 안 된다. 파일 트리를 직접
세야 한다").

그래서 **같은 판정을 아무나 다시 돌릴 수 있게** 만든다. 이 스크립트는 인용이 아니라
재현 절차다.

## 쓰는 법

    python scripts/pkns_preflight.py --data <스토어 디렉터리> [--json]

⚠ **라이브 DB 를 직접 열지 마라.** 허브 DB 는 Azure Files 위 단일 writer 이고, SMB
위에서 임베디드 DB 를 직접 read-write 한 것이 하드다운 3회의 공통 원인이었다
([[vinc.ops.deployment]]). 공유 스냅샷을 뜨고 거기서 받은 사본에 대고 돌릴 것:

    az storage share snapshot ...
    az storage file download --snapshot <ts> ...

이 스크립트는 쓰기를 하지 않지만, **여는 것 자체가 WAL 을 건드릴 수 있다**는 것이
저 사고의 교훈이다. 사본에 돌리는 이유가 그것이다.

## 무엇을 재는가

각 항목이 이관을 막을 수 있는 **구체적 조건** 하나에 대응한다. 통과 조건을 함께
찍으므로, 읽는 사람이 "몇 건이면 괜찮은가"를 따로 물을 필요가 없다.

  rows            노드 테이블별 행 수. 이관 규모 = 위험의 대부분이 여기서 온다.
  already_physical  PK 에 SEP 이 이미 있는 행. 이관이 멱등인지 보는 값이고, 0 이
                  아니면 **부분 이관된 스토어**라 다시 돌리기 전에 사람이 봐야 한다.
  sep_in_pk       PK 안에 U+001F 가 든 행 — **이 수 하나가 판정 대상이다. 0 이어야
                  한다.** 그 바이트가 물리 키의 판별자라, 사용자 저작 슬러그에 있으면
                  이관 뒤 strip 이 슬러그를 잘라낸다.
                  ⚠ 아래 `looks_physical`/`looks_slug` 분해는 **조언이지 판정이 아니다.**
                  `pkns.looks_physical` 은 휴리스틱이고(SEP 앞에 공백이 없고 짧으면
                  물리 키로 본다) 공백 없는 슬러그가 그것을 그대로 통과한다 — 실측:
                  `concept:has\x1fsep` 가 True 로 나온다. 그래서 두 갈래 **어느 쪽이든
                  막는다**: 물리 키면 부분 이관, 슬러그면 strip 손상이다.
  owners          서로 다른 owner 수. 1 이면 합성이 무의미에 가깝고, 크면 그만큼
                  이 기능이 실제로 막고 있던 충돌이 많다는 뜻이다.
  ⚠ **"같은 슬러그를 두 owner 가 나눠 갖는가" 는 재지 않는다 — 잴 것이 없다.**
     노드 PK 는 각 테이블의 PRIMARY KEY 이고 엔진이 유일성을 강제한다(실측:
     같은 `concept_id` 로 두 번째 행을 심으면 "violates the uniqueness constraint
     of the primary key column" 으로 거절된다). 즉 플래그가 꺼진 스토어에서 그 수는
     **언제나 0 이고, 그것은 측정이 아니라 항진명제다.** 그 0 을 "이관이 안전하다"
     의 근거로 인용하면 아무것도 근거하지 않은 것이다.

     그리고 그 유일성이 정확히 Vinculums/Vinc#121 의 메커니즘이다: PK 가 한 전역
     이름공간이라, 개인 → 팀 복사에서 보내는 사람이 이미 소유한 id 를 SEC-3 가드가
     전부 건너뛴다. 이관이 1:1 인 이유도 같다 — 합성은 owner 를 **덧붙일** 뿐이라
     충돌을 만들 수 없고, 원본이 유일했으므로 결과도 유일하다. 사전에 보장된다.
  legacy_owners   `_NORMALIZE` 표가 다루는 레거시 기본 owner(tenant:local ·
                  user:local · tenant:shared)를 단 행. 표에 없는 값이 있으면
                  `compose(normalize=True)` 경로가 raise 한다.
  meta            메타 표별 행 수와 이관 계획(재작성할 열 · 나를 것인가 · 행이 있으면
                  멈출 것인가). 계획은 `migrate_pk_namespacing.META_PLAN` 이 정본이고
                  여기서는 읽기만 한다 — 표가 둘이면 점검과 이관이 다른 것을 말한다.

## 무엇을 재지 **않는가**

판정을 하지 않는다. `verdict` 는 위 조건들의 기계적 AND 일 뿐이고, 켤지 말지는
발주자의 결정이다(Vinculums/Vinc#121 이 두 선택지를 적어 두고 있다). 그리고 이
스크립트는 **이관을 수행하지 않는다** — 재는 것과 바꾸는 것을 한 프로그램에 넣으면
`--dry-run` 을 빠뜨린 한 번이 프로덕션이 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import pkns                          # noqa: E402
from store import embedded_schema as schema     # noqa: E402
from store.backend import open_store            # noqa: E402
from store.config import load_config            # noqa: E402

# 이관 계획의 정본. 점검이 이관을 읽는다(반대가 아니다) — 막을지 말지를 정하는 쪽이
# 무엇을 어떻게 바꾸는지 아는 쪽이어야 한다.
from migrate_pk_namespacing import META_PLAN     # noqa: E402

#: `_NORMALIZE` 가 다루려던 레거시 기본 owner. 표 자체를 읽지 않고 여기 적는 이유는
#: 표가 **비어 있는 것이 정상**이기 때문이다(측정 전에는 채우지 않는 것이 계약이다) —
#: 표를 읽으면 "아직 안 채웠다"와 "그런 행이 없다"가 구분되지 않는다.
LEGACY_OWNERS = ("tenant:local", "user:local", "tenant:shared")


def _rows(store, cypher: str) -> list[dict]:
    """드라이버 차이를 한 자리에서 흡수한다. 실패는 삼키지 않는다 — 못 잰 것을
    0 으로 보고하면 이 도구가 정확히 반대의 일을 한다."""
    return list(store.run(cypher) or [])


def _scalar(store, cypher: str, key: str, default=0):
    rows = _rows(store, cypher)
    return rows[0].get(key, default) if rows else default


def survey_nodes(store) -> dict:
    """노드 테이블별 실측. 라벨·PK 열은 스키마에서 파생한다."""
    out: dict[str, dict] = {}
    for label, spec in schema.node_table_specs().items():
        pk = spec["pk"]
        has_owner = "owner_id" in spec["cols"]
        n = _scalar(store, f"MATCH (n:{label}) RETURN count(n) AS c", "c")
        if not n:
            continue
        # SEP 을 담은 PK. 이미 물리 키인 것과 슬러그에 그 바이트가 섞인 것을 가른다 —
        # 둘 다 SEP 을 갖지만 뜻이 정반대고, 뭉치면 판정이 무의미해진다.
        sep_rows = [r.get("k") or "" for r in _rows(
            store, f"MATCH (n:{label}) WHERE n.{pk} CONTAINS '{pkns.SEP}' "
                   f"RETURN n.{pk} AS k")]
        already = sum(1 for k in sep_rows if pkns.looks_physical(k))
        info = {"rows": n, "pk": pk,
                # 판정은 sep_in_pk 하나로 한다. 아래 둘은 사람이 원인을 짐작하는 데
                # 쓰는 조언일 뿐이고, 휴리스틱이라 서로 새어 든다(모듈 독스트링 참조).
                "sep_in_pk": len(sep_rows),
                "looks_physical": already, "looks_slug": len(sep_rows) - already,
                "owner_column": has_owner}
        if has_owner:
            owners = _rows(store, f"MATCH (n:{label}) RETURN DISTINCT n.owner_id AS o")
            vals = [r.get("o") or "" for r in owners]
            info["owners"] = len([v for v in vals if v])
            info["blank_owner"] = _scalar(
                store, f"MATCH (n:{label}) WHERE n.owner_id IS NULL OR n.owner_id = '' "
                       f"RETURN count(n) AS c", "c")
            info["legacy_owners"] = {v: _scalar(
                store, f"MATCH (n:{label}) WHERE n.owner_id = '{v}' "
                       f"RETURN count(n) AS c", "c")
                for v in LEGACY_OWNERS
                if _scalar(store, f"MATCH (n:{label}) WHERE n.owner_id = '{v}' "
                                  f"RETURN count(n) AS c", "c")}
        out[label] = info
    return out


def survey_meta(store) -> dict:
    """메타 표별 행 수와 **이관 계획**. 계획은 `migrate_pk_namespacing.META_PLAN` 에서
    읽는다 — 여기 따로 적으면 점검과 이관이 다른 것을 말하게 된다.

    ⚠ 이 목록을 처음 손으로 적었을 때 `_EdgeTombstone` 의 `src`·`dst` 를 재작성
    대상에 넣었는데 **틀렸다.** 그 두 열은 동기화 와이어로 그대로 나가고 와이어에
    U+001F 가 있어서는 안 된다(계약 테스트가 고정한다) — 합성하면 이관이 그 계약을
    깬다. 표를 하나로 합친 이유가 이것이다.

    엣지(관계 테이블)는 애초에 대상이 아니다 — 엔진이 노드 정체성으로 잇고 있어 PK 를
    다시 쓰면 따라온다. 반면 툼스톤의 `node_id`·`ekey` 는 그냥 문자열이라 아무도 안
    고쳐 주고, 안 고치면 **삭제 표식이 상쇄되지 않아 지운 것이 부활한다.**
    """
    out: dict[str, dict] = {}
    for table, plan in META_PLAN.items():
        if table not in schema._META_TABLES:
            # 스키마에서 사라진 표를 조용히 건너뛰지 않는다 — 계획이 스키마를 못
            # 따라간 것이 이 도구의 가장 나쁜 고장이다.
            out[table] = {"error": "이 표가 스키마에 없다 — META_PLAN 을 갱신할 것"}
            continue
        try:
            n = _scalar(store, f"MATCH (t:{table}) RETURN count(t) AS c", "c")
        except Exception as e:      # noqa: BLE001 — 못 재면 못 잰 대로 말한다
            out[table] = {"error": str(e)}
            continue
        out[table] = {"rows": n, "rewrite": list(plan["rewrite"]),
                      "carry": plan["carry"], "refuse_if_rows": plan["refuse"]}
    return out


def verdict(nodes: dict, meta: dict) -> dict:
    """조건들의 **기계적 AND**. 판정이 아니라 요약이다 — 켤지 말지는 사람이 정한다."""
    total = sum(v["rows"] for v in nodes.values())
    sep = sum(v["sep_in_pk"] for v in nodes.values())
    already = sum(v["looks_physical"] for v in nodes.values())
    blank = sum(v.get("blank_owner", 0) for v in nodes.values())
    legacy = {}
    for v in nodes.values():
        for k, n in (v.get("legacy_owners") or {}).items():
            legacy[k] = legacy.get(k, 0) + n
    blockers = []
    if sep:
        # 한 조건으로 합친다. 물리 키인지 손상된 슬러그인지는 휴리스틱으로만 갈리고
        # (looks_physical 이 공백 없는 슬러그를 통과시킨다) **둘 다 막아야 할 조건**이라,
        # 가르면 판정이 판별자의 정확도에 묶인다.
        blockers.append(
            f"PK 에 U+001F 가 든 행 {sep}건 — 물리 키처럼 보이는 것 {already}건("
            f"부분 이관 의심), 슬러그처럼 보이는 것 {sep - already}건(이관 뒤 strip 이 "
            "잘라낸다). 갈래는 휴리스틱이므로 둘 다 사람이 볼 것")
    if blank:
        blockers.append(f"owner 가 빈 행 {blank}건 — 합성할 owner 가 없다")
    missing = [k for k, v in legacy.items() if k not in pkns._NORMALIZE]
    if missing:
        blockers.append(f"정규화 표에 없는 레거시 owner {missing} — compose(normalize=True) "
                        "가 raise 한다")
    return {"node_rows": total, "meta_rows": sum(m.get("rows", 0) for m in meta.values()),
            "sep_in_pk": sep, "looks_physical": already, "blank_owner": blank,
            "legacy_owner_rows": legacy,
            "blockers": blockers, "clear": not blockers}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True,
                    help="스토어 디렉터리. **라이브가 아니라 스냅샷 사본**을 줄 것")
    ap.add_argument("--json", action="store_true", help="기계 판독용 출력")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data})
    store = open_store(cfg)
    if store is None:
        print("스토어를 열지 못했다 — 드라이버가 없거나 경로가 틀렸다", file=sys.stderr)
        return 2
    try:
        nodes = survey_nodes(store)
        meta = survey_meta(store)
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    report = {"data_dir": args.data, "nodes": nodes, "meta": meta,
              "summary": verdict(nodes, meta)}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    s = report["summary"]
    print(f"스토어: {args.data}")
    print(f"노드 {s['node_rows']}행 · 메타 {s['meta_rows']}행\n")
    print(f"{'라벨':<18}{'행':>8}{'PK에SEP':>10}{'owner수':>9}{'owner빈칸':>11}")
    for label, v in sorted(nodes.items(), key=lambda kv: -kv[1]["rows"]):
        print(f"{label:<18}{v['rows']:>8}{v['sep_in_pk']:>10}"
              f"{v.get('owners', '-'):>9}{v.get('blank_owner', '-'):>11}")
    print("\n메타 표 (이관 계획은 migrate_pk_namespacing.META_PLAN 이 정본):")
    for t, m in meta.items():
        if "error" in m:
            print(f"  {t:<18} ⚠ {m['error']}")
            continue
        what = (("재작성 " + ",".join(m["rewrite"])) if m["rewrite"]
                else ("그대로 나름" if m["carry"] else "안 나름"))
        if m["refuse_if_rows"]:
            what += "  ⚠ 행이 있으면 이관이 멈춘다"
        print(f"  {t:<18}{m['rows']:>8}행  {what}")
    if s["legacy_owner_rows"]:
        print(f"\n레거시 owner: {s['legacy_owner_rows']}")
    print()
    if s["clear"]:
        print("막는 조건 없음.")
        print("이관은 1:1 재작성이다 — 다만 그것은 이 측정의 결론이 아니라 PK 유일성")
        print("제약이 **사전에 보장**하는 것이다(모듈 독스트링 참조).")
        print("⚠ 그래도 켤지 말지는 발주자의 결정이다(Vinculums/Vinc#121 이 선택지 둘을 적었다).")
    else:
        print("막는 조건:")
        for b in s["blockers"]:
            print(f"  - {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
