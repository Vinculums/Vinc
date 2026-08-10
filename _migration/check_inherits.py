"""상속 그래프의 순환과 계층 역전을 찾는다.

구 코퍼스는 canon/spec 구분이 없어 "C 가 C 를 상속" 이 자연스러웠다. 그것을 신 계층으로
기계 변환하면 **계약이 구현에 기대는** 방향이 생긴다. 그걸 드러낸다.

계층 순서: canon < spec < {ops, plan, release, research}
  · canon 이 canon 밖을 상속 → 역전(계약이 구현·기록에 기댄다)
  · spec 이 spec/canon 밖을 상속 → 역전(as-built 가 연구·절차에 기댄다)
"""
import io, os, sys
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
ORDER = {"canon": 0, "spec": 1, "ops": 2, "plan": 2, "release": 2, "research": 2}

def layer(doc_id: str) -> str:
    parts = doc_id.split(".")
    return parts[1] if len(parts) > 1 else "?"

edges, decl = [], {}
for i, l in enumerate(io.open(os.path.join(BASE, "inherits_map.tsv"), encoding="utf-8")):
    if i == 0:
        continue
    c = l.rstrip("\n").split("\t")
    if not c or not c[0]:
        continue
    tgts = [x for x in (c[1].split(",") if len(c) > 1 and c[1] else []) if x]
    decl[c[0]] = tgts
    for t in tgts:
        edges.append((c[0], t))

# 원장의 신 doc_id 집합과 대조
valid = set()
for i, l in enumerate(io.open(os.path.join(BASE, "ledger-documents.tsv"), encoding="utf-8")):
    if i == 0:
        continue
    c = l.rstrip("\n").split("\t")
    if len(c) > 1 and c[1] not in ("DROP", ""):
        valid.add(c[1])

print(f"선언 {len(decl)}건 · 엣지 {len(edges)}개")
missing_doc = sorted(valid - set(decl))
if missing_doc:
    print(f"  선언이 없는 문서: {missing_doc}")
bad_t = sorted({t for _, t in edges if t not in valid})
if bad_t:
    print(f"  존재하지 않는 대상: {bad_t}")

print()
print("=== 계층 역전 ===")
inv = [(s, t) for s, t in edges if ORDER.get(layer(t), 9) > ORDER.get(layer(s), 9)]
if not inv:
    print("  없음")
for s, t in sorted(inv):
    print(f"  {layer(s):8s} {s}\n           -> {layer(t):8s} {t}")
print(f"  합계 {len(inv)} / {len(edges)}")

print()
print("=== 순환 ===")
g = defaultdict(list)
for s, t in edges:
    g[s].append(t)
WHITE, GREY, BLACK = 0, 1, 2
color, cycles = defaultdict(int), []
def dfs(n, stack):
    color[n] = GREY
    stack.append(n)
    for m in g.get(n, []):
        if color[m] == GREY:
            cycles.append(stack[stack.index(m):] + [m])
        elif color[m] == WHITE:
            dfs(m, stack)
    stack.pop()
    color[n] = BLACK
for n in list(decl):
    if color[n] == WHITE:
        dfs(n, [])
if not cycles:
    print("  없음")
for c in cycles:
    print("  " + " -> ".join(c))

print()
print("=== 피상속 상위 (허브) ===")
cnt = defaultdict(int)
for _, t in edges:
    cnt[t] += 1
for t, n in sorted(cnt.items(), key=lambda x: -x[1])[:8]:
    print(f"  {n:3d}  {t}")
