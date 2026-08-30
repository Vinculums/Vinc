/** 카메라 겨눔 자체 검사 — 브라우저도 three.js 도 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/aim-check.ts
 *
 *  cone-check.ts 와 같은 지위다(`app/dev/` 는 tsconfig include 밖이라 여기서만 `.ts`
 *  확장자를 붙여 import 한다). `camera-aim.ts` 가 import 0 인 것이 성립 조건이다.
 *
 *  이 검사가 있는 이유: 이 화면은 픽셀을 못 본 채로 나가는 일이 있었다(v0.8.5 계획
 *  §7 의 "map-side pixels were never seen"). 수식이 맞는지만이라도 손 없이 답한다. */
import assert from "node:assert/strict";
import { aimAt, camRadius, restRadius, zoomStep, type Vec3 } from "../src/camera-aim.ts";

const len = (v: Vec3) => Math.hypot(v.x, v.y, v.z);
const dot = (a: Vec3, b: Vec3) => a.x * b.x + a.y * b.y + a.z * b.z;
const cross = (a: Vec3, b: Vec3): Vec3 => ({
  x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x,
});
const near = (got: number, want: number, tol = 1e-9) =>
  assert.ok(Math.abs(got - want) <= tol * Math.max(1, Math.abs(want)),
            `${got} != ${want}`);

// ── 입체: 거리는 지키고 방향만 바꾼다 ───────────────────────────────────────
{
  const cam = { x: 0, y: 0, z: 500 };            // 정면에서 500 떨어져 있다
  const node = { x: 30, y: -12, z: 4 };
  const aim = aimAt(node, cam, false)!;
  assert.ok(aim, "입체에서 겨눔이 나와야 한다");

  // 1. **줌이 없다** — 원점에서의 거리가 그대로다. 이 한 줄이 이 변경의 전부다.
  near(len(aim.pos), 500);

  // 2. **중심에 온다** — 노드가 카메라와 원점을 잇는 선 위에 있다(평행 ⇒ 외적 0),
  //    그리고 카메라와 같은 쪽이다(내적 > 0). 반대편이면 등 뒤가 된다.
  const c = cross(aim.pos, node);
  assert.ok(len(c) < 1e-9, `시축 위에 없다: |cross|=${len(c)}`);
  assert.ok(dot(aim.pos, node) > 0, "노드가 카메라 반대편이다");

  // 3. 원점을 계속 본다 — 전체를 담은 채로 도는 것이 목적이다.
  assert.deepEqual(aim.tgt, { x: 0, y: 0, z: 0 });
}

// ── 입체: 뿌리는 방향이 없다 ────────────────────────────────────────────────
assert.equal(aimAt({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 500 }, false), null,
             "원점 노드는 이미 중심이라 옮길 것이 없다");

// ── 입체: 카메라가 아직 없으면 노드 거리에서 판단한다(0으로 나누지 않는다) ──
{
  const aim = aimAt({ x: 0, y: 0, z: 10 }, null, false)!;
  near(len(aim.pos), 22);                        // 10 * 2.2
}

// ── 평면: 눈높이를 지키고 옆으로만 간다 ─────────────────────────────────────
{
  const cam = { x: 1, y: 2, z: 300 };
  const aim = aimAt({ x: 40, y: -7, z: 0 }, cam, true)!;
  near(aim.pos.z, 300);                          // 줌 없음 = z 그대로
  near(aim.pos.x, 40); near(aim.pos.y, -7);      // 노드 바로 위
  assert.deepEqual(aim.tgt, { x: 40, y: -7, z: 0 });
}

// ── 평면: 뿌리도 유효하다(평면에는 "방향 없음"이 없다) ──────────────────────
assert.ok(aimAt({ x: 0, y: 0, z: 0 }, { x: 0, y: 0, z: 90 }, true),
          "평면에서는 원점 노드도 겨눌 수 있다");

