/** 영역 열쇠 — 레이아웃(3D 칸 묶기)과 색(영역 보석)이 같은 규칙으로 영역을 가르도록 한 자리에 둔다.
 *  둘이 갈리면 같은 영역의 주제가 다른 색을 입거나 부피 밖에 선다. 의존이 없어 레이아웃 서비스와
 *  좌측 트리 어느 쪽 번들에 들어가도 가볍다. */

/** domain 마다 영역 열쇠. 모든 domain 의 공통 접두(경로 단위)를 벗기고 남은 첫 단계다. 허브 지도가
 * 뿌리를 정하는 방식(공통 접두를 벗긴다)과 같다. 접두와 같은 domain(뿌리 주제)은 빈 열쇠다. */
export function areaKeys(domains: string[]): string[] {
  const parts = domains.map(d => d ? d.split("/") : []);
  let common = parts.length ? Math.min(...parts.map(p => p.length)) : 0;
  for (let i = 0; i < common; i++) if (parts.some(p => p[i] !== parts[0][i])) { common = i; break; }
  return parts.map(p => p.slice(common, common + 1).join("/"));
}

/** 영역 부피 상자(타원체) — x 가 시간 축, y·z 가 단면. 반지름은 껍질 여유를 더한 값이다. */
export type AreaShell = { cx: number; cy: number; cz: number; hx: number; rc: number };

/** 영역 부피를 그려도 되는가 — 시간이 겹치는 두 영역의 단면이 서로 떨어져 있을 때만이다.
 *  영역별로 칸을 묶는 배치(시간순 가지)에서는 참이고, 연결 수로 동심원을 짜는 배치(중심에서)나
 *  그 배치를 먼저 그려 둔 동안에는 영역마다 지도 전체를 감싸 한가운데서 겹치므로 거짓이다.
 *  잣대는 두 단면 중심 거리 / 두 반지름 합이다. 영역별로 묶은 배치는 가장 가까운 쌍이 0.85 안팎이고
 *  (4,507 항목 · 8 영역 픽스처), 동심원 배치는 중심이 한 점이라 0 에 가깝다. 그 사이의 `0.5` 로 가른다. */
export function areasApart(shells: AreaShell[]): boolean {
  for (let i = 0; i < shells.length; i++) {
    for (let j = i + 1; j < shells.length; j++) {
      const a = shells[i], b = shells[j];
      if (Math.abs(a.cx - b.cx) >= a.hx + b.hx) continue;          // 시간이 안 겹친다
      if (Math.hypot(a.cy - b.cy, a.cz - b.cz) < 0.5 * (a.rc + b.rc)) return false;
    }
  }
  return true;
}
