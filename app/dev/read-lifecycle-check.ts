// The actual Reader module runs with deferred transport and deterministic DOM/frame scheduling.
// Run: node --experimental-strip-types --disable-warning=ExperimentalWarning dev/read-lifecycle-check.ts
// Existing app CI runs every dev/*-check.ts. Optional source path reproduces the pre-fix failure.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import ts from "typescript";

const source = readFileSync(process.argv[2] || new URL("../src/surfaces/read.ts", import.meta.url), "utf8");
const routes = readFileSync(new URL("../src/view-url.ts", import.meta.url), "utf8");
function fixture() {
  const requests = [];
  const frames = new Map();
  const events = [];
  let frameId = 0;
  const cfg = { owner: "owner:a", base: "http://fixture-a", team: "", token: "fixture-token" };
  const location = { hash: "#read/trace/concept%3Aa" };
  const api = Object.fromEntries(["nodeContext", "docGet"].map((method) =>
    [method, (...args) => new Promise((resolve, reject) => requests.push({ method, args, resolve, reject }))]));
  const sandbox = createContext({
    location, console,
    requestAnimationFrame: (fn) => { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame: (id) => frames.delete(id),
  });
  const compile = (src, dependencies) => {
    const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
    const module = { exports: {} };
    runInContext("(function(require,module,exports){" + js + "\n})", sandbox)(
      (name) => { assert.ok(name in dependencies, name); return dependencies[name]; }, module, module.exports);
    return module.exports;
  };
  const url = compile(routes, {});
  const read = compile(source, { "../api": { api, cfg }, "../view-url": url });
  const listeners = new Set();
  let html = "", top = 0;
  const el = {
    isConnected: true,
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; top = 0; events.push(() => listeners.forEach((f) => f())); },
    get scrollTop() { return top; },
    set scrollTop(value) { top = value; events.push(() => listeners.forEach((f) => f())); },
    addEventListener(type, fn) { assert.equal(type, "scroll"); listeners.add(fn); },
    removeEventListener(type, fn) { assert.equal(type, "scroll"); listeners.delete(fn); },
  };
  const eventsNow = () => { for (const fn of events.splice(0)) fn(); };
  const frame = () => {
    eventsNow();
    const batch = [...frames.values()]; frames.clear();
    for (const fn of batch) fn();
    eventsNow();
  };
  const settle = () => { frame(); frame(); frame(); };
  return { ...read, requests, cfg, location, el, frame, settle, eventsNow, listeners, frames };
}
const node = (title) => ({ executed: true, title, excerpts: [] });
const doc = (title) => ({ executed: true, verified: true, title, text: "Body " + title });
let count = 0;
async function check(name, fn) { await fn(); console.log("PASS " + name); count++; }

