#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""게이트 레지스트리: 이 리포의 게이트와 생성물 검사를 여기 한 곳에 적고, 로컬과 CI 가 이 목록을 돈다.

    python -X utf8 scripts/gates.py                    # origin/main 대비 바뀐 파일이 닿는 게이트
    python -X utf8 scripts/gates.py --all              # 전부
    python -X utf8 scripts/gates.py forbidden escape   # 이름 댄 것만
    python -X utf8 scripts/gates.py --list             # 돌리지 않고 표만 찍는다
    python -X utf8 scripts/gates.py --ci core          # CI: ci.yml 의 그 잡에 배정된 게이트 전부

출력은 게이트 하나에 한 줄(PASS · FAIL · SKIP 과 이유)이고, 막는 게이트가 하나라도 FAIL 이면
exit 1, 쓰임이 틀리면 2. 알림(advisory) 게이트의 FAIL 은 줄에 적히지만 종료코드를 바꾸지 않는다.
GitHub Actions 안(`GITHUB_ACTIONS`)에서는 막는 게이트의 FAIL 마다 `::error` 주석 한 줄을 더 찍어
체크 화면이 그 게이트 이름을 보이게 한다. 로컬 출력은 그대로다.

게이트마다 제한 시간(`timeout`, 기본 `DEFAULT_TIMEOUT` 초)이 있다. 넘으면 그 게이트는
"timed out after N s" 로 FAIL 이고, 게이트가 띄운 프로세스는 자식까지 그룹째 멈춘다.

필요한 것(`needs`)이 없으면 로컬에서는 SKIP 하고 이유를 적는다. `--ci` 에서는 SKIP 이 아니라
FAIL 이다: 러너에 도구가 빠져 게이트가 조용히 건너뛰면 초록이 거짓이 된다. CI 에서 SKIP 할 수
있는 것은 `range`(풀 리퀘스트에서만 BASE·HEAD 가 있다) 하나뿐이다.

이 파일에 없는 `scripts/gate_*.py` 가 생기거나, 여기 적힌 명령의 파일이 없어지거나, 워크플로가
여기 적히지 않은 자리에서 게이트를 손으로 부르면 tests/platform/test_gate_registry.py 가 떨어진다.
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/gates.py"

#: 어느 파일이 바뀌어도 관련이 있다(빈 정규식은 모든 경로에 맞는다).
ANY: tuple[str, ...] = ("",)
_DESIGN = (r"^app/src/", r"^scripts/design/")

#: 게이트 하나(명령 전부를 합친 것)의 기본 제한 시간, 초. CI 에서 가장 느린 레지스트리 게이트가
#: 몇 초라 그 수십 배를 둔다: 느린 러너나 느린 네트워크로는 떨어지지 않고 멈춘 게이트만 떨어진다.
DEFAULT_TIMEOUT = 120
#: 제한 시간을 넘은 그룹에 SIGTERM 을 보낸 뒤 스스로 끝나기를 기다리는 초. 넘으면 SIGKILL.
_TERM_GRACE = 5.0


@dataclass(frozen=True)
class Gate:
    id: str
    #: 차례로 돌리는 명령들. 첫 낱말 `python` 은 이 스크립트를 돌리는 인터프리터로 바뀐다.
    #: `{BASE}` `{HEAD}` `{TITLE}` 은 실행할 때 채운다. 하나라도 0 이 아니면 FAIL 이고 뒤는 안 돈다.
    cmd: tuple[tuple[str, ...], ...]
    #: 'node'(app/node_modules 와 node) · 'network' · 'range'(BASE·HEAD) · 'title'(PR 제목)
    #: · 'tool:<실행 파일>' · 'module:<파이썬 모듈>' · 'secret:<환경 변수>'
    needs: tuple[str, ...] = ()
    #: 이 게이트가 읽는 경로(정규식, `re.search`). 바뀐 파일 하나라도 맞으면 관련이 있다.
    #: 명령에 적힌 스크립트 파일과 이 파일 자신은 따로 적지 않아도 관련으로 친다.
    paths: tuple[str, ...] = ANY
    #: ci.yml 의 이 잡이 `scripts/gates.py --ci <잡>` 한 스텝으로 이것을 돈다.
    ci: str | None = None
    #: 손으로 쓴 워크플로 스텝이 이것을 도는 자리, '<워크플로 파일>:<잡 id>'.
    #: 레지스트리 스텝으로 옮기지 않고 그 자리에 둔 것이며, 테스트가 실제로 거기 있는지 본다.
    steps: tuple[str, ...] = ()
    #: 실패해도 막지 않는다(워크플로의 continue-on-error 와 같은 신분).
    advisory: bool = False
    #: 리포 루트 기준 작업 디렉터리.
    cwd: str = ""
    #: 제한 시간, 초. 넘으면 FAIL("timed out after N s")이고 프로세스 그룹을 멈춘다.
    timeout: int = DEFAULT_TIMEOUT
    #: 워크플로 `run:` 에서 이 게이트를 알아보는 글자. 비우면 명령의 첫 파일 경로.
    marker: str = ""
    #: 표에 함께 찍는 한 줄 설명.
    note: str = ""


