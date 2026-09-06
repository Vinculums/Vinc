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

## 쓰는 법

    python -X utf8 scripts/figma_inventory.py            # 보고서, 위 FAIL 이면 exit 1

라이브 덤프는 `imports/figma-inventory.json` 이고 에이전트가 `use_figma` 로 뜬다:

```js
const want = ["01 · Foundations", "02 · App Components", "04 · Surfaces", "05 · Overlays", "06 · States",
              "08 · Reference", "10 · Site Components", "11 · Site Screens", "12 · Docs Screens"];   // 2026-09-06 구조
const pages = {};
for (const p of figma.root.children) {            // ⚠ 페이지 전환은 호출당 한 번뿐이라
  if (!want.includes(p.name)) continue;           //    이 루프는 페이지별로 나눠 돌린다
}
// 페이지 하나당 한 번:
const page = await figma.getNodeByIdAsync(PAGE_ID);
await figma.setCurrentPageAsync(page);
const frames = page.children.filter(n => n.type !== "COMPONENT" && n.type !== "COMPONENT_SET")
  .map(n => ({ name: n.name, children: (n.children || []).map(c => c.name) }));
const sets = page.findAllWithCriteria({ types: ["COMPONENT", "COMPONENT_SET"] })
  .filter(n => !(n.parent && n.parent.type === "COMPONENT_SET"));
const variants = {};                              // 02 · App Components 만 쓴다
for (const n of sets)
  if (n.type === "COMPONENT_SET") variants[n.name] = n.children.map(c => c.name);
return { name: page.name, frames, components: sets.map(n => n.name), variants };
```

⚠ 덤프는 손으로 고치지 않는다. 고치면 게이트가 Figma 가 아니라 그 파일과 합의한다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HUB = pathlib.Path(__file__).resolve().parent.parent
#: 앱 컴포넌트 세트가 사는 페이지. 2026-09-06 에 파일을 제품 구조대로 재편하면서
#: `Design System(App)` 이 이 이름이 됐다(vinc.plan.figma-product-structure §2). 화면 보드는
#: 04 · Surfaces / 05 · Overlays / 06 · States 세 페이지에 나뉘어 살지만 프레임 축은 페이지를
#: 가리지 않고 전부 합쳐 세므로 여기 적을 것은 이 하나다.
APP_COMPONENTS_PAGE = "02 · App Components"
VINC = HUB.parent
SITE = VINC.parent / "Vinc-homepage" / "site"
APP = VINC / "app" / "src"

