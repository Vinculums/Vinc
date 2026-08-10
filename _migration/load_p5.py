# -*- coding: utf-8 -*-
"""load_p5.py — P5 적재. 원장(P3)과 재작성본(P4)을 라이브 그래프에 올린다.

왜 이게 미뤄져 있었나: P7(구본 삭제)이 `Record 94 분류`·엣지 재배선 판정을 기다리는
동안 P5 까지 같이 묶여 있었다. 그런데 원장 자신이 적어 뒀듯 **신 문서 id 는 구 id 와
전부 다르므로**(`vinc.canon.*` 대 `vinc.a.*`) 적재는 구본을 건드리지 않는다. 즉
P5 는 P7 을 기다릴 이유가 없었다.

단계 (각각 따로 돌린다 — 한 번에 하지 않는다):

  nodes   원장 198행 중 keep·rewrite 192건을 **제자리 재작성**한다. id 가 같으므로
          MERGE 가 title 을 영어로, domain 을 신 7종으로, props.note 를 영어 노트로
          덮는다. 이게 "구 그래프를 신 체계로" 의 실체다.
  drops   원장이 drop 으로 표시한 6건 삭제(P2 학습자료 잔여 5 + 낡은 record 1).
  docs    재작성 54건을 신 doc_id 로 put. 구본과 id 가 달라 병존한다.
  olddocs 구 문서 54건 삭제. **docs 검증 후에만** 돈다.

    python -X utf8 load_p5.py nodes [--apply]
    python -X utf8 load_p5.py drops --apply
    python -X utf8 load_p5.py docs  --apply
    python -X utf8 load_p5.py olddocs --apply

기본은 dry-run 이다. `--apply` 없이는 아무것도 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VINC = HERE.parents[1] / "Vinc"
sys.path.insert(0, str(VINC))

OWNER = "user:019fae48-cc62-7984-b2b0-1698f4de55d4"   # 계정 그래프. 빠뜨리면 user:local 로 샌다
BATCH = 30


def _remote():
    from vinc_mcp import remote
    return remote


def _rows(name: str) -> list[dict]:
    with (HERE / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _node_payload(r: dict) -> dict:
    n = {"id": r["id"], "type": r["kind"], "title": r["title_en"]}
    # 도메인이 빈 행이 11개 있다(bridge 6 + concept 5). 빈 문자열을 보내면 기존 값을
    # 지울 수 있으므로 **아예 싣지 않는다** — 미지정 시 보존이 계약이다.
    if r.get("new_domain"):
        n["domain"] = r["new_domain"]
    if r.get("note_en"):
        n["props"] = {"note": r["note_en"]}
    return n


def cmd_nodes(apply: bool) -> int:
    rows = [r for r in _rows("ledger-nodes.tsv") if r["disposition"] in ("keep", "rewrite")]
    print(f"대상 {len(rows)}건 (keep·rewrite) · 배치 {BATCH}")
    if not apply:
        print(json.dumps(_node_payload(rows[0]), ensure_ascii=False, indent=2)[:400])
        print("\n(dry-run — 쓰려면 --apply)")
        return 0
    remote = _remote()
    done = warn = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        res = remote.call("vinc_ingest", {"owner": OWNER,
                                          "nodes": [_node_payload(r) for r in chunk]})
        if not res.get("executed"):
            print(f"  ✗ 배치 {i//BATCH+1} 실패: {str(res)[:200]}", file=sys.stderr)
            return 1
        m = res.get("merged", {}).get("nodes", {})
        done += m.get("created", 0) + m.get("matched", 0)
        for w in res.get("warnings") or []:
            warn += 1
            print(f"  ⚠ {w[:160]}")
        print(f"  배치 {i//BATCH+1}: created {m.get('created')} · matched {m.get('matched')}")
    print(f"\n적재 {done}/{len(rows)} · 경고 {warn}")
    return 0 if done == len(rows) else 1


def cmd_drops(apply: bool) -> int:
    ids = [r["id"] for r in _rows("ledger-nodes.tsv") if r["disposition"] == "drop"]
    print(f"삭제 대상 {len(ids)}건:")
    for i in ids:
        print("  " + i)
    if not apply:
        print("\n(dry-run — 지우려면 --apply)")
        return 0
    remote = _remote()
    for i in ids:
        res = remote.call("vinc_delete", {"owner": OWNER, "node_id": i})
        print(f"  {i}: executed={res.get('executed')} found={res.get('found')}")
    return 0


def cmd_docs(apply: bool) -> int:
    files = sorted((HERE / "rewrite").glob("*.md"))
    print(f"재작성 문서 {len(files)}건")
    if not apply:
        print("표본:", files[0].name)
        print("\n(dry-run — 올리려면 --apply)")
        return 0
    remote = _remote()
    ok = 0
    for f in files:
        doc_id = f.stem
        text = f.read_text(encoding="utf-8")
        res = remote.call("vinc_doc_put", {
            "owner": OWNER, "doc_id": doc_id, "text": text,
            "title": _title_of(text) or doc_id,
            "origin": f"Vinc-docs/_migration/rewrite/{f.name}@P5-2026-08-08"})
        if res.get("executed"):
            ok += 1
            print(f"  ✓ {doc_id} · chunks {res.get('chunks')} · mentions {res.get('mentions')}")
        else:
            print(f"  ✗ {doc_id}: {str(res)[:160]}", file=sys.stderr)
    print(f"\n적재 {ok}/{len(files)}")
    return 0 if ok == len(files) else 1


def _title_of(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


#: 원장에서 "구본 없음"(신규 문서)을 뜻하는 표식. 빈 문자열이 아니라 `-` 를 쓰므로
#: `if r.get(...)` 만으로는 안 걸러진다 — 실제로 55건이 잡혔고 그중 하나가 이것이었다.
#: 지금은 그런 이름의 문서가 없어 무해하지만, 삭제 대상 목록에 표식이 섞여 있는 것을
#: 정상으로 두면 언젠가 진짜 `-` 문서를 지운다.
NO_OLD_DOC = {"", "-", "n/a", "none"}


def cmd_olddocs(apply: bool) -> int:
    """구본을 지운다. **신 id 와 겹치는 것은 빼고.**

    ⚠ 이 모듈의 docstring 은 "신 문서 id 는 구 id 와 전부 다르다"고 적고 있는데
    **4건에서 거짓이다** — `vinc.ops.deployment` · `vinc.ops.incident-2026-08-04-easyauth`
    · `vinc.ops.risk-audit-2026-08-05` · `vinc.plan.code-signing` 은 재작성 때 id 를
    그대로 뒀다(주소가 이미 신 체계에 맞았으므로 옳은 판단이다). 그 결과 `docs` 단계는
    이 4건을 **제자리 재작성**했고, 여기서 구 id 로 지우면 방금 올린 재작성본이 지워진다.
    아무 오류 없이, 총계만 4 줄어든 채로.
    """
    rows = _rows("ledger-documents.tsv")
    keep = {(r.get("new_doc_id") or "").strip() for r in rows} - {"", "-", "DROP"}
    old, skipped = [], []
    for r in rows:
        oid = (r.get("old_doc_id") or "").strip()
        if oid.lower() in NO_OLD_DOC:
            continue
        (skipped if oid in keep else old).append(oid)
    if skipped:
        print(f"⚠ 제자리 재작성이라 **삭제하지 않는다** {len(skipped)}건:")
        for s in skipped:
            print("   " + s)
    print(f"구 문서 {len(old)}건 삭제 대상")
    if not apply:
        for o in old[:10]:
            print("  " + o)
        print(f"  … 외 {max(0, len(old)-10)}건\n(dry-run — 지우려면 --apply)")
        return 0
    remote = _remote()
    gone = 0
    for o in old:
        res = remote.call("vinc_doc_delete", {"owner": OWNER, "doc_id": o})
        if res.get("executed"):
            gone += 1
        print(f"  {o}: executed={res.get('executed')} found={res.get('found')}")
    print(f"\n삭제 {gone}/{len(old)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="P5 적재")
    ap.add_argument("stage", choices=["nodes", "drops", "docs", "olddocs"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not os.environ.get("VINC_REMOTE_MCP_TOKEN"):
        print("VINC_REMOTE_MCP_TOKEN 이 없다", file=sys.stderr)
        return 3
    return {"nodes": cmd_nodes, "drops": cmd_drops,
            "docs": cmd_docs, "olddocs": cmd_olddocs}[a.stage](a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
