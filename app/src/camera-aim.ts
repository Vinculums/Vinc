/** 카메라를 노드에 겨누는 수식 한 벌 — **import 0.**
 *
 *  `spotlight.ts` 와 같은 이유로 여기 따로 있다: 이 모듈이 아무것도 안 물어야
 *  `app/dev/aim-check.ts` 가 브라우저도 three.js 도 없이 `node
 *  --experimental-strip-types` 로 돌려 볼 수 있다. `view3d.ts` 안에 두면 그 파일이
 *  렌더러를 통째로 끌고 오므로 검사할 방법이 사라진다.
 *
 *  약속 하나: **중심에 놓되 거리는 건드리지 않는다.** 클릭이 부르던 `flyTo3d` 는 한
 *  손짓에 둘을 했다(중심 이동 + 거리 축소). 고르는 것과 파고드는 것은 다른 일이라
 *  뒤엣것을 카드 버튼으로 내렸고, 남은 절반이 이 수식이다.
 */

export type Vec3 = { x: number; y: number; z: number };
export type Aim = { pos: Vec3; tgt: Vec3 };

const EPS = 1e-6;

/** 원점에서의 거리. 카메라가 없으면 `null` — 호출측이 "할 게 없다"로 읽는다. */
export function radius(v: Vec3 | null | undefined): number | null {
  if (!v) return null;
  const r = Math.hypot(v.x || 0, v.y || 0, v.z || 0);
  return r > EPS ? r : null;
}

/** 노드를 화면 중심에 놓는 카메라 자리. 옮길 이유가 없으면 `null`.
 *
 *  · 입체(flat=false): 카메라를 `R·normalize(node)` 로 **돌리고** 계속 원점을 본다.
 *    그러면 노드가 카메라와 원점을 잇는 시축 위에 놓여 화면 중심에 투영된다. 평행이동이
 *    아닌 이유는 레이아웃이 원점 둘레의 공이어서다 — 밀고 들어가면 카메라가 그 공
 *    안쪽에 갇히고, 그 고장은 `view3d.ts` 의 `flyTo3d` V4 주석에 실측으로 남아 있다.
 *  · 평면(flat=true): 공이 없다. 눈높이(z)를 그대로 들고 옆으로만 간다. 여기서 원점
 *    둘레로 돌리면 시축이 평면을 벗어나 화면이 기울어 버린다.
 *
 *  원점에 앉은 노드(뿌리)는 방향이 없으므로 입체에서 `null` 이다 — 이미 중심이다. */
export function aimAt(node: Vec3, cam: Vec3 | null | undefined, flat: boolean): Aim | null {
  const { x, y, z } = node;
  if (flat) {
    const eye = cam && Number.isFinite(cam.z) ? cam.z : Math.max(70, Math.hypot(x, y) * 2.2);
    return { pos: { x, y, z: eye }, tgt: { x, y, z: 0 } };
  }
  const len = Math.hypot(x, y, z);
  if (len < EPS) return null;
  const R = radius(cam) ?? len * 2.2;
  const k = R / len;
  return { pos: { x: x * k, y: y * k, z: z * k }, tgt: { x: 0, y: 0, z: 0 } };
}