# ── 사이트 라우트 ↔ Figma 프레임 ────────────────────────────────────────────
# 한 프레임이 여러 라우트를 담기도 한다(로그인과 환영이 한 장에 있다). 값이 None 이면
# "코드에는 있고 Figma 에는 아직 없다"고 **선언한** 것이다.
ROUTES = {
    "/": "Site · Landing / dark",
    "/404": "Site · 404 / dark (신설)",
    "/account/": "Site · Account / dark",
    "/account/delete/": "Site · Account · Delete states / dark",
    "/account/team/": "Site · Account · Team / dark",
    "/auth/verify/": "Site · Auth · Verify states / dark",
    "/community/": "Site · Community / dark",
    "/connect/": "Site · Connect & Connect-app states / dark",
    "/connect-app/": "Site · Connect & Connect-app states / dark",
    "/download/": "Site · Download / dark",
    "/login/": "Site · Auth · Login & Welcome / dark",
    "/welcome/": "Site · Auth · Login & Welcome / dark",
    "/pricing/": "Site · Pricing / dark",
    "/privacy/": "Site · Privacy / dark",
    "/release/": "Site · Release / dark",
    "/subscribe/confirm/": "Site · Auth · Subscribe & Unsubscribe states / dark",
    "/unsubscribe/": "Site · Auth · Subscribe & Unsubscribe states / dark",
    "/docs/": "Docs · Overview / dark",
    "/docs/quickstart/": "Docs · Quickstart / dark",
    "/docs/use-cases/": "Docs · What to add / dark",
    "/docs/import/": "Docs · Import / dark",
    "/docs/view/": "Docs · View / dark",
    "/docs/search/": "Docs · Search / dark",
    "/docs/packs/": "Docs · Packs & community / dark",
    "/docs/shortcuts/": "Docs · Shortcuts / dark",
    "/docs/mcp/": "Docs · MCP Setup / dark",
    "/docs/mcp/tools/": "Docs · MCP Tools / dark",
    "/docs/cli/": "Docs · CLI Reference / dark",
    "/docs/graph-md/": "Docs · GRAPH.md What / dark",
    "/docs/graph-md/build/": "Docs · GRAPH.md Generate / dark",
    "/docs/graph-md/spec/": "Docs · GRAPH.md Spec / dark",
    "/docs/graph-md/check/": "Docs · GRAPH.md Validate / dark",
    # 아래 일곱은 2026-09-05 에 결손으로 잡혔다가 같은 날 신설했다. 마지막 둘은 게이트가
    # **처음으로 잡아낸 회귀**다 — 로컬 사이트 체크아웃이 origin 보다 6커밋 뒤처져 있었고,
    # 최신화하자마자 미선언으로 빨개졌다.
    # ⚠ 셸과 제목만 실물이고 본문은 아직 도너(Overview)의 것이다. 프레임이 있다는 것과
    # 그려졌다는 것은 다른 말이라, 프레임 주석에 그 사실을 적어 두었다.
    "/docs/mcp/sign-in/": "Docs · MCP Sign-in / dark",
    "/docs/agents/": "Docs · Agents / dark",
    "/docs/agents/hosts/": "Docs · Agents · Hosts / dark",
    "/docs/agents/openclaw/": "Docs · Agents · OpenClaw / dark",
    "/docs/agents/roles/": "Docs · Agents · Roles / dark",
    "/docs/agents/packs/": "Docs · Agents · Packs / dark",
    "/docs/one-graph/": "Docs · One graph / dark",
}
#: 라우트로 세지 않는 것. 내부 점검용이라 시안이 있을 이유가 없다.
ROUTE_IGNORE = {"/vt-check/", "/vt-check/b"}

