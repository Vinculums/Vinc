/** 노드 속성(props) 편집의 순수 로직 — 값 ↔ 편집 줄 변환과 검사.
 *
 *  import 0 이 성립 조건이다: `app/dev/props-edit-check.ts` 가 브라우저 없이
 *  `node --experimental-strip-types` 로 이 파일을 직접 돌린다.
 *
 *  줄마다 **형식**을 갖는다. 저장된 값의 모양에서 고르고(글 · 목록 · 숫자 · 예/아니요 ·
 *  JSON), 사람이 바꿀 수 있다. 형식을 따로 두는 이유: 같은 `42` 가 글일 수도 숫자일
 *  수도 있어서, 글자만 보고 추측하면 저장할 때마다 값의 종류가 바뀐다. 에이전트 역할의
 *  계약(`never` · `reads` · `gates` …)은 대부분 글 목록이라 목록은 한 줄에 하나로 쓴다.
 *
 *  서버는 props 를 **통째로 교체**한다(안 실은 키는 경고 없이 사라진다). 그래서 이
 *  모듈이 돌려주는 것은 언제나 편집 결과 전체이고, 줄을 지운 것이 곧 키를 지운 것이다.
 *  근거: [[decision:node-props-are-edited-in-the-document-pane-as-a-whole-and-checked-against-the-read-revision-2026-09-26]] */

export type PropKind = "text" | "list" | "number" | "bool" | "json";
export const PROP_KINDS: PropKind[] = ["text", "list", "number", "bool", "json"];

export type PropRow = { key: string; kind: PropKind; text: string };

type RowErrorCode = "key-empty" | "key-dup" | "number" | "bool" | "json";
type RowError = { index: number; code: RowErrorCode };

/** 저장된 값의 형식. 글만 담은 배열은 목록이고, 그 밖의 배열·객체·null 은 JSON 이다. */
export function kindOf(v: unknown): PropKind {
  if (typeof v === "string") return "text";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "bool";
  if (Array.isArray(v) && v.every((x) => typeof x === "string")) return "list";
  return "json";
}

/** 값을 그 형식의 편집 글자로. 형식이 값과 안 맞으면(사람이 형식을 바꾼 직후) JSON 으로 쓴다. */
function textOf(v: unknown, kind: PropKind): string {
  if (v === undefined) return "";
  if (kind === "text" && typeof v === "string") return v;
  if (kind === "list" && Array.isArray(v)) return v.map(String).join("\n");
  if ((kind === "number" || kind === "bool") && typeof v !== "object") return String(v);
  return JSON.stringify(v, null, 2);
}

/** props → 편집 줄. `order` 에 없는 키는 저장된 순서대로 뒤에 붙는다. */
export function rowsFrom(props: Record<string, unknown>, order: string[] = []): PropRow[] {
  const keys = [...order.filter((k) => k in props),
                ...Object.keys(props).filter((k) => !order.includes(k))];
  return keys.map((key) => {
    const kind = kindOf(props[key]);
    return { key, kind, text: textOf(props[key], kind) };
  });
}

/** 형식을 바꿀 때 지금 글자를 새 형식으로 옮긴다. 목록 ↔ 글은 줄바꿈 그대로 옮긴다. */
export function convertText(text: string, from: PropKind, to: PropKind): string {
  if (from === to) return text;
  if (to === "json") {
    const parsed = parseRow(text, from);
    return parsed.ok ? JSON.stringify(parsed.value, null, 2) : text;
  }
  if (from === "json") {
    try { return textOf(JSON.parse(text), to); } catch { return text; }
  }
  if (to === "bool") return text.trim() === "true" ? "true" : "false";
  return text;
}

type Parsed = { ok: true; value: unknown } | { ok: false; code: RowErrorCode };

