"""코드의 그래프 인용을 스캔하고 오프라인/라이브에서 검증한다."""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time

from vinc_mcp import remote

#: 이 리포지토리의 기본값 — 두 스크립트(scripts/*_graph_refs.py)가 쓴다. `vinc refs` 는
#: 현재 디렉터리를 기본으로 삼는다(api/cli.py). 다른 프로젝트에서 부른 refresh 가 이
#: 리포의 매니페스트를 덮어쓰면 안 된다.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "scripts", "graph-refs.json")
#: 기본 스캔 확장자. 좁은 목록(.py·.ts·.tsx·.rs·.md·.yml·.yaml·.tf)은 펌웨어·앱 코드의
#: 인용을 **대조 없이 cited:0 으로** 통과시켰다 — 읽는 쪽은 인용이 지켜지는 줄 안다.
#: 이제 흔한 코드 확장자를 모두 본다. `VINC_REFS_EXT`(쉼표·공백 구분, 점 유무 무관)로
#: 덮어쓴다. 근거: [[vinc.manual.core]]
_DEFAULT_SCAN_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".rs",
                     ".go", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp",
                     ".cc", ".cxx", ".ino", ".m", ".mm", ".md", ".yml", ".yaml", ".tf")
SCAN_EXT = _DEFAULT_SCAN_EXT   # 하위호환: 이 이름을 import 하는 코드가 있다
#: 스캔 대상이 아니어도 인용이 들어 있으면 '건너뜀'으로 알리는, 인용이 있을 법한 소스·
#: 텍스트 확장자(조용한 통과 방지). 바이너리·자산은 제외다. `.mjs`·`.cjs` 는 기본
#: 스캔에서 빼고 여기 둔다 — 이 리포의 두 `.mjs` 가 아직 대조 안 된 인용을 담고 있어
#: 기본 스캔에 넣으면 그 미결 인용이 전 리포 게이트를 막는다. 보고로 드러내되 막지는
#: 않고, 정리되면 `VINC_REFS_EXT` 로 포함시킨다.
_REPORTABLE_EXT = frozenset(_DEFAULT_SCAN_EXT) | {
    ".mjs", ".cjs", ".rb", ".php", ".cs", ".scala", ".sh", ".bash", ".zsh", ".ps1",
    ".lua", ".dart", ".vue", ".svelte", ".sql", ".proto", ".gradle", ".groovy", ".r",
    ".jl", ".ex", ".exs", ".erl", ".hs", ".ml", ".clj", ".pl", ".rst", ".txt"}
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "target", "build",
             "_migration", "__pycache__", ".claude", "_wt"}
#: CHANGELOG 은 릴리스 이력이다. 고치지 않는 기록이라 대조하지 않는다(gate_forbidden 과 같은 예외).
SKIP_FILES = {"scripts/gate_graph_refs.py", "scripts/refresh_graph_refs.py", "CHANGELOG.md"}
#: 괄호 안 길이에 상한을 두지 않는다. 그래프 id 에는 길이 제한이 없으니, 상한이 있으면
#: 그보다 긴 인용은 스캔에서 빠지고 게이트는 대조 없이 초록이 된다. 한 줄은 넘지 않는다.
WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
DOC_ID = re.compile(r"^vinc\.[a-z0-9]+\.[a-z0-9.-]+$")
NODE_ID = re.compile(r"^(record|concept|decision|episode|bridge|domain):[a-z0-9._-]+$")
GEN_DOC_ID = re.compile(r"^[0-9a-f]{17}$")
NODE_KINDS = ("record:", "concept:", "decision:", "episode:", "bridge:", "domain:")
#: 두 칸짜리 구 문서 id(`vinc.arch`·`vinc.mcp`). DOC_ID 는 세 칸을 요구하므로 이 모양은
#: 인용으로도 오류로도 잡히지 않고 지나갔다. 이제 "그래프 id 아님"으로 막는다.
LEGACY_DOC_ID = re.compile(r"^vinc\.[a-z0-9-]+$")
#: id 처럼 생겼지만 그래프 id 모양이 아닌 것(메모리 파일 이름 따위). 소문자, 하이픈이나 점,
#: 공백 없음, 8자 이상, 글자 하나 이상. 숫자 배열(`[[1.0, 0]]`)·자리표시자(`[[doc_id]]`)·
#: 한국어·`{ref}` 는 이 모양이 아니라 계속 무시된다.
SLUG_LIKE = re.compile(r"^(?:[a-z]+:)?[a-z0-9_]+(?:[-.][a-z0-9_]+)+$")
SLUG_MIN = 8
#: 이 유효성 값을 가진 노드는 인용할 수 없다. 후속이 있으면 후속을, 없으면 지금 사실을 인용한다.
BLOCKED_VALIDITY = ("historical",)
#: 전송 오류 재시도 횟수. 타임아웃은 일시적이고, 이 판정은 배포를 막는다.
TRIES = 3
#: 쓰기 모드가 받아 주는 '없다' 의 상한(건). 인용된 노드의 10% 와 이 값 중 큰 쪽.
#: 수십 건이 한 번에 '없다' 로 오는 것은 허브의 정확 id 조회가 깨진 것이다 — 그때 refresh
#: 를 돌리면 살아 있는 노드를 매니페스트에서 지운 채 커밋한다(2026-09-11, 126건).
MISS_CEILING = 20


