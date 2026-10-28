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