for (const method of ["node", "doc"]) {
  await check(method + " late response cannot overwrite Search", async () => {
    const f = fixture();
    if (method === "doc") f.location.hash = "#read/doc/doc%3Aa";
    const pending = f.renderRead(f.el);
    f.location.hash = "#search"; f.disposeRead?.(); f.el.innerHTML = "SEARCH";
    f.requests[0].resolve(method === "doc" ? doc("OLD") : node("OLD"));
    await pending; f.settle();
    assert.equal(f.el.innerHTML, "SEARCH"); assert.equal(f.listeners.size, 0);
  });
}
await check("route changes invalidate results before router disposal runs", async () => {
  const f = fixture(), p = f.renderRead(f.el);
  f.location.hash = "#search"; f.el.innerHTML = "SEARCH";
  f.requests[0].resolve(node("OLD")); await p;
  assert.equal(f.el.innerHTML, "SEARCH");
});
await check("A B A route generations cannot resurrect first A", async () => {
  const f = fixture(), a1 = f.renderRead(f.el);
  f.location.hash = "#read/trace/concept%3Ab"; const b = f.renderRead(f.el);
  f.location.hash = "#read/trace/concept%3Aa"; const a2 = f.renderRead(f.el);
  f.requests[2].resolve(node("NEW A")); await a2;
  f.requests[0].resolve(node("OLD A")); f.requests[1].resolve(node("OLD B")); await Promise.all([a1, b]);
  assert.match(f.el.innerHTML, /NEW A/); assert.doesNotMatch(f.el.innerHTML, /OLD/);
});
for (const key of ["owner", "base", "team", "token"]) {
  await check(key + " changes reject a same-route old response", async () => {
    const f = fixture(), p = f.renderRead(f.el);
    f.cfg[key] += "-changed"; f.el.innerHTML = "NEW CONTEXT";
    f.requests[0].resolve(node("OLD")); await p;
    assert.equal(f.el.innerHTML, "NEW CONTEXT");
    assert.equal(f.requests[0].args[0], "owner:a");
  });
}
await check("context A B A with new requests preserves latest generation", async () => {
  const f = fixture(), a1 = f.renderRead(f.el);
  f.cfg.owner = "owner:b"; const b = f.renderRead(f.el);
  f.cfg.owner = "owner:a"; const a2 = f.renderRead(f.el);
  f.requests[2].resolve(node("NEW")); await a2;
  f.requests[0].resolve(node("OLD")); f.requests[1].resolve(node("FOREIGN")); await Promise.all([a1, b]);
  assert.match(f.el.innerHTML, /NEW/); assert.doesNotMatch(f.el.innerHTML, /OLD|FOREIGN/);
});
await check("detached container rejects result", async () => {
  const f = fixture(), p = f.renderRead(f.el);
  f.el.isConnected = false; f.requests[0].resolve(node("OLD")); await p;
  assert.doesNotMatch(f.el.innerHTML, /OLD/);
});
for (const method of ["node", "doc"]) {
  await check(method + " request rejection ends loading safely", async () => {
    const f = fixture(); if (method === "doc") f.location.hash = "#read/doc/doc%3Aa";
    const p = f.renderRead(f.el); f.requests[0].reject(new Error("private transport detail")); await p;
    assert.match(f.el.innerHTML, /Not available/); assert.doesNotMatch(f.el.innerHTML, /private|Loading/);
  });
}
await check("obsolete rejection does not overwrite new content", async () => {
  const f = fixture(), old = f.renderRead(f.el), latest = f.renderRead(f.el);
  f.requests[1].resolve(node("LATEST")); await latest;
  f.requests[0].reject(new Error("old")); await old;
  assert.match(f.el.innerHTML, /LATEST/);
});
for (const got of [{...doc("SECRET"), verified:false}, {...doc("SECRET"), verified:undefined},
  {...doc("SECRET"), text:undefined}, {...doc("SECRET"), executed:false}]) {
  await check("unavailable or unverified document never renders its body/title", async () => {
    const f = fixture(); f.location.hash = "#read/doc/doc%3Aa";
    const p = f.renderRead(f.el); f.requests[0].resolve(got); await p;
    assert.match(f.el.innerHTML, /Not available/); assert.doesNotMatch(f.el.innerHTML, /SECRET/);
  });
}
await check("verified empty document is valid and body markup is escaped", async () => {
  const f = fixture(); f.location.hash = "#read/doc/doc%3Aa";
  let p = f.renderRead(f.el); f.requests[0].resolve({...doc("EMPTY"), text:""}); await p;
  assert.match(f.el.innerHTML, /<pre><\/pre>/);
  p = f.renderRead(f.el); f.requests[1].resolve({...doc("<unsafe>"), text:"<script>bad()</script>"}); await p;
  assert.match(f.el.innerHTML, /&lt;script&gt;/); assert.doesNotMatch(f.el.innerHTML, /<script>/);
});
await check("scroll survives View round trip and loading reset", async () => {
  const f = fixture(), p = f.renderRead(f.el);
  f.requests[0].resolve(node("A")); await p; f.settle();
  f.el.scrollTop = 900; f.eventsNow();
  f.location.hash = "#view"; f.disposeRead(); f.el.innerHTML = "VIEW"; f.el.scrollTop = 200; f.eventsNow();
  f.location.hash = "#read/trace/concept%3Aa"; const again = f.renderRead(f.el);
  f.eventsNow(); f.requests[1].resolve(node("A")); await again; f.settle();
  assert.equal(f.el.scrollTop, 900); assert.equal(f.listeners.size, 1);
});
for (const key of ["owner", "base", "team"]) {
  await check("scroll stays bound to " + key, async () => {
    const f = fixture(), p = f.renderRead(f.el);
    f.requests[0].resolve(node("A")); await p; f.settle(); f.el.scrollTop = 700; f.eventsNow();
    f.disposeRead(); const original = f.cfg[key]; f.cfg[key] += "-b";
    let next = f.renderRead(f.el); f.requests[1].resolve(node("B")); await next; f.settle();
    assert.equal(f.el.scrollTop, 0); f.el.scrollTop = 300; f.eventsNow(); f.disposeRead();
    f.cfg[key] = original; next = f.renderRead(f.el); f.requests[2].resolve(node("A")); await next; f.settle();
    assert.equal(f.el.scrollTop, 700);
  });
}
await check("obsolete restore frame cannot scroll a replacement surface", async () => {
  const f = fixture(), p = f.renderRead(f.el); f.requests[0].resolve(node("A")); await p;
  f.location.hash = "#search"; f.disposeRead(); f.el.innerHTML = "SEARCH"; f.el.scrollTop = 123;
  f.settle(); assert.equal(f.el.scrollTop, 123); assert.equal(f.frames.size, 0);
});
await check("router disposes Reader before clearing shared surface", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const route = main.slice(main.indexOf("async function route()"));
  assert.ok(route.indexOf("disposeRead();") < route.indexOf('surfaceEl.innerHTML = "";'));
});

