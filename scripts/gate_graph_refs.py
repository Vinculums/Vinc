# -*- coding: utf-8 -*-
"""그래프 인용 게이트 — 코드가 `[[...]]` 로 가리키는 그래프 id 가 실재하는지 본다.

이 리포는 원래부터 코드 주석에서 그래프 문서를 인용해 왔고, **그 인용의 대다수가
조용히 끊어져 있었다**(2026-08-18 실측: 24건 중 19건. P5 개명 2026-08-09 이후로
`vinc.c.chunk-ssot` 같은 구 id 가 그대로 남았고, F8 개명 때 어떤 세션이 **주석 속
인용의 철자까지** 바꿔 존재한 적 없는 id 를 만들기도 했다 — 링크가 끊어진 덕에
어휘 게이트를 통과했다). 근거를 그래프로 옮기는 작업이 진행 중이라 인용은 앞으로 더
늘어난다 — 그래서 인용은 검사받아야 한다.

**오프라인이다.** CI 에 그래프 키를 넣지 않는다. 대조 대상은 커밋된 매니페스트
(`scripts/graph-refs.json`)이고, 그 매니페스트는 `scripts/refresh_graph_refs.py` 가
원격 그래프에서 만들어 사람이 코드 변경과 **같은 커밋에** 넣는다.

검사 형태는 `[[...]]` 하나뿐이다:

    [[vinc.spec.dataplane]]        문서
    [[vinc.spec.dataplane §7]]     문서 + 절(절은 대조하지 않는다)
    [[record:some-fact]]           노드

그 밖의 `[[...]]`(예: 에디터 코드의 `[[doc_id]]` 자리표시자, 테스트 데이터)는 모양이
안 맞으므로 **무시**한다 — 위키링크 문법 자체가 이 제품의 기능이라 전부 인용으로
읽으면 오탐이 난다.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "scripts", "graph-refs.json")

_SCAN_EXT = (".py", ".ts", ".tsx", ".rs", ".md")
_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "target", "build",
              "_migration", "__pycache__", ".claude", "design-hub"}
# 이 문법을 **정의·예시**하는 파일들. gate_forbidden 이 `CLAUDE.md` 를 빼는 것과 같은
# 사유 — 규칙을 적은 자리를 규칙이 잡으면 규칙을 적을 수가 없다.
_SKIP_FILES = {"scripts/gate_graph_refs.py", "scripts/refresh_graph_refs.py"}

_WIKILINK = re.compile(r"\[\[([^\]\n]{1,120})\]\]")
_DOC_ID = re.compile(r"^vinc\.[a-z0-9]+\.[a-z0-9.-]+$")
_NODE_ID = re.compile(r"^(record|concept|decision|episode|bridge|domain):[a-z0-9._-]+$")


def normalize(raw: str) -> str:
    """`vinc.spec.dataplane §7` → `vinc.spec.dataplane`. 절 표기는 대조하지 않는다 —
    절 번호는 문서가 개정되면 움직이는데, 게이트가 그것까지 붙들면 문서를 고칠 때마다
    코드가 빨개진다. 존재 여부만 본다."""
    return re.split(r"\s*§", raw.strip(), maxsplit=1)[0].strip()


def is_citation(ref: str) -> bool:
    return bool(_DOC_ID.match(ref) or _NODE_ID.match(ref))


def scan_file(path: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            for m in _WIKILINK.finditer(line):
                ref = normalize(m.group(1))
                if is_citation(ref):
                    out.append((lineno, ref))
    return out


def scan_repo(root: str = ROOT) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(_SCAN_EXT):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if rel in _SKIP_FILES:
                continue
            for lineno, ref in scan_file(full):
                hits.append((rel, lineno, ref))
    return hits


def load_manifest(path: str = MANIFEST) -> set[str]:
    """매니페스트는 **id 목록**이다. 제목은 싣지 않는다 — 제목에는 금지 어휘가 들어
    있고(예: 구 서피스명을 담은 문서 제목), 그러면 이 파일 자체가 `gate_forbidden`
    위반이 된다. 게이트가 필요로 하는 것은 존재 여부뿐이다."""
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return set(data.get("docs") or []) | set(data.get("nodes") or [])


def main() -> int:
    if not os.path.exists(MANIFEST):
        print(f"[gate_graph_refs] BLOCK — 매니페스트가 없다: {MANIFEST}\n"
              f"  `python scripts/refresh_graph_refs.py` 로 만들고 커밋할 것.")
        return 2
    known = load_manifest()
    hits = scan_repo()
    missing = [(f, n, r) for f, n, r in hits if r not in known]
    if missing:
        print(f"[gate_graph_refs] BLOCK — 그래프에 없는 인용 {len(missing)}건 "
              f"(매니페스트 {len(known)}개 대조)")
        for f, n, r in missing:
            print(f"  {f}:{n}  [[{r}]]")
        print("\n  고치는 법: 그래프에 그 문서·노드를 쓰거나, 인용을 실재하는 id 로 바꾼다.\n"
              "  그래프에 방금 썼다면 `python scripts/refresh_graph_refs.py` 로 매니페스트를\n"
              "  갱신해 같은 커밋에 넣을 것 — 매니페스트가 낡으면 이 게이트가 거짓말한다.")
        return 1
    print(f"[gate_graph_refs] PASS — 인용 {len(hits)}건 전부 실재 "
          f"(매니페스트 {len(known)}개, root={ROOT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