// ── radius: 0 벡터는 null(호출측이 "할 게 없다"로 읽는다) ───────────────────
assert.equal(camRadius({ x: 0, y: 0, z: 0 }), null);
assert.equal(camRadius(null), null);
near(camRadius({ x: 3, y: 4, z: 0 })!, 5);

// ── 줌아웃: 가까이 있던 카메라가 **기본 거리로 물러난다** ──────────────────
//    이것이 없어서 줌아웃이 아무 일도 안 했다(2026-08-27). 줌인이 거리를 줄여 놓은
//    뒤에는 "지금 거리 지키기"가 곧 "그대로 있기"였다.
{
  const node = { x: 30, y: -12, z: 4 };
  const S = 140;                                 // SCALE * spread(1)
  const near3 = { x: 31, y: -12, z: 5 };         // 줌인 직후 — 노드 코앞
  const kept = aimAt(node, near3, false)!;       // 클릭: 지금 거리를 지킨다
  near(len(kept.pos), len(near3));
  const out = aimAt(node, near3, false, restRadius(S))!;   // 줌아웃: 물러난다
  near(len(out.pos), restRadius(S));
  assert.ok(len(out.pos) > len(kept.pos) * 5, "줌아웃이 실제로 멀어져야 한다");
  // 물러나도 여전히 그 노드를 중심에 둔다 — 멀어지기만 하면 자리를 잃는다.
  assert.ok(len(cross(out.pos, node)) < 1e-9, "물러난 뒤에도 시축 위에 있어야 한다");
  assert.ok(dot(out.pos, node) > 0, "노드가 카메라 반대편이면 안 된다");
}

// 기본 거리는 첫 마운트 카메라 (1.1S, 0.9S, 2.1S) 의 원점 거리와 같아야 한다.
near(restRadius(140), Math.hypot(140 * 1.1, 140 * 0.9, 140 * 2.1));

// ── 줌: **방향은 그대로, 거리만** ───────────────────────────────────────────
//    이것이 없어서 줌인·줌아웃이 노드 둘레를 돌았다(2026-08-27). 거리만 바뀌는지는
//    "새 위치가 옛 위치와 같은 반직선 위에 있는가"로 판정한다.
{
  const tgt = { x: 0, y: 0, z: 0 };
  const cam = { x: 100, y: -40, z: 60 };
  const before = len(cam);

  const inn = zoomStep(cam, tgt, 0.55, 10, 5000)!;
  near(len(inn), before * 0.55);
  assert.ok(len(cross(inn, cam)) < 1e-9, "줌인이 방향을 바꾸면 안 된다");
  assert.ok(dot(inn, cam) > 0, "같은 쪽에 있어야 한다(뒤집히면 안 된다)");

  const out = zoomStep(cam, tgt, 1.8, 10, 5000)!;
  near(len(out), before * 1.8);
  assert.ok(len(cross(out, cam)) < 1e-9, "줌아웃도 방향을 바꾸면 안 된다");

  // 울타리: 몇 번을 눌러도 그 밖으로 못 나간다.
  let far = cam;
  for (let i = 0; i < 40; i++) far = zoomStep(far, tgt, 1.8, 10, 500) ?? far;
  near(len(far), 500);
  assert.equal(zoomStep(far, tgt, 1.8, 10, 500), null, "더 갈 곳이 없으면 null");

  // 시선 대상이 원점이 아니어도 그 대상 기준으로 움직인다.
  const t2 = { x: 10, y: 10, z: 10 };
  const c2 = { x: 30, y: 10, z: 10 };
  const z2 = zoomStep(c2, t2, 2, 1, 1000)!;
  assert.deepEqual(z2, { x: 50, y: 10, z: 10 });
}

// 카메라가 시선 대상에 앉아 있으면 방향이 없다 — 0으로 나누지 않는다.
assert.equal(zoomStep({ x: 5, y: 5, z: 5 }, { x: 5, y: 5, z: 5 }, 2, 1, 100), null);

console.log("aim-check: 전부 통과");