for (const kind of ["leave", "owner", "base", "team", "token", "new request"]) {
  await check("explicit source late response is ignored after " + kind, async () => {
    const f = fixture();
    f.location.hash = "#read/trace/concept%3Aa/src/doc%3Asecond";
    const pending = f.renderRead(f.el);
    f.requests[0].resolve(node("NODE A")); await new Promise(setImmediate);
    assert.equal(f.requests[1].method, "docGet"); assert.equal(f.requests[1].args[1], "doc:second");
    if (kind === "leave") { f.location.hash = "#search"; f.disposeRead(); }
    else if (kind === "new request") {
      const latest = f.renderRead(f.el);
      f.requests[2].resolve(node("NEW NODE")); await new Promise(setImmediate);
      f.requests[3].resolve(doc("NEW SOURCE")); await latest;
    } else f.cfg[kind] += "-changed";
    const expected = kind === "new request" ? f.el.innerHTML : (f.el.innerHTML = "NEW CONTEXT");
    f.requests[1].resolve(doc("OLD SOURCE")); await pending;
    assert.equal(f.el.innerHTML, expected);
  });
}
for (const mode of ["reject", "unverified", "no body", "unavailable"]) {
  await check("explicit source " + mode + " preserves authorized Node and hides source", async () => {
    const f = fixture(); f.location.hash = "#read/trace/concept%3Aa/src/doc%3Asecond";
    const pending = f.renderRead(f.el);
    f.requests[0].resolve(node("AUTHORIZED NODE")); await new Promise(setImmediate);
    if (mode === "reject") f.requests[1].reject(new Error("PRIVATE"));
    else f.requests[1].resolve({...doc("PRIVATE"),
      ...(mode === "unverified" ? {verified:false} : mode === "no body" ? {text:undefined} : {executed:false})});
    await pending;
    assert.match(f.el.innerHTML, /AUTHORIZED NODE/);
    assert.match(f.el.innerHTML, /That source is not available/);
    assert.doesNotMatch(f.el.innerHTML, /PRIVATE/);
  });
}
await check("exact source link and scroll survive View round trip", async () => {
  const f = fixture(); const route = "#read/trace/concept%3Aa/src/doc%3Asecond";
  f.location.hash = route; let pending = f.renderRead(f.el);
  f.requests[0].resolve(node("NODE")); await new Promise(setImmediate);
  f.requests[1].resolve(doc("SECOND")); await pending; f.settle();
  assert.match(f.el.innerHTML, /#view\/trace\/concept%3Aa\/src\/doc%3Asecond/);
  assert.match(f.el.innerHTML, /Body SECOND/);
  f.el.scrollTop = 1200; f.eventsNow();
  f.location.hash = "#view/trace/concept%3Aa/src/doc%3Asecond"; f.disposeRead(); f.el.innerHTML = "VIEW"; f.eventsNow();
  f.location.hash = route; pending = f.renderRead(f.el); f.eventsNow();
  f.requests[2].resolve(node("NODE")); await new Promise(setImmediate); f.eventsNow();
  f.requests[3].resolve(doc("SECOND")); await pending; f.settle();
  assert.equal(f.el.scrollTop, 1200);
});
console.log(count + " total Reader lifecycle checks passed");