def py(*args: str) -> tuple[tuple[str, ...], ...]:
    """파이썬 명령 하나짜리 cmd."""
    return (("python", *args),)


_DEV_CHECKS = ('set -euo pipefail; for f in dev/*-check.ts; do echo "── $f"; '
               'node --experimental-strip-types --disable-warning=ExperimentalWarning "$f"; done')

# ── 레지스트리 ─────────────────────────────────────────────────────────────────
# 순서가 곧 `--ci` 의 실행 순서다. 명령의 플래그(-X utf8 유무 포함)는 워크플로에서 돌던
# 그대로 옮겼다: 게이트가 무엇을 보는지는 이 파일이 바꾸지 않는다.
GATES: tuple[Gate, ...] = (
    # ── ci.yml `changes` 잡 ─────────────────────────────────────────────────
    # 추적되는 모든 파일에 주인 팀이 있는가(.github/CODEOWNERS ← agents/teams.json).
    # BASE·HEAD 가 있으면 닿은 팀 목록도 적는다. 지표 원천이지 테스트를 끄는 스위치가 아니다.
    Gate("ownership", py("-X", "utf8", "scripts/gate_ownership.py"), ci="changes",
         note="every tracked file has an owning team"),
    # 허브의 역할 출력 게이트(vinc_mcp/role_output_gate.py)는 SDK 원본에서 생성한 사본이다.
    Gate("output-gate-copy", py("-X", "utf8", "scripts/sync_output_gate.py", "--check"),
         paths=(r"^integrations/client/python/src/vinc_client/output_gate\.py$",
                r"^vinc_mcp/role_output_gate\.py$"),
         ci="changes", marker="sync_output_gate.py --check",
         note="role output gate copy is fresh"),
    # 죽은 파이썬 코드(vulture 신뢰도 80 이상). 허용 목록은 scripts/vulture-allow.txt.
    # 앱(TS)의 미사용 검사는 여기가 아니라 app 잡의 tsc 가 한다.
    Gate("vulture", py("-X", "utf8", "scripts/gate_vulture.py"), needs=("module:vulture",),
         paths=(r"\.py$", r"^scripts/vulture-allow\.txt$"), ci="changes",
         note="dead Python, 80 percent confidence"),
    # 주석 속 이력 표지가 파일마다 기준선을 넘지 않는가. BASE·HEAD 는 위반 파일에서 이 PR 이
    # 더한 줄을 골라 보이는 데만 쓴다. 계약: [[vinc.canon.code-annotation]]
    Gate("comment-history", py("-X", "utf8", "scripts/gate_comment_history.py"), ci="changes",
         note="comment history budget per file"),
    # 직렬화(cancel-in-progress: false) 워크플로의 runs-on 이 이 PR 에서 바뀌는가. 두 리비전을
    # 비교하므로 fetch-depth 0 체크아웃이 전제다. 바뀌면 Actions 큐를 읽으므로 토큰이 든다.
    Gate("runner-move",
         py("-X", "utf8", "scripts/gate_runner_move.py", "{BASE}", "{HEAD}", "--check-queues"),
         needs=("range", "module:yaml"), paths=(r"^\.github/workflows/",), ci="changes",
         note="serialising workflow changes its runner"),
    # 홈페이지가 이 PR 의 MCP 도구 목록과 REST 레퍼런스를 따라왔는가(공개 문서 페이지와 대조).
    # 알림이다: 홈페이지는 이 PR 이 머지돼야 따라올 수 있다. 셀프테스트를 먼저 돌린다.
    Gate("homepage-drift",
         (("python", "-X", "utf8", "scripts/gate_homepage_drift.py", "--selftest"),
          ("python", "-X", "utf8", "scripts/gate_homepage_drift.py")),
         needs=("range", "network"),
         paths=(r"^vinc_mcp/", r"^api/", r"^scripts/gen_openapi\.py$"),
         ci="changes", advisory=True, note="homepage follows MCP tools and REST reference"),
    # 이 PR 이 건드린 경로를 다스리는 기록·결정을 호스티드 그래프에서 찾아 알린다. 시크릿과
    # 실행 기록 아티팩트가 얽혀 ci.yml 의 손 스텝으로 남는다. 그 스텝의 명령은 이것에 CI 전용
    # 선택지(`--format json` · `--summary` · `--trace` · `--run-id` · `--task-ref` · `--host`)만
    # 더한 것이고 `--diff` 는 `origin/$BASE_REF` 다. 둘 다 `REF...HEAD` 의 merge-base 로 읽으므로
    # 고르는 경로는 같다. 테스트가 그 차이 밖으로 어긋나지 않는지 본다.
    Gate("decision-gate",
         py("-X", "utf8", "-m", "api.cli", "--target", "account", "gate", "check",
            "--diff", "{BASE}"),
         needs=("range", "network", "secret:VINC_API_KEY"),
         steps=("ci.yml:changes",), advisory=True, marker="gate check",
         note="recorded decisions on touched paths"),

    # ── ci.yml `core` 잡 ────────────────────────────────────────────────────
    # 카피 보이스 하드 룰(과금 식별자 · 학습 프레이밍 · 구 명칭)과 금지어.
    Gate("forbidden", py("scripts/gate_forbidden.py"), ci="core", note="forbidden terms"),
    # 코드가 `[[...]]` 로 가리키는 그래프 문서·노드가 커밋된 scripts/graph-refs.json 에 있는가.
    # 오프라인이다. 라이브 대조는 아래 graph-refs-live.
    Gate("graph-refs", py("-X", "utf8", "scripts/gate_graph_refs.py"), ci="core",
         note="code citations exist in the committed manifest"),
    # LadybugDB 전용 구문이 어댑터 밖에서 늘지 않는가. 허용 목록은 줄기만 한다.
    Gate("engine-dialect", py("-X", "utf8", "scripts/gate_engine_dialect.py"),
         paths=(r"\.py$", r"^scripts/engine-dialect-allowlist\.json$"), ci="core",
         note="engine dialect stays inside the adapters"),
    Gate("openapi", py("-X", "utf8", "scripts/gen_openapi.py", "--check"),
         paths=(r"^api/", r"^vinc_mcp/", r"^store/", r"^engines/"),
         ci="core", marker="gen_openapi.py --check", note="OpenAPI v1 file is fresh"),
    # 그래프 유래 문자열이 esc() 없이 innerHTML 로 가는가. CSP 와 withGlobalTauri:false 가
    # 하중을 받고 있으므로 여기서 고정한다.
    Gate("escape", py("-X", "utf8", "scripts/gate_escape.py"), paths=(r"^app/src/",),
         ci="core", note="graph values are escaped before innerHTML"),
    # View 마크 대비(WCAG 2.x 와 APCA 둘 다). 생성물 theme.ts 를 읽는 순수 파이썬이다.
    Gate("contrast", py("scripts/gate_contrast.py"), paths=_DESIGN, ci="core",
         note="view mark contrast"),
    # 토큰 생성물이 손으로 고쳐졌는가(각인 해시만 본다).
    Gate("design-sync", py("scripts/gate_design_sync.py"), paths=_DESIGN, ci="core",
         note="generated token copies are not hand edited"),
    # 소비 사본이 토큰 스냅샷(scripts/design/tokens.json)에서 다시 만든 것과 같은가.
    Gate("design-token-drift", py("-X", "utf8", "scripts/design/sync_out.py", "--check"),
         paths=_DESIGN, ci="core", marker="design/sync_out.py --check",
         note="token copies match the snapshot"),
    # 하드코딩 색. 면제는 `ds-allow: <이유>` 주석이고 건수와 이유가 늘 출력된다.
    Gate("design-surface", py("-X", "utf8", "scripts/design/gate_surface.py", "app/src"),
         paths=_DESIGN, ci="core", note="no hardcoded colours in app/src"),
    # 글씨 크기는 토큰(--fs-*)에서 온다. 면제는 `fs-allow: <이유>` 주석.
    Gate("type-scale", py("-X", "utf8", "scripts/design/gate_type_scale.py", "app/src"),
         paths=_DESIGN, ci="core", note="font sizes come from tokens"),
    # Figma 덤프(scripts/design/figma/figma-live.json)가 토큰을 따라오는가. 덤프는 손으로
    # 고치지 않고 다시 뜬다(방법은 figma_diff.py 독스트링).
    Gate("figma-diff", py("-X", "utf8", "scripts/design/figma_diff.py"), paths=_DESIGN,
         ci="core", note="Figma dump follows the tokens"),
    # 코드의 화면·모달·아이콘 목록에 Figma 대응이 선언돼 있는가(아직 안 그린 것은 None 선언).
    Gate("figma-inventory", py("-X", "utf8", "scripts/design/figma_inventory.py"),
         paths=_DESIGN, ci="core", note="screens, modals and icons have a Figma entry"),
    # 앱 보드 캡처가 지금 app/src 와 같은 트리에서 찍혔는가. 말만 하고 막지 않는다.
    # 근거: [[decision:figma-app-boards-no-longer-gate-a-release-2026-09-26]]
    Gate("figma-app-copy", py("-X", "utf8", "scripts/design/figma_app_copy.py"),
         paths=_DESIGN, ci="core", steps=("release.yml:version-gate",), advisory=True,
         note="app board captures are current"),
    # 역할 껍데기(agents/shells/*.md)와 CODEOWNERS 가 커밋된 스냅샷에서 생성한 그대로인가.
    Gate("agents-roster", py("-X", "utf8", "scripts/agents_sync.py", "--check"),
         paths=(r"^agents/", r"^\.github/CODEOWNERS$", r"^vinc_mcp/roles\.py$",
                r"^scripts/worktree_hook\.py$"),
         ci="core", marker="agents_sync.py --check", note="role shells match the snapshot"),

    # ── 다른 워크플로의 손 스텝 ────────────────────────────────────────────
    # PR 제목 계약. 제목 편집에도 돌도록 전용 워크플로에 산다.
    Gate("pr-title", py("-X", "utf8", "scripts/gate_pr_title.py", "{TITLE}"), needs=("title",),
         steps=("pr-title.yml:pr-title",), note="pull request title contract"),
    # 허브 컨테이너 앱의 구조가 기준선과 같은가. 배포 뒤 az 로그인이 있는 자리에서 돈다.
    Gate("app-config", py("scripts/gate_app_config.py"), needs=("tool:az", "network"),
         paths=(r"^scripts/app-config-baseline\.json$", r"^infra/"),
         steps=("hub-deploy.yml:build-and-deploy",), advisory=True,
         note="hub container app structure matches the baseline"),
    # 인용된 id 가 라이브 그래프에 아직 있는가. 읽기 전용 키로 배포 뒤에 돈다.
    Gate("graph-refs-live", py("scripts/refresh_graph_refs.py", "--check"),
         needs=("network", "secret:VINC_REMOTE_MCP_TOKEN"),
         steps=("hub-deploy.yml:build-and-deploy",), advisory=True,
         marker="refresh_graph_refs.py --check", note="cited ids exist in the live graph"),
    # 버전이 적힌 모든 자리가 같은가(태그에서는 태그와도).
    Gate("version-sync", py("scripts/version_sync.py", "--check"),
         paths=(r"^pyproject\.toml$", r"^app/", r"^integrations/", r"^api/", r"^vinc_mcp/"),
         steps=("release.yml:version-gate",), marker="version_sync.py --check",
         note="every version site agrees"),
    # 타입체크 · 레이아웃 테스트 · 프로덕션 빌드. `app (typecheck)` 잡이 경로 조건과 함께 돈다.
    # 노드 빌드와 cargo 를 부르는 셋은 분 단위라 제한 시간을 그 잡의 제한(20분) 안에서 따로 둔다.
    Gate("app-build", (("npm", "run", "build"),), needs=("node",),
         paths=(r"^app/", r"^api/contract/"), steps=("ci.yml:app",), cwd="app",
         timeout=900, marker="npm run build", note="app typecheck, layout tests and build"),
    # 브라우저 없이 도는 app/dev/*-check.ts. tsconfig include 밖이라 위 빌드가 덮지 않는다.
    Gate("app-dev-checks", (("bash", "-c", _DEV_CHECKS),), needs=("node", "tool:bash"),
         paths=(r"^app/", r"^api/contract/"), steps=("ci.yml:app", "verify-hosted.yml:app"),
         cwd="app", timeout=900, marker="for f in dev/*-check.ts",
         note="app/dev checks without a browser"),

    # ── CI 가 스텝으로 부르지 않는 생성물 검사 ──────────────────────────────
    # 앞의 둘은 core-pkns 의 pytest 가 같은 것을 단언한다(tests/mcp/test_tool_reference.py,
    # tests/core/test_cli_shim.py). 여기 두는 것은 로컬에서 바로 고칠 명령을 보이기 위해서다.
    Gate("tool-reference", py("-X", "utf8", "-m", "vinc_mcp.tool_reference", "--check"),
         paths=(r"^vinc_mcp/", r"^api/"), marker="vinc_mcp.tool_reference",
         note="vinc_mcp/tool_reference.json is fresh (pytest asserts it in CI)"),
    Gate("shim-registry", py("-X", "utf8", "scripts/sync_shim_registry.py", "--check"),
         paths=(r"^integrations/client/python/src/vinc_client/shims\.py$",
                r"^api/shim_registry\.py$"),
         marker="sync_shim_registry.py --check",
         note="api/shim_registry.py is fresh (pytest asserts it in CI)"),
    # 릴리스는 생성기를 돌리기만 하고 커밋된 사본과 대조하지 않는다. 어디서도 막지 않으므로 알림이다.
    Gate("third-party-notices", py("-X", "utf8", "scripts/third_party_notices.py", "--check"),
         needs=("tool:cargo", "node"), paths=(r"^app/package-lock\.json$", r"^app/src-tauri/Cargo"),
         advisory=True, timeout=900, marker="third_party_notices.py --check",
         note="committed notices match the dependencies"),
)

