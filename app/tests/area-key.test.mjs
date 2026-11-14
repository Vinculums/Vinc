import test from "node:test";
import assert from "node:assert/strict";
import { loadTs } from "./ts-load.mjs";

const { areaKeys } = await loadTs("area-key.ts");

test("area keys strip the common domain prefix and take the next step", () => {
  assert.deepEqual(areaKeys(["vinc/design/tokens", "vinc/ai", "vinc", "vinc/design"]), ["design", "ai", "", "design"]);
  assert.deepEqual(areaKeys(["vinc/design/a", "vinc/design/b/c"]), ["a", "b"]);
  assert.deepEqual(areaKeys(["", "notes"]), ["", "notes"]);
  assert.deepEqual(areaKeys([]), []);
});
