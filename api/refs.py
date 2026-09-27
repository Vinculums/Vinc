"""코드의 그래프 인용을 스캔하고 오프라인/라이브에서 검증한다."""
from __future__ import annotations

import io
import json
import os
import re
import sys

from scripts.gate_forbidden import ALL_RULES
from vinc_mcp import remote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "scripts", "graph-refs.json")
SCAN_EXT = (".py", ".ts", ".tsx", ".rs", ".md", ".yml", ".yaml", ".tf")
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "target", "build",
             "_migration", "__pycache__", ".claude"}
SKIP_FILES = {"scripts/gate_graph_refs.py", "scripts/refresh_graph_refs.py"}
WIKILINK = re.compile(r"\[\[([^\]\n]{1,120})\]\]")
DOC_ID = re.compile(r"^vinc\.[a-z0-9]+\.[a-z0-9.-]+$")
NODE_ID = re.compile(r"^(record|concept|decision|episode|bridge|domain):[a-z0-9._-]+$")
GEN_DOC_ID = re.compile(r"^[0-9a-f]{17}$")
NODE_KINDS = ("record:", "concept:", "decision:", "episode:", "bridge:", "domain:")
TRIES = 3
MISS_CEILING = 20


def normalize(raw: str) -> str:
    return re.split(r"\s*§", raw.strip(), maxsplit=1)[0].strip()


def is_citation(ref: str) -> bool:
    return bool(DOC_ID.match(ref) or NODE_ID.match(ref) or GEN_DOC_ID.match(ref))


def scan_file(path: str) -> list[tuple[int, str]]:
    out = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            for match in WIKILINK.finditer(line):
                ref = normalize(match.group(1))
                if is_citation(ref):
                    out.append((lineno, ref))
    return out


def scan_repo(root: str = ROOT) -> list[tuple[str, int, str]]:
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(SCAN_EXT):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if rel in SKIP_FILES:
                continue
            for lineno, ref in scan_file(full):
                hits.append((rel, lineno, ref))
    return hits


def scan_roots(roots: list[str]) -> list[tuple[str, int, str]]:
    return [(f"{i}:{path}" if len(roots) > 1 else path, line, ref)
            for i, root in enumerate(roots) for path, line, ref in scan_repo(root)]


def load_manifest(path: str = MANIFEST) -> set[str]:
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return set(data.get("docs") or []) | set(data.get("nodes") or [])


def offline_check(*, manifest: str = MANIFEST, roots: list[str] | None = None,
                  scan=None, load=None) -> int:
    roots = roots or [ROOT]
    if not os.path.exists(manifest):
        print(f"[gate_graph_refs] BLOCK — 매니페스트가 없다: {manifest}\n"
              f"  `python scripts/refresh_graph_refs.py` 로 만들고 커밋할 것.")
        return 2
    known = (load or load_manifest)(manifest)
    hits = (scan or scan_repo)() if roots == [ROOT] else scan_roots(roots)
    missing = [(f, n, r) for f, n, r in hits if r not in known]
    if missing:
        print(f"[gate_graph_refs] BLOCK — 그래프에 없는 인용 {len(missing)}건 "
              f"(매니페스트 {len(known)}개 대조)")
        for file, lineno, ref in missing:
            print(f"  {file}:{lineno}  [[{ref}]]")
        print("\n  고치는 법: 그래프에 그 문서·노드를 쓰거나, 인용을 실재하는 id 로 바꾼다.\n"
              "  그래프에 방금 썼다면 `python scripts/refresh_graph_refs.py` 로 매니페스트를\n"
              "  갱신해 같은 커밋에 넣을 것 — 매니페스트가 낡으면 이 게이트가 거짓말한다.")
        return 1
    print(f"[gate_graph_refs] PASS — 인용 {len(hits)}건 전부 실재 "
          f"(매니페스트 {len(known)}개, root={roots[0]})")
    return 0


def _citable(ident: str) -> bool:
    return not any(rule.pattern.search(ident) for rule in ALL_RULES)


def fetch_docs() -> list[str]:
    docs = []
    cursor = None
    while True:
        args = {"limit": 200}
        if cursor:
            args["cursor"] = cursor
        res = remote.call("vinc_doc_list", args)
        if res.get("isError") or res.get("error"):
            raise SystemExit(f"[refresh] 원격 조회 실패: {res.get('error')}")
        docs.extend(d["doc_id"] for d in res.get("docs", []) if _citable(d["doc_id"]))
        if not res.get("has_more"):
            return docs
        cursor = res.get("next_cursor")
        if not cursor:
            return docs


