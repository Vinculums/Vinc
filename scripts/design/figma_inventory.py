# -*- coding: utf-8 -*-
"""figma_inventory.py — 코드의 화면·모달·아이콘·컴포넌트를 Figma 의 그것과 대조한다.

`figma_diff.py` 가 토큰 **값**을 본다면 이쪽은 **목록**을 본다. 둘을 가른 이유는
고장의 종류가 달라서다: 값은 "다르다"가 문제고, 목록은 **"한쪽에만 있다"**가 문제다.

왜 필요한가. 2026-09-05 에 손으로 한 번 세어 보니 앱 모달 다섯과 사이트 문서 다섯 쪽이
Figma 에 아예 없었다. 그때까지 이 칸은 계획서에 "대조 안 됨"이라고 적힌 채로 몇 주를
서 있었다 — 아무도 세지 않았기 때문이지 어렵기 때문이 아니었다. 손으로 세면 다음 주에
같은 자리에 다시 선다. 근거: [[vinc.research.figma-code-sync-2026-09-05]]

## 무엇이 실패인가 (그리고 무엇이 아닌가)

- **FAIL · 미선언**: 코드에 화면이나 모달이 새로 생겼는데 아래 맵에 대응이 없다. 이게
  이 게이트의 존재 이유다. 화면을 늘리면서 Figma 를 잊는 일이 여기서 막힌다.
- **FAIL · 맵 부패**: 맵이 가리키는 Figma 프레임이 실제로 없다(개명됐거나 지워졌다).
- **GAP · 기지의 결손**: 맵에 `None` 으로 적힌 것. 코드에는 있고 Figma 에는 아직 없다고
  **선언한** 항목이라 실패로 세지 않고 매 실행 목록으로 보여 준다. 그리지 않은 것을
  빨갛게 만들면 게이트가 곧 꺼지고, 조용히 넘기면 애초에 없느니만 못하다. 그려 넣은 뒤
  `None` 을 프레임 이름으로 바꾸면 그때부터 실재가 검증된다.
- **FAIL · 표식 부패**: 컴포넌트 변형이 가리키는 코드 표식이 `app/src` 에 없다. 세는
  축들과 달리 이쪽은 **대응**을 본다 — 개수가 맞아도 이름이 어긋나면 여기서 걸린다.
- **INFO · Figma 에만**: 맵이 참조하지 않는 Figma 프레임. 시안이거나 폐기 대기라 판정이
  필요한 것들이므로 세어서 보여만 준다.
- **FAIL · 스토리보드 부패**: `06 · Storyboards` 의 단계가 가리키는 보드가 03 · App · 04 · Site 에
  없거나, 그 캡처 상태가 매니페스트에서 빠졌다. 화면을 걷어 내면서 여정 줄을 잊는 일이 여기서 막힌다.

## 쓰는 법

    python -X utf8 scripts/design/figma_inventory.py            # 보고서, 위 FAIL 이면 exit 1

라이브 덤프는 `figma/figma-inventory.json` 이고 에이전트가 `use_figma` 로 뜬다:

```js
const want = ["01 · Foundations", "02 · Components", "03 · App", "04 · Site"];   // 여덟 쪽 구조에서 게이트가 세는 넷
const pages = {};
for (const p of figma.root.children) {            // ⚠ 페이지 전환은 호출당 한 번뿐이라
  if (!want.includes(p.name)) continue;           //    이 루프는 페이지별로 나눠 돌린다
}
// 페이지 하나당 한 번:
const page = await figma.getNodeByIdAsync(PAGE_ID);
await figma.setCurrentPageAsync(page);
// 보드는 섹션 안에 산다(03 · App 의 Surfaces · Overlays …). 섹션은 투명하게 펼친다.
const tops = page.children.flatMap(n => n.type === "SECTION" ? n.children : [n]);
const frames = tops.filter(n => n.type !== "COMPONENT" && n.type !== "COMPONENT_SET" && n.type !== "SECTION")
  .map(n => ({ name: n.name, children: (n.children || []).map(c => c.name) }));
const sets = page.findAllWithCriteria({ types: ["COMPONENT", "COMPONENT_SET"] })
  .filter(n => !(n.parent && n.parent.type === "COMPONENT_SET"));
const variants = {};                              // 02 · Components 만 쓴다(App / * 세트를 대조한다)
for (const n of sets)
  if (n.type === "COMPONENT_SET") variants[n.name] = n.children.map(c => c.name);
return { name: page.name, frames, components: sets.map(n => n.name), variants };
```

`06 · Storyboards` 는 따로 한 번 뜬다. 보드가 아니라 여정 줄이라 모양이 다르다:

```js
const page = figma.root.children.find(p => p.name === "06 · Storyboards");
await figma.setCurrentPageAsync(page);
// 줄 = 이름 아래 `steps` 프레임을 가진 프레임. 단계 = `steps` 의 프레임 자식(화살표 텍스트는 뺀다).
const journeys = {};
for (const row of page.findAll(n => n.type === "FRAME" && n.children?.some(c => c.type === "FRAME" && c.name === "steps"))) {
  const steps = row.children.find(c => c.type === "FRAME" && c.name === "steps");
  journeys[row.name] = steps.children.filter(c => c.type === "FRAME").map(c => c.name);
}
return { name: page.name, journeys };
```

⚠ 덤프는 손으로 고치지 않는다. 고치면 게이트가 Figma 가 아니라 그 파일과 합의한다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

HUB = pathlib.Path(__file__).resolve().parent
#: 컴포넌트가 사는 페이지. App · Shell · Overlay · Site · Community 컴포넌트가 한 페이지에
#: 모여 있다. 이 축이 대조하는 것은 그중 `App / *` 세트뿐이다(Shell · Overlay 는 캡처 승격이라 캡처가 표식이다).
#: 화면 보드는 03 · App(앱) · 04 · Site(사이트)의 섹션 안에 살고, 프레임 축은 페이지를 가리지 않고 합쳐 센다.
#: 근거: [[vinc.canon.design-system-operations]]
APP_COMPONENTS_PAGE = "02 · Components"
APP_COMPONENT_PREFIX = "App / "
VINC = HUB.parent.parent
# figma_copy.py 와 같은 덮어쓰기: worktree 에서는 형제 체크아웃이 없으므로 VINC_SITE_DIR 로 사이트 트리를 준다.
SITE = pathlib.Path(os.environ["VINC_SITE_DIR"]).resolve() if os.environ.get("VINC_SITE_DIR") else VINC.parent / "Vinc-homepage" / "site"
APP = VINC / "app" / "src"

# ── 사이트 라우트 ↔ Figma 프레임 ────────────────────────────────────────────
# 사이트 보드는 2026-09-06 부터 **코드 캡처**다(scripts/design/capture_site_figma.mjs): 라우트×상태 하나가
# 보드 하나고, 이름은 `Site · <Route>[ · <state>] / dark` 로 캡처 매니페스트의 상태 id 에서 나온다.
# 그래서 이 축의 기대 집합은 손으로 적지 않고 매니페스트에서 만든다 — 매니페스트에 있는데 보드가
# 없으면 FAIL, 코드에 라우트가 생겼는데 매니페스트에 없으면 미선언 FAIL. 문서는 셸 하나를 공유해서
# 매니페스트가 고른 대표 경로만 보드를 두고, 나머지 문서 경로는 라우트 축에서 뺀다(site_routes).
# 근거: [[decision:site-boards-are-captures-of-the-running-code]].
SITE_MANIFEST = HUB / "captures" / "site" / "manifest.json"
SITE_ROUTE_TITLES = {
    "landing": "Landing", "community": "Community", "account": "Account", "team": "Account · Team",
    "login": "Auth · Login", "verify": "Auth · Verify", "welcome": "Auth · Welcome", "connect": "Connect",
    "connect-app": "Connect-app", "subscribe": "Subscribe", "unsubscribe": "Unsubscribe",
    "account-delete": "Account · Delete", "pricing": "Pricing", "download": "Download", "release": "Release",
    "privacy": "Privacy", "404": "404", "docs": "Docs",
    "licensing": "Licensing", "support": "Support", "terms": "Terms", "demo": "Demo",
}
#: 매니페스트에는 있지만 보드로는 두지 않는 상태. 값은 이유다.
REFERENCE_ONLY = {
    "landing__light": "라이트는 변수 모드로 본다(§8 D3), 보드를 두 벌 두지 않는다",
    "landing__ko": "보드는 영어다(§8 D2); 한국어 렌더는 캡처 PNG(captures/site)로 남는다",
    "pricing__ko": "보드는 영어다(§8 D2)",
    "team__invite-accepted": "수락 직후 700ms 에 페이지가 새로 고쳐져 DOM 직렬화가 끝나기 전에 사라진다; PNG 참조만",
}
#: 매니페스트에 새로 들어왔지만 아직 보드를 그리지 않은 상태. 값이 None 인 상태는 부패가 아니라
#: 선언된 결손(gap)으로 센다. 보드를 그리면 여기서 지운다.
BOARD_PENDING = frozenset({
    "community__home", "community__page-read", "community__talk",
    "community__agent-share", "community__agent-take",
})
MANIFEST_ROUTE_ALIAS = {"/this-page-is-not-here/": "/404"}


def site_board_name(state_id: str) -> str:
    route, state = state_id.split("__", 1)
    return f"Site · {SITE_ROUTE_TITLES[route]}" + ("" if state == "default" else f" · {state}") + " / dark"


def _site_axis_from_manifest() -> tuple[dict, dict]:
    """(라우트 → 대표 보드, 라우트 → {상태: 보드}). 매니페스트가 없으면 둘 다 비어 있고
    그 사실은 main 이 출력한다 — 조용히 통과시키지 않는다."""
    if not SITE_MANIFEST.exists():
        return {}, {}
    rows = json.loads(SITE_MANIFEST.read_text(encoding="utf-8"))["states"]
    routes, states = {}, {}
    for row in rows:
        sid = row["id"]
        if sid in REFERENCE_ONLY:
            continue
        route = MANIFEST_ROUTE_ALIAS.get(row["route"], row["route"])
        board = None if sid in BOARD_PENDING else site_board_name(sid)
        routes.setdefault(route, board)
        states.setdefault(route, {})[sid.split("__", 1)[1]] = board
    return routes, states


_MANIFEST_ROUTES, _MANIFEST_STATES = _site_axis_from_manifest()
#: 문서 경로 중 보드를 두는 것은 캡처 매니페스트가 고른 대표 경로뿐이다(위 _MANIFEST_ROUTES 에 들어 있다).
#: 손으로 그린 문서 보드는 90 · Archive 에 있다.
DOCS_ROUTES: dict[str, str | None] = {}
ROUTES = {**_MANIFEST_ROUTES, **DOCS_ROUTES}
#: 라우트로 세지 않는 것. 내부 점검용이라 시안이 있을 이유가 없다.
#: /bo/ = 운영자 콘솔(noindex). /community-view/ = 커뮤니티 그래프 쪽(assets/community.js graphView)이 싣는 iframe 이라 그 쪽 보드에 그려진다.
ROUTE_IGNORE = {"/vt-check/", "/vt-check/b", "/bo/", "/community-view/"}

#: 상태 매트릭스 — **화면이 아니라 화면×상태가 단위다.** 2026-09-06 부터 매니페스트가 정한다:
#: 상태 하나가 보드 하나(`Site · Community · modal-busy / dark`). 여기 없는 라우트는
#: "상태를 아직 캡처하지 않음"으로 세어 출력한다.
ROUTE_STATES = _MANIFEST_STATES

# ── 앱 모달 ↔ Figma 오버레이 상태 ───────────────────────────────────────────
# 키는 `모듈:제목식`. 줄 번호가 아니라 식이라 코드가 위아래로 움직여도 안 깨진다.
DIALOGS = {
    # 값은 03 · App 의 Overlays 섹션 캡처 보드다(App · Topbar|View · <state> / dark). None 은 선언된 결손.
    "ask.ts:c.historyTitle": "App · Topbar · ask-history / dark",
    # 문서 편집기 시트의 충돌 배너는 다른 기기의 쓰기가 있어야 뜬다 — 단일 스토어 캡처로는 못 만든다.
    "doc-editor.ts:opts.docId ? COPY.ui.viewx.readerTitle : COPY.ui.viewx.edNew": None,
    "empty-invite.ts:COPY.ui.sheet.importTitle": "App · Topbar · import-pack-sheet / dark",
    "main.ts:COPY.ui.palette.keyboard": "App · Topbar · cheatsheet / dark",
    # 2026-09-22 scope-back(VIN-88): 설정 · GRAPH.md · Tools(쓰기/가져오기/공유) 세 모달이
    # 레일 바닥 설정 버튼이 여는 **시트 하나**(분류 여덟)로 합쳐졌다. 그래서 여기 있던
    # `main.ts:COPY.ui.topbar.settings` · `conventions.ts:C.title` · `tools.ts:T.title`
    # 세 줄이 이 한 줄이 됐다. 옛 보드(settings-account · graphmd · tools-create)는 탭이
    # 가로로 선 옛 상자를 찍은 것이라 이 시트를 대표하지 못한다 — 분류 레일 시트의
    # 캡처 보드가 생기기 전까지는 **선언된 결손**으로 둔다.
    # 분류 레일 시트를 찍은 보드(settings-account · reading · about …)가 이 시트를 대표한다.
    "surfaces/settings.ts:COPY.ui.topbar.settings": "App · Topbar · settings-account / dark",
    "sheets.ts:COPY.ui.sheet.shareTitle": "App · Topbar · share-sheet / dark",
    "sheets.ts:COPY.ui.sheet.importTitle": "App · Topbar · import-pack-sheet / dark",
    "lnb.ts:openContextMenu": "App · View · topic-context-menu / dark",
    "lnb.ts:promptText": "App · View · topic-rename-prompt / dark",
    # 캔버스 위 노드 우클릭 메뉴는 캔버스 좌표가 필요해 아직 캡처하지 않았다(매트릭스의 갭).
    "surfaces/view.ts:openContextMenu": None,
    "surfaces/view.ts:confirmDestructive": "App · View · node-delete-confirm / dark",
    # 문서 홈의 문서 지우기는 같은 파괴 확인(confirmDestructive)이다. 따로 찍은 보드는 없고 노드 지우기 보드가 그 모양이다.
    "surfaces/home.ts:confirmDestructive": "App · View · node-delete-confirm / dark",
    # 작업하는 자리에서 만들기(Part 3). 옛 `new-node-sheet` 폼은 컴포저로 바뀌었다 — 옛 보드는
    # 폼 하나를 찍은 것이라 컴포저(종류 넷 · id 확인 · 연결)를 대표하지 못한다. 디자인 라운드
    # (A1)를 건너뛰고 구현했으므로 셋 다 **선언된 결손**이다.
    "node-composer.ts:COPY.author.composerTitle": None,
    # 역할 편집은 시트가 아니라 Agents 보드 안의 면이다(`#home/agents/<id>`). 보드의 팀 꾸리기는
    # 이미 있는 대화창 셋을 빌린다: 팀 이름은 주제 이름 바꾸기와 같은 입력창, 해체는 노드 지우기와
    # 같은 파괴 확인. 카드 · 팀 메뉴는 캔버스 메뉴와 같이 아직 찍지 않았다(선언된 결손).
    "surfaces/agents-board.ts:promptText": "App · View · topic-rename-prompt / dark",
    "surfaces/agents-board.ts:confirmDestructive": "App · View · node-delete-confirm / dark",
    "surfaces/agents-board.ts:openContextMenu": None,
    "connect.ts:A.connectTitle": None,
    # The owner excluded Figma work for this package. Reuse the existing action
    # sheet; record its missing design counterpart without inventing a node id.
    "team-key.ts:c.title": None,
    "authoring.ts:openContextMenu": None,
    # 빈 화면에 파일 여럿을 끌어다 놓으면 뜨는 일괄 가져오기(여태 없는 라우트로 가 사라졌다).
    "empty-invite.ts:COPY.author.menuImport": None,
    # 첫 실행 로그인은 2026-09-26 부터 시트가 아니라 창 전체 화면이다(firstrun.ts
    # openSignInScreen) — 모달 대응에서 빠지고, 보드 `App · View · first-run / dark` 는
    # 표면×상태 축(캡처 매니페스트의 view__first-run)이 계속 대조한다.
    "sheets.ts:S.docTitle": "App · Topbar · import-doc-sheet / dark",
    "topbar.ts:COPY.ui.topbar.browseAll": "App · Topbar · browse-sheet / dark",
}
#: 앱 표면 ↔ Figma 화면 상태. 사이트의 ROUTE_STATES 와 같은 축이다 — 단위는 표면이
#: 아니라 **표면×상태**다. 앱 쪽은 상태가 프레임 이름 자체에 들어 있어서(예: `App ·
#: Search · no results …`) 값이 최상위 프레임 이름이거나 상태 보드의 자식 이름이다.
APP_MANIFEST = HUB / "captures" / "app" / "manifest.json"
APP_SURFACE_TITLES = {"home": "Home", "view": "View", "topbar": "Topbar", "search": "Search", "packs": "Packs"}


def app_board_name(state_id: str) -> str:
    """캡처 보드 이름. 라이트 테마 상태는 `/ light` 로 끝난다(§8 D3: 테마는 변수 모드지만 캡처는 렌더 결과다)."""
    surf, state = state_id.split("__", 1)
    theme = "light" if state.endswith("light") or state == "light-lens" else "dark"
    return f"App · {APP_SURFACE_TITLES[surf]}" + ("" if state == "default" else f" · {state}") + f" / {theme}"


def _app_module_for(state_id: str) -> str:
    """상태가 어느 코드 모듈의 것인가 — check_states 의 축은 표면 모듈이다."""
    surf, state = state_id.split("__", 1)
    if surf == "packs" and state.startswith("intake-"):
        return "surfaces/intake.ts"
    if surf == "topbar":
        if state.startswith("ask-"):
            return "ask.ts"
        if state.startswith("browse-"):
            return "surfaces/records.ts"
        if state.startswith("settings-"):
            return "surfaces/settings.ts"
        return "topbar.ts"
    return f"surfaces/{surf}.ts"


def _app_states_from_manifest() -> dict:
    if not APP_MANIFEST.exists():
        return {}
    out: dict = {}
    for row in json.loads(APP_MANIFEST.read_text(encoding="utf-8"))["states"]:
        sid = row["id"]
        out.setdefault(_app_module_for(sid), {})[sid.split("__", 1)[1]] = app_board_name(sid)
    return out


APP_STATES = {
    **_app_states_from_manifest(),
    # 계정 연결(기기 흐름)은 계정과 브라우저 승인이 있어야 그 상태에 닿는다 — 선언된 결손.
    "account-connect.ts": {"not-connected": None, "waiting": None, "connected": None, "expired": None, "failed": None},
}

# ── 앱 컴포넌트 (변형×코드 표식) ──────────────────────────────────────────────
#: 데스크탑 컴포넌트 하나하나를, 그것을 정의하는 **코드의 표식**에 건다.
#:
#: 왜 이 축이 따로 필요한가. 위의 축들은 전부 **세는** 축이다 — 있나 없나. 그런데
#: 2026-09-06 대조에서 나온 어긋남 넷은 값도 개수도 맞고 **이름만** 코드와 달랐다:
#: DocRow 의 잉크 눈금이 1/2/3(코드는 `--ink:0|1|2`), StateMark 의 첫 값이 Full
#: (코드는 `stateDot("filled"…)`), PackCard 의 Highlight 는 코드에 근거가 아예 없었고,
#: Chip 은 코드의 `.chip-static` 을 안 그리고 있었다. 세는 게이트 둘은 그 넷을 전부
#: 통과시켰다. 세어서 맞다는 것과 대응한다는 것은 다른 말이다.
#: 근거: [[vinc.spec.surface-tree]]
#:
#: 값의 뜻:
#:   문자열 — `app/src` 어딘가에 그대로 있어야 하는 표식. 없으면 FAIL(개명·삭제됐다).
#:   None   — 코드에 **고유 표식이 없는 기본값**(수식자 없는 평범한 상태). 결손이 아니다.
APP_COMPONENTS: dict[str, dict[str, str | None]] = {
    "App / Button":      {"Kind=Primary": 'btn-primary', "Kind=Ghost": 'class="btn"',
                          "Kind=Quiet": "btn-quiet", "Kind=Danger": "btn-danger"},
    "App / Chip":        {"State=Default": "chip", "State=Active": "chip.active",
                          "State=Static": "chip-static"},
    "App / NavItem":     {"State=Default": "lnb-navrow", "State=Active": "lnb-navrow.on"},
    "App / DomainRow":   {"State=Default": "lnb-group", "State=Active": "lnb-group.on"},
    # 잉크 1단은 코드에서 색을 주지 않는 기본값이라 표식이 없다. 0 과 2 만 CSS 에 산다.
    "App / DocRow":      {"Ink=0": "--ink:0", "Ink=1": None, "Ink=2": "--ink:2",
                          "Ink=On": "lnb-doc.on"},
    "App / ListRow":     {"State=Default": "list-row", "State=Hover": "list-row:hover"},
    "App / StateMark":   {"State=Filled": '"filled"', "State=Half": '"half"',
                          "State=Empty": '"empty"'},
    "App / MenuItem":    {"Tone=Default": "menu-item", "Tone=Hover": "menu-item:hover",
                          "Tone=Danger": "danger"},
    # App / PackCard 는 대응하는 코드가 없어 여기 두지 않는다(보드는 90 · Archive). 표식을 주석에서만
    # 찾으면 게이트가 거짓으로 초록이 된다.
    "App / UpdateToast": {"State=Available": "t.available", "State=Downloading": "t.downloading",
                          "State=Installing": "t.installing", "State=Failed": "t.failed"},
    # 변형이 없는 것들. 컴포넌트 자체의 표식 하나만 건다.
    "App / ProgressBar": {"": "proc-bar"},
    "App / ErrorBanner": {"": "error-banner"},
    "App / EmptyState":  {"": "empty-invite"},
    "App / Tooltip":     {"": "tooltip"},
    "App / Slider":      {"": "slider"},
    # 2026-09-07: 캡처에서 승격한 원자들. 표식은 그 원자가 사는 CSS 클래스나 분기 문자열이다.
    "App / IconButton":  {"": "tb-btn"},
    "App / Kbd":         {"": "tb-kbd"},
    "App / SearchField": {"": "tb-q"},
    "App / SearchModeButton": {"Mode=Find": 'mode === "find"', "Mode=Ask": 'mode === "ask"'},
    # 유휴 타일은 aria-checked 가 템플릿 문자열로 찍혀 고정 표식이 없다(DocRow Ink=1 과 같은 사정).
    "App / TileRadio":   {"State=Selected": 'aria-checked="true"', "State=Idle": None},
    # 2026-09-10: 켬·끔 세그를 토글(`.switch`)로 바꿨다. 끔은 aria-checked 가 템플릿 값이라 고정 표식이 없다.
    "App / SwitchRow":   {"State=On": 'aria-checked="true"', "State=Off": None},
    "App / SliderRow":   {"": "slider-row"},
    # 문서 | 그래프 전환(머리의 `#tb-switch`). 옛 트리 모드 표식(`"docs"`·`"nodes"`)은 다른 코드의
    # 같은 글자에 우연히 걸려 있었다.
    "App / TreeMode":    {"Mode=Documents": "tb-to-docs", "Mode=Nodes": "tb-to-graph"},
    # App / FloatCard · Sheet · Titlebar · Topbar 는 2026-09-07 에 Archive 로 갔다. 그 자리는 02 · Components 의
    # `Shell / *` · `Overlay / *` 가 맡는데, 둘은 캡처 승격이라 이 표식 축이 아직 세지 않는다.
}

#: 헬퍼가 **정의된** 파일. 호출부가 아니므로 세지 않는다.
DIALOG_DEF_FILES = {"components.ts"}
HELPERS = ("openSheet", "confirmDestructive", "promptText", "openContextMenu")
_DIALOG_RX = re.compile(r"\b(" + "|".join(HELPERS) + r")\(\s*\{?\s*(?:title:\s*([^,}\n]+))?")

# ── 아이콘 ──────────────────────────────────────────────────────────────────
#: 코드에만 있어도 Figma 컴포넌트를 기대하지 않는 것. 문서 페이지의 장식용 별칭
#: (`i-*`)과, 마크 자체(로고 섹션이 따로 문서화한다)다.
ICON_IGNORE_PREFIX = ("i-",)
ICON_IGNORE = {"view"}
#: 코드에는 있고 Figma 아이콘 시트에는 아직 없는 것(2026-09-05 실측 22건). 라우트·모달의
#: `None` 과 같은 뜻이다 — **선언된** 결손이라 실패로 세지 않는다. 그려 넣으면 여기서 뺀다.
#: 여기 없는 새 아이콘이 코드에 생기면 그때는 FAIL 이다. 그것이 이 목록의 쓸모다.
#: 2026-09-05: 17개를 사이트 모듈의 실제 도형으로 Figma 에 만들어 넣었고, 유령 셋
#: (display·width·email)은 추출기를 고쳐 사라졌다. 남은 둘은 원본이 이 경로에 없다.
#: `more`(가로 점 셋)는 Agents 보드의 카드·팀 메뉴 손잡이로 생겼고 아이콘 시트에는 아직 없다.
#: 사이트 모듈의 아이콘 열다섯(backup … tags)도 01 · Foundations 아이콘 시트에 없다. CI 는 사이트를 체크아웃하지
#: 않아 이 축을 못 보므로 사이트를 둔 로컬 실행에서만 드러난다. 그리는 일은 route B 라 선언된 결손으로 둔다.
ICON_GAPS: set[str] = {"more", "backup", "copy", "desktop", "identity", "info", "key", "link", "mail", "offline",
                       "open", "parse", "plug", "portable", "shield", "tags"}


def site_present() -> bool:
    """`Vinc-homepage` 가 체크아웃돼 있는가.

    ⚠ CI 는 자기 리포 하나만 체크아웃한다. 그래서 사이트가 없는 것은 **정상**이고,
    그때 라우트 대조는 원리상 성립하지 않는다. 이걸 다루지 않으면 두 가지로 다 틀린다
    — 빈 집합을 코드 목록으로 쓰면 맵의 38줄이 전부 "코드에서 사라졌다"로 빨개지고,
    조용히 통과시키면 "검사했다"는 거짓이 된다. `sync_out.py` 가 같은 문제를 먼저
    만났고 결론도 같았다: **건너뛰되 세어서 출력한다.**
    """
    return SITE.exists()


def site_routes() -> set[str]:
    if not site_present():
        return set()
    out = set()
    for p in SITE.rglob("index.html"):
        rel = p.parent.relative_to(SITE).as_posix()
        out.add("/" if rel == "." else f"/{rel}/")
    for p in SITE.glob("*.html"):
        if p.name != "index.html":
            out.add("/" + p.stem)
    # 문서는 셸 하나를 공유한다. 매니페스트에 없는 문서 경로는 대표 보드가 답하므로 라우트로 세지 않는다.
    return {r for r in out if r not in ROUTE_IGNORE
            and not (r.startswith("/docs/") and r not in _MANIFEST_ROUTES)}


def app_dialogs() -> set[str]:
    out = set()
    for p in sorted(APP.rglob("*.ts")):
        rel = p.relative_to(APP).as_posix()
        if rel in DIALOG_DEF_FILES:
            continue
        for m in _DIALOG_RX.finditer(p.read_text(encoding="utf-8")):
            helper, title = m.group(1), (m.group(2) or "").strip()
            out.add(f"{rel}:{title or helper}")
    return out


def app_surfaces() -> set[str]:
    """앱이 실제로 그리는 표면 모듈. `surfaces/` 전부와, 표면급으로 쓰이는 최상위 둘."""
    out = {"surfaces/" + p.name for p in (APP / "surfaces").glob("*.ts")}
    for extra in ("ask.ts", "account-connect.ts", "topbar.ts"):   # topbar.ts: 셸 핸들이 여는 오버레이의 축
        if (APP / extra).exists():
            out.add(extra)
    return out


def code_icons() -> set[str]:
    """앱의 SVG 파일 + 사이트 아이콘 모듈의 항목 이름.

    ⚠ 사이트 모듈은 `이름: [그림, '설명']` 꼴이라 **값이 `[` 로 시작하는 것만** 아이콘이다.
    처음에는 `이름:` 을 전부 잡았는데, 그러면 모듈 안의 CSS 문자열(`display:` · `width:`)과
    주석 문자열(`'email: magic links…'`)까지 아이콘으로 세어 **있지도 않은 결손 셋**을
    만들어 낸다. 실제로 그 셋을 Figma 에 그리려다 원본이 없어서 알았다.
    """
    out = {p.stem for p in (APP / "icons").glob("*.svg")}
    js = SITE / "assets" / "vinc-icons.js"
    if js.exists():
        out |= set(re.findall(r"^\s*([a-z0-9-]+)\s*:\s*\[", js.read_text(encoding="utf-8"), re.M))
    return {i for i in out if i not in ICON_IGNORE and not i.startswith(ICON_IGNORE_PREFIX)}


def figma_names(live: dict) -> tuple[set[str], set[str], set[str], set[str]]:
    """(프레임 이름, 오버레이 상태 이름, 아이콘 이름, 모든 자식 이름).

    마지막 것이 화면 상태의 후보다 — 상태는 상태 보드 프레임의 **자식**으로 산다
    (`VERIFY · EXPIRED` 처럼). 오버레이용 `states` 와 달리 이쪽은 프레임 이름으로
    거르지 않는다: 상태 보드의 이름 규칙이 하나가 아니라서다.
    """
    frames, states, icons, kids = set(), set(), set(), set()
    for pname, page in live.get("pages", {}).items():
        for f in page.get("frames", []):
            frames.add(f["name"])
            kids |= set(f.get("children", []))
            if "Overlays" in f["name"] or "states" in f["name"] or "tab" in f["name"]:
                states |= set(f.get("children", []))
        for c in page.get("components", []):
            if c.startswith("Icon / "):
                icons.add(c[len("Icon / "):])
    return frames, states, icons, kids


def check_states(routes: set[str], matrix: dict, live_kids: set[str]) -> dict:
    """선언된 상태 매트릭스를 Figma 자식 프레임과 대조한다.

    라우트 자체의 존재는 `_check` 가 이미 본다. 여기는 그 위층이다 — 그 라우트가
    가진다고 **선언한** 상태 하나하나가 실재하는가. 매트릭스가 아예 없는 라우트는
    실패가 아니라 `undeclared` 로 세어 출력한다: 상태가 하나뿐이라고 거짓 주장하는
    대신, 아직 안 정했다고 말하는 쪽이 맞다.
    """
    rotten, gaps, counted = [], [], 0
    for route, states in matrix.items():
        if route not in routes:
            continue                     # 라우트 자체가 사라졌다 — _check 가 잡는다
        for name, frame in states.items():
            counted += 1
            if frame is None:
                gaps.append(f"{route}#{name}")
            elif frame not in live_kids:
                rotten.append((f"{route}#{name}", frame))
    return {"rotten": rotten, "gaps": gaps, "declared": counted,
            "undeclared_routes": sorted(routes - set(matrix))}


def app_source() -> str:
    """앱 소스 전문을 한 덩어리로. 표식이 **어느 파일에** 있는지는 여기선 안 따진다 —
    묻는 것은 "코드에 아직 있는가" 하나뿐이고, 파일이 갈라져도 그 답은 안 변한다."""
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(APP.rglob("*")) if p.suffix in (".ts", ".css"))


def check_components(live: dict, mapping: dict, source: str) -> dict:
    """Figma 컴포넌트·변형을 코드 표식과 맞대 본다.

    네 가지가 갈린다:
      - 미선언  : Figma 에 컴포넌트나 변형이 늘었는데 맵에 없다(FAIL)
      - 잔여    : 맵에 있는데 Figma 에서 사라졌다(FAIL)
      - 표식 부패: 맵이 가리키는 코드 표식이 `app/src` 에 없다(FAIL) — 개명이 여기서 잡힌다
      - 기본값  : 코드에 고유 표식이 없는 변형(None). 결손이 아니라 정보다
    """
    dsapp = live.get("pages", {}).get(APP_COMPONENTS_PAGE, {})
    comps = {c for c in dsapp.get("components", []) if c.startswith(APP_COMPONENT_PREFIX)}
    variants = dsapp.get("variants", {})
    undeclared, stale, rotten, plain, counted = [], [], [], [], 0

    undeclared += sorted(f"{c} (컴포넌트)" for c in comps - set(mapping))
    stale += sorted(f"{c} (컴포넌트)" for c in set(mapping) - comps)

    for name in sorted(comps & set(mapping)):
        want = mapping[name]
        # 변형이 없는 컴포넌트는 덤프에 variants 항목이 없다. 맵도 `{"": 표식}` 한 줄이다.
        live_vals = set(variants.get(name, [""] if "" in want else []))
        undeclared += sorted(f"{name} · {v}" for v in live_vals - set(want))
        stale += sorted(f"{name} · {v}" for v in set(want) - live_vals)
        for v in sorted(live_vals & set(want)):
            counted += 1
            token = want[v]
            if token is None:
                plain.append(f"{name} · {v}")
            elif token not in source:
                rotten.append((f"{name} · {v}" if v else name, token))
    return {"undeclared": undeclared, "stale": stale, "rotten": rotten,
            "plain": plain, "declared": counted}


#: 여정 줄이 사는 페이지와, 단계가 가리키는 보드가 사는 페이지.
STORYBOARD_PAGE = "06 · Storyboards"
SCREEN_PAGES = ("03 · App", "04 · Site")
_STEP_RX = re.compile(r"^\d+ · (.+)$")


def captured_boards() -> set[str]:
    """지금 캡처 매니페스트가 만드는 보드 이름 전부(앱·사이트). 보드 두기를 미룬 상태도 포함한다
    — 스토리보드가 묻는 것은 "그 상태가 아직 제품에 있는가"이지 "보드가 다 그려졌는가"가 아니다."""
    out: set[str] = set()
    for path, name in ((APP_MANIFEST, app_board_name), (SITE_MANIFEST, site_board_name)):
        if path.exists():
            out |= {name(r["id"]) for r in json.loads(path.read_text(encoding="utf-8"))["states"]}
    return out


def check_storyboards(live: dict, captured: set[str]) -> dict:
    """여정 줄의 단계 하나하나가 실재하는 화면을 가리키는지 본다.

    단계 프레임 이름은 `<순번> · <보드 이름>` 이다. 두 가지로 썩는다:
      - 보드 없음  : 03 · App · 04 · Site 에 그 이름의 보드가 없다(개명·보관됐다)
      - 상태 퇴역  : 보드는 남아 있어도 그 캡처 상태가 매니페스트에서 빠졌다. 화면을 걷어 낸
                     변경이 보드 보관을 빠뜨리면 앞의 검사만으로는 통과한다.
    덤프에 이 페이지가 없으면 실패다 — 덤프를 다시 뜨면서 이 축을 빼먹은 것이다.
    근거: [[vinc.canon.design-system-operations]]
    """
    pages = live.get("pages", {})
    page = pages.get(STORYBOARD_PAGE)
    if page is None:
        return {"missing_page": True, "rotten": [], "retired": [], "malformed": [], "journeys": 0, "steps": 0}
    boards = {f["name"] for p in SCREEN_PAGES for f in pages.get(p, {}).get("frames", [])}
    rotten, retired, malformed, steps = [], [], [], 0
    for journey, names in page.get("journeys", {}).items():
        for step in names:
            steps += 1
            m = _STEP_RX.match(step)
            if not m:
                malformed.append(f"{journey} › {step}")
                continue
            board = m.group(1)
            if board not in boards:
                rotten.append((f"{journey} › {step}", board))
            elif board not in captured:
                retired.append((f"{journey} › {step}", board))
    return {"missing_page": False, "rotten": rotten, "retired": retired, "malformed": malformed,
            "journeys": len(page.get("journeys", {})), "steps": steps}


def _check(kind: str, code: set[str], mapping: dict, live: set[str],
           gaps_set: frozenset = frozenset()) -> dict:
    """코드 항목을 맵에 비추고, 맵이 가리키는 이름이 Figma 에 실재하는지 본다.

    `gaps_set` 은 아이콘처럼 맵을 한 줄씩 손으로 적지 않고 이름 규칙으로 미는 경우에
    쓴다 — 맵이 가리키는 이름이 Figma 에 없더라도 그 키가 여기 있으면 **선언된 결손**
    이지 부패가 아니다. 없으면 FAIL 이고, 그게 새 아이콘을 잡는 자리다.
    """
    undeclared = sorted(code - set(mapping))
    gaps = sorted(k for k in code & set(mapping)
                  if mapping[k] is None or (mapping[k] not in live and k in gaps_set))
    rotten = sorted((k, mapping[k]) for k in code & set(mapping)
                    if mapping[k] is not None and mapping[k] not in live and k not in gaps_set)
    stale_map = sorted(set(mapping) - code)
    referenced = {mapping[k] for k in mapping if mapping[k]}
    return {"kind": kind, "undeclared": undeclared, "gaps": gaps, "rotten": rotten,
            "stale_map": stale_map, "figma_only": sorted(live - referenced)}


def main(argv: list[str] | None = None) -> int:
    # argv 를 인자로 받는 이유: 테스트가 이 함수를 부를 때 `sys.argv` 에는 러너의
    # 인자가 들어 있어 argparse 가 그걸 먹고 SystemExit 2 로 죽는다(실제로 물렸다).
    ap = argparse.ArgumentParser(description="코드 화면·모달·아이콘 목록 ↔ Figma 대조")
    ap.add_argument("--live", default="figma/figma-inventory.json")
    a = ap.parse_args(argv)
    p = HUB / a.live
    if not p.exists():
        print(f"[ERROR] 인벤토리 덤프가 없다: {p}\n        docstring 의 JS 를 use_figma 로 돌려 저장하라.",
              file=sys.stderr)
        return 3
    live = json.loads(p.read_text(encoding="utf-8"))
    frames, states, icons, kids = figma_names(live)

    have_site = site_present()
    results, skipped, matrix = [], [], None
    if have_site:
        routes = site_routes()
        results.append(_check("사이트 라우트", routes, ROUTES, frames))
        # 상태는 보통 상태 보드의 **자식**이지만, 단일 상태 라우트는 상태가 곧 최상위
        # 프레임이다(문서 15쪽이 그렇다). 둘 다 봐야 한쪽이 거짓 실패로 잡히지 않는다.
        matrix = check_states(routes, ROUTE_STATES, kids | frames)
    else:
        skipped.append("사이트 라우트 · 상태 매트릭스 — Vinc-homepage 가 체크아웃돼 있지 않다")
    results.append(_check("앱 모달", app_dialogs(), DIALOGS, frames | states))   # 오버레이는 이제 보드 하나씩이다
    results.append(_check("아이콘" + ("" if have_site else " (앱만 · 사이트 미체크아웃)"),
                          code_icons(), {i: "Icon / " + i for i in code_icons()},
                          {"Icon / " + i for i in icons}, frozenset(ICON_GAPS)))

    fail = 0
    for r in results:
        print(f"── {r['kind']}")
        for k in r["undeclared"]:
            print(f"  [FAIL·미선언] {k} — 코드에 있는데 Figma 대응을 선언하지 않았다")
            fail += 1
        for k, v in r["rotten"]:
            print(f"  [FAIL·맵 부패] {k} → '{v}' 가 Figma 에 없다(개명·삭제됐다)")
            fail += 1
        for k in r["stale_map"]:
            print(f"  [FAIL·맵 잔여] {k} — 맵에 있는데 코드에서 사라졌다")
            fail += 1
        for k in r["gaps"]:
            print(f"  [GAP] {k} — Figma 에 아직 없다(선언된 결손)")
        if r["figma_only"]:
            print(f"  [INFO] Figma 에만 {len(r['figma_only'])}건: {', '.join(r['figma_only'][:8])}"
                  + (" …" if len(r["figma_only"]) > 8 else ""))
        print(f"  결손 {len(r['gaps'])}건 · 실패 "
              f"{len(r['undeclared']) + len(r['rotten']) + len(r['stale_map'])}건")

    # 앱 표면도 같은 축으로 본다. 사이트와 달리 리포가 늘 있으므로 건너뛸 일이 없다.
    app = check_states(app_surfaces(), APP_STATES, frames | states)
    print("── 앱 표면 상태 (표면×상태)")
    for k, v in app["rotten"]:
        print(f"  [FAIL·맵 부패] {k} → '{v}' 가 Figma 에 없다")
        fail += 1
    for k in app["gaps"]:
        print(f"  [GAP] {k} — Figma 에 아직 없다(선언된 결손)")
    total_gap_app = len(app["gaps"])
    if app["undeclared_routes"]:
        print(f"  [MATRIX] 상태 미선언 {len(app['undeclared_routes'])}건: "
              + ", ".join(app["undeclared_routes"][:6])
              + (" …" if len(app["undeclared_routes"]) > 6 else ""))
    print(f"  선언된 상태 {app['declared']}건 · 실패 {len(app['rotten'])}건")

    # 컴포넌트는 세는 축이 아니라 **대응하는** 축이다. 위 축들이 전부 초록인 채로
    # 이름만 코드와 어긋난 적이 있어서(2026-09-06) 따로 둔다.
    comp = check_components(live, APP_COMPONENTS, app_source())
    print("── 앱 컴포넌트 (변형×코드 표식)")
    for k in comp["undeclared"]:
        print(f"  [FAIL·미선언] {k} — Figma 에 있는데 코드 표식을 선언하지 않았다")
        fail += 1
    for k in comp["stale"]:
        print(f"  [FAIL·맵 잔여] {k} — 맵에 있는데 Figma 에서 사라졌다")
        fail += 1
    for k, v in comp["rotten"]:
        print(f"  [FAIL·표식 부패] {k} → 코드에 '{v}' 가 없다(개명·삭제됐다)")
        fail += 1
    if comp["plain"]:
        print(f"  [INFO] 코드에 고유 표식이 없는 기본값 {len(comp['plain'])}건: "
              + ", ".join(comp["plain"]))
    print(f"  대조된 변형 {comp['declared']}건 · 실패 "
          f"{len(comp['undeclared']) + len(comp['stale']) + len(comp['rotten'])}건")

    sb = check_storyboards(live, captured_boards())
    print("── 스토리보드 (여정 단계×보드)")
    if sb["missing_page"]:
        print(f"  [FAIL·덤프 결손] 덤프에 '{STORYBOARD_PAGE}' 가 없다. docstring 의 두 번째 JS 로 떠서 넣어라")
        fail += 1
    for k, v in sb["rotten"]:
        print(f"  [FAIL·스토리보드 부패] {k} → '{v}' 보드가 03 · App · 04 · Site 에 없다")
        fail += 1
    for k, v in sb["retired"]:
        print(f"  [FAIL·스토리보드 부패] {k} → '{v}' 의 캡처 상태가 매니페스트에서 빠졌다(화면이 퇴역했다)")
        fail += 1
    for k in sb["malformed"]:
        print(f"  [FAIL·이름 규칙] {k} — 단계 이름은 '<순번> · <보드 이름>' 이어야 한다")
        fail += 1
    print(f"  여정 {sb['journeys']}줄 · 단계 {sb['steps']}건 · 실패 "
          f"{int(sb['missing_page']) + len(sb['rotten']) + len(sb['retired']) + len(sb['malformed'])}건")

    total_gap = sum(len(r["gaps"]) for r in results) + total_gap_app
    if matrix is not None:
        print("── 화면 상태 (화면×상태)")
        for k, v in matrix["rotten"]:
            print(f"  [FAIL·맵 부패] {k} → '{v}' 가 Figma 에 없다")
            fail += 1
        for k in matrix["gaps"]:
            print(f"  [GAP] {k} — Figma 에 아직 없다(선언된 결손)")
        total_gap += len(matrix["gaps"])
        if matrix["undeclared_routes"]:
            print(f"  [MATRIX] 상태 미선언 {len(matrix['undeclared_routes'])}건: "
                  + ", ".join(matrix["undeclared_routes"][:6])
                  + (" …" if len(matrix["undeclared_routes"]) > 6 else ""))
        print(f"  선언된 상태 {matrix['declared']}건 · 실패 {len(matrix['rotten'])}건")

    for s in skipped:
        print(f"── [SKIP] {s}")
    tail = f" · 검사 못 함 {len(skipped)}건(리포 부재)" if skipped else ""
    matrix_tail = (f" · 상태 미선언 라우트 {len(matrix['undeclared_routes'])}건"
                   if matrix and matrix["undeclared_routes"] else "")
    print(f"\n{'통과' if not fail else f'실패 {fail}건'} · 선언된 결손 {total_gap}건{matrix_tail}{tail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
