# -*- coding: utf-8 -*-
"""`scripts/graph-refs.json` 을 원격 그래프에서 다시 만든다 — `gate_graph_refs.py` 의 대조표.

**여기만 네트워크를 쓴다.** 게이트는 오프라인이고 CI 에 그래프 키는 없다. 그래서 이
스크립트는 사람이 로컬에서 돌리고, 결과 파일을 코드 변경과 **같은 커밋에** 넣는다.

    set VINC_REMOTE_MCP_TOKEN=vinc_sk_...
    python -X utf8 scripts/refresh_graph_refs.py

문서는 전량을 싣는다(90개 규모라 통째로 싸도 싸다). 노드는 **코드가 실제로 인용한
것만** 확인해 싣는다 — 노드는 수천 개라 전량을 매니페스트로 만들면 커밋마다 흔들리고
리뷰가 못 읽는다.
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.gate_forbidden import ALL_RULES  # noqa: E402
from scripts.gate_graph_refs import MANIFEST, ROOT, scan_repo  # noqa: E402
from vinc_mcp import remote  # noqa: E402

_NODE_KINDS = ("record:", "concept:", "decision:", "episode:", "bridge:", "domain:")


def _citable(ident: str) -> bool:
    """금지 어휘를 담은 id 는 매니페스트에 싣지 않는다.

    인용은 소스 파일에 남고 `gate_forbidden.py` 는 소스 파일을 읽는다. 그래서 구
    서피스명을 id 에 담은 레거시 문서는 **인용 자체가 위반**이다. 매니페스트는 '그래프에 있는 것'이 아니라 '코드가 인용해도 되는 것'의
    목록이므로 여기서 거른다 — 이 파일이 스스로 위반 문자열을 담는 것도 같이 막힌다.
    정본은 `record:legacy-surface-name-doc-ids-are-not-citable-from-code`."""
    return not any(r.pattern.search(ident) for r in ALL_RULES)


def fetch_docs() -> list[str]:
    """인용 가능한 문서 id 전량. 커서 페이지네이션은 vinc_doc_list 의 계약 그대로."""
    docs: list[str] = []
    cursor = None
    while True:
        args = {"limit": 200}
        if cursor:
            args["cursor"] = cursor
        res = remote.call("vinc_doc_list", args)
        if res.get("isError") or res.get("error"):
            raise SystemExit(f"[refresh] 원격 조회 실패: {res.get('error')}")
        for d in res.get("docs", []):
            if _citable(d["doc_id"]):
                docs.append(d["doc_id"])
        if not res.get("has_more"):
            return docs
        cursor = res.get("next_cursor")
        if not cursor:
            return docs


def verify_nodes(ids: list[str]) -> list[str]:
    """인용된 노드가 실재하는지 한 건씩 본다. `found:false` 는 싣지 않는다 — 못 실으면
    게이트가 그 인용을 잡는데, 그게 맞다(없는 노드를 가리키는 주석이다).

    ⚠ **`vinc_graph` 로 물으면 안 된다.** 그쪽은 이웃 전체를 좌표·랭킹까지 실어 오므로
    허브 노드에서 응답 상한(72KB)을 넘고, 그러면 `isError` 로 돌아온다. 그것을 부재로
    읽으면 **중심 개념일수록 인용이 막히는** 정반대의 게이트가 된다 — 실제로
    `concept:agent-orchestration`(이웃 31개, 170KB)이 "그래프에 없다"로 찍혔다.
    `vinc_neighbors` 는 같은 `found` 를 주면서 `limit=1` 로 잘리므로 크기에 걸리지
    않는다. 존재 확인에 이웃 목록은 필요 없다."""
    out: list[str] = []
    for node_id in sorted(set(ids)):
        res = remote.call("vinc_neighbors", {"node": node_id, "limit": 1})
        if res.get("isError") or not res.get("found"):
            why = res.get("error") or res.get("text") or "그래프에 없다"
            print(f"  [miss] {node_id} — {why}")
            continue
        out.append(node_id)
    return out


def check() -> int:
    """라이브 대조 — 파일을 쓰지 않는다. 인용이 **라이브 그래프에** 실재하는가만 묻는다.

    스냅샷 게이트(`gate_graph_refs.py`, 오프라인)는 '인용 ⊆ 매니페스트'를 지키고,
    이 모드는 '인용 ⊆ 라이브'를 지킨다. 앞의 것이 원리적으로 못 보는 드리프트가
    정확히 하나 있다: **매니페스트에 실린 id 가 그 뒤 그래프에서 죽는 것** — 재키잉이
    실제로 그랬다(2026-08-28, 구 해시 id 전멸). 그때 스냅샷 게이트는 초록인 채
    인용이 썩는다. 매니페스트 전체가 아니라 **인용된 id 만** 묻는 이유: 아무도
    인용하지 않는 매니페스트 항목이 죽는 것은 무해하고, 문서가 늘어나는 것은
    드리프트가 아니다 — 전량 대조는 그 둘로 매번 울어 알람 신뢰를 깎는다.

    hub-deploy 가 배포 직후 이것을 돌린다(읽기 전용 키). 빨강은 "코드 주석이 없는
    노드를 가리킨다"는 경보이지 배포 실패가 아니다."""
    if not os.environ.get("VINC_REMOTE_MCP_TOKEN"):
        print("[check] VINC_REMOTE_MCP_TOKEN 미설정 — 라이브 대조는 키가 필요하다",
              file=sys.stderr)
        return 2
    hits = scan_repo()
    cited = sorted({r for _, _, r in hits})
    cited_nodes = [r for r in cited if r.startswith(_NODE_KINDS)]
    cited_docs = [r for r in cited if not r.startswith(_NODE_KINDS)]
    live_docs = set(fetch_docs())
    alive_nodes = set(verify_nodes(cited_nodes))
    dead = ([d for d in cited_docs if d not in live_docs]
            + [n for n in cited_nodes if n not in alive_nodes])
    if dead:
        print(f"[check] 죽은 인용 {len(dead)}건 — 코드가 가리키는 id 가 라이브 그래프에 없다.",
              file=sys.stderr)
        where = {}
        for path, lineno, ref in hits:
            where.setdefault(ref, []).append(f"{path}:{lineno}")
        for ref in sorted(dead):
            print(f"  [dead] {ref} — {', '.join(where.get(ref, ['?']))}",
                  file=sys.stderr)
        print("  그래프 쪽이 옳으면 인용을 후속 id 로 옮기고, 코드 쪽이 옳으면 "
              "노드를 복원한 뒤 refresh 로 매니페스트를 갱신한다.", file=sys.stderr)
        return 1
    print(f"[check] 인용 {len(cited)}종 전부 라이브에 실재 "
          f"(문서 {len(cited_docs)} · 노드 {len(cited_nodes)})")
    return 0


def main() -> int:
    if "--check" in sys.argv[1:]:
        return check()
    cited = [ref for _, _, ref in scan_repo()]
    cited_nodes = [r for r in cited if r.startswith(_NODE_KINDS)]
    print(f"[refresh] 코드가 인용한 id {len(set(cited))}종 (노드 {len(set(cited_nodes))}종)")

    docs = fetch_docs()
    print(f"[refresh] 문서 {len(docs)}개")
    nodes = verify_nodes(cited_nodes)
    print(f"[refresh] 노드 {len(nodes)}개 확인")

    payload = {
        "note": "generated by scripts/refresh_graph_refs.py — do not hand edit. "
                "gate_graph_refs.py checks code citations against this. "
                "Identifiers only: titles would carry forbidden vocabulary into the repo.",
        "docs": sorted(docs),
        "nodes": sorted(nodes),
    }
    with io.open(MANIFEST, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"[refresh] 썼다: {os.path.relpath(MANIFEST, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