#: 상태 매트릭스 — **화면이 아니라 화면×상태가 단위다.**
#:
#: 위의 ROUTES 는 "이 라우트에 시안이 있는가"만 답한다. 그래서 상태가 여섯 개 필요한
#: `/auth/verify/` 와 하나뿐인 `/pricing/` 이 똑같이 초록으로 보인다 — 기본 화면 하나만
#: 있어도 덮였다고 말한다. 이 표가 그 축을 더한다.
#:
#: 값이 프레임 이름이면 실재를 검증하고, `None` 이면 선언된 결손이다. 여기 **없는**
#: 라우트는 "상태를 아직 선언하지 않음"으로 세어서 출력한다 — 거짓으로 하나뿐이라고
#: 주장하지 않기 위해서다. 그 수가 곧 "시안이 몇 장 더 필요한가"다.
ROUTE_STATES = {
    "/login/": {"form": "LOGIN · FORM", "sent": "LOGIN · SENT"},
    "/welcome/": {"loading": "WELCOME · LOADING", "form": "WELCOME · FORM",
                  "available": "WELCOME · AVAILABLE"},
    "/auth/verify/": {"confirm": "VERIFY · CONFIRM", "verifying": "VERIFY · VERIFYING",
                      "confirm-context": "VERIFY · CONFIRM CONTEXT", "expired": "VERIFY · EXPIRED",
                      "invalid": "VERIFY · INVALID", "error": "VERIFY · ERROR"},
    "/account/delete/": {"confirm": "DELETE · CONFIRM", "deleting": "DELETE · DELETING",
                         "deleted": "DELETE · DELETED", "expired": "DELETE · EXPIRED",
                         "error": "DELETE · ERROR"},
    "/subscribe/confirm/": {"confirm": "SUBSCRIBE · CONFIRM", "confirming": "SUBSCRIBE · CONFIRMING",
                            "confirmed": "SUBSCRIBE · CONFIRMED", "expired": "SUBSCRIBE · EXPIRED",
                            "error": "SUBSCRIBE · ERROR"},
    "/unsubscribe/": {"confirm": "UNSUB · CONFIRM", "unsubscribing": "UNSUB · UNSUBSCRIBING",
                      "unsubscribed": "UNSUB · UNSUBSCRIBED", "invalid": "UNSUB · INVALID",
                      "error": "UNSUB · ERROR"},
    "/connect/": {"load": "CONNECT · LOAD", "ask": "CONNECT · ASK", "login": "CONNECT · LOGIN",
                  "done": "CONNECT · DONE", "error": "CONNECT · ERROR"},
    "/connect-app/": {"load": "CONNECT-APP · LOAD", "ask": "CONNECT-APP · ASK",
                      "login": "CONNECT-APP · LOGIN", "done": "CONNECT-APP · DONE",
                      "manual": "CONNECT-APP · MANUAL", "error": "CONNECT-APP · ERROR"},

    # ── 단일 상태 라우트 ────────────────────────────────────────────────────
    # 읽기만 하는 정적 페이지들이다. 상태가 하나라는 것은 **주장이지 결손이 아니라서**
    # 미선언으로 두면 영영 31 이라는 수가 남는다. 실제로 하나뿐이면 하나라고 적는다.
    "/": {"default": "Site · Landing / dark"},
    "/404": {"default": "Site · 404 / dark (신설)"},
    "/pricing/": {"default": "Site · Pricing / dark"},
    "/privacy/": {"default": "Site · Privacy / dark"},
    "/release/": {"default": "Site · Release / dark"},
    "/download/": {"default": "Site · Download / dark"},
    "/docs/": {"default": "Docs · Overview / dark"},
    "/docs/quickstart/": {"default": "Docs · Quickstart / dark"},
    "/docs/use-cases/": {"default": "Docs · What to add / dark"},
    "/docs/import/": {"default": "Docs · Import / dark"},
    "/docs/view/": {"default": "Docs · View / dark"},
    "/docs/search/": {"default": "Docs · Search / dark"},
    "/docs/packs/": {"default": "Docs · Packs & community / dark"},
    "/docs/shortcuts/": {"default": "Docs · Shortcuts / dark"},
    "/docs/mcp/": {"default": "Docs · MCP Setup / dark"},
    "/docs/mcp/tools/": {"default": "Docs · MCP Tools / dark"},
    "/docs/mcp/sign-in/": {"default": "Docs · MCP Sign-in / dark"},
    "/docs/cli/": {"default": "Docs · CLI Reference / dark"},
    "/docs/graph-md/": {"default": "Docs · GRAPH.md What / dark"},
    "/docs/graph-md/build/": {"default": "Docs · GRAPH.md Generate / dark"},
    "/docs/graph-md/spec/": {"default": "Docs · GRAPH.md Spec / dark"},
    "/docs/graph-md/check/": {"default": "Docs · GRAPH.md Validate / dark"},
    "/docs/agents/": {"default": "Docs · Agents / dark"},
    "/docs/agents/hosts/": {"default": "Docs · Agents · Hosts / dark"},
    "/docs/agents/openclaw/": {"default": "Docs · Agents · OpenClaw / dark"},
    "/docs/agents/roles/": {"default": "Docs · Agents · Roles / dark"},
    "/docs/agents/packs/": {"default": "Docs · Agents · Packs / dark"},
    "/docs/one-graph/": {"default": "Docs · One graph / dark"},

    # ── 상태가 더 필요한 라우트 ──────────────────────────────────────────────
    # 코드에 상태 분기가 실재하는데 Figma 에 기본 화면만 있다. 결손으로 **선언**한다.
    "/account/": {"default": "Site · Account / dark",
                  "signed-out": "ACCOUNT · SIGNED OUT",       # Service states 보드에 있다
                  "rate-limited": "429 · RATE LIMITED",
                  "graph-unavailable": "WEB GRAPH UNAVAILABLE",
                  "key-created": "ACCOUNT · KEY CREATED",
                  "username-taken": "ACCOUNT · USERNAME TAKEN"},
    "/account/team/": {"default": "Site · Account · Team / dark",
                       "invited": "TEAM · INVITED", "no-teams": "TEAM · NO TEAMS",
                       "seat-full": "TEAM · SEAT FULL"},
    "/community/": {"default": "Site · Community / dark", "modal-ask": "modal-card",
                    "loading": "COMMUNITY · LOADING", "empty": "COMMUNITY · EMPTY",
                    "no-match": "COMMUNITY · NO MATCH", "error": "COMMUNITY · ERROR"},
}

