#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PK 네임스페이싱 이관 — 노드 물리 키를 `slug` 에서 `owner\\x1fslug` 로 옮긴다.

Vinculums/Vinc#121 의 근본 처방(선택지 1). 발주자 결정: 2026-08-30.

## 왜 새 스토어를 짓는가 (제자리 갱신이 아니라)

**PK 는 제자리에서 못 바꾼다.** 엔진이 거절한다(실측):

    Binder exception: Cannot set property concept_id in table Concept
    because it is used as primary key. Try delete and then insert.

그러니 어차피 지우고 다시 넣어야 하는데, 같은 스토어 안에서 하면 중간 상태가
반쯤 이관된 스토어가 되고 실패 지점마다 다른 모양으로 남는다. 그래서 **원본을
읽기만 하고 결과를 새 디렉터리에 쓴다.** 롤백은 원본을 그대로 두는 것이다 —
되돌리는 절차가 따로 없다는 것이 이 형태의 요점이다.

## 무엇을 다시 쓰고 무엇을 그대로 두는가

이 표가 이 파일의 본체다. `scripts/pkns_preflight.py` 가 이것을 읽는다 — 표가 둘이면
점검과 이관이 다른 것을 말하게 된다.

  노드 PK          owner_id 열이 있는 라벨만 합성한다. `Domain`(pk=name)과
                   `SkillExecution` 은 owner 열이 없어 **합성할 수 없고**, 런타임도
                   `pkns.key(None, id)` 로 논리 id 를 그대로 쓴다. 여기서 억지로
                   합성하면 이관본이 런타임과 어긋난다.
  엣지 끝점        덤프의 `from`/`to` 는 노드 PK 값이므로 **끝점의 owner 로** 합성한다.
                   owner 는 노드 덤프에서 만든 pk → owner 표에서 찾는다.
  _Tombstone       `node_id` 를 합성한다. 안 하면 두 계정이 같은 슬러그를 지울 때
                   **툼스톤 행 하나를 공유**해 진 쪽의 삭제가 전파를 멈춘다(감사 M3).
  _EdgeTombstone   `ekey` 만 `pkns.ekey` 로 다시 짓는다.
                   ⚠ **`src`·`dst` 열은 논리로 남긴다.** 이 행은 동기화 와이어로
                   그대로 나가고 와이어에 U+001F 가 있어서는 안 된다(계약 테스트가
                   고정한다). 여기서 같이 합성하면 이관이 그 계약을 깬다.
  _DocMeta         `doc_id` 는 노드 id 가 아니다 — docstore 는 `doc_id` 를 합성하지
  _DocTombstone    않는다(`pkns.has_sep` 로 **거부**할 뿐이다). 그대로 나른다.
  _SyncMeta        커서·매핑. 그대로 나른다.
  _SchemaMeta      대상 스토어 자신의 사실이라 **안 나른다**(#115 와 같은 판단).
  _MergeRejection  `pair_key`·`a_id`·`b_id` 는 전부 **논리** id 다(api/query.py
                   `rejection_pair_key`). 그대로 나른다.
  _SyncBase        owner 열이 **없어** 합성할 수 없다. 행이 있으면 **거절한다** —
                   추측해서 옮기느니 멈추는 쪽이다. 로컬 전용 표라 허브는 0 이다.

## 먼저 재라 — 재는 도구는 이미 있다

`scripts/measure_pk_collisions.py` 가 PR-0 때부터 이 판정에 필요한 것을 전부 잰다:
라벨별 owner 분포 · 레거시 기본 owner · PK 나 문자열 필드의 U+001F · owner 를 나눠
가진 슬러그 · title==id 폴백 행 · doc 미러 id 형식.

    python scripts/measure_pk_collisions.py --data-dir <원본 사본>

⚠ 그 스크립트의 머리주석이 **"슬러그 중복 소유 0" 을 어떻게 읽어야 하는지 이미 적어
두었다**: 엔진이 라벨별 PK 유일성을 강제하므로 그 수는 어떤 단일 스토어에서도 늘 0이고,
"충돌이 없었다"가 아니라 "이 스토어의 데이터에 보이지 않는다"로만 읽어야 한다. 그것이
H1 이 **조용한** 결함인 이유이기도 하다.

⚠ 이 자리에 `scripts/pkns_preflight.py` 라는 두 번째 측정 도구를 잠깐 두었다가 걷어냈다.
같은 것을 두 번 재고 있었고, 위의 주의사항까지 "새로 발견한 것"으로 다시 적고 있었다.
찾아보지 않고 지은 대가다. 재는 것은 저쪽 하나이고 이 파일은 옮기기만 한다.

## 쓰는 법

    python scripts/migrate_pk_namespacing.py --data <원본> --out <대상>          # 예행
    python scripts/migrate_pk_namespacing.py --data <원본> --out <대상> --apply  # 수행

⚠ **라이브 DB 를 원본으로 주지 마라.** 공유 스냅샷을 뜨고 받은 사본을 줄 것
(`scripts/pkns_preflight.py` 독스트링의 절차). SMB 위에서 임베디드 DB 를 직접
여는 것이 하드다운 3회의 공통 원인이었다.

⚠ **`VINC_PK_NAMESPACED` 를 켠 채로 돌리지 마라.** 이 스크립트는 합성된 값을
**리터럴로** 쓴다. 플래그가 켜져 있으면 읽기 경로가 그 값을 한 번 더 벗겨 낼 수 있어
검증이 무엇을 재는지 모르게 된다. 켜는 것은 이관이 끝나고 대상 스토어를 붙일 때다.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import pkns                          # noqa: E402
from store import embedded_schema as schema     # noqa: E402
from store import migrate as logical            # noqa: E402
from store.backend import open_store            # noqa: E402
from store.config import load_config            # noqa: E402

#: 메타 표별 처리. **이 표가 정본이다** — 사전점검이 이것을 읽는다.
#:   rewrite : 합성해서 다시 쓸 열 (빈 튜플이면 그대로 나른다)
#:   carry   : 나를 것인가
#:   refuse  : 행이 있으면 이관을 멈출 것인가
META_PLAN: dict[str, dict] = {
    "_Tombstone":      {"rewrite": ("node_id",), "carry": True,  "refuse": False},
    # ekey 만. src·dst 는 와이어 계약 때문에 논리로 남는다(모듈 독스트링 참조).
    "_EdgeTombstone":  {"rewrite": ("ekey",),    "carry": True,  "refuse": False},
    "_DocMeta":        {"rewrite": (),           "carry": True,  "refuse": False},
    "_DocTombstone":   {"rewrite": (),           "carry": True,  "refuse": False},
    "_SyncMeta":       {"rewrite": (),           "carry": True,  "refuse": False},
    "_MergeRejection": {"rewrite": (),           "carry": True,  "refuse": False},
    # owner 열이 없어 합성할 수 없다. 추측하느니 멈춘다.
    "_SyncBase":       {"rewrite": (),           "carry": False, "refuse": True},
    # 대상 스토어 자신의 사실이다.
    "_SchemaMeta":     {"rewrite": (),           "carry": False, "refuse": False},
}


@contextlib.contextmanager
def namespacing_on():
    """`transform` 동안만 플래그를 켠다.

    ⚠ **`pkns.compose` 는 플래그가 꺼져 있으면 항등함수다**(`if not enabled(): return id`).
    이 스크립트는 플래그를 끄고 돌리라고 요구하므로, 그냥 부르면 합성이 통째로 무효가
    되고 **행 수는 맞는데 키가 안 바뀐 이관본**이 나온다. 실제로 처음 그렇게 만들었고,
    아래 `verify` 의 "합성 안 된 PK 가 N행 남았다"가 그것을 잡았다.

    같은 로직을 여기 베껴 쓰지 않는 이유: 합성 규칙(구분자·멱등성)이 표 둘이 되면 이관과
    런타임이 다른 키를 만드는 날이 온다. 그러니 **진짜 compose 를 부르되 그 순간만 켠다.**
    읽기·쓰기 경로는 꺼진 채로 둔다 — 켠 채로 export 하면 백엔드가 `strip_deep` 으로
    읽은 값을 다시 벗겨 내 무엇을 재는지 모르게 된다.
    """
    prev = os.environ.get("VINC_PK_NAMESPACED")
    os.environ["VINC_PK_NAMESPACED"] = "on"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("VINC_PK_NAMESPACED", None)
        else:
            os.environ["VINC_PK_NAMESPACED"] = prev


def owner_scoped() -> set[str]:
    """PK 를 합성할 수 있는 노드 라벨. 스키마 파생이라 새 라벨이 자동 추종한다."""
    return set(schema.owner_scoped_labels())


def read_meta(store) -> dict[str, list[dict]]:
    """메타 표 전량을 읽는다. `store/migrate.export_store` 는 노드·엣지만 나르므로
    (main 기준 — `meta_table_specs` 는 아직 없다) 여기서 직접 읽는다."""
    out: dict[str, list[dict]] = {}
    for table, spec in schema._META_TABLES.items():
        cols = [c for c, _ in spec["cols"]]
        proj = ", ".join(f"t.{c} AS {c}" for c in cols)
        try:
            rows = store.run(f"MATCH (t:{table}) RETURN {proj}")
        except Exception:
            rows = []       # 미프로비저닝 표 — 빈 집합
        out[table] = list(rows or [])
    return out


def transform(dump: dict, meta: dict) -> tuple[dict, dict, dict]:
    """덤프와 메타를 이관본으로 바꾼다. 원본 객체는 안 건드린다.

    돌려주는 셋: 새 덤프 · 새 메타 · 통계."""
    scoped = owner_scoped()
    specs = schema.node_table_specs()
    stats = {"nodes_composed": 0, "nodes_left_logical": 0,
             "edges_composed": 0, "meta_rewritten": 0}

    # pk → owner. 엣지 끝점을 합성하려면 **그 노드의** owner 가 필요한데 엣지 덤프에는
    # 없다. 라벨별로 나눠 담는 이유는 다른 라벨이 같은 슬러그를 가질 수 있어서다
    # (PK 유일성은 표 단위다).
    owner_of: dict[tuple[str, str], str] = {}
    for label, rows in (dump.get("nodes") or {}).items():
        if label not in scoped:
            continue
        pk = specs[label]["pk"]
        for r in rows:
            o = r.get("owner_id") or ""
            if o:
                owner_of[(label, r.get(pk) or "")] = o

    # 이 이관이 **자기 자신을 지키는** 최소 검사. 전체 측정은 measure_pk_collisions.py
    # 가 하지만, 그것을 안 돌리고 온 사람이 있을 수 있고 여기서 걸리는 두 조건은
    # 이관을 직접 깨뜨린다: PK 에 이미 SEP 이 있으면 재합성이 멱등으로 접혀 부분
    # 이관본이 그대로 통과하고, owner 가 비면 합성할 것이 없다.
    for label, rows in (dump.get("nodes") or {}).items():
        if label not in scoped:
            continue
        pk = specs[label]["pk"]
        dirty = [r.get(pk) for r in rows if pkns.SEP in (r.get(pk) or "")]
        if dirty:
            raise SystemExit(
                f"{label}: PK 에 U+001F 가 든 행이 {len(dirty)}건 있다(예: {dirty[0]!r}). "
                "이미 물리 키면 부분 이관본이고, 슬러그에 섞인 것이면 이관 뒤 strip 이 "
                "그것을 잘라낸다. scripts/measure_pk_collisions.py 로 먼저 살필 것")

    new_nodes: dict[str, list[dict]] = {}
    for label, rows in (dump.get("nodes") or {}).items():
        pk = specs[label]["pk"]
        fresh = []
        for r in rows:
            r = dict(r)
            if label in scoped:
                o = r.get("owner_id") or ""
                nid = r.get(pk) or ""
                if o and nid:
                    r[pk] = pkns.compose(o, nid, normalize=False)
                    stats["nodes_composed"] += 1
                else:
                    # 사전점검이 막았어야 하는 행. 여기 오면 그건 점검을 건너뛴 것이다.
                    raise SystemExit(
                        f"{label} 의 행에 owner 가 없다(pk={nid!r}) — 사전점검을 "
                        "먼저 돌릴 것. 합성할 owner 없이는 이관할 수 없다")
            else:
                stats["nodes_left_logical"] += 1
            fresh.append(r)
        new_nodes[label] = fresh

    new_edges = []
    for e in dump.get("edges") or []:
        e = dict(e)
        fl, tl = e.get("from_label"), e.get("to_label")
        if fl in scoped:
            o = owner_of.get((fl, e["from"]))
            if o:
                e["from"] = pkns.compose(o, e["from"], normalize=False)
        if tl in scoped:
            o = owner_of.get((tl, e["to"]))
            if o:
                e["to"] = pkns.compose(o, e["to"], normalize=False)
        stats["edges_composed"] += 1
        new_edges.append(e)

    new_meta: dict[str, list[dict]] = {}
    for table, rows in meta.items():
        plan = META_PLAN.get(table)
        if plan is None:
            # 스키마에 표가 늘었는데 계획이 없다. **조용히 나르거나 버리지 않는다** —
            # 어느 쪽이든 사람이 판단해야 하는 자리다.
            raise SystemExit(
                f"메타 표 {table} 에 이관 계획이 없다 — META_PLAN 을 갱신할 것")
        if plan["refuse"] and rows:
            raise SystemExit(
                f"{table} 에 {len(rows)}행이 있다. 이 표는 owner 열이 없어 합성할 수 "
                "없다 — 추측해서 옮기느니 멈춘다. 로컬 전용 표이므로 허브 스냅샷이면 "
                "0 이어야 한다")
        if not plan["carry"]:
            continue
        fresh = []
        for r in rows:
            r = dict(r)
            o = r.get("owner_id") or ""
            for col in plan["rewrite"]:
                v = r.get(col) or ""
                if not (o and v):
                    continue
                if col == "ekey":
                    # 논리 src|REL|dst 를 짓고 **한 번만** 합성한다. 끝점을 먼저
                    # strip 하므로 어느 형태를 넘겨도 결과가 같다(pkns.ekey 계약).
                    r[col] = pkns.ekey(o, r.get("src") or "", r.get("rel") or "",
                                       r.get("dst") or "")
                else:
                    r[col] = pkns.compose(o, v, normalize=False)
                stats["meta_rewritten"] += 1
            fresh.append(r)
        new_meta[table] = fresh
    return ({**dump, "nodes": new_nodes, "edges": new_edges}, new_meta, stats)


def write_meta(store, meta: dict) -> int:
    """메타 행을 대상 스토어에 MERGE. PK 로 병합하고 나머지 열을 SET."""
    n = 0
    for table, rows in meta.items():
        spec = schema._META_TABLES[table]
        pk = spec["pk"]
        cols = [c for c, _ in spec["cols"]]
        setters = [f"t.{c} = ${c}" for c in cols if c != pk]
        setc = (" SET " + ", ".join(setters)) if setters else ""
        for r in rows:
            store.run(f"MERGE (t:{table} {{{pk}: ${pk}}}){setc}",
                      {c: r.get(c) for c in cols})
            n += 1
    return n


def verify(store, expect_nodes: dict, expect_meta: dict,
           source_nodes: dict) -> list[str]:
    """이관본이 기대와 같은가. **행 수만 세지 않는다** — 수가 맞아도 키가 안 바뀌었으면
    아무것도 안 한 것이고, 그건 이 이관에서 가장 조용한 고장이다.

    ⚠ 왕복 대조의 기준은 **변환 전** 행이어야 한다(`source_nodes`). 처음에 변환 후
    행에서 기준 집합을 뽑았더니 `strip(합성키)` 가 논리 id 를 주는데 기준에는 합성키만
    있어 전부 실패했다 — 검증이 자기 출력과 자기를 대조하면 아무것도 검증하지 않는다.
    같은 함정을 이 세션에서 계정 테스트에서도 한 번 밟았다."""
    problems: list[str] = []
    scoped = owner_scoped()
    specs = schema.node_table_specs()
    for label, rows in expect_nodes.items():
        pk = specs[label]["pk"]
        got = store.run(f"MATCH (n:{label}) RETURN count(n) AS c")
        got_n = (got[0]["c"] if got else 0)
        if got_n != len(rows):
            problems.append(f"{label}: {len(rows)}행을 넣었는데 {got_n}행이다")
        if label in scoped and rows:
            bad = store.run(
                f"MATCH (n:{label}) WHERE NOT n.{pk} CONTAINS '{pkns.SEP}' "
                f"RETURN count(n) AS c")
            if bad and bad[0]["c"]:
                problems.append(
                    f"{label}: 합성 안 된 PK 가 {bad[0]['c']}행 남았다")
            # strip 왕복 — 합성이 논리 id 를 되돌려 주는지. 여기서 깨지면 사용자
            # 대면 id 가 바뀐 것이고, 그건 이관이 아니라 데이터 변조다.
            sample = store.run(f"MATCH (n:{label}) RETURN n.{pk} AS k LIMIT 20")
            originals = {r.get(pk) for r in (source_nodes.get(label) or [])}
            for r in sample or []:
                if pkns.strip(r["k"]) not in originals:
                    problems.append(
                        f"{label}: strip 왕복 실패 — {r['k']!r} 가 원본 id 로 안 돌아온다")
                    break
    for table, rows in expect_meta.items():
        got = store.run(f"MATCH (t:{table}) RETURN count(t) AS c")
        got_n = (got[0]["c"] if got else 0)
        if got_n != len(rows):
            problems.append(f"{table}: {len(rows)}행을 넣었는데 {got_n}행이다")
    # 와이어 계약 — 엣지 툼스톤의 src·dst 에 SEP 이 들어가면 안 된다.
    leaked = store.run(
        f"MATCH (t:_EdgeTombstone) WHERE t.src CONTAINS '{pkns.SEP}' "
        f"OR t.dst CONTAINS '{pkns.SEP}' RETURN count(t) AS c")
    if leaked and leaked[0]["c"]:
        problems.append(
            f"_EdgeTombstone: src/dst 에 U+001F 가 {leaked[0]['c']}행 — 동기화 "
            "와이어 계약 위반이다(이 열들은 논리로 남아야 한다)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="원본 스토어. **스냅샷 사본**을 줄 것")
    ap.add_argument("--out", required=True, help="대상 스토어(없는 경로여야 한다)")
    ap.add_argument("--apply", action="store_true",
                    help="실제로 쓴다. 없으면 예행이고 대상은 안 만든다")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if pkns.enabled():
        print("VINC_PK_NAMESPACED 가 켜져 있다 — 끄고 돌릴 것(모듈 독스트링 참조)",
              file=sys.stderr)
        return 2
    if os.path.exists(args.out):
        print(f"대상 경로가 이미 있다: {args.out} — 덮어쓰지 않는다", file=sys.stderr)
        return 2

    src_cfg = load_config({"data_dir": args.data})
    src = open_store(src_cfg)
    if src is None:
        print("원본 스토어를 열지 못했다", file=sys.stderr)
        return 2
    try:
        dump = logical.export_store(src)
        meta = read_meta(src)
    finally:
        close = getattr(src, "close", None)
        if callable(close):
            close()

    with namespacing_on():
        new_dump, new_meta, stats = transform(dump, meta)
    report = {"source": args.data, "out": args.out, "applied": bool(args.apply),
              "stats": stats,
              "nodes": {l: len(r) for l, r in (new_dump["nodes"] or {}).items() if r},
              "edges": len(new_dump["edges"]),
              "meta": {t: len(r) for t, r in new_meta.items() if r}}

    if args.apply:
        dst_cfg = load_config({"data_dir": args.out})
        dst = open_store(dst_cfg)
        if dst is None:
            print("대상 스토어를 열지 못했다", file=sys.stderr)
            return 2
        try:
            res = logical.import_store(dst, new_dump, dim=dst_cfg.embedding_dim)
            res["meta"] = write_meta(dst, new_meta)
            report["imported"] = res
            problems = verify(dst, new_dump["nodes"], new_meta,
                              source_nodes=dump["nodes"])
        finally:
            close = getattr(dst, "close", None)
            if callable(close):
                close()
        report["problems"] = problems
        report["ok"] = not problems
        if problems:
            # 반쯤 이관된 스토어를 **남기지 않는다.** 남기면 누군가 그것을 붙이고,
            # 그때 증상은 "일부 노드가 안 보인다"라 원인까지 멀다. 원본은 그대로다.
            shutil.rmtree(args.out, ignore_errors=True)
            report["out_removed"] = True

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report.get("ok", True) else 1

    print(f"원본: {args.data}")
    print(f"대상: {args.out}{'' if args.apply else '  (예행 — 아무것도 안 쓴다)'}\n")
    print(f"노드 합성 {stats['nodes_composed']} · 논리 유지 {stats['nodes_left_logical']}"
          f" · 엣지 {stats['edges_composed']} · 메타 키 재작성 {stats['meta_rewritten']}")
    if report["meta"]:
        print(f"나르는 메타: {report['meta']}")
    if not args.apply:
        print("\n수행하려면 --apply. 원본은 어느 쪽이든 안 바뀐다 — 롤백은 원본을 "
              "그대로 두는 것이다.")
        return 0
    if report["problems"]:
        print("\n검증 실패:")
        for p in report["problems"]:
            print(f"  - {p}")
        print("\n⚠ 대상 스토어를 지웠다 — 반쯤 이관된 것을 남기면 누군가 붙인다."
              " 원본은 그대로다.")
        return 1
    print(f"\n적재: {report['imported']}")
    print("검증 통과 — 행 수 · 전량 합성 · strip 왕복 · 와이어 계약.")
    print("⚠ 대상을 붙일 때 VINC_PK_NAMESPACED=on 을 함께 켤 것. 안 켜면 읽기가 "
          "물리 키를 논리 id 로 취급해 아무것도 못 찾는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