BY_ID = {g.id: g for g in GATES}


# ── 명령이 가리키는 파일 ──────────────────────────────────────────────────────

def _path_like(tok: str) -> bool:
    return (not tok.startswith("-") and "{" not in tok and "*" not in tok and " " not in tok
            and ("/" in tok or tok.endswith((".py", ".ts", ".mjs"))))


def files(g: Gate) -> list[str]:
    """명령이 가리키는 리포 안의 파일·디렉터리(리포 루트 기준). `-m 모듈` 은 모듈 파일로 읽는다."""
    out: list[str] = []
    for argv in g.cmd:
        if not argv or argv[0] != "python":
            continue
        args = list(argv[1:])
        for i, tok in enumerate(args):
            if tok == "-m" and i + 1 < len(args):
                out.append(args[i + 1].replace(".", "/") + ".py")
            elif _path_like(tok) and (i == 0 or args[i - 1] not in ("-m", "-X")):
                out.append(str(Path(g.cwd, tok).as_posix()) if g.cwd else tok)
    return list(dict.fromkeys(out))


def marker(g: Gate) -> str:
    if g.marker:
        return g.marker
    fs = files(g)
    return fs[0] if fs else g.id


# ── 레지스트리 자기 점검(테스트가 부른다) ────────────────────────────────────

