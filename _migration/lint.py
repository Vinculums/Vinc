"""신 코퍼스 린트 — 재작성 문서가 규칙을 지키는지 본다.

규칙(계획서 §P4):
  영어 전용 · 이모지 0 · em-dash 0 · Vinclums 0 · [[...]] 전량 신 doc_id 집합 내

**한국어와 `Vinclums` 는 무조건 위반이 아니다.** 금지 어휘 표가 차단 대상을 *값으로*
싣기 때문이다(`나의 기억`·`Vinclums` 자체가 데이터다). 그래서 백틱 안은 검사에서 뺀다 —
백틱 밖에 있으면 그때가 산문이고 위반이다.

사용: python lint.py rewrite/*.md
"""
import re, sys, pathlib, unicodedata

LEDGER = pathlib.Path(__file__).parent / "ledger-documents.tsv"

def new_doc_ids() -> set:
    ids = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) > 1 and cols[1] not in ("DROP", ""):
            ids.add(cols[1])
    return ids

def strip_code(s: str) -> str:
    """백틱 구간과 코드펜스를 지운다(길이·줄 보존)."""
    def blank(m): return re.sub(r"[^\n]", " ", m.group(0))
    s = re.sub(r"```.*?```", blank, s, flags=re.S)
    s = re.sub(r"`[^`\n]*`", blank, s)
    return s

def is_emoji(ch: str) -> bool:
    # 기호·그림문자 영역. 한글·한자·CJK 문장부호는 제외한다.
    o = ord(ch)
    return (0x1F300 <= o <= 0x1FAFF) or (0x2600 <= o <= 0x27BF) or o in (0xFE0F, 0x2B50)

KOREAN = re.compile(r"[가-힣]")

def lint(path: pathlib.Path, valid_ids: set) -> list:
    raw = path.read_text(encoding="utf-8")
    prose = strip_code(raw)          # 백틱 밖 = 산문
    issues = []

    for i, line in enumerate(prose.split("\n"), 1):
        if "—" in line:
            issues.append((i, "em-dash", line.strip()[:90]))
        for ch in line:
            if is_emoji(ch):
                issues.append((i, "emoji", repr(ch)))
                break
        if KOREAN.search(line):
            issues.append((i, "korean-prose", line.strip()[:90]))
        if "Vinclums" in line:
            issues.append((i, "old-org", line.strip()[:90]))

    # 상호참조도 **산문에서만** 본다. 백틱 안의 `[[doc_id]]` 는 문법을 보여주는
    # 예시이지 참조가 아니다(첫 문서에서 이 오탐을 실제로 냈다).
    for m in re.finditer(r"\[\[([^\]]+)\]\]", prose):
        target = m.group(1).split("#")[0].strip()
        if target not in valid_ids:
            ln = prose[:m.start()].count("\n") + 1
            issues.append((ln, "dangling-ref", target))

    # 프론트매터가 **YAML 로 파싱되는가**. 키 존재만 보면 부족했다 — 제목에 콜론이 있는데
    # 따옴표가 없으면 `mapping values are not allowed here` 로 블록 전체가 깨진다.
    # 실제로 3건이 그렇게 나갔고 서브에이전트가 잡았다(2026-08-07). 도구가 못 보는 결함은
    # 반드시 다시 생기므로 검사에 넣는다.
    head = raw.split("---")[1] if raw.startswith("---") else ""
    if head:
        try:
            import yaml
            yaml.safe_load(head)
        except ImportError:
            pass                                   # PyYAML 없으면 이 검사만 건너뛴다
        except Exception as e:
            issues.append((2, "frontmatter-yaml", str(e).split("\n")[0][:80]))

    for k in ("doc_id", "layer", "domain", "authority", "title", "updated"):
        if not re.search(rf"^{k}:", head, re.M):
            issues.append((1, "frontmatter", f"missing {k}"))
    if re.search(r"^status:", head, re.M):
        issues.append((1, "frontmatter", "status is abolished, use authority"))

    # 상속은 **계층 방향만** 가리킨다(canon < spec < 나머지). 구 코퍼스는 canon/spec 구분이
    # 없어 "C 가 C 를 상속"이 자연스러웠고, 신 계층으로 기계 변환하니 계약이 구현에 기대는
    # 방향이 10건 생겼다. 그것을 끊고 출처는 derives_from 으로 옮겼다(발주자 결정 2026-08-07).
    # 이 검사가 없으면 다음 사람이 같은 방향을 다시 그린다.
    ORDER = {"canon": 0, "spec": 1, "ops": 2, "plan": 2, "release": 2, "research": 2}
    def layer_of(d):
        p = d.split(".")
        return p[1] if len(p) > 1 else "?"
    me = re.search(r"^doc_id:\s*(\S+)", head, re.M)
    if me:
        my = ORDER.get(layer_of(me.group(1)), 9)
        m = re.search(r"^inherits_from:\s*\[([^\]]*)\]", head, re.M)
        for t in ([x.strip() for x in m.group(1).split(",")] if m else []):
            if not t:
                continue
            if t not in valid_ids:
                issues.append((1, "inherits-missing", t))
            elif ORDER.get(layer_of(t), 9) > my:
                issues.append((1, "inherits-inversion", f"{layer_of(me.group(1))} -> {layer_of(t)}: {t}"))
        m = re.search(r"^derives_from:\s*\[([^\]]*)\]", head, re.M)
        for t in ([x.strip() for x in m.group(1).split(",")] if m else []):
            if t and t not in valid_ids:
                issues.append((1, "derives-missing", t))

    return issues

def main():
    valid = new_doc_ids()
    total = 0
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        if not p.is_file():
            continue
        issues = lint(p, valid)
        if issues:
            print(f"\n### {p.name}")
            for ln, kind, detail in issues:
                print(f"  {ln:5d}  {kind:14s} {detail}")
            total += len(issues)
        else:
            print(f"PASS  {p.name}")
    print(f"\n=== 위반 합계 {total} ===")
    return 1 if total else 0

if __name__ == "__main__":
    sys.exit(main())
