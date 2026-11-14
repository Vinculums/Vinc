/** 앱이 부르는 HTTP 경로가 사이드카 계약(`api/contract/sidecar.json`)과 맞는가.
 *
 *     node --experimental-strip-types app/dev/sidecar-contract-check.ts
 *
 *  두 방향을 본다:
 *    1. 앱의 호출이 전부 계약에 있고, 그 연산이 `x-internal: true` 가 아니다.
 *    2. 계약이 `x-internal: false` 로 적은 연산을 앱이 실제로 부른다. 안 부르면 그 연산은
 *       내부용이다 — 표지가 낡지 않게 여기서 막는다.
 *  서버 쪽 두 방향(라우트 표 ↔ 계약)은 `tests/core/test_sidecar_contract.py` 가 본다.
 *
 *  호출은 소스를 실행하지 않고 TypeScript 파서로 읽는다(`typescript` 는 devDependency):
 *    - `app/src` 의 `call`·`localCall`·`accountCall`·`previewDelete`·`applyDelete`·
 *      `viaProxy`·`webFetch`·`fetch` 호출에서 경로 인자(문자열·템플릿·`new URL("…")`)와 메서드.
 *    - `app/src-tauri/src` 의 Rust 셸이 손으로 쓰는 요청 줄(`"POST /shutdown HTTP/1.1`).
 *  경로나 메서드를 글자로 읽을 수 없는 호출은 실패다. 예외는 `api.ts` 의 그 함수들 자신이
 *  인자로 받은 경로를 넘기는 자리뿐이다(그 경로는 함수를 부른 쪽에서 이미 읽혔다).
 *
 *  ⚠ CI 의 `app` 잡은 `app/` 이나 `api/contract/` 가 바뀔 때 돈다(`ci.yml` 의 `changes`).
 *  계약만 고친 PR 에서도 이 검사가 돌아야 앱이 부르는 경로를 지운 것이 드러난다. */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const APP = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONTRACT = join(APP, "..", "api", "contract", "sidecar.json");
const API_TS = join(APP, "src", "api.ts");

type Method = "GET" | "POST";
type Call = { method: Method; path: string; at: string };
type Op = { method: Method; path: string; internal: boolean; re: RegExp };

// 경로 인자 위치와 메서드를 정하는 법. `body2` 는 세 번째 인자(본문)가 있으면 POST 다. `init1` 은 두 번째 인자의
// `method`(없으면 GET)로, `fetch` 와 같이 읽는다.
const WRAPPERS: Record<string, { path: number; method: "body2" | "init1" | Method | number }> = {
  call: { path: 0, method: "body2" },
  localCall: { path: 0, method: "body2" },
  accountCall: { path: 0, method: "body2" },
  previewDelete: { path: 0, method: "POST" },
  applyDelete: { path: 0, method: "POST" },
  viaProxy: { path: 2, method: 1 },
  webFetch: { path: 0, method: "init1" },
};

function files(dir: string, ext: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p, ext));
    else if (name.endsWith(ext) && !name.endsWith(".d.ts")) out.push(p);
  }
  return out;
}

const rel = (p: string): string => relative(join(APP, ".."), p).split(sep).join("/");

/** 문자열·템플릿·`new URL("…", base)` 에서 경로를 읽는다. `${…}` 는 `{}` 로, 쿼리는 뗀다. */
function literalPath(node: ts.Expression | undefined): string | null {
  if (!node) return null;
  let s: string | null = null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) s = node.text;
  else if (ts.isTemplateExpression(node))
    s = node.head.text + node.templateSpans.map((sp) => "{}" + sp.literal.text).join("");
  else if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)
           && node.expression.text === "URL") return literalPath(node.arguments?.[0]);
  if (s === null || !s.startsWith("/")) return null;
  return s.split("?")[0]!;
}

/** `fetch` 의 두 번째 인자에서 메서드. 없으면 GET, 글자로 못 읽으면 null. */
function fetchMethod(init: ts.Expression | undefined): Method | null {
  if (!init || !ts.isObjectLiteralExpression(init)) return "GET";
  for (const prop of init.properties) {
    if (ts.isPropertyAssignment(prop) && prop.name.getText() === "method") {
      return ts.isStringLiteral(prop.initializer)
        ? (prop.initializer.text.toUpperCase() as Method) : null;
    }
  }
  return "GET";
}

/** 이 노드를 감싼 이름 있는 함수 선언. 화살표 함수는 건너뛴다. */
function enclosingFunction(node: ts.Node): string | null {
  for (let n: ts.Node | undefined = node.parent; n; n = n.parent)
    if (ts.isFunctionDeclaration(n) && n.name) return n.name.text;
  return null;
}

function callee(expr: ts.LeftHandSideExpression): string | null {
  if (ts.isIdentifier(expr)) return expr.text;
  // `window.fetch(…)` 같은 꼴도 fetch 로 본다. 그 밖의 속성 호출(`x.call(…)`)은 아니다.
  if (ts.isPropertyAccessExpression(expr) && expr.name.text === "fetch") return "fetch";
  return null;
}