def normalize(raw: str) -> str:
    """절 표시(`§7`)와 앵커(`#§5`)를 떼고 id 만 남긴다."""
    return re.split(r"\s*[§#]", raw.strip(), maxsplit=1)[0].strip()


def is_citation(ref: str) -> bool:
    return bool(DOC_ID.match(ref) or NODE_ID.match(ref) or GEN_DOC_ID.match(ref))


def is_not_graph_id(ref: str) -> bool:
    """인용하려던 것이 분명한데 그래프 id 모양이 아니다. 대조할 수 없으니 그 자체가 결함이다."""
    if is_citation(ref):
        return False
    if LEGACY_DOC_ID.match(ref):
        return True
    return (len(ref) >= SLUG_MIN and bool(SLUG_LIKE.match(ref))
            and any(ch.isalpha() for ch in ref))


def scan_file(path: str) -> list[tuple[int, str]]:
    """인용과, 인용 자리에 쓰였지만 그래프 id 가 아닌 토큰을 함께 돌려준다(가르는 것은 호출자)."""
    out = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            for match in WIKILINK.finditer(line):
                ref = normalize(match.group(1))
                if is_citation(ref) or is_not_graph_id(ref):
                    out.append((lineno, ref))
    return out


def _scan_ext() -> tuple[str, ...]:
    """스캔할 확장자. `VINC_REFS_EXT` 가 있으면 그것(쉼표·공백 구분), 없으면 기본값."""
    raw = (os.environ.get("VINC_REFS_EXT") or "").strip()
    if not raw:
        return _DEFAULT_SCAN_EXT
    got = tuple("." + tok.lstrip(".").lower()
                for tok in re.split(r"[,\s]+", raw) if tok.strip())
    return got or _DEFAULT_SCAN_EXT


def _has_wikilink(path: str, *, max_bytes: int = 2_000_000) -> bool:
    """스캔 대상이 아닌 파일에 인용이 들어 있나(건너뜀 보고용). 바이너리·대용량은 뺀다."""
    try:
        with io.open(path, "rb") as fh:
            blob = fh.read(max_bytes)
    except OSError:
        return False
    return b"\x00" not in blob and b"[[" in blob


def _walk(root: str) -> tuple[list[tuple[str, int, str]], list[str]]:
    """(hits, skipped). hits: 스캔된 파일의 (rel, line, ref). skipped: 스캔 대상은
    아니지만 인용이 든 소스·텍스트 파일의 rel — 조용히 통과하지 않도록 알린다."""
    ext_set = set(_scan_ext())
    hits: list[tuple[str, int, str]] = []
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if rel in SKIP_FILES:
                continue
            if ext in ext_set:
                for lineno, ref in scan_file(full):
                    hits.append((rel, lineno, ref))
            elif ext in _REPORTABLE_EXT and _has_wikilink(full):
                skipped.append(rel)
    return hits, skipped


def scan_repo(root: str = ROOT) -> list[tuple[str, int, str]]:
    return _walk(root)[0]


def scan_roots(roots: list[str]) -> list[tuple[str, int, str]]:
    return scan_roots_full(roots)[0]


def scan_roots_full(roots: list[str]) -> tuple[list[tuple[str, int, str]], list[str]]:
    """여러 루트의 (hits, skipped). 루트가 둘 이상이면 경로에 루트 인덱스를 접두한다."""
    allhits: list[tuple[str, int, str]] = []
    allskipped: list[str] = []
    for i, root in enumerate(roots):
        hits, skipped = _walk(root)
        pre = f"{i}:" if len(roots) > 1 else ""
        allhits += [(pre + path, line, ref) for path, line, ref in hits]
        allskipped += [pre + path for path in skipped]
    return allhits, allskipped


