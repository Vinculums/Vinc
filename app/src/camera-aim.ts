/** 카메라를 노드에 겨누는 수식 한 벌 — **import 0.**
 *
 *  `spotlight.ts` 와 같은 이유로 여기 따로 있다: 이 모듈이 아무것도 안 물어야
 *  `app/dev/aim-check.ts` 가 브라우저도 three.js 도 없이 `node
 *  --experimental-strip-types` 로 돌려 볼 수 있다. `view3d.ts` 안에 두면 그 파일이
 *  렌더러를 통째로 끌고 오므로 검사할 방법이 사라진다.
 *
 *  두 가지 일을 한다. 클릭은 **거리를 지키고**(고르는 행위가 카메라를 끌고 들어가면
 *  안 된다), 줌아웃은 **거리를 되돌린다**. 처음엔 줌아웃이 클릭과 같은 호출을
 *  재사용했는데, 그 함수가 "지금 거리"를 지키는 물건이라 줌인이 줄여 놓은 거리를
 *  그대로 지켜 **아무 일도 일어나지 않았다**(실측 2026-08-27). 그래서 `radius` 가 있다.
 */

export type Vec3 = { x: number; y: number; z: number };
type Aim = { pos: Vec3; tgt: Vec3 };

const EPS = 1e-6;

/** 원점에서의 거리. 카메라가 없거나 원점에 있으면 `null`. */
export function camRadius(v: Vec3 | null | undefined): number | null {
  if (!v) return null;
  const r = Math.hypot(v.x || 0, v.y || 0, v.z || 0);
  return r > EPS ? r : null;
}

/** 첫 마운트 카메라의 원점 거리 ÷ S.
 *
 *  `view3d` 의 첫 마운트는 카메라를 `(1.1S, 0.9S, 2.1S)` 에 둔다. 그 자리의 원점
 *  거리가 곧 "밖에서 전체를 보는" 기본 거리이고, 줌아웃이 돌아가야 할 곳이다.
 *  상수로 두는 이유: 값을 베껴 두면 한쪽만 바뀐다. */
const REST_K = Math.hypot(1.1, 0.9, 2.1);

/** 씬 크기 S 에 대한 기본 카메라 거리. */
export function restRadius(S: number): number {
  return S * REST_K;
}

/** 노드를 화면 중심에 놓는 카메라 자리. 옮길 이유가 없으면 `null`.
 *
 *  · 입체(flat=false): 카메라를 `R·normalize(node)` 로 **돌리고** 계속 원점을 본다.
 *    그러면 노드가 카메라와 원점을 잇는 시축 위에 놓여 화면 중심에 투영된다. 평행이동이
 *    아닌 이유는 레이아웃이 원점 둘레의 공이어서다 — 밀고 들어가면 카메라가 그 공
 *    안쪽에 갇힌다. 실측으로 물린 적이 있다: 옛 `flyTo3d` 의 거리 공식이 `42 + degree`
 *    였을 때 연결 33개짜리 허브에서 오히려 **가까워져** 카메라가 클러스터 안으로
 *    파고들었고, 화면이 거대 구체와 엣지 벽이 되어 공간 감각이 사라졌다. 그 함수는
 *    이제 없지만(줌이 거리를 맡는다) 교훈은 이 수식이 지킨다.
 *  · 평면(flat=true): 공이 없다. 눈높이(z)를 지키고 옆으로만 간다. 여기서 원점 둘레로
 *    돌리면 시축이 평면을 벗어나 화면이 기울어 버린다.
 *
 *  `radius` 를 주면 그 거리로 간다(줌아웃). 안 주면 지금 카메라의 거리를 지킨다(클릭).
 *  원점에 앉은 노드(뿌리)는 방향이 없으므로 입체에서 `null` 이다 — 이미 중심이다. */
export function aimAt(node: Vec3, cam: Vec3 | null | undefined, flat: boolean,
                      radius?: number): Aim | null {
  const { x, y, z } = node;
  if (flat) {
    const eye = radius
      ?? (cam && Number.isFinite(cam.z) ? cam.z : Math.max(70, Math.hypot(x, y) * 2.2));
    return { pos: { x, y, z: eye }, tgt: { x, y, z: 0 } };
  }
  const len = Math.hypot(x, y, z);
  if (len < EPS) return null;
  const R = radius ?? camRadius(cam) ?? len * 2.2;
  const k = R / len;
  return { pos: { x: x * k, y: y * k, z: z * k }, tgt: { x: 0, y: 0, z: 0 } };
}

/** 줌 한 걸음 — **방향은 그대로, 거리만** 바꾼다.
 *
 *  이것이 없어서 줌인·줌아웃이 노드 둘레를 돌았다(실측 2026-08-27). 줌인은 노드 옆
 *  고정 오프셋으로 날아갔고, 줌아웃은 노드 축으로 카메라를 돌렸다(`aimAt`).
 *  둘 다 거리와 **방향을 함께** 바꾸므로 화면이 회전한다. 고르는 것(회전해서 중심에
 *  놓기)과 들여다보는 것(거리)은 다른 일이고, 버튼 이름이 이미 "줌"이라고 말한다.
 *
 *  시선 대상에서 카메라로 향하는 벡터를 `factor` 배 한다. `min`/`max` 는 씬 밖으로
 *  튀어나가거나 노드 안으로 파고드는 것을 막는 울타리다 — 없으면 몇 번만 눌러도
 *  화면이 아무것도 아닌 것이 된다. 더 갈 곳이 없으면 `null`(호출측이 조용히 멈춘다). */
export function zoomStep(cam: Vec3, tgt: Vec3, factor: number,
                         min: number, max: number): Vec3 | null {
  const d = { x: cam.x - tgt.x, y: cam.y - tgt.y, z: cam.z - tgt.z };
  const len = Math.hypot(d.x, d.y, d.z);
  if (len < EPS) return null;
  const want = Math.min(max, Math.max(min, len * factor));
  if (Math.abs(want - len) < 0.5) return null;          // 이미 울타리에 닿았다
  const k = want / len;
  return { x: tgt.x + d.x * k, y: tgt.y + d.y * k, z: tgt.z + d.z * k };
}
