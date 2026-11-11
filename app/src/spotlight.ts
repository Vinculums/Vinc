/** 관계 열쇠 — 레이아웃 선(`view-layout-wire.ts`)이 관계 하나를 가리키는 문자열. 레이아웃 서비스
 *  (`app/service`)와 앱이 같이 쓰므로 열쇠 모양을 바꾸면 허브에 저장된 답과 짝이 어긋난다. */

type E = { source: string; target: string; type: string };

/** 같은 쌍의 평행 관계가 서로 다른 열쇠를 갖도록 타입까지 넣는다. 허브가 보낸 레이아웃 답과
 *  앱이 합치는 쪽이 이 한 벌로 관계를 짝짓는다(`edgeKeys`). */
export const edgeKey = (e: E): string => `${e.source}\u0000${e.target}\u0000${e.type}`;
