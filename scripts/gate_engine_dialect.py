#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""엔진 방언 게이트 — LadybugDB 전용 구문이 어댑터 밖에서 늘지 않는가.

    python -X utf8 scripts/gate_engine_dialect.py            # 검사
    python -X utf8 scripts/gate_engine_dialect.py --list     # 위치를 전부 출력
    python -X utf8 scripts/gate_engine_dialect.py --update   # 줄어든 수를 허용 목록에 반영

exit 0=통과 · 1=위반.

무엇을 보나:

  ✅ 운영 코드(`api` `store` `engines` `vinc_mcp` `vinc_rag` `vinc_store` `scripts`)의
     **문자열 리터럴** 안에 있는 엔진 전용 구문을 계열별로 센다. 주석·독스트링은 보지
     않는다 — 질의로 엔진에 가는 것은 문자열뿐이다. f-string 의 고정 조각도 센다.
  ✅ 어댑터 파일(`ADAPTERS`)은 방언이 사는 자리라 세지 않는다.
  ✅ (파일, 계열) 별 수가 `scripts/engine-dialect-allowlist.json` 보다 **많으면 실패**다.
     새 질의는 저장소 계층(GraphStore)의 연산을 쓰고, 방언은 어댑터에 둔다.
  ✅ 수가 **줄었는데 허용 목록이 그대로면 실패**다. `--update` 로 줄인 수를 같은 커밋에
     넣는다. 허용 목록은 줄기만 한다 — `--update` 는 늘어난 항목이 있으면 거절한다.
  ❌ 테스트는 보지 않는다. 테스트는 실엔진을 직접 다루는 것이 정상이다.

계열은 외부 엔진(Neo4j) 어댑터로 옮길 때의 작업 단위와 같다(traversal · fts · vector ·
catalog · schema · engine_ops · functions).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

try:
    from scripts._gitutil import git
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _gitutil import git
    from _root import ROOT

ALLOWLIST = ROOT / "scripts" / "engine-dialect-allowlist.json"

#: 검사하는 최상위 폴더. 테스트(`tests/`)는 일부러 뺀다.
SCOPES = ("api", "store", "engines", "vinc_mcp", "vinc_rag", "vinc_store", "scripts")

#: 방언이 살아도 되는 파일 — 엔진 어댑터다. 새 어댑터를 만들면 여기에 더한다.
ADAPTERS = frozenset({
    "store/embedded_backend.py",
    "store/embedded_schema.py",
})

#: 그래프 스토어가 아닌 SQLite 를 쓰는 파일. `PRAGMA table_info`·`ALTER TABLE` 이
#: 같은 글자라 걸리지만 엔진 방언이 아니다.
NOT_GRAPH = frozenset({
    "vinc_mcp/store.py",
})

#: 계열 → 패턴. 대문자 키워드는 대소문자를 구분한다(영어 문장 속 소문자와 섞이지 않게).
FAMILIES: dict[str, re.Pattern] = {
    # 가변 길이 경로의 순회 의미론 — Neo4j 에는 이 표기가 없다.
    "traversal": re.compile(r"\*\s*(?:ALL\s+)?(?:TRAIL|ACYCLIC|W?SHORTEST)\b"),
    "fts": re.compile(r"\b(?:QUERY|CREATE|DROP)_FTS_INDEX\b"),
    "vector": re.compile(r"\b(?:QUERY|CREATE|DROP)_VECTOR_INDEX\b|\barray_cosine_similarity\s*\("),
    "catalog": re.compile(r"\b(?:show_tables|table_info|show_indexes|show_connection)\s*\("),
    "schema": re.compile(r"\bCREATE\s+(?:NODE|REL)\s+TABLE\b|\b(?:ALTER|DROP)\s+TABLE\b"
                         r"|\bCOPY\s+\w+\s+(?:FROM|TO)\b"),
    "engine_ops": re.compile(r"\bCHECKPOINT\b|\bLOAD\s+EXTENSION\b|\bINSTALL\s+\w+"
                             r"|\b(?:EXPORT|IMPORT)\s+DATABASE\b"
                             r"|\benable_cached_prepared_statement\b"),
    "functions": re.compile(r"\b(?:label|list_contains|list_distinct|list_cat|list_concat"
                            r"|json_path|to_json|list_extract|string_split)\s*\("),
}