def verify_nodes(ids: list[str]) -> tuple[list[str], list[str]]:
    alive, unreachable = [], []
    for node_id in sorted(set(ids)):
        for _ in range(TRIES):
            res = remote.call("vinc_neighbors", {"node": node_id, "limit": 1})
            if not res.get("isError"):
                break
        if res.get("isError"):
            unreachable.append(node_id)
            print(f"  [unreachable] {node_id} — {res.get('error')}")
        elif res.get("found"):
            alive.append(node_id)
        else:
            print(f"  [miss] {node_id} — 그래프에 없다")
    return alive, unreachable


def live_check(*, scan=scan_repo, fetch=fetch_docs, verify=verify_nodes) -> int:
    if not os.environ.get("VINC_REMOTE_MCP_TOKEN"):
        print("[check] VINC_REMOTE_MCP_TOKEN 미설정 — 라이브 대조는 키가 필요하다",
              file=sys.stderr)
        return 2
    hits = scan()
    cited = sorted({r for _, _, r in hits})
    cited_nodes = [r for r in cited if r.startswith(NODE_KINDS)]
    cited_docs = [r for r in cited if not r.startswith(NODE_KINDS)]
    live_docs = set(fetch())
    alive_nodes, unreachable = verify(cited_nodes)
    alive_nodes = set(alive_nodes)
    if unreachable:
        print(f"::notice::라이브 대조를 완료하지 못했다 — 노드 {len(unreachable)}건을 "
              "물어보지 못했다(원격 도달 실패). 인용이 썩었다는 뜻이 아니다.")
        return 0
    dead = ([d for d in cited_docs if d not in live_docs]
            + [n for n in cited_nodes if n not in alive_nodes])
    if dead:
        print(f"[check] 죽은 인용 {len(dead)}건 — 코드가 가리키는 id 가 라이브 그래프에 없다.",
              file=sys.stderr)
        where = {}
        for path, lineno, ref in hits:
            where.setdefault(ref, []).append(f"{path}:{lineno}")
        for ref in sorted(dead):
            print(f"  [dead] {ref} — {', '.join(where.get(ref, ['?']))}", file=sys.stderr)
        print("  그래프 쪽이 옳으면 인용을 후속 id 로 옮기고, 코드 쪽이 옳으면 "
              "노드를 복원한 뒤 refresh 로 매니페스트를 갱신한다.", file=sys.stderr)
        return 1
    print(f"[check] 인용 {len(cited)}종 전부 라이브에 실재 "
          f"(문서 {len(cited_docs)} · 노드 {len(cited_nodes)})")
    return 0


def refresh(*, manifest: str = MANIFEST, root: str = ROOT,
            scan=scan_repo, fetch=fetch_docs, verify=verify_nodes) -> int:
    cited = [ref for _, _, ref in scan()]
    cited_nodes = [r for r in cited if r.startswith(NODE_KINDS)]
    print(f"[refresh] 코드가 인용한 id {len(set(cited))}종 (노드 {len(set(cited_nodes))}종)")
    docs = fetch()
    print(f"[refresh] 문서 {len(docs)}개")
    nodes, unreachable = verify(cited_nodes)
    if unreachable:
        print(f"[refresh] 중단 — 노드 {len(unreachable)}건을 물어보지 못했다. "
              "매니페스트를 구멍 난 채로 쓰지 않는다.", file=sys.stderr)
        return 1
    missing = set(cited_nodes) - set(nodes)
    ceiling = max(MISS_CEILING, len(set(cited_nodes)) // 10)
    if len(missing) > ceiling:
        print(f"[refresh] 중단 — 인용 노드 {len(set(cited_nodes))}종 중 {len(missing)}건이 "
              f"'없다'로 왔다(상한 {ceiling}). 이 규모는 인용이 썩은 것이 아니라 허브의 "
              "정확 id 조회가 깨진 것이다. 매니페스트를 쓰지 않는다.", file=sys.stderr)
        return 1
    print(f"[refresh] 노드 {len(nodes)}개 확인")
    payload = {"note": "generated by scripts/refresh_graph_refs.py — do not hand edit. "
                       "gate_graph_refs.py checks code citations against this. "
                       "Identifiers only: titles would carry forbidden vocabulary into the repo.",
               "docs": sorted(docs), "nodes": sorted(nodes)}
    with io.open(manifest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"[refresh] 썼다: {os.path.relpath(manifest, root)}")
    return 0
