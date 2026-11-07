/** 속성(props) 편집 로직 자체 검사 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/props-edit-check.ts
 *
 *  `props-edit.ts` 가 import 0 인 것이 성립 조건이다. */
import assert from "node:assert/strict";
import {
  kindOf, rowsFrom, propsFrom, convertText, sameProps, propsEditable, propsPatchOf,
} from "../src/props-edit.ts";

// 형식은 값의 모양에서 고른다. 글만 담은 배열이 목록이고, 섞이면 JSON 이다.
assert.equal(kindOf("x"), "text");
assert.equal(kindOf(["a", "b"]), "list");
assert.equal(kindOf([]), "list");
assert.equal(kindOf([1, "b"]), "json");
assert.equal(kindOf(3), "number");
assert.equal(kindOf(false), "bool");
assert.equal(kindOf({ a: 1 }), "json");
assert.equal(kindOf(null), "json");

// 에이전트 역할 계약: 순서 목록이 먼저, 나머지는 저장된 순서. 편집 없이 되돌리면 같은 값이다.
const role = {
  isolation: "worktree", gates: ["scripts/gate_contrast.py", "scripts/gate_forbidden.py"],
  job_title: "Frontend Engineer", mission: "Package discipline.", rung: 2, writes_graph: true,
  extra: { nested: [1, 2] },
};
const rows = rowsFrom(role, ["mission", "job_title", "gates"]);
assert.deepEqual(rows.map((r) => r.key),
  ["mission", "job_title", "gates", "isolation", "rung", "writes_graph", "extra"]);
assert.equal(rows[2].text, "scripts/gate_contrast.py\nscripts/gate_forbidden.py");
const back = propsFrom(rows);
assert.deepEqual(back.errors, []);
assert.deepEqual(back.props, role);
assert.ok(sameProps(back.props, role));

// 목록은 한 줄에 하나, 앞뒤 공백과 빈 줄은 버린다.
assert.deepEqual(
  propsFrom([{ key: "never", kind: "list", text: " merge \n\nwiden scope\r\n" }]).props,
  { never: ["merge", "widen scope"] });

// 줄을 지우면 키가 사라진다 — 서버가 통째로 교체하므로 결과가 곧 새 전체다.
const without = propsFrom(rows.filter((r) => r.key !== "isolation")).props;
assert.ok(!("isolation" in without));

// 이름·값이 다 빈 줄은 없던 줄이다. 값만 있고 이름이 없으면 오류.
assert.deepEqual(propsFrom([{ key: " ", kind: "text", text: "" }]), { props: {}, errors: [] });
assert.deepEqual(propsFrom([{ key: "", kind: "text", text: "v" }]).errors,
  [{ index: 0, code: "key-empty" }]);
// 같은 이름 둘 — 뒤의 줄이 오류다(앞의 값은 남는다).
const dup = propsFrom([{ key: "a", kind: "text", text: "1" }, { key: " a", kind: "text", text: "2" }]);
assert.deepEqual(dup.errors, [{ index: 1, code: "key-dup" }]);
assert.deepEqual(dup.props, { a: "1" });
// 형식 검사.
assert.deepEqual(propsFrom([{ key: "n", kind: "number", text: "12x" }]).errors, [{ index: 0, code: "number" }]);
assert.deepEqual(propsFrom([{ key: "n", kind: "number", text: " " }]).errors, [{ index: 0, code: "number" }]);
assert.deepEqual(propsFrom([{ key: "j", kind: "json", text: "{bad" }]).errors, [{ index: 0, code: "json" }]);
assert.deepEqual(propsFrom([{ key: "b", kind: "bool", text: "yes" }]).errors, [{ index: 0, code: "bool" }]);
// 글은 글자 그대로다 — "42" 를 숫자로 바꾸지 않는다.
assert.deepEqual(propsFrom([{ key: "t", kind: "text", text: "42" }]).props, { t: "42" });

// 형식 전환: 글 ↔ 목록은 줄 그대로, JSON 으로는 값으로, JSON 에서는 값을 풀어서.
assert.equal(convertText("a\nb", "list", "text"), "a\nb");
assert.equal(convertText("a\nb", "list", "json"), JSON.stringify(["a", "b"], null, 2));
assert.equal(convertText('["a","b"]', "json", "list"), "a\nb");
assert.equal(convertText("7", "text", "number"), "7");
assert.equal(convertText("true", "text", "bool"), "true");
assert.equal(convertText("nope", "text", "bool"), "false");
assert.equal(convertText("{bad", "json", "list"), "{bad");   // 못 풀면 글자를 지킨다

// 키 순서는 같음 판정에 안 든다. 값이 다르면 다르다.
assert.ok(sameProps({ a: 1, b: [1, { x: 1, y: 2 }] }, { b: [1, { y: 2, x: 1 }], a: 1 }));
assert.ok(!sameProps({ a: ["x"] }, { a: ["x", "y"] }));

// 0.9.8 props_patch: 편집 전후의 차이를 merge patch 로. 서버의 합치기(`api/mutation_contract.py`
// merge_patch)를 여기 옮겨 돌려서 "patch 를 합친 값 = 편집 결과"를 확인한다.
const mergePatch = (cur: unknown, patch: unknown): unknown => {
  if (typeof patch !== "object" || patch === null || Array.isArray(patch)) return structuredClone(patch);
  const out: Record<string, unknown> = typeof cur === "object" && cur !== null && !Array.isArray(cur)
    ? structuredClone(cur as Record<string, unknown>) : {};
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete out[k]; else out[k] = mergePatch(out[k], v);
  }
  return out;
};
const edited: Record<string, unknown> = { ...role, mission: "New mission.",
  gates: ["scripts/gate_forbidden.py"], extra: { nested: [1, 2], more: true }, added: "x" };
delete edited.rung;
const patch = propsPatchOf(role, edited)!;
assert.deepEqual(patch, { rung: null, mission: "New mission.", gates: ["scripts/gate_forbidden.py"],
                          extra: { more: true }, added: "x" });
assert.deepEqual(mergePatch(role, patch), edited);
// 안의 키를 지우면 null 로 싣는다(객체는 합쳐지므로 빼기만 하면 지운 키가 남는다).
const shrunk = { ...role, extra: {} };
assert.deepEqual(propsPatchOf(role, shrunk), { extra: { nested: null } });
assert.deepEqual(mergePatch(role, propsPatchOf(role, shrunk)), shrunk);
// 바뀐 것이 없으면 빈 patch 다.
assert.deepEqual(propsPatchOf(role, structuredClone(role)), {});
// 값으로서의 null 은 patch 로 못 싣는다: 호출부가 전체를 보내도록 null 을 돌려준다.
assert.equal(propsPatchOf(role, { ...role, extra: { nested: null } }), null);
assert.equal(propsPatchOf(role, { ...role, added: null }), null);
// 배열 안의 null 은 배열이 통째로 교체되므로 실을 수 있다.
assert.deepEqual(propsPatchOf({ a: [1] }, { a: [null] }), { a: [null] });

// props 를 싣는 종만 편집 단추를 낸다.
assert.ok(propsEditable("concept") && propsEditable("decision") && propsEditable("record"));
assert.ok(!propsEditable("episode") && !propsEditable("entity") && !propsEditable(undefined));

console.log("props-edit-check: ok");