def _docstring_ids(tree: ast.AST) -> set[int]:
    """식문으로만 선 문자열(독스트링 포함)의 노드 id. 질의가 아니므로 세지 않는다."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out.add(id(node.value))
    return out


def scan_source(source: str, filename: str = "<src>") -> list[tuple[str, int, str]]:
    """소스 하나에서 (계열, 줄 번호, 일치한 조각) 목록. 문법 오류면 빈 목록이 아니라 예외다."""
    tree = ast.parse(source, filename=filename)
    skip = _docstring_ids(tree)
    hits: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skip:
            continue
        text = node.value
        for family, pat in FAMILIES.items():
            for m in pat.finditer(text):
                line = getattr(node, "lineno", 0) + text.count("\n", 0, m.start())
                hits.append((family, line, m.group(0)))
    return hits


def tracked_files() -> list[str]:
    out = git(["ls-files", "--", *SCOPES], cwd=ROOT)
    return sorted(p for p in out.splitlines()
                  if p.endswith(".py") and p not in ADAPTERS and p not in NOT_GRAPH)


def scan_repo() -> dict[str, dict[str, list[tuple[int, str]]]]:
    """파일 → 계열 → [(줄, 조각)]. 일치가 없는 파일은 싣지 않는다."""
    found: dict[str, dict[str, list[tuple[int, str]]]] = {}
    for rel in tracked_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        for family, line, frag in scan_source(path.read_text(encoding="utf-8"), rel):
            found.setdefault(rel, {}).setdefault(family, []).append((line, frag))
    return found


def counts_of(found: dict) -> dict[str, dict[str, int]]:
    return {f: {fam: len(v) for fam, v in sorted(fams.items())} for f, fams in sorted(found.items())}


def compare(current: dict[str, dict[str, int]],
            allowed: dict[str, dict[str, int]]) -> tuple[list[str], list[str]]:
    """(늘어난 항목, 줄어든 항목). 문장으로 돌려준다."""
    grew, shrank = [], []
    for f in sorted(set(current) | set(allowed)):
        for fam in sorted(set(current.get(f, {})) | set(allowed.get(f, {}))):
            now, cap = current.get(f, {}).get(fam, 0), allowed.get(f, {}).get(fam, 0)
            if now > cap:
                grew.append(f"{f} [{fam}] {cap} -> {now}")
            elif now < cap:
                shrank.append(f"{f} [{fam}] {cap} -> {now}")
    return grew, shrank


def load_allowlist() -> dict[str, dict[str, int]]:
    if not ALLOWLIST.is_file():
        return {}
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))["files"]


def write_allowlist(current: dict[str, dict[str, int]]) -> None:
    total = sum(sum(v.values()) for v in current.values())
    body = {
        "_comment": "scripts/gate_engine_dialect.py 의 허용 목록. 손으로 늘리지 않는다. "
                    "줄일 때는 --update 로 다시 쓴다.",
        "total": total,
        "files": current,
    }
    ALLOWLIST.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    found = scan_repo()
    current = counts_of(found)
    if "--list" in argv:
        for f, fams in found.items():
            for fam, rows in fams.items():
                for line, frag in rows:
                    print(f"{f}:{line}: [{fam}] {frag}")
        return 0
    allowed = load_allowlist()
    grew, shrank = compare(current, allowed)
    total = sum(sum(v.values()) for v in current.values())
    if "--update" in argv:
        if grew and "--init" not in argv:
            print("허용 목록은 줄기만 한다. 늘어난 항목:", *grew, sep="\n  ")
            return 1
        write_allowlist(current)
        print(f"engine dialect allowlist: {total} sites in {len(current)} files")
        return 0
    if grew:
        print("어댑터 밖에 엔진 전용 구문이 늘었다. 저장소 계층의 연산을 쓰거나 어댑터로 옮긴다:")
        for g in grew:
            print("  " + g)
        for f, fams in found.items():
            for fam, rows in fams.items():
                if any(g.startswith(f"{f} [{fam}]") for g in grew):
                    for line, frag in rows:
                        print(f"    {f}:{line}: {frag}")
        return 1
    if shrank:
        print("엔진 전용 구문이 줄었다. 허용 목록을 같은 커밋에서 줄인다"
              " (python -X utf8 scripts/gate_engine_dialect.py --update):")
        for s in shrank:
            print("  " + s)
        return 1
    print(f"engine dialect gate: ok ({total} sites in {len(current)} files outside adapters)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
