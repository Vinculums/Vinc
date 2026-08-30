/** View 주소 문법 한 벌 — 읽기와 쓰기를 같은 자리에 둔다.
 *
 *  선택이 주소가 되고, 그 주소를 붙여넣으면 어시스턴트가 같은 곳을 본다.
 *  근거: [[decision:a-selection-becomes-an-address-you-paste]]
 *
 *  ⚠ **거울이 둘이다** — 여기가 만들고 `vinc_mcp/viewurl.py` 가 읽는다. 이 리포는
 *  같은 모양으로 여러 번 물렸고(역할표·op 표·MCP 계약) 처방은 하나다: 거울을 두되
 *  테스트가 대조한다. 접점은 파이썬 쪽 `GRAMMAR` 이고, 여기서 새 형태를 만들면
 *  거기부터 늘려야 `tests/test_view_url.py` 가 그것을 받는다.
 *
 *  문법(꼬리에서 머리 순으로 읽으면 왜 이 모양인지 보인다):
 *
 *      #view                        전체 지도
 *      #view/<a,b>                  주제 렌즈(쉼표)
 *      #view/trace/<id>             노드 하나
 *      #view/ids/<a,b,c>            임의 선택 — 명시 목록
 *      #view/doc/<id>               문서
 *      …/d<n>                       깊이. **꼬리다**
 *      …/as/<viewpoint>             관점. 늘 맨 뒤
 *
 *  급소 둘. 둘 다 코드를 읽고 안 것이지 짐작이 아니다:
 *
 *  · `as` 는 **탐색으로** 찾는다(`indexOf`). 그래서 깊이를 꼬리에 붙여도 기존 해석이
 *    안 깨지고, 구 클라이언트는 모르는 꼬리를 그냥 무시한다. 깊이가 꼬리인 이유다.
 *  · 첫 세그먼트가 예약어가 **아니면 전부 주제 목록으로 읽힌다.** 그래서 새 키워드는
 *    반드시 `KEYWORDS` 에 등록해야 한다 — 빠뜨리면 "ids 라는 이름의 주제"가 되어
 *    빈 화면이 뜨고 **오류조차 안 난다**.
 */

/** 첫 세그먼트가 이것들이면 주제 목록이 아니라 지시어다. 새 형태를 더할 때 여기부터. */
export const KEYWORDS = ["trace", "ids", "doc", "as"] as const;

export type ViewSelection =
  | { kind: "all" }
  | { kind: "domains"; domains: string[] }
  | { kind: "node"; id: string }
  | { kind: "ids"; ids: string[] }
  | { kind: "doc"; id: string };

/** 깊이는 `d` 를 붙여 적는다 — 숫자만 두면 주제 이름과 구분이 안 된다. */
const DEPTH_RE = /^d([0-9]{1,2})$/;

const enc = encodeURIComponent;

/** 선택 + 옵션 → 해시. **파이썬 GRAMMAR 에 있는 형태만 만든다.** */
export function formatViewUrl(
  sel: ViewSelection,
  opts: { depth?: number; viewpoint?: string } = {},
): string {
  let base = "#view";
  if (sel.kind === "domains" && sel.domains.length)
    base += "/" + sel.domains.map(enc).join(",");
  else if (sel.kind === "node") base += `/trace/${enc(sel.id)}`;
  else if (sel.kind === "ids" && sel.ids.length)
    base += "/ids/" + sel.ids.map(enc).join(",");
  else if (sel.kind === "doc") base += `/doc/${enc(sel.id)}`;
  // 깊이는 관점보다 **앞**이다. 관점이 늘 맨 뒤라는 규칙이 있어서, 그 뒤에 붙이면
  // 파서가 관점을 떼어 낼 때 함께 잘려 나간다.
  if (opts.depth && opts.depth > 0) base += `/d${opts.depth}`;
  if (opts.viewpoint && opts.viewpoint !== "3d") base += `/as/${enc(opts.viewpoint)}`;
  return base;
}

/** 해시(또는 전체 URL) → 선택 + 옵션. 못 읽으면 `kind: "all"` 로 되돌린다. */
export function parseViewUrl(
  url: string,
): { sel: ViewSelection; depth: number; viewpoint: string } {
  const none = { sel: { kind: "all" } as ViewSelection, depth: 0, viewpoint: "" };
  if (!url || url.indexOf("#") < 0) return none;
  const parts = url.slice(url.indexOf("#")).split("/").map(decodeURIComponent);
  if (parts[0] !== "#view") return none;
  let body = parts.slice(1);
  let viewpoint = "";
  const asAt = body.indexOf("as");        // 위치가 아니라 탐색 — 꼬리가 늘어도 안 깨진다
  if (asAt >= 0) {
    viewpoint = body[asAt + 1] || "";
    body = body.slice(0, asAt);
  }
  let depth = 0;
  const tail = body[body.length - 1];
  const m = tail ? DEPTH_RE.exec(tail) : null;
  if (m) { depth = Number(m[1]); body = body.slice(0, -1); }
  if (!body.length) return { sel: { kind: "all" }, depth, viewpoint };
  const head = body[0]!;
  if (head === "trace")
    return body[1]
      ? { sel: { kind: "node", id: body[1] }, depth, viewpoint }
      : { ...none, depth, viewpoint };
  if (head === "ids") {
    const ids = (body[1] || "").split(",").filter(Boolean);
    return ids.length
      ? { sel: { kind: "ids", ids }, depth, viewpoint }
      : { ...none, depth, viewpoint };
  }
  if (head === "doc")
    return body[1]
      ? { sel: { kind: "doc", id: body[1] }, depth, viewpoint }
      : { ...none, depth, viewpoint };
  // 예약어가 아니면 주제다. 슬래시가 든 주제는 여러 조각으로 쪼개져 있으므로 다시 잇는다
  // (`vinc/engineering` → `#view/vinc/engineering`).
  const domains = body.join("/").split(",").filter(Boolean);
  return domains.length
    ? { sel: { kind: "domains", domains }, depth, viewpoint }
    : { ...none, depth, viewpoint };
}

/** 복사해서 건넬 절대 주소. 해시만 주면 받는 쪽이 어느 설치인지 모른다. */
export function absoluteViewUrl(
  sel: ViewSelection, opts: { depth?: number; viewpoint?: string } = {},
): string {
  return location.origin + location.pathname + formatViewUrl(sel, opts);
}
