# -*- coding: utf-8 -*-
"""figma_inventory.py — 코드의 화면·모달·아이콘 목록과 Figma 의 그것을 대조한다.

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
- **INFO · Figma 에만**: 맵이 참조하지 않는 Figma 프레임. 시안이거나 폐기 대기라 판정이
  필요한 것들이므로 세어서 보여만 준다.

## 쓰는 법

    python -X utf8 scripts/figma_inventory.py            # 보고서, 위 FAIL 이면 exit 1

라이브 덤프는 `imports/figma-inventory.json` 이고 에이전트가 `use_figma` 로 뜬다:

```js
const want = ["Homepage", "Homepage(Docs)", "App(Desktop)", "Design System(Homepage)", "Design System(App)"];
const pages = {};
for (const p of figma.root.children) {            // ⚠ 페이지 전환은 호출당 한 번뿐이라
  if (!want.includes(p.name)) continue;           //    이 루프는 페이지별로 나눠 돌린다
}
// 페이지 하나당 한 번:
const page = await figma.getNodeByIdAsync(PAGE_ID);
await figma.setCurrentPageAsync(page);
const frames = page.children.filter(n => n.type !== "COMPONENT" && n.type !== "COMPONENT_SET")
  .map(n => ({ name: n.name, children: (n.children || []).map(c => c.name) }));
const comps = page.findAllWithCriteria({ types: ["COMPONENT", "COMPONENT_SET"] })
  .filter(n => !(n.parent && n.parent.type === "COMPONENT_SET")).map(n => n.name);
return { name: page.name, frames, components: comps };
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
VINC = HUB.parent
SITE = VINC.parent / "Vinc-homepage" / "site"
APP = VINC / "app" / "src"

# ── 사이트 라우트 ↔ Figma 프레임 ────────────────────────────────────────────
# 한 프레임이 여러 라우트를 담기도 한다(로그인과 환영이 한 장에 있다). 값이 None 이면
# "코드에는 있고 Figma 에는 아직 없다"고 **선언한** 것이다.
ROUTES = {
    "/": "Home · Landing / dark",
    "/404": "Home · 404 / dark (신설)",
    "/account/": "Home · Account / dark",
    "/account/delete/": "Home · Account · Delete states / dark",
    "/account/team/": "Home · Account · Team / dark",
    "/auth/verify/": "Home · Auth · Verify states / dark",
    "/community/": "Home · Community / dark",
    "/connect/": "Home · Connect & Connect-app states / dark",
    "/connect-app/": "Home · Connect & Connect-app states / dark",
    "/download/": "Home · Download / dark",
    "/login/": "Home · Auth · Login & Welcome / dark",
    "/welcome/": "Home · Auth · Login & Welcome / dark",
    "/pricing/": "Home · Pricing / dark",
    "/privacy/": "Home · Privacy / dark",
    "/release/": "Home · Release / dark",
    "/subscribe/confirm/": "Home · Auth · Subscribe & Unsubscribe states / dark",
    "/unsubscribe/": "Home · Auth · Subscribe & Unsubscribe states / dark",
    "/docs/": "Docs · Overview / dark",
    "/docs/quickstart/": "Docs · Quickstart / dark",
    "/docs/use-cases/": "Docs · What to put in it / dark",
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
ICON_GAPS = {
    "mail",   # 사이트 모듈에는 있는데 이 세션에서 렌더로 못 받았다. 다음 회차에 추가.
    "mcp",    # app/src/icons/mcp.svg. 180 좌표계라 24 로 재척도해야 하고, MCP 공식 마크다.
}


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
        matrix = check_states(routes, ROUTE_STATES, kids)
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

    total_gap = sum(len(r["gaps"]) for r in results)
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