def audit(root: Path = ROOT, gates: tuple[Gate, ...] = GATES) -> list[str]:
    """어긋난 것을 문장으로 돌려준다. 빈 목록이면 정합이다.

    - `scripts/gate_*.py`·`scripts/design/gate_*.py` 가 있는데 어느 게이트의 명령에도 없다.
    - 명령이 가리키는 파일·디렉터리가 없다.
    - id 가 겹친다.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for g in gates:
        if g.id in seen:
            problems.append(f"duplicate gate id: {g.id}")
        seen.add(g.id)
    named = {f for g in gates for f in files(g)}
    for pattern in ("scripts/gate_*.py", "scripts/design/gate_*.py"):
        for p in sorted(root.glob(pattern)):
            rel = p.relative_to(root).as_posix()
            if rel not in named:
                problems.append(f"{rel} exists but no gate in {SELF} runs it")
    for g in gates:
        for f in files(g):
            if not (root / f).exists():
                problems.append(f"gate {g.id}: {f} is named in its command but does not exist")
    return problems


# ── 실행 ─────────────────────────────────────────────────────────────────────

def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def local_range(base_ref: str) -> tuple[str, str] | None:
    """로컬 실행의 BASE·HEAD: base_ref 와 HEAD 의 merge-base, 그리고 HEAD."""
    base = _git("merge-base", base_ref, "HEAD")
    head = _git("rev-parse", "HEAD")
    return (base, head) if base and head else None


def changed_files(base: str) -> list[str]:
    """base 이후 바뀐 파일: 커밋된 것, 작업 트리의 것, 추적 안 되는 새 파일."""
    diff = _git("diff", "--name-only", base) or ""
    new = _git("ls-files", "--others", "--exclude-standard") or ""
    return sorted({p for p in (diff + "\n" + new).splitlines() if p.strip()})


def relevant(g: Gate, changed: list[str]) -> bool:
    own = set(files(g)) | {SELF}
    for path in changed:
        if path in own or any(re.search(rx, path) for rx in g.paths):
            return True
    return False


@dataclass
class Ctx:
    ci: bool
    network: bool
    base: str
    head: str
    title: str


def missing(g: Gate, ctx: Ctx) -> str | None:
    """돌릴 수 없으면 그 이유. 돌릴 수 있으면 None."""
    for need in g.needs:
        kind, _, arg = need.partition(":")
        if kind == "range" and not (ctx.base and ctx.head):
            return ("pull request only: BASE and HEAD are unset" if ctx.ci
                    else "needs a base revision: git fetch origin main, or pass --base")
        if kind == "title" and not ctx.title:
            return "needs the pull request title: --title '<title>'"
        if kind == "network" and not ctx.network:
            return "needs the network: pass --network to run it"
        if kind == "secret" and not os.environ.get(arg):
            return f"needs the secret {arg} in the environment"
        if kind == "tool" and not shutil.which(arg):
            return f"needs {arg} on PATH"
        if kind == "module" and find_spec(arg) is None:
            return f"needs the Python module {arg}"
        if kind == "node" and not (shutil.which("node") and (ROOT / "app" / "node_modules").is_dir()):
            return "needs node and `npm ci` in app/"
    return None


def _argv(argv: tuple[str, ...], ctx: Ctx) -> list[str]:
    out = [sys.executable if i == 0 and a == "python" else a for i, a in enumerate(argv)]
    return [a.replace("{BASE}", ctx.base).replace("{HEAD}", ctx.head).replace("{TITLE}", ctx.title)
            for a in out]


def _group_kw() -> dict:
    """자식을 새 프로세스 그룹의 우두머리로 띄운다: 멈출 때 손자까지 한 번에 닿게."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop_group(proc: subprocess.Popen) -> None:
    """proc 과 그 그룹을 멈춘다. POSIX 는 SIGTERM, 유예 뒤 남은 것에 SIGKILL. 윈도는 트리째 끝낸다."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        proc.wait()
        return
    # start_new_session 으로 띄웠으므로 그룹 id 는 자식의 pid 다.
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        proc.wait()
        return
    deadline = time.monotonic() + _TERM_GRACE
    while time.monotonic() < deadline:
        # 자식을 거둬야 좀비가 그룹에 남아 살아 있는 것처럼 보이지 않는다.
        proc.poll()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def _pump(pipe, sink: list[str], echo: bool) -> None:
    """자식 출력을 줄마다 붙잡고, echo 면 받는 대로 이 프로세스의 stdout 에 그대로 흘린다."""
    for raw in iter(pipe.readline, b""):
        sink.append(raw.decode("utf-8", errors="replace").replace("\r\n", "\n"))
        if echo:
            out = sys.stdout
            buf = getattr(out, "buffer", None)
            if buf is not None:
                buf.write(raw)
                buf.flush()
            else:
                out.write(sink[-1])
                out.flush()
    pipe.close()


def _run_cmd(argv: list[str], cwd: Path, env: dict, stream: bool,
             deadline: float) -> tuple[int | None, str]:
    """명령 하나를 deadline(monotonic)까지 돌린다. (종료코드, 출력). 시간을 넘기면 종료코드는 None."""
    if stream:
        sys.stdout.flush()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **_group_kw())
    out: list[str] = []
    reader = threading.Thread(target=_pump, args=(proc.stdout, out, stream), daemon=True)
    reader.start()
    try:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            # 자식이 끝나도 그룹에 남은 손자가 파이프를 쥐고 있으면 그것도 시간 안에 끝나야 한다.
            reader.join(max(0.0, deadline - time.monotonic()))
            timed_out = reader.is_alive()
        except subprocess.TimeoutExpired:
            timed_out = True
        if timed_out:
            _stop_group(proc)
            reader.join(1.0)
    except BaseException:
        # Ctrl-C 등으로 러너가 빠질 때 새 그룹의 자식이 혼자 남지 않게 한다.
        _stop_group(proc)
        raise
    return (None if timed_out else proc.returncode), "".join(out)


def run_gate(g: Gate, ctx: Ctx, stream: bool) -> tuple[str, str, str]:
    """(상태, 이유, 붙잡은 출력). 상태는 PASS · FAIL · SKIP. stream 이면 출력을 받는 대로도 흘린다."""
    why = missing(g, ctx)
    if why:
        # CI 에서 도구가 빠진 것은 건너뛸 일이 아니라 러너 설정의 고장이다.
        if ctx.ci and not why.startswith("pull request only"):
            return "FAIL", why, ""
        return "SKIP", why, ""
    env = dict(os.environ)
    if ctx.base and ctx.head:
        env.setdefault("BASE", ctx.base)
        env.setdefault("HEAD", ctx.head)
    captured: list[str] = []
    started = time.monotonic()
    # 제한 시간은 명령마다가 아니라 게이트 하나의 명령 전부에 걸린다.
    deadline = started + g.timeout
    for argv in g.cmd:
        try:
            code, out = _run_cmd(_argv(argv, ctx), ROOT / g.cwd, env, stream, deadline)
        except OSError as exc:
            return "FAIL", f"could not start: {exc}", "".join(captured)
        captured.append(out)
        if code is None:
            return "FAIL", f"timed out after {g.timeout} s", "".join(captured)
        if code != 0:
            return "FAIL", f"exit {code}", "".join(captured)
    return "PASS", f"{time.monotonic() - started:.1f}s", "".join(captured)


def _escape_data(s: str) -> str:
    """워크플로 명령의 메시지 부분 이스케이프."""
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(s: str) -> str:
    """워크플로 명령의 속성 값 이스케이프. 메시지 규칙에 `:` 와 `,` 가 더해진다."""
    return _escape_data(s).replace(":", "%3A").replace(",", "%2C")


def annotation(gate_id: str, message: str) -> str:
    """막는 게이트의 FAIL 하나를 GitHub 체크 화면에 올리는 `::error` 한 줄."""
    return f"::error title={_escape_property('gate ' + gate_id)}::{_escape_data(message)}"


#: 출력에서 실패를 말하는 줄의 표지. 게이트 출력의 첫 줄은 흔히 머리말이라 이 줄을 먼저 고른다.
_FAILURE_WORD = re.compile(r"\b(?:fail(?:ed|ure)?|error|drift|block(?:ed)?)\b", re.I)


def failure_line(why: str, out: str) -> str:
    """주석에 실을 실패의 첫 줄: 이유(exit N · timed out …)와, 출력에서 실패를 말하는 첫 줄
    (없으면 비어 있지 않은 첫 줄)."""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    first = next((ln for ln in lines if _FAILURE_WORD.search(ln)), lines[0] if lines else "")
    return f"{why}: {first}" if first else why


def _line(status: str, g: Gate, why: str) -> str:
    tag = status + (" (advisory)" if g.advisory and status == "FAIL" else "")
    return f"{tag:<16} {g.id:<20} {why}"


def print_table(gates: list[Gate]) -> None:
    for g in gates:
        where = ", ".join(([f"ci.yml:{g.ci} (registry)"] if g.ci else []) + list(g.steps)) or "local only"
        needs = ", ".join(g.needs) or "python"
        paths = "any file" if g.paths == ANY else " ".join(g.paths)
        kind = "advisory" if g.advisory else "blocking"
        print(f"{g.id:<20} {kind:<9} needs: {needs}\n{'':<30} runs in: {where}\n"
              f"{'':<30} relevant: {paths}\n{'':<30} {g.note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run the repository's gates from one registry")
    ap.add_argument("ids", nargs="*", help="gate ids to run (default: the gates the change touches)")
    ap.add_argument("--all", action="store_true", help="run every gate")
    ap.add_argument("--ci", action="append", metavar="JOB",
                    help="run every gate assigned to this ci.yml job (repeatable)")
    ap.add_argument("--list", action="store_true", help="print the registry and exit")
    ap.add_argument("--base", default="origin/main", help="base for changed files (default origin/main)")
    ap.add_argument("--network", action="store_true", help="also run gates that need the network")
    ap.add_argument("--title", default="", help="pull request title for the pr-title gate")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every gate's output")
    a = ap.parse_args(argv)

    unknown = [i for i in a.ids if i not in BY_ID]
    if unknown:
        print(f"unknown gate id: {', '.join(unknown)} (see --list)", file=sys.stderr)
        return 2

    if a.list:
        print_table(list(GATES))
        return 0

    ci = bool(a.ci)
    if ci:
        # CI 는 BASE·HEAD 를 이벤트에서만 받는다. 스스로 구하면 push 에서도 PR 전용 게이트가 돈다.
        base, head = os.environ.get("BASE", ""), os.environ.get("HEAD", "")
        chosen = [g for g in GATES if g.ci in a.ci]
        empty = [j for j in a.ci if not any(g.ci == j for g in GATES)]
        if empty:
            print(f"no gate is assigned to ci job: {', '.join(empty)}", file=sys.stderr)
            return 2
    else:
        base, head = os.environ.get("BASE", ""), os.environ.get("HEAD", "")
        if not (base and head):
            rng = local_range(a.base)
            base, head = rng if rng else ("", "")
        if a.ids:
            chosen = [BY_ID[i] for i in a.ids]
        elif a.all:
            chosen = list(GATES)
        elif not base:
            # 바뀐 파일을 못 구하면 덜 돌리지 않고 전부 돈다.
            print(f"cannot find the merge base with {a.base}; running every gate")
            chosen = list(GATES)
        else:
            changed = changed_files(base)
            print(f"{len(changed)} file(s) changed against {a.base} ({base[:12]})")
            chosen = []
            for g in GATES:
                if relevant(g, changed):
                    chosen.append(g)
                else:
                    print(_line("SKIP", g, "no changed file it reads"))
    ctx = Ctx(ci=ci, network=ci or a.network, base=base, head=head, title=a.title)

    results: list[tuple[Gate, str, str]] = []
    failures: list[tuple[Gate, str]] = []
    for g in chosen:
        if ci:
            print(f"::group::{g.id}", flush=True)
        status, why, out = run_gate(g, ctx, stream=ci)
        if ci:
            print("::endgroup::", flush=True)
        elif out and (status == "FAIL" or a.verbose):
            print(out.rstrip())
        print(_line(status, g, why), flush=True)
        results.append((g, status, why))
        if status == "FAIL" and not g.advisory:
            failures.append((g, failure_line(why, out)))

    blocking = [g.id for g, s, _ in results if s == "FAIL" and not g.advisory]
    if ci:
        print("\n── gates")
        for g, s, why in results:
            print(_line(s, g, why))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"### Gates ({', '.join(a.ci)})\n\n| gate | result | detail |\n|---|---|---|\n")
                for g, s, why in results:
                    fh.write(f"| {g.id} | {s}{' (advisory)' if g.advisory and s == 'FAIL' else ''} | {why} |\n")
    if os.environ.get("GITHUB_ACTIONS"):
        for g, msg in failures:
            print(annotation(g.id, msg), flush=True)
    print(f"{len(results)} gate(s) run, {len(blocking)} blocking failure(s)"
          + (f": {', '.join(blocking)}" if blocking else ""))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
