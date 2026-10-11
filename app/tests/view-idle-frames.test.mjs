import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const result = await build({ entryPoints: [fileURLToPath(new URL("../src/surfaces/view-idle-frames.ts", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false });
const { ViewIdleFrames } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
const setup = () => {
  const state = { now: 0, continuous: false, unpainted: false, calls: [], queued: [], frames: [], tasks: [] };
  const idle = new ViewIdleFrames({ now: () => state.now, defer: fn => state.queued.push(fn),
    deferResume: fn => state.frames.push(fn),
    deferSettle: fn => state.tasks.push(fn),
    pause: () => state.calls.push("pause"), resume: () => state.calls.push("resume"),
    continuous: () => state.continuous, hasUnpaintedChanges: () => state.unpainted });
  const flush = () => {
    while (state.queued.length) state.queued.shift()();
    while (state.tasks.length) state.tasks.shift()();
    while (state.frames.length) state.frames.shift()();
  };
  return { state, idle, flush };
};

test("an expired long render cancels only after the next frame was registered", () => {
  const { state, idle, flush } = setup(); state.now = 3000;
  idle.afterRender(); assert.deepEqual(state.calls, []);
  state.calls.push("register frame"); flush();
  assert.deepEqual(state.calls, ["register frame", "pause"]);
  idle.check(); idle.afterRender(); flush(); assert.equal(state.calls.length, 2);
});

test("camera changes made after rendering must be painted before pausing", () => {
  const { state, idle, flush } = setup(); state.now = 3000;
  idle.afterRender(); state.unpainted = true; flush(); idle.check();
  assert.deepEqual(state.calls, []);
  state.unpainted = false; idle.afterRender(); flush();
  assert.deepEqual(state.calls, ["pause"]);
});

test("camera commands start before synchronous resume renders", () => {
  const { state, idle, flush } = setup(); state.now = 3000; idle.check();
  idle.wake(520); state.calls.push("start camera tween");
  state.now = 4000; idle.check();
  assert.deepEqual(state.calls, ["pause", "start camera tween"]);
  flush(); assert.deepEqual(state.calls, ["pause", "start camera tween", "resume"]);
});

test("a newer wake protects a pending post-render pause", () => {
  const { state, idle, flush } = setup(); state.now = 3000;
  idle.afterRender(); idle.wake(600); flush(); assert.deepEqual(state.calls, []);
  state.now = 3600; idle.check(); assert.deepEqual(state.calls, []);
  state.now = 3601; idle.afterRender(); flush(); assert.deepEqual(state.calls, ["pause"]);
});

test("particle animation stays awake past the deadline and can return to idle", () => {
  const { state, idle, flush } = setup(); state.continuous = true; state.now = 10000;
  idle.afterRender(); idle.check(); flush(); assert.deepEqual(state.calls, []);
  state.continuous = false; idle.afterRender(); flush(); assert.deepEqual(state.calls, ["pause"]);
});

test("destroying a scene cancels both pending nap and pending resume", () => {
  for (const resume of [false, true]) {
    const { state, idle, flush } = setup(); state.now = 3000;
    if (resume) { idle.check(); idle.wake(); } else idle.afterRender();
    const count = state.calls.length; idle.dispose(); flush(); idle.check(); idle.wake();
    assert.equal(state.calls.length, count);
  }
});

test("repeated wakes schedule only one resume and retain the longest budget", () => {
  const { state, idle, flush } = setup(); state.now = 3000; idle.check();
  idle.wake(700); idle.wake(300); assert.equal(state.frames.length, 1); flush();
  state.now = 3500; idle.check(); assert.deepEqual(state.calls, ["pause", "resume"]);
  state.now = 3701; idle.afterRender(); flush(); assert.deepEqual(state.calls, ["pause", "resume", "pause"]);
});

test("a long frame before setter digest must be followed by a settled paint", () => {
  const { state, idle, flush } = setup(); state.now = 3000; idle.wake(600);
  state.now = 4000; idle.afterRender();
  while (state.queued.length) state.queued.shift()();
  assert.deepEqual(state.calls, []); // 첫 프레임 뒤에 digest가 남아 있다.
  flush(); idle.check(); assert.deepEqual(state.calls, []);
  idle.afterRender(); flush(); assert.deepEqual(state.calls, ["pause"]);
});

test("a later wake cannot be settled by an older digest task", () => {
  const { state, idle, flush } = setup(); state.now = 3000; idle.wake(); idle.wake();
  state.tasks.shift()(); state.now = 4000; idle.afterRender();
  while (state.queued.length) state.queued.shift()();
  assert.deepEqual(state.calls, []);
  flush(); idle.afterRender(); flush(); assert.deepEqual(state.calls, ["pause"]);
});
