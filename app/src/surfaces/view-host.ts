// 그래프 표면(`view.ts`)과 거기서 떼어 낸 묶음들이 주고받는 형식: 노드 한 개와, 표면이 묶음에 내주는 손잡이.
// 형식만 둔다(실행되는 코드가 없다). 묶음을 더 떼어 낼 때는 그 묶음이 쓰는 손잡이만 `ViewHost` 에 더한다.

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
}
