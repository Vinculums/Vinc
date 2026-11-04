# -*- coding: utf-8 -*-
"""캡처한 프레임을 보드로 세운다: 찾기 · 이름 · 자리 · 노드 id 되적기.

`capture_app_figma.mjs`·`capture_site_figma.mjs` 는 프레임을 제출만 한다. 2026-11-04 부터 Figma 캡처
서비스는 (1) 씨앗에 준 노드 안이 아니라 **그 페이지 최상위**에 프레임을 놓고 (2) 제출 응답의 claimUrl 에
**node-id 를 싣지 않는다**. 그래서 결과 파일의 `nodeId` 가 비고, 보드 이름·섹션 배치·노드 id 를 손으로
맞춰야 했다. 이 스크립트가 그 뒷단계를 한 벌로 한다. 응답에 node-id 가 다시 오면 그 id 를 그대로 쓴다.

  python scripts/design/figma_capture_place.py plan  <job.result.json> --surface app|site \
         --page <pageId> --section "<섹션 이름>"  > place.js
      → use_figma 에 그대로 넣을 스크립트(한 페이지, 한 호출). 돌려받은 JSON 을 파일로 둔다.
  python scripts/design/figma_capture_place.py merge <job.result.json> <placement.json>
      → 결과 파일에 nodeId · board · placed 를 되적는다. 못 찾은 상태는 이름과 함께 실패로 끝난다.

찾는 규칙: 프레임 이름은 캡처 때의 문서 제목 = 상태 id 다(두 캡처 스크립트가 `document.title = id`).
페이지 최상위 FRAME 중 이름이 상태 id 와 **정확히** 같은 것만 본다. 같은 이름이 여럿이면(재시도) 가장
나중 것(노드 id 의 숫자가 큰 것)을 쓰고 나머지는 `duplicates` 로 돌려준다 — 지우지 않는다(보관 규칙,
[[vinc.canon.design-system-operations]] §2). 이미 보드 이름으로 바뀐 것은 다시 찾지 않는다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("figma_inventory", _HERE / "figma_inventory.py")
_fi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fi)


def board_name(surface: str, state_id: str) -> str:
    """보드 이름의 정본은 인벤토리 게이트다 — 이름을 여기서 따로 짓지 않는다(둘이 갈리면 게이트가 부패로 센다)."""
    if surface == "app":
        return _fi.app_board_name(state_id)
    if surface == "site":
        return _fi.site_board_name(state_id)
    raise ValueError(f"unknown surface {surface!r}")


def pending(results: dict) -> list[str]:
    """세울 상태: 캡처에 성공했고 아직 세우지 않은 것. 실패한 상태는 보드가 없으니 건너뛴다."""
    return sorted(k for k, r in results.items() if r.get("ok") and not r.get("placed"))


def plan(results: dict, surface: str, page_id: str, section: str) -> dict:
    """use_figma 스크립트에 실을 입력. 응답에 node-id 가 왔던 상태는 그 id 로, 아니면 이름으로 찾는다."""
    items = []
    for sid in pending(results):
        r = results[sid]
        items.append({"state": sid, "board": board_name(surface, sid), "nodeId": r.get("nodeId") or None})
    return {"page": page_id, "section": section, "items": items}


# use_figma 안에서 도는 쪽. 플러그인 런타임 규칙: 페이지 전환은 한 번, 반환값이 유일한 출력, 실패하면 통째로
# 되감긴다(그래서 다시 돌려도 안전하다 — 이미 이름이 바뀐 프레임은 찾기에서 빠진다).
_JS = r"""
const P = __PLAN__;
const page = await figma.getNodeByIdAsync(P.page);
if (!page || page.type !== "PAGE") throw new Error("not a page: " + P.page);
await figma.setCurrentPageAsync(page);
const num = (id) => id.split(":").map(Number);
const newer = (a, b) => { const x = num(a.id), y = num(b.id); return x[0] - y[0] || x[1] - y[1]; };
let sec = page.children.find((n) => n.type === "SECTION" && n.name === P.section) || null;
const created = [];
const GAP = 80, ROW = 6;
let y = 0, rowH = 0, col = 0, moved = 0;
// 섹션은 옮길 프레임이 하나라도 있을 때 처음 만든다. 찾을 것이 없으면 파일에 아무것도 남기지 않는다
// (이름이 틀린 섹션을 주면 빈 섹션이 생기던 것을 막는다, 2026-11-04 실측).
const ensureSection = () => {
  if (sec) return sec;
  sec = figma.createSection(); sec.name = P.section; page.appendChild(sec);
  let maxY = 0; for (const n of page.children) if (n !== sec) maxY = Math.max(maxY, n.y + n.height);
  sec.x = 0; sec.y = maxY + 400; created.push(sec.id);
  return sec;
};
if (sec) for (const n of sec.children) y = Math.max(y, n.y + n.height + GAP);
const placed = {}, missing = [], duplicates = {};
for (const it of P.items) {
  let node = it.nodeId ? await figma.getNodeByIdAsync(it.nodeId) : null;
  if (!node) {
    const same = page.children.filter((n) => n.type === "FRAME" && n.name === it.state).sort(newer);
    if (same.length > 1) duplicates[it.state] = same.slice(0, -1).map((n) => n.id);
    node = same[same.length - 1] || null;
  }
  if (!node) { missing.push(it.state); continue; }
  node.name = it.board;
  if (!sec || node.parent !== sec) {
    ensureSection().appendChild(node); moved++;
    node.x = 80 + col * (node.width + GAP); node.y = 80 + y; rowH = Math.max(rowH, node.height);
    if (++col === ROW) { col = 0; y += rowH + GAP; rowH = 0; }
  }
  placed[it.state] = { nodeId: node.id, board: it.board };
}
// 옮긴 것이 없으면 기존 섹션의 크기도 건드리지 않는다(다시 돌려도 파일이 그대로다).
if (moved) {
  let w = 0, h = 0; for (const n of sec.children) { w = Math.max(w, n.x + n.width); h = Math.max(h, n.y + n.height); }
  sec.resizeWithoutConstraints(Math.max(sec.width, w + 80), Math.max(sec.height, h + 80));
}
return { section: sec ? sec.id : null, createdNodeIds: created, mutatedNodeIds: Object.values(placed).map((p) => p.nodeId), placed, missing, duplicates };
"""


def script(p: dict) -> str:
    return _JS.replace("__PLAN__", json.dumps(p, ensure_ascii=False))


def merge(results: dict, placement: dict) -> tuple[dict, list[str]]:
    """use_figma 가 돌려준 배치를 결과에 되적는다. 반환: (새 결과, 세우지 못한 상태)."""
    out = {k: dict(v) for k, v in results.items()}
    for sid, p in (placement.get("placed") or {}).items():
        if sid in out:
            out[sid].update(nodeId=p["nodeId"], board=p["board"], placed=True)
    still = [s for s in pending(out)]
    return out, still


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan")
    pp.add_argument("result"); pp.add_argument("--surface", required=True, choices=["app", "site"])
    pp.add_argument("--page", required=True); pp.add_argument("--section", required=True)
    pm = sub.add_parser("merge")
    pm.add_argument("result"); pm.add_argument("placement")
    a = ap.parse_args(argv)
    results = json.loads(pathlib.Path(a.result).read_text(encoding="utf-8"))
    if a.cmd == "plan":
        p = plan(results, a.surface, a.page, a.section)
        if not p["items"]:
            print("nothing to place", file=sys.stderr)
            return 0
        sys.stdout.write(script(p))
        return 0
    placement = json.loads(pathlib.Path(a.placement).read_text(encoding="utf-8"))
    new, still = merge(results, placement)
    pathlib.Path(a.result).write_text(json.dumps(new, indent=1, ensure_ascii=False), encoding="utf-8")
    dup = placement.get("duplicates") or {}
    if dup:
        print(f"{len(dup)} states had older frames of the same name left on the page (not deleted): "
              + ", ".join(sorted(dup)), file=sys.stderr)
    if still:
        print("not placed: " + ", ".join(still), file=sys.stderr)
        return 1
    print(f"placed {sum(1 for r in new.values() if r.get('placed'))} boards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