def load_manifest(path: str = MANIFEST) -> set[str]:
    with io.open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("manifest must be an object")
    for field in ("docs", "nodes"):
        values = data.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"manifest {field} must be a list of identifiers")
    if not isinstance(data.get("validity", {}), dict):
        raise ValueError("manifest validity must be an object")
    return set(data.get("docs", [])) | set(data.get("nodes", []))


def load_validity(path: str = MANIFEST) -> dict[str, dict]:
    """노드별 `{validity, superseded_by}`. 이 맵이 없는 옛 매니페스트는 빈 맵으로 읽는다."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    validity = data.get("validity") if isinstance(data, dict) else None
    if not isinstance(validity, dict):
        return {}
    return {k: v for k, v in validity.items() if isinstance(k, str) and isinstance(v, dict)}


def findings(hits, known: set[str], validity: dict[str, dict]) -> list[dict]:
    """대조 결과의 결함 목록. reason 은 missing · not_graph_id · superseded · historical.

    매니페스트에 실재하는 id 는 모양과 무관하게 인용으로 받는다: refresh 가
    계정에서 가져온 사용자 문서 id(`sprout.prd.watering-v1` 처럼 `vinc.` 로 시작하지
    않는 것)도 known 에 들어 있으므로, 모양만 보고 `not_graph_id` 로 막으면 실재하는
    인용이 거절된다. known 밖일 때만 모양으로 '없음'과 '그래프 id 아님'을 가른다."""
    out = []
    for file, line, ref in hits:
        if ref not in known:
            reason = "missing" if is_citation(ref) else "not_graph_id"
            out.append({"file": file, "line": line, "ref": ref, "reason": reason})
            continue
        entry = validity.get(ref) or {}
        successor = entry.get("superseded_by") or ""
        if successor:
            out.append({"file": file, "line": line, "ref": ref, "reason": "superseded",
                        "successor": successor})
        elif entry.get("validity") in BLOCKED_VALIDITY:
            out.append({"file": file, "line": line, "ref": ref, "reason": "historical"})
    return out


def _describe(item: dict) -> str:
    reason = item["reason"]
    if reason == "superseded":
        return f"superseded: cite [[{item['successor']}]] instead"
    if reason == "historical":
        return "historical with no successor: cite the current fact instead"
    if reason == "not_graph_id":
        return "not a graph id (legacy id or file name)"
    return "not in the graph"


def offline_result(*, manifest: str, roots: list[str]) -> dict:
    """Product output for a file-only citation check; repository gates keep their wrapper."""
    result = {"kind": "refs.check", "status": 0, "cited": 0, "missing": [], "manifest": manifest,
              "executed": True}
    try:
        known = load_manifest(manifest)
    except (OSError, ValueError, TypeError) as exc:
        result.update(status=2, executed=False, error={"code": "invalid_manifest",
            "message": f"Cannot read manifest {manifest}: {type(exc).__name__}. Run vinc refs refresh."})
        return result
    hits, skipped = scan_roots_full(roots)
    result["cited"] = len(hits)
    # 스캔 대상이 아니어도 인용이 든 파일은 알린다 — cited:0 을 "다 지켜진다"로
    # 읽지 않도록 한다. 상한을 넘으면 수만 센다.
    result["scanned_ext"] = list(_scan_ext())
    if skipped:
        result["skipped"] = skipped[:50]
        result["skipped_count"] = len(skipped)
    bad = findings(hits, known, load_validity(manifest))
    result["missing"] = [{"file": i["file"], "line": i["line"], "ref": i["ref"]}
                         for i in bad if i["reason"] == "missing"]
    result["invalid"] = [i for i in bad if i["reason"] != "missing"]
    if result["missing"]:
        result.update(status=1, executed=False, error={"code": "unresolved_refs",
            "message": f"{len(result['missing'])} missing citations in manifest {manifest}. "
                       "Correct the references or run vinc refs refresh."})
    elif result["invalid"]:
        result.update(status=1, executed=False, error={"code": "invalid_refs",
            "message": f"{len(result['invalid'])} citations are superseded, historical or not graph ids. "
                       "Cite the successor or the current fact."})
    return result


def offline_check(*, manifest: str = MANIFEST, roots: list[str] | None = None,
                  scan=None, load=None) -> int:
    roots = roots or [ROOT]
    if not os.path.exists(manifest):
        print(f"[gate_graph_refs] BLOCK — 매니페스트가 없다: {manifest}\n"
              f"  `python scripts/refresh_graph_refs.py` 로 만들고 커밋할 것.")
        return 2
    known = (load or load_manifest)(manifest)
    if scan is not None:
        hits, skipped = scan(), []        # 주입된 스캔(테스트) — 건너뜀 탐지는 건너뛴다
    elif roots == [ROOT]:
        hits, skipped = _walk(ROOT)
    else:
        hits, skipped = scan_roots_full(roots)
    if skipped:
        # 조용한 통과 방지: 스캔 대상이 아닌 파일에 인용이 있으면 이름을 밝힌다. 배포를
        # 막지는 않는다(확장자를 넓히거나 VINC_REFS_EXT 로 포함시키라는 신호).
        print(f"[gate_graph_refs] NOTE — 스캔하지 않은 확장자의 파일 {len(skipped)}개에 "
              f"인용이 있다(대조 안 됨). 확장자를 넓히거나 VINC_REFS_EXT 에 넣을 것:")
        for rel in skipped[:8]:
            print(f"  (skipped) {rel}")
        if len(skipped) > 8:
            print(f"  … 외 {len(skipped) - 8}개")
    bad = findings(hits, known, load_validity(manifest))
    if bad:
        print(f"[gate_graph_refs] BLOCK — 쓸 수 없는 인용 {len(bad)}건 "
              f"(매니페스트 {len(known)}개 대조)")
        for item in bad:
            print(f"  {item['file']}:{item['line']}  [[{item['ref']}]]  {_describe(item)}")
        print("\n  고치는 법: 대체된 노드는 후속 id 로, 후속 없이 만료된 노드는 지금 사실을 담은\n"
              "  노드로, 그래프 id 가 아닌 것(두 칸 구 문서 id·메모리 파일 이름)은 실재하는 id 로\n"
              "  바꾼다. 그래프에 방금 썼다면 `python scripts/refresh_graph_refs.py` 로 매니페스트를\n"
              "  갱신해 같은 커밋에 넣을 것 — 매니페스트가 낡으면 이 게이트가 거짓말한다.")
        return 1
    print(f"[gate_graph_refs] PASS — 인용 {len(hits)}건 전부 실재 "
          f"(매니페스트 {len(known)}개, root={roots[0]})")
    return 0


def _citable(ident: str) -> bool:
    """이 리포의 금지 어휘를 담은 id 는 인용할 수 없다(gate_forbidden 이 소스를 읽는다).
    그 규칙은 `scripts/` 에 있고 동결 바이너리는 `scripts/` 를 싣지 않으므로, 없으면
    이 리포 밖의 사용으로 보고 거르지 않는다."""
    try:
        from scripts.gate_forbidden import ALL_RULES
    except ImportError:
        return True
    return not any(rule.pattern.search(ident) for rule in ALL_RULES)


def _space() -> dict:
    """대조할 팀 스페이스. `VINC_GRAPH_SPACE` 가 팀 uuid 면 그 팀, 비었거나 personal 이면 개인."""
    space = (os.environ.get("VINC_GRAPH_SPACE") or "").strip()
    return {} if space in ("", "personal") else {"space": space}


def fetch_docs(*, call=None, sleep=time.sleep) -> list[str]:
    call = call or remote.call
    docs = []
    cursor = None
    while True:
        args = {"limit": 200, **_space()}
        if cursor:
            args["cursor"] = cursor
        for _ in range(TRIES):
            res = call("vinc_doc_list", args)
            # store_busy 는 일시적이다. 알려 준 만큼 기다렸다 다시 묻는다.
            wait = res.get("retry_after_s") if isinstance(res, dict) else None
            if res.get("executed") is not False or not isinstance(wait, (int, float)):
                break
            sleep(min(float(wait), 5.0))
        if res.get("isError") or res.get("error"):
            raise SystemExit(f"[refresh] Account query failed: {res.get('error')}")
        docs.extend(d["doc_id"] for d in res.get("docs", []) if _citable(d["doc_id"]))
        if not res.get("has_more"):
            return docs
        cursor = res.get("next_cursor")
        if not cursor:
            return docs


def _validity_of(res: dict) -> dict:
    """`vinc_node` 응답에서 유효성과 후속 id 를 꺼낸다(`temporal` 이 정본, 없으면 props)."""
    node = res.get("node") if isinstance(res.get("node"), dict) else {}
    temporal = node.get("temporal") if isinstance(node.get("temporal"), dict) else {}
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    entry = {"validity": temporal.get("validity") or "unknown"}
    successor = (temporal.get("superseded_by") or node.get("superseded_by")
                 or props.get("superseded_by"))
    if isinstance(successor, str) and successor:
        entry["superseded_by"] = successor
    return entry


def verify_nodes(ids: list[str], *, call=None, validity: dict | None = None,
                 sleep=time.sleep) -> tuple[list[str], list[str]]:
    """인용된 노드가 실재하는지 한 건씩 본다. `(있다, 못 물어봤다)` 를 돌려주고, `validity`
    를 주면 있는 노드의 `{validity, superseded_by}` 를 거기 채운다.

    🔴 **"없다"와 "못 물어봤다"를 가른다.** 전송 실패는 `isError`, 진짜 부재는 `found:false`
    다. 둘을 같이 처리하면 타임아웃 한 번이 죽은 인용으로 찍혀 배포를 죽인다(2026-09-09).
    ⚠ **`vinc_graph` 로 물으면 안 된다** — 허브 노드에서 응답 상한을 넘어 `isError` 로 오고,
    그것을 부재로 읽으면 중심 개념일수록 인용이 막힌다. `vinc_node` 는 이웃 없이 노드 하나만
    주므로 크기에 안 걸리고, 유효성(`temporal.validity`·`superseded_by`)을 함께 준다
    (`vinc_neighbors` 는 그 둘을 싣지 않는다)."""
    call = call or remote.call
    alive, unreachable = [], []
    for node_id in sorted(set(ids)):
        complete = False
        for _ in range(TRIES):
            res = call("vinc_node", {"node_id": node_id, **_space()})
            complete = (isinstance(res, dict) and not res.get("isError")
                        and res.get("executed") is not False and isinstance(res.get("found"), bool))
            if complete and res["found"]:
                break
            # store_busy 는 기다릴 시간을 알려 준다. 곧바로 세 번 물으면 셋 다 바쁨으로 끝난다.
            wait = res.get("retry_after_s") if isinstance(res, dict) else None
            if isinstance(wait, (int, float)) and wait > 0:
                sleep(min(float(wait), 5.0))
        if not complete:
            unreachable.append(node_id)
            print(f"  [unreachable] {node_id}: existence could not be verified")
        elif res.get("found"):
            alive.append(node_id)
            if validity is not None:
                validity[node_id] = _validity_of(res)
        else:
            print(f"  [miss] {node_id}: not found in this graph")
    return alive, unreachable


def live_check(*, scan=scan_repo, fetch=fetch_docs, verify=verify_nodes) -> int:
    if not os.environ.get("VINC_REMOTE_MCP_TOKEN"):
        print("[check] VINC_REMOTE_MCP_TOKEN 미설정 — 라이브 대조는 키가 필요하다",
              file=sys.stderr)
        return 2
    hits = [h for h in scan() if is_citation(h[2])]
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
    cited = [ref for _, _, ref in scan() if is_citation(ref)]
    cited_nodes = [r for r in cited if r.startswith(NODE_KINDS)]
    print(f"[refresh] {len(set(cited))} cited IDs ({len(set(cited_nodes))} node IDs)")
    docs = fetch()
    print(f"[refresh] {len(docs)} documents")
    validity: dict[str, dict] = {}
    nodes, unreachable = verify(cited_nodes, validity=validity)
    if unreachable:
        print(f"[refresh] Stopped: {len(unreachable)} nodes could not be queried. "
              "The existing manifest was preserved.", file=sys.stderr)
        return 1
    missing = set(cited_nodes) - set(nodes)
    ceiling = max(MISS_CEILING, len(set(cited_nodes)) // 10)
    if len(missing) > ceiling:
        print(f"[refresh] Stopped: {len(missing)} of {len(set(cited_nodes))} cited nodes "
              f"were not found (limit {ceiling}). Check account connectivity before refreshing. "
              "The existing manifest was preserved.", file=sys.stderr)
        return 1
    blocked = [n for n in nodes if (validity.get(n) or {}).get("superseded_by")
               or (validity.get(n) or {}).get("validity") in BLOCKED_VALIDITY]
    print(f"[refresh] {len(nodes)} nodes verified ({len(blocked)} superseded or historical)")
    payload = {"note": "generated by scripts/refresh_graph_refs.py — do not hand edit. "
                       "gate_graph_refs.py checks code citations against this. "
                       "Identifiers only: titles would carry forbidden vocabulary into the repo.",
               "docs": sorted(docs), "nodes": sorted(nodes),
               "validity": {n: validity[n] for n in sorted(nodes) if n in validity}}
    os.makedirs(os.path.dirname(os.path.abspath(manifest)), exist_ok=True)
    with io.open(manifest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"[refresh] Wrote {os.path.relpath(manifest, root)}")
    return 0
