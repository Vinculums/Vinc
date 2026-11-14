// 그래프 표면(`view.ts`)과 거기서 떼어 낸 묶음들이 주고받는 형식: 노드 한 개와, 표면이 묶음에 내주는 손잡이.
// 형식만 둔다(실행되는 코드가 없다). 묶음을 더 떼어 낼 때는 그 묶음이 쓰는 손잡이만 `ViewHost` 에 더한다.
import type { GraphEdge, GraphNode, NodeContext } from "../api";

/** 카드 · 상세 레일 · 지도 초점이 주고받는 노드 한 개. `toN3` 가 그래프 응답에서 만든다. */
export type N3 = {
  id: string; label: string; domain?: string; degree?: number;
  kind?: string;              // concept | decision | episode | record | bridge | domain
  gateway?: boolean; focus?: boolean;
  ts?: string; historical?: boolean; conflict?: boolean;
  x?: number; y?: number; z?: number;
};

/** `renderView` 가 떼어 낸 묶음에 내주는 것. 묶음은 이것으로만 표면의 상태에 닿는다
 *  (표면의 닫힌 변수를 직접 고치지 않는다). */
export interface ViewHost {
  /** 상세 열(`#view-detail`). 레일이 열고 닫으며 비우고, 다른 묶음은 이 안의 자기 절만 다시 그린다. */
  readonly detail: HTMLElement;
  /** 노드 맥락 메모를 비우고 그 노드의 상세 열을 새로 읽어 그린다. */
  reloadNode(nd: N3): Promise<void>;
  /** 이 마운트가 아직 살아 있는가. 라우터가 표면을 갈아끼우면 거짓이다. 묶음이 건 관찰자는 거짓을 보면 스스로 물러난다. */
  isLive(): boolean;
  /** 잇기 대상 고르기를 푼다. 열이 열리거나 닫히면 고르던 자리가 사라진다. */
  dropPick(): void;
  /** 열이 닫혔다: 뒤로가기용 마지막 노드를 놓고, 주소가 노드를 싣고 있으면 주소도 그 노드를 놓는다. */
  forgetNode(): void;
  /** 열을 닫는다(레일의 `closeRail`). */
  closeRail(): void;
  /** 그 노드의 상세를 레일에 연다. 떠 있는 카드는 손대지 않는다. */
  railFor(nd: N3): void;
  /** 노드로 간다. 지도에 없으면 범위를 넓혀 데려오고, `card` 면 떠 있는 카드를 연다. 착지한 노드를 돌려준다(없으면 null). */
  focusNode(id: string, domain?: string, card?: boolean, keepHistory?: boolean): Promise<N3 | null>;
  /** 관계 하나의 상세(문장 · 근거 · 양 끝 · 지우기)를 레일에 연다. */
  openEdgeDetail(e: { id?: string; source: string; target: string; type: string;
                      srcTitle?: string; dstTitle?: string }): Promise<void>;
  /** 주제(`domain:`)의 카드를 띄운다. */
  openTopicCard(nd: N3): void;
  /** 떠 있는 카드의 집: 지도 카드의 좌상단에서 한 칸 들어온 화면 좌표. */
  homeCardPos(): { x: number; y: number };
  /** 그 주제의 색. 나침반이 지은 표(트리와 지도가 같이 쓰는 한 벌)에서 읽는다. 나침반이 없으면 undefined, 표에 없으면 "". */
  topicColour(domain: string): string | undefined;
  /** 상세의 뒤로가기 줄에 돌아갈 노드가 있는가. */
  hasHistory(): boolean;
  /** `scope` 안의 뒤로 단추를 배선한다. `pane` 이면 돌아간 노드를 레일에 다시 열고, 아니면 카드를 띄운다. */
  wireDetailBack(scope: HTMLElement, pane: boolean): void;
  /** 지도에서 마지막으로 고른 노드(Reader 뒤로가기 자리). 열이 닫히면 놓는다. */
  lastNode(): N3 | null;
  /** 지금 초점인 노드 id(주소가 싣는 것). 없으면 빈 문자열. */
  focusedId(): string;
  /** 그 노드의 맥락(`/node-context`)을 한 번만 받는다. 레일과 카드가 같은 약속을 나눠 쓴다. */
  nodeContextOnce(id: string): Promise<NodeContext>;
  /** 노드 맥락 메모를 비운다. 다음 `nodeContextOnce` 는 새로 받는다. */
  dropNodeContext(): void;
  /** 지금 층(trail 의 끝)을 다시 그린다. `keepPanel` · `keepCamera` 면 열과 나침반의 자리를 둔 채 다시 그린다. */
  showLevel(opts?: { keepPanel?: boolean; keepCamera?: boolean }): Promise<void>;
  /** 열을 연다(레일의 `openRail`). 안의 편집기를 먼저 확정하고, 새 판을 돌려준다. */
  openRail(): number;
  /** 열 머리 한 벌의 마크업(레일의 `railHeadHTML`): 캡션 · 크기 전환 · 닫기. */
  railHeadHTML(caption: string): string;
  /** 지금 열에 그린 머리의 크기 전환 · 닫기 단추를 배선한다(레일의 `wireRailHead`). */
  wireRailHead(): void;
  /** 지금 층에 그린 노드. */
  curNodes(): GraphNode[];
  /** 지금 층에 그린 관계. 층을 다시 그리면 새 배열이 된다: 늦게 온 응답은 같은 배열인지로 가린다. */
  curEdges(): GraphEdge[];
  /** 지도 밖 진입점의 열기: 그 노드를 고르고(카드 · 주소) 레일까지 연다. */
  openDetail(nd: N3): void;
  /** 지금 열의 판(레일의 `revision`). 열을 다시 열거나 층을 다시 그리면 바뀐다: 늦게 온 응답은 판이 같은지로 가린다. */
  railRevision(): number;
  /** 열이 넓게(지도를 덮는 크기로) 열려 있는가(레일의 `full`). */
  railFull(): boolean;
  /** 노드 상세의 ask 줄에 수치를 채우는 손. 노드 상세가 그리지 않았거나 다른 것이 열을 차지하면 null. */
  railAskCounts(): ((deg: number, docs: number) => void) | null;
  /** 그 손을 놓는다. 같은 열에 문서를 그릴 때 늦게 온 노드 수치가 문서의 메뉴를 덮지 않게 한다. */
  dropRailAskCounts(): void;
  /** 그 노드를 지도에 다시 세우고 문서 열의 노드 상세로 연다(기본 정보를 저장한 뒤). */
  openNodePane(id: string, domain?: string, label?: string): Promise<void>;
}