const calls: Call[] = [];
const unreadable: string[] = [];
let forwarded = 0;

for (const file of files(join(APP, "src"), ".ts")) {
  const src = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const name = callee(node.expression);
      if (name && (name === "fetch" || Object.hasOwn(WRAPPERS, name))) {
        const { line } = src.getLineAndCharacterOfPosition(node.getStart(src));
        const at = `${rel(file)}:${line + 1}`;
        const args = node.arguments;
        const spec = WRAPPERS[name];
        const path = literalPath(name === "fetch" ? args[0] : args[spec!.path]);
        let method: Method | null;
        if (name === "fetch" || spec!.method === "init1") method = fetchMethod(args[1]);
        else if (spec!.method === "body2")
          method = args.length > 2 && !(ts.isIdentifier(args[2]!) && args[2]!.text === "undefined")
            ? "POST" : "GET";
        else if (typeof spec!.method === "number") {
          const m = args[spec!.method];
          method = m && ts.isStringLiteral(m) ? (m.text.toUpperCase() as Method) : null;
        } else method = spec!.method;

        if (path && method) calls.push({ method, path, at });
        else if (file === API_TS && Object.hasOwn(WRAPPERS, enclosingFunction(node) ?? "")) forwarded++;
        else unreadable.push(`${at} ${node.getText(src).slice(0, 80)}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(src);
}

// Rust 셸은 요청 줄을 손으로 쓴다(`main.rs` 의 `request_shutdown`).
const REQUEST_LINE = /"(GET|POST|PUT|PATCH|DELETE) (\/[^\s"?]*)[^\s"]* HTTP\/1\.[01]/g;
for (const file of files(join(APP, "src-tauri", "src"), ".rs")) {
  const text = readFileSync(file, "utf8");
  for (const m of text.matchAll(REQUEST_LINE)) {
    const line = text.slice(0, m.index).split("\n").length;
    calls.push({ method: m[1] as Method, path: m[2]!, at: `${rel(file)}:${line}` });
  }
}

// ── 계약 ─────────────────────────────────────────────────────────────────────
const contract = JSON.parse(readFileSync(CONTRACT, "utf8"));
const ops: Op[] = [];
for (const [path, item] of Object.entries<any>(contract.paths)) {
  for (const m of ["get", "post"] as const) {
    if (!item[m]) continue;
    const re = new RegExp("^" + path.replace(/[.*+?^$()|[\]\\]/g, "\\$&")
      .replace(/\{[^}/]+\}/g, "[^/]+") + "$");
    ops.push({ method: m.toUpperCase() as Method, path, internal: item[m]["x-internal"] === true, re });
  }
}

/** 호출 하나가 닿는 연산. 구체 경로(`/ingest/fragment`)가 템플릿(`/ingest/{engine}`)보다 먼저다. */
function opFor(c: Call): Op | undefined {
  const same = ops.filter((o) => o.method === c.method);
  return same.find((o) => o.path === c.path)
    ?? same.find((o) => o.path.includes("{") && o.re.test(c.path));
}

const outside: string[] = [];
const toInternal: string[] = [];
const used = new Set<Op>();
for (const c of calls) {
  const op = opFor(c);
  if (!op) outside.push(`${c.method} ${c.path}  (${c.at})`);
  else if (op.internal) toInternal.push(`${c.method} ${op.path}  (${c.at})`);
  else used.add(op);
}
const uncalled = ops.filter((o) => !o.internal && !used.has(o)).map((o) => `${o.method} ${o.path}`);

const show = (xs: string[]) => xs.map((x) => "\n    " + x).join("");
assert.deepEqual(unreadable, [], `경로나 메서드를 글자로 읽을 수 없는 호출(문자열·템플릿으로 `
  + `쓰거나 api.ts 의 래퍼를 거칠 것):${show(unreadable)}`);
assert.deepEqual(outside, [], `계약에 없는 호출(api/contract/sidecar.json 에 적을 것):${show(outside)}`);
assert.deepEqual(toInternal, [], `x-internal: true 로 적힌 연산을 앱이 부른다(false 로 고칠 것):`
  + show(toInternal));
assert.deepEqual(uncalled, [], `x-internal: false 인데 앱이 부르지 않는 연산(true 로 고칠 것):`
  + show(uncalled));

const distinct = new Set(calls.map((c) => `${c.method} ${c.path}`)).size;
console.log(`  ok 호출 ${calls.length}곳(서로 다른 ${distinct}개, 래퍼 안의 전달 ${forwarded}곳)이 `
  + `전부 계약 안이고 내부용이 아니다`);
console.log(`  ok 계약의 앱 연산 ${used.size}개를 전부 앱이 부른다 `
  + `(내부용 ${ops.filter((o) => o.internal).length}개, 연산 ${ops.length}개)`);
