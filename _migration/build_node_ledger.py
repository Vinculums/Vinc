"""노드 원장의 뼈대를 만든다 — 기계로 되는 것만 채우고 판단이 필요한 자리를 비운다.

⚠ **이 스크립트를 다시 돌리면 ledger-nodes.tsv 를 통째로 덮어쓴다.** 2026-08-07 에 198행
전부(영어 제목·note·disposition·수작업 도메인)가 채워졌고 그것은 기계로 복원되지 않는다.
뼈대는 이미 세워졌으므로 다시 돌릴 일이 없다. 정말 필요하면 먼저 원장을 복사해 두고,
`lint_nodes.py` 로 결과를 대조할 것.


**id 는 안 바꾼다.** 198개 전부 이미 ASCII 슬러그이고(비ASCII 0건), ADR 처럼 체계가
어긋난 것도 아니다. 바꾸는 것은 제목·props(영어)와 도메인(8종 → 7종)이다.

도메인 재배정은 대부분 기계다. 다만 `vinc/web` 은 SCHEME 이 **해체**하기로 했으므로
(정책은 product · 구현은 engineering · 운영은 infra) 한 건씩 봐야 한다. `vinc/timeline` 도
노드 종류를 도메인으로 쓴 오류였으니 Episode 48개는 각자 주제 도메인을 받아야 한다.
그 둘은 비워 두고 사람이 채운다.
"""
import io, os, re
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))

# 기계로 되는 것: 1:1 이거나 흡수가 명확한 것
AUTO = {
    "vinc/governance":   "vinc/governance",
    "vinc/security":     "vinc/security",
    "vinc/infra":        "vinc/infra",
    "vinc/design":       "vinc/design",
    "vinc/architecture": "vinc/engineering",   # SCHEME: 가르는 실익이 없어 흡수
    "vinc/mcp":          "vinc/engineering",   # SCHEME: 인터페이스는 구현이다
}
# 판단이 필요한 것
MANUAL = {
    "vinc/web":      "해체 — 정책은 product · 구현은 engineering · 운영은 infra",
    "vinc/timeline": "노드 종류를 도메인으로 쓴 오류 — 각자 주제 도메인으로",
    "":              "도메인 없음 — bridge 는 구조 노드라 원래 비어 있다",
}

rows = [l.rstrip("\n").split("\t") for l in io.open(os.path.join(BASE, "inventory", "nodes.tsv"), encoding="utf-8")][1:]
target = [r for r in rows
          if not r[0].startswith("concept:dec-") and not r[0].startswith("concept:doc.")]

out = ["id\tkind\tnew_domain\ttitle_en\tnote_en\tdisposition\told_title\told_domain\textra"]
need_domain = need_title = 0
for r in target:
    nid, kind = r[0], r[1]
    old_title = r[2] if len(r) > 2 else ""
    old_dom = r[3] if len(r) > 3 else ""
    extra = r[5] if len(r) > 5 else ""
    new_dom = AUTO.get(old_dom, "")
    if not new_dom:
        need_domain += 1
    need_title += 1
    out.append("\t".join([nid, kind, new_dom, "", "", "", old_title, old_dom, extra]))

io.open(os.path.join(BASE, "ledger-nodes.tsv"), "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")

print(f"뼈대 {len(target)}행 작성 → ledger-nodes.tsv")
print(f"  도메인 기계 배정  {len(target)-need_domain}")
print(f"  도메인 판단 필요  {need_domain}")
print(f"  영어 제목 필요    {need_title}")
print()
print("판단이 필요한 도메인:")
for d, why in MANUAL.items():
    n = sum(1 for r in target if (r[3] if len(r) > 3 else "") == d)
    if n:
        print(f"  {d or '(빈)':16s} {n:3d}   {why}")
print()
print("기계 배정 결과:")
for d, n in Counter(AUTO.get(r[3] if len(r) > 3 else "", "(판단)") for r in target).most_common():
    print(f"  {d:20s} {n:3d}")
