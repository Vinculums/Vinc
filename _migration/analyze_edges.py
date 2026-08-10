"""엣지 467개를 **판단이 필요한 것**과 **따라오는 것**으로 가른다.

"467개를 다시 설계한다"는 다루기 어려운 크기다. 실제로는 성격이 다른 네 무리이고,
사람이 한 건씩 물어야 하는 것은 그중 하나뿐이다. 이 스크립트가 그 분류를 낸다.
"""
import io, os, sys
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))

def load(path, skip=1):
    out = []
    for i, l in enumerate(io.open(os.path.join(BASE, path), encoding="utf-8")):
        if i < skip:
            continue
        c = l.rstrip("\n").split("\t")
        if c and c[0]:
            out.append(c)
    return out

edges = [r for r in load("inventory/edges.tsv") if len(r) >= 3]
nodes = {r[0]: r for r in load("inventory/nodes.tsv") if len(r) >= 3}

def kind(nid: str) -> str:
    if nid.startswith("concept:dec-"):
        return "ADR"
    if nid.startswith("concept:doc."):
        return "DOC"
    return nid.split(":")[0].upper()

def domain(nid: str) -> str:
    r = nodes.get(nid)
    return (r[3] if r and len(r) > 3 else "") or ""

print("=== 1. 타입별 · 양끝 종류 ===")
for t, n in Counter(r[1] for r in edges).most_common():
    pairs = Counter(f"{kind(r[0])}>{kind(r[2])}" for r in edges if r[1] == t)
    top = "  ".join(f"{k} {v}" for k, v in pairs.most_common(4))
    print(f"  {t:16s} {n:3d}   {top}")

print()
print("=== 2. 네 무리로 분류 ===")
DERIVED   = {"inherits_from"}                      # 프론트매터 미러 — 신 코퍼스에 원천 없음
STRUCT    = {"attaches_to", "about"}               # 노드에 딸린 부착 — 노드가 정해지면 따라온다
BRIDGE    = {"connects_via"}                       # 물리화 여부가 별도 결정
JUDGE     = {"relates_to", "derives_from", "prerequisite_of"}

buckets = defaultdict(list)
for r in edges:
    t = r[1]
    b = ("derived" if t in DERIVED else
         "structural" if t in STRUCT else
         "bridge" if t in BRIDGE else
         "judgement" if t in JUDGE else "unknown")
    buckets[b].append(r)

label = {
    "derived":    "파생 — 원천이 사라졌다. 옮기는 것이 아니라 다시 만들거나 버린다",
    "structural": "구조 — 노드가 정해지면 따라온다. 사람 판단 불요",
    "bridge":     "브리지 — 물리화를 유지할지가 별도 결정",
    "judgement":  "판단 — 이 관계가 무엇을 주장하는지 한 건씩 물어야 한다",
}
for b in ("derived", "structural", "bridge", "judgement", "unknown"):
    if buckets[b]:
        print(f"  {b:11s} {len(buckets[b]):3d}   {label.get(b,'')}")

print()
print("=== 3. 판단 대상의 실제 크기 ===")
j = buckets["judgement"]
same = sum(1 for r in j if domain(r[0]) and domain(r[0]) == domain(r[2]))
cross = sum(1 for r in j if domain(r[0]) and domain(r[2]) and domain(r[0]) != domain(r[2]))
unk = len(j) - same - cross
print(f"  같은 도메인   {same:3d}  (도메인 8>7 재편으로 판정이 바뀔 수 있다)")
print(f"  도메인 넘음   {cross:3d}  (교차 관계 — 값이 높다)")
print(f"  도메인 미상   {unk:3d}")

print()
print("=== 4. relates_to 만 따로 — 양끝이 둘 다 개념인가 ===")
rt = [r for r in j if r[1] == "relates_to"]
cc = sum(1 for r in rt if kind(r[0]) == "CONCEPT" and kind(r[2]) == "CONCEPT")
withadr = sum(1 for r in rt if "ADR" in (kind(r[0]), kind(r[2])))
withdoc = sum(1 for r in rt if "DOC" in (kind(r[0]), kind(r[2])))
print(f"  개념 <> 개념        {cc:3d}")
print(f"  한쪽이 ADR          {withadr:3d}   (결정과 개념의 연결 — 유지 가치 높다)")
print(f"  한쪽이 문서 스텁    {withdoc:3d}   (문서 언급으로 대체 가능한지 검토)")

print()
print("=== 5. 차수 상위 노드 (엣지가 몰린 곳 = 재설계 효과가 큰 곳) ===")
deg = Counter()
for r in edges:
    deg[r[0]] += 1
    deg[r[2]] += 1
for nid, d in deg.most_common(12):
    r = nodes.get(nid)
    title = (r[2] if r and len(r) > 2 else "")[:38]
    print(f"  {d:3d}  {nid:42s} {title}")
