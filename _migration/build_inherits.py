"""구 `inherits_from` 선언을 신 doc_id 로 옮기고, 빠진 곳을 드러낸다.

구 체계는 이것을 프론트매터에 선언하고 엣지로 미러했다(75개). 신 문서는 그 선언을
하나도 안 했으므로 원천이 사라진 상태다. 발주자 결정: **명시 복원**.

의미는 계층 사슬이 아니다. `vinc.a.terms` 가 7회, `vinc.d.index` 가 9회 피상속된 것이
말해 주듯 **"이 문서의 사실이 저 문서에 기댄다"** 는 실제 의존이다. 그 성격을 유지한다.

옮길 때 손대는 것 셋:
  · `weg.c.memory-standalone` 은 코퍼스 밖이라 영원히 해소 불가 → 버린다(산문으로 이미 기록)
  · `vinc.b.architecture` 는 1→2 로 갈렸다. 상속자들이 기댄 것은 **불변 원칙**이므로 principles 로
  · `vinc.next-session` 은 drop 이라 그 선언도 함께 사라진다
"""
import io, os, re, sys

BASE = os.path.dirname(os.path.abspath(__file__))
OUTSIDE = {"weg.c.memory-standalone"}

def ledger():
    m = {}
    for i, l in enumerate(io.open(os.path.join(BASE, "ledger-documents.tsv"), encoding="utf-8")):
        if i == 0:
            continue
        c = l.rstrip("\n").split("\t")
        if len(c) > 1 and c[0] and c[0] != "-":
            m[c[0]] = c[1]
    return m

MAP = ledger()
# 분할된 문서를 기댄 쪽은 불변 원칙에 기댄 것이다.
MAP_OVERRIDE = {"vinc.b.architecture": "vinc.canon.principles"}

def to_new(old: str):
    old = old.strip()
    if old in OUTSIDE:
        return None
    n = MAP_OVERRIDE.get(old) or MAP.get(old)
    if not n or n == "DROP":
        return None
    return n

def old_decl(path):
    s = io.open(path, encoding="utf-8").read()
    body = s.split("-->", 1)[-1]
    m = re.search(r"^inherits_from:\s*\[([^\]]*)\]", body[:2000], re.M)
    if not m:
        return []
    return [x.strip() for x in m.group(1).split(",") if x.strip()]

rows = []
for fn in sorted(os.listdir(os.path.join(BASE, "export"))):
    if not fn.endswith(".md"):
        continue
    old_id = fn[:-3]
    new_id = MAP.get(old_id)
    if not new_id or new_id == "DROP":
        continue
    olds = old_decl(os.path.join(BASE, "export", fn))
    news, dropped = [], []
    for o in olds:
        n = to_new(o)
        (news if n else dropped).append(n or o)
    seen, uniq = set(), []
    for n in news:
        if n != new_id and n not in seen:
            seen.add(n)
            uniq.append(n)
    rows.append((new_id, uniq, dropped, bool(olds)))

have = [r for r in rows if r[1]]
none = [r for r in rows if not r[1]]
print(f"신 문서 {len(rows)}건 중 상속 선언이 옮겨진 것 {len(have)} · 빈 것 {len(none)}")
print()
print("=== 옮겨진 선언 ===")
for new_id, ns, dr, had in sorted(have):
    d = f"   (버림: {', '.join(dr)})" if dr else ""
    print(f"  {new_id}\n      -> {', '.join(ns)}{d}")
print()
print("=== 선언이 비는 문서 (판단으로 채워야 한다) ===")
for new_id, _, dr, had in sorted(none):
    why = "구 선언이 코퍼스 밖만 가리켰다" if dr else ("구 선언 없음" if not had else "")
    print(f"  {new_id:46s} {why}")