function parseRow(text: string, kind: PropKind): Parsed {
  switch (kind) {
    case "text": return { ok: true, value: text };
    case "list":
      return { ok: true, value: text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean) };
    case "number": {
      const t = text.trim();
      const n = Number(t);
      return t !== "" && Number.isFinite(n) ? { ok: true, value: n } : { ok: false, code: "number" };
    }
    case "bool": {
      const t = text.trim();
      return t === "true" || t === "false" ? { ok: true, value: t === "true" }
        : { ok: false, code: "bool" };
    }
    case "json":
      try { return { ok: true, value: JSON.parse(text) }; }
      catch { return { ok: false, code: "json" }; }
  }
}

/** 편집 줄 → props 전체. 이름도 값도 빈 줄은 없던 줄로 친다(추가만 누르고 둔 줄). */
export function propsFrom(rows: PropRow[]): { props: Record<string, unknown>; errors: RowError[] } {
  const props: Record<string, unknown> = {};
  const errors: RowError[] = [];
  const seen = new Set<string>();
  rows.forEach((r, index) => {
    const key = r.key.trim();
    if (!key) {
      if (r.text.trim() !== "") errors.push({ index, code: "key-empty" });
      return;
    }
    if (seen.has(key)) { errors.push({ index, code: "key-dup" }); return; }
    seen.add(key);
    const p = parseRow(r.text, r.kind);
    if (!p.ok) { errors.push({ index, code: p.code }); return; }
    props[key] = p.value;
  });
  return { props, errors };
}

/** 두 props 가 같은가 — 키 순서는 따지지 않는다(저장 전 "바뀐 것 없음" 판정용). */
export function sameProps(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const canon = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(canon);
    if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      return Object.keys(o).sort().reduce<Record<string, unknown>>((acc, k) => {
        acc[k] = canon(o[k]); return acc;
      }, {});
    }
    return v;
  };
  return JSON.stringify(canon(a)) === JSON.stringify(canon(b));
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
/** 객체 안(배열 밖)에 null 이 있나. merge patch 에서 null 은 "지운다"라서 값으로 못 싣는다. */
const holdsNull = (v: unknown): boolean =>
  v === null || (isObj(v) && Object.values(v).some(holdsNull));

/** 편집 전후 props 의 차이를 JSON Merge Patch(RFC 7386)로 만든다. 서버(0.9.8
 *  `props_patch`)가 저장된 props 에 합친다: null 은 키 삭제, 객체는 안으로 합침, 그 밖의
 *  값(배열 포함)은 통째로 교체. 바꾼 값에 null 이 들어 있으면 patch 로 표현할 수 없어서
 *  null 을 돌려주고, 호출부는 편집 결과 전체(`props`)를 보낸다. */
export function propsPatchOf(before: Record<string, unknown>,
                             after: Record<string, unknown>): Record<string, unknown> | null {
  const patch: Record<string, unknown> = {};
  for (const k of Object.keys(before)) if (!(k in after)) patch[k] = null;
  for (const [k, v] of Object.entries(after)) {
    const b = before[k];
    if (isObj(v) && isObj(b)) {
      const sub = propsPatchOf(b, v);
      if (sub === null) return null;
      if (Object.keys(sub).length) patch[k] = sub;
    } else if (!(k in before) || !sameProps({ v: b }, { v })) {
      if (holdsNull(v)) return null;
      patch[k] = v;
    }
  }
  return patch;
}

/** props 를 싣는 종. 여섯 전용 테이블 가운데 상세 열이 여는 셋이다(Episode 는 node-context
 *  가 props 를 주지 않고, Bridge · Domain 은 상세 열로 열리지 않는다). 그 밖의 종은 Entity
 *  캐리어라 props 가 **조용히 사라진다** — 편집 단추를 아예 내지 않는다. */
export const PROPS_EDITABLE_KINDS = ["concept", "decision", "record"];
export const propsEditable = (kind: string | undefined): boolean =>
  !!kind && PROPS_EDITABLE_KINDS.includes(kind);
