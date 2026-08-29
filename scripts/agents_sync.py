# -*- coding: utf-8 -*-
"""agents_sync.py — 그래프의 역할 정의(`vinc/agent`)와 `.claude/agents/*.md` 를 잇는다.

역할 정의의 정본은 그래프다([[concept:agent-orchestration]]). 하지만 하네스는 실행
시점에 `.claude/agents/<name>.md` 를 **파일로** 읽는다. 그래서 파일이 있어야 하고,
파일이 있으면 손으로 고쳐진다.

같은 일을 디자인 허브에서 이미 겪었다 — "그래프에서 재생성한다"고 규율에 적어 두고
재생성 경로가 없으니, 실제 관행은 파일을 고치고 그래프에 복사하는 **반대 방향**이
됐다. 물리적으로 불가능한 규칙은 규칙이 아니다. 그래서 생성기를 먼저 만든다.

**CI 는 그래프를 못 읽는다.** 워크트리에 `VINC_REMOTE_MCP_TOKEN` 이 없고, CI 에 그래프
키를 넣지 않는다(`gate_graph_refs.py` 와 같은 사유). 그래서 동사를 셋으로 가른다:

    python -X utf8 scripts/agents_sync.py --pull    # 토큰 있는 사람: 그래프 → agents/roles.json
    python -X utf8 scripts/agents_sync.py --gen     # 스냅샷 → .claude/agents/*.md
    python -X utf8 scripts/agents_sync.py --check   # CI, 오프라인: 껍데기가 스냅샷과 같은가

무엇을 잡고 무엇을 못 잡는가(정직하게):

  ✅ 껍데기를 손으로 고친 것 — 재생성한 것과 바이트가 다르다
  ✅ 껍데기가 없는 것 · 스냅샷에 없는 껍데기가 남아 있는 것
  ✅ 역할의 `reads` 가 **없는 문서**를 가리키는 것 — 인용 매니페스트
     (`scripts/graph-refs.json`)의 문서 목록과 대조한다. 두 스냅샷은 같은 그래프를
     보므로 함께 갱신된다.
  ❌ **금지의 강제**는 절반만이다. `disallowedTools` 가 Edit·Write 를 빼도 Bash 가
     남는 역할에서는 sed 한 줄로 파일이 써진다. 진짜 벽인 것은 Bash 까지 빠진
     `agent-copy-writer` 하나뿐이고, MCP 도구를 지목하는 금지(스튜어드의
     vinc_delete 금지 등)는 서버 이름 문제로 아예 강제할 수 없다.
  ❌ **그래프가 앞서간 것**(스냅샷 미갱신) — 오프라인에서는 원리적으로 안 보인다.
     `--pull` 뒤 `git diff` 가 그것을 보여주며, 그건 스튜어드의 주기 작업이지 CI
     게이트가 아니다. `gate_design_sync.py` 와 `sync_out.py --check` 가 서로 다른
     고장을 잡는 것과 같은 분업이다.

**렌더러는 여기 없다.** `build_payload`·`render_shell` 은 `vinc_mcp/roles.py` 에 있고
`prompts/get` 이 같은 함수를 쓴다(W2). 사본을 두지 않는 이유는 R7 이 출력 계약을 열여섯
껍데기가 아니라 꼬리 한 곳에 둔 것과 같다 — 사본이 둘이면 보편 규칙 하나를 고칠 때 한
벌만 고쳐진다. 서버 쪽에 둔 이유는 동결 바이너리가 `scripts/` 를 안 싣기 때문이다.

스냅샷이 **읽히는 필드만** 싣는 것이 이 파일의 불변식이다. 노드의 one_liner 는 쓸 수는
있어도 어떤 MCP 도구도 돌려주지 않으므로([[record:one-liner-is-write-only-over-mcp]]),
생성기가 재현해야 하는 것은 전부 `props` 에 있어야 한다. 임무 문장이 `props.mission`
인 이유가 그것이다.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 스크립트로 직접 부르는 경로(`python -X utf8 scripts/agents_sync.py --gen`)에서도
# `vinc_mcp` 를 찾을 수 있어야 한다 — 임포트보다 **먼저** 넣는다.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vinc_mcp.roles import (DOMAIN, ID_PREFIX, build_payload,  # noqa: E402
                            render_shell)

SNAPSHOT = ROOT / "agents" / "roles.json"
SHELL_DIR = ROOT / ".claude" / "agents"

# ── 스냅샷 ────────────────────────────────────────────────────────────────────

def write_snapshot(payload: dict) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    with io.open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def read_snapshot() -> dict:
    if not SNAPSHOT.exists():
        raise SystemExit(f"[agents] 스냅샷이 없다: {SNAPSHOT.relative_to(ROOT)} "
                         f"— 토큰 있는 곳에서 --pull 을 먼저 돌린다")
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _manifest_docs() -> set[str] | None:
    """인용 매니페스트가 아는 문서 id 전량. 파일이 없으면 None 을 돌려 이 검사만
    건너뛴다 — 매니페스트는 다른 게이트의 소유물이라 그 부재를 여기서 실패로 만들면
    이 게이트가 남의 고장을 보고하게 된다."""
    fp = ROOT / "scripts" / "graph-refs.json"
    if not fp.exists():
        return None
    return set(json.loads(fp.read_text(encoding="utf-8")).get("docs") or [])


def dangling_reads(snapshot: dict, docs: set[str]) -> list[tuple[str, str]]:
    """`reads` 가 가리키는데 매니페스트에 없는 (역할, 문서 id) 쌍.

    왜 필요한가: `reads` 는 이 설계의 핵심이면서(프롬프트 헤더를 대체하는 자리) 지금껏
    **아무도 검사하지 않는 문자열**이었다. 표기가 두 형식으로 섞여 있는 것이 그것을
    가린다 — P5 코퍼스는 점 표기 자체가 store 키이고(`vinc.spec.dataplane`), 그 뒤에
    쓰인 문서 중 일부는 해시다(`d8dc6d55b36241dab`). 해시로 통일하는 길은 없다. 앞쪽
    41건에는 해시가 아예 존재하지 않기 때문이다. 그래서 두 형식 모두 존재를 검사한다.

    ⚠ 2026-08-28 에 해시 문서 14건을 **선언된 점 표기 id 로 재키했다**(front matter 가
    선언한 id 와 저장 id 가 달랐던 것들). 섞임 자체는 남는다 — front matter 가 없어
    선언 id 가 아예 없는 문서는 해시로 남았고, 그것이 위 예시다. 검사가 두 형식을 다
    보는 이유는 그대로다.

    이것이 잡는 실제 사고: 2026-08-09 P5 개명이 문서 id 를 통째로 옮겼을 때 코드 주석의
    인용 24건 중 19건이 **조용히** 끊어졌다([[record:code-citations-rot-silently-without-a-gate]]).
    같은 개명이 다시 오면 점 표기 reads 가 같은 방식으로 끊어진다."""
    out: list[tuple[str, str]] = []
    for role in snapshot.get("roles") or []:
        for doc_id in (role.get("props") or {}).get("reads") or []:
            if doc_id not in docs:
                out.append((role.get("name", "?"), doc_id))
    return out


# ── 껍데기 ────────────────────────────────────────────────────────────────────

def shell_path(role: dict) -> Path:
    return SHELL_DIR / f"{role['name']}.md"


# ── 동사 ──────────────────────────────────────────────────────────────────────

def cmd_pull() -> int:
    """**여기만 네트워크를 쓴다** — `refresh_graph_refs.py` 와 같은 자리, 같은 이유."""
    from vinc_mcp import remote

    res = remote.call("vinc_neighbors",
                      {"node": f"domain:{DOMAIN}", "limit": 200})
    if res.get("isError") or not res.get("found"):
        raise SystemExit(f"[agents] 도메인 조회 실패: {res.get('error') or res}")
    ids = sorted({n["id"] for n in res.get("neighbors", [])
                  if str(n.get("id", "")).startswith(ID_PREFIX)})
    if not ids:
        raise SystemExit(f"[agents] {DOMAIN} 에 역할 노드가 없다 — 도메인 이름을 확인하라")
    print(f"[agents] 역할 {len(ids)}개")

    roles = []
    for rid in ids:
        got = remote.call("vinc_graph", {"seed_id": rid, "depth": 1})
        if got.get("isError") or not got.get("found"):
            raise SystemExit(f"[agents] {rid} 조회 실패: {got.get('error')}")
        node = next((n for n in got.get("nodes", []) if n.get("id") == rid), None)
        if node is None:
            # 고립 노드가 빈 필드로 보고되던 전례가 있다
            # ([[record:isolated-seed-read-back-reported-empty-fields]]). 조용히 빈
            # 역할을 쓰느니 여기서 죽는 편이 낫다.
            raise SystemExit(f"[agents] {rid} 가 자기 응답의 nodes 에 없다")
        roles.append(node)

    write_snapshot(build_payload(roles))
    print(f"[agents] 썼다: {SNAPSHOT.relative_to(ROOT)} — git diff 가 그래프가 앞서간 만큼이다")
    return 0


def cmd_gen() -> int:
    snap = read_snapshot()
    SHELL_DIR.mkdir(parents=True, exist_ok=True)
    for role in snap["roles"]:
        fp = shell_path(role)
        with io.open(fp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_shell(role))
        print(f"[agents] {fp.relative_to(ROOT)}")
    print(f"[agents] 껍데기 {len(snap['roles'])}개 생성")
    return 0


def cmd_check() -> int:
    snap = read_snapshot()
    bad = 0
    expected = set()
    for role in snap["roles"]:
        fp = shell_path(role)
        expected.add(fp.resolve())
        if not fp.exists():
            print(f"[MISSING] {fp.relative_to(ROOT)} — --gen 을 돌려야 한다")
            bad += 1
            continue
        if fp.read_text(encoding="utf-8") != render_shell(role):
            print(f"[TAMPERED] {fp.relative_to(ROOT)} — 스냅샷에서 재생성한 것과 다르다. "
                  f"생성물이라 여기서 고치면 안 된다: 그래프의 {role['id']} 를 고치고 "
                  f"--pull → --gen")
            bad += 1
        else:
            print(f"[OK] {fp.relative_to(ROOT)}")

    if SHELL_DIR.exists():
        for fp in sorted(SHELL_DIR.glob("*.md")):
            if fp.resolve() not in expected:
                print(f"[UNLISTED] {fp.relative_to(ROOT)} 가 스냅샷에 없다 — 역할이 그래프에서 "
                      f"사라졌으면 파일도 지우고, 아니면 --pull 로 스냅샷을 갱신하라")
                bad += 1

    docs = _manifest_docs()
    if docs is None:
        print("[SKIP] scripts/graph-refs.json 이 없어 reads 검사를 건너뛴다")
    else:
        for name, doc_id in dangling_reads(snap, docs):
            print(f"[DANGLING] {name} 의 reads 가 매니페스트에 없는 문서를 가리킨다: "
                  f"{doc_id} — 문서가 개명·삭제됐거나 매니페스트가 낡았다. "
                  f"scripts/refresh_graph_refs.py 를 돌려 같은 커밋에 넣는다")
            bad += 1

    print(f"\n{'역할 껍데기 이상 없음' if not bad else f'문제 {bad}건'}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="agent roster: graph to snapshot to shells")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--pull", action="store_true",
                       help="그래프에서 스냅샷을 다시 만든다(토큰 필요)")
    group.add_argument("--gen", action="store_true", help="스냅샷에서 껍데기를 생성한다")
    group.add_argument("--check", action="store_true",
                       help="껍데기가 스냅샷과 같은가(오프라인)")
    args = ap.parse_args(argv)
    if args.pull:
        return cmd_pull()
    if args.gen:
        return cmd_gen()
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