# ── 앱 모달 ↔ Figma 오버레이 상태 ───────────────────────────────────────────
# 키는 `모듈:제목식`. 줄 번호가 아니라 식이라 코드가 위아래로 움직여도 안 깨진다.
DIALOGS = {
    "ask.ts:c.historyTitle": "ASK HISTORY · CHEATSHEET",
    "conventions.ts:C.title": "GRAPH.MD",
    "doc-editor.ts:opts.docId ? COPY.ui.viewx.readerTitle : COPY.ui.viewx.edNew": "DOC EDITOR · CONFLICT",
    "empty-invite.ts:COPY.ui.sheet.importTitle": "IMPORT A PACK",
    "main.ts:COPY.ui.palette.keyboard": "ASK HISTORY · CHEATSHEET",
    "sheets.ts:COPY.ui.sheet.shareTitle": "SHARE AS A PACK",
    "sheets.ts:COPY.ui.sheet.importTitle": "IMPORT A PACK",
    "tools.ts:T.title": "TOOLS · CREATE",
    "lnb.ts:openContextMenu": "CONTEXT MENUS",
    "lnb.ts:promptText": "TEXT PROMPT",
    "surfaces/view.ts:openContextMenu": "CONTEXT MENUS",
    "surfaces/view.ts:confirmDestructive": "DESTRUCTIVE CONFIRM",
    # 2026-09-05 에 결손으로 잡혔다가 같은 날 `App · Overlays 4` 로 신설했다.
    # ⚠ 제목·부제·닫기는 실물이고 본문은 아직 도너(팩 공유)의 것이다. 보드 주석에 적었다.
    "empty-invite.ts:c.newSheetTitle": "NEW NODE",
    "firstrun.ts:c.title": "FIRST RUN",
    "sheets.ts:S.docTitle": "IMPORT A DOCUMENT",
    "topbar.ts:COPY.ui.topbar.browseAll": "ALL DOCUMENTS",
}
#: 앱 표면 ↔ Figma 화면 상태. 사이트의 ROUTE_STATES 와 같은 축이다 — 단위는 표면이
#: 아니라 **표면×상태**다. 앱 쪽은 상태가 프레임 이름 자체에 들어 있어서(예: `App ·
#: Search · no results …`) 값이 최상위 프레임 이름이거나 상태 보드의 자식 이름이다.
APP_STATES = {
    "surfaces/view.ts": {
        "default": "App · View · default (canvas placeholder) / dark",
        "empty": "App · View · empty / dark",
        "lens": "App · View · lens panel (Options) / dark",
        "coachmark": "App · View · coachmark & lasso / dark",
        "node-detail": "App · View · node detail + related card / dark",
        "edge-detail": "App · View · edge detail / dark",
        "reader": "App · View · reader (verified, saved) / dark",
    },
    "surfaces/search.ts": {
        "results": "App · Search · results / dark",
        "no-results": "App · Search · no results + reindex badge / dark",
        "error": "App · Search · error + browse / dark",
    },
    "ask.ts": {"answer": "App · Ask · answer card / dark"},
    "surfaces/packs.ts": {"default": "App · Packs (with Intake dropzone) / dark"},
    "surfaces/intake.ts": {
        "ingesting": "INGESTING (42:5)", "success": "SUCCESS (42:7)", "error": "ERROR",
    },
    "surfaces/settings.ts": {
        "account": "App · Settings · Account tab / dark",
        "rest": "App · Settings · Connections + Data + Advanced / dark",
    },
    "account-connect.ts": {
        "not-connected": "NOT CONNECTED", "waiting": "WAITING", "connected": "CONNECTED",
        "expired": "EXPIRED", "failed": "FAILED",
    },
    # 그래프 렌더러 넷은 화면이 아니라 **캔버스 안의 그림**이라 Node 페이지에 예시로 산다.
    "surfaces/view3d.ts": {"dark": "Graph · 3D · dark (constellation glow, view3d.ts 재현)",
                           "light": "Graph · 3D · light (matte ink, D77)"},
    "surfaces/view2d.ts": {"default": "Graph · 2D · dark (sigma 팬 레이아웃, view2d.ts 재현)"},
    "surfaces/view-planar.ts": {
        "timeline": "Graph · Timeline · dark (수직 시간축 + 루트/브랜치, view-planar.ts 재현)"},
    # ⚠ 제품이 생키를 버리고 방사형 번들(Weave)로 갔는데 Node 페이지 예시는 아직 생키다.
    # 이름만 바꾸면 없는 화면을 현행처럼 보이게 하므로 SUPERSEDED 로 표시해 두었고,
    # Weave 예시는 **다시 그려야 한다** — 개명이 아니라 작도다.
    "surfaces/view-bundle.ts": {"weave": "Graph · Weave · dark (방사형 계층 번들, view-bundle.ts 재현)"},
    # records 는 흡수된 표면이다([[vinc.spec.surface-records]]). 화면이 없는 것이 맞다.
    "surfaces/records.ts": {},
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
    "App / DocRow":      {"Ink=0": "--ink:0", "Ink=1": None, "Ink=2": "--ink:2"},
    "App / ListRow":     {"State=Default": "list-row", "State=Hover": "list-row:hover"},
    "App / StateMark":   {"State=Filled": '"filled"', "State=Half": '"half"',
                          "State=Empty": '"empty"'},
    "App / Sheet":       {"Size=Action": '|| "action"', "Size=Write": 'data-size="write"',
                          "Size=Browse": 'data-size="browse"'},
    # 네 번째의 코드 키는 `bundle` 이고 Weave 는 화면 라벨이다. 표식은 코드 쪽을 건다.
    "App / ViewSeg":     {"Active=3D": '"3d"', "Active=2D": '"2d"',
                          "Active=Timeline": '"timeline"', "Active=Weave": '"bundle"'},
    "App / MenuItem":    {"Tone=Default": "menu-item", "Tone=Hover": "menu-item:hover",
                          "Tone=Danger": "danger"},
    "App / GraphNode":   {"Tier=Focus": '"focus"', "Tier=Near": '"near"',
                          "Tier=Mid": '"mid"', "Tier=Far": '"far"'},
    "App / PackCard":    {"State=Default": "pack-card"},
    "App / UpdateToast": {"State=Available": "t.available", "State=Downloading": "t.downloading",
                          "State=Installing": "t.installing", "State=Failed": "t.failed"},
    # 변형이 없는 것들. 컴포넌트 자체의 표식 하나만 건다.
    "App / ProgressBar": {"": "proc-bar"},
    "App / ErrorBanner": {"": "error-banner"},
    "App / EmptyState":  {"": "empty-invite"},
    "App / Tooltip":     {"": "tooltip"},
    "App / Slider":      {"": "slider"},
    "App / FloatCard":   {"": "float-card"},
    "App / Titlebar":    {"": "titlebar"},
    "App / Topbar":      {"": "topbar"},
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
ICON_GAPS: set[str] = set()   # 2026-09-05: 전부 채웠다. 새 아이콘이 코드에 생기면 FAIL 이다.


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
    return {r for r in out if r not in ROUTE_IGNORE}


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
    for extra in ("ask.ts", "account-connect.ts"):
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
    comps = set(dsapp.get("components", []))
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
    ap.add_argument("--live", default="imports/figma-inventory.json")
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
    results.append(_check("앱 모달", app_dialogs(), DIALOGS, states))
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
